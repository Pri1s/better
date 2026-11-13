"""As-of features for every player in every game, built in one forward pass.

The pass walks the calendar one day at a time. For each day it first *emits* a
feature row for every player in that day's games from the state it holds, and only
then *updates* the state with that day's box scores. A day's outcomes therefore
cannot reach that day's features, by construction rather than by care: the code
that reads state and the code that writes it are separate steps.

Training and live prediction share this path. A game that has not been played is
just a row that is emitted and never used for an update.

Everything known "as of" a day means: box scores of earlier days, the schedule,
and the injury report that was the latest one published by the cutoff (12:00 UTC
on the game date; see specs/decisions.md, D5).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date

import duckdb
import numpy as np

from better import stats

FIRST_SEASON = 2019  # 2018-19, the first season with injury reports
CUTOFF_HOUR_UTC = 12

# Half-lives, in games played, of the three exponentially weighted player histories.
HALF_LIVES = (6.0, 30.0, 120.0)
SHORT, MEDIUM, LONG = 0, 1, 2
TEAM_HALF_LIFE = 15.0  # team pace and defence
AVAILABILITY_HALF_LIFE = 10.0  # how often he has been playing lately
BASELINE_HALF_LIFE = 10.0  # how short-handed his team has been in the games he played

# What the state tracks per player: the box-score columns and the two flags.
TRACKED: tuple[str, ...] = (*stats.COMPONENTS, "double_double", "triple_double")
N_TRACKED = len(TRACKED)
_I = {name: index for index, name in enumerate(TRACKED)}

POSITION_GROUP = {"G": 0, "PG": 0, "SG": 0, "F": 1, "SF": 1, "PF": 1, "C": 2}
N_GROUPS = 3
N_TEAMS = 31  # ESPN numbers the franchises 1 to 30; index 0 is unused

# Status weights are observed sit rates from earlier completed games only.
# An unseen status stays missing rather than introducing an assumed prior.
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")

COMMON_NAMES: tuple[str, ...] = (
    # Minutes and role
    "min_s",
    "min_m",
    "min_l",
    "min_last",
    "min_sd",
    "min_season",
    "start_s",
    "start_m",
    "start_last",
    "gp_season",
    "gp_career",
    "days_since_played",
    "availability",
    "usage_s",
    "usage_m",
    # Schedule
    "is_home",
    "rest_days",
    "is_back_to_back",
    "games_prior_6_days",
    "team_game_number",
    "opp_rest_days",
    "is_playoff",
    "position",
    # The two teams
    "team_pace",
    "opp_pace",
    "team_off",
    "team_def",
    "opp_off",
    "opp_def",
    "net_diff",
    # The injury report as of the cutoff
    "has_report",
    "own_status",
    "team_min_out",
    "team_usage_out",
    "team_ast_out",
    "team_reb_out",
    "pos_min_out",
    "min_out_delta",
    "usage_out_delta",
    "pos_min_out_delta",
    "opp_min_out",
)
_C = {name: index for index, name in enumerate(COMMON_NAMES)}

STAT_NAMES: tuple[str, ...] = (
    "stat_rate_s",
    "stat_rate_m",
    "stat_rate_l",  # per minute
    "stat_pg_s",
    "stat_pg_m",
    "stat_pg_l",  # per game
    "stat_proj_s",
    "stat_proj_m",
    "stat_proj_l",  # per-minute rate times projected minutes
    "stat_season",  # season-to-date per game
    "stat_prev_rate",  # previous season, per minute
    "stat_opp_allow",  # what the opponent allows, vs the league
    "stat_opp_pos_allow",  # ... to players at his position
)


def weights_of(stat: stats.Stat) -> np.ndarray:
    """The stat as a weighted sum of the tracked quantities."""
    weights = np.zeros(N_TRACKED)
    if stat.is_linear:
        for column, weight in stat.weights:
            weights[_I[column]] = weight
    else:
        weights[_I[stat.key]] = 1.0  # a flag is tracked under its own name
    return weights


@dataclass
class FeatureTable:
    """One row per player per game, in date order, with everything known beforehand."""

    game_id: np.ndarray
    player_id: np.ndarray
    team_id: np.ndarray
    opponent_team_id: np.ndarray
    game_date: np.ndarray  # datetime64[D]
    season: np.ndarray
    played: np.ndarray  # True when the row has an outcome
    minutes: np.ndarray  # NaN until played
    actual: np.ndarray  # (rows, N_TRACKED); NaN until played
    common: np.ndarray  # (rows, len(COMMON_NAMES))
    rate: np.ndarray  # (rows, 3 half-lives, N_TRACKED), per minute
    season_pg: np.ndarray  # (rows, N_TRACKED) season-to-date per game
    previous_rate: np.ndarray  # (rows, N_TRACKED), immediately preceding season
    opp_allow: np.ndarray  # (rows, N_TRACKED) opponent allows per game
    league_allow: np.ndarray  # (rows, N_TRACKED) league average of the same
    opp_pos_allow: np.ndarray  # the same two, to his position group only
    league_pos_allow: np.ndarray

    def __len__(self) -> int:
        return len(self.game_id)

    def column(self, name: str) -> np.ndarray:
        return self.common[:, _C[name]]

    def target(self, stat: stats.Stat) -> np.ndarray:
        """What the stat turned out to be. NaN for games not played."""
        return _weighted(self.actual, weights_of(stat))

    def stat_columns(self, stat: stats.Stat, proj_minutes: np.ndarray) -> np.ndarray:
        """The columns in ``STAT_NAMES`` for one stat, given projected minutes."""
        weights = weights_of(stat).astype(self.rate.dtype)
        rate = _weighted(self.rate, weights)  # (rows, 3)
        minutes = self.common[:, [_C["min_s"], _C["min_m"], _C["min_l"]]]
        per_game = rate * minutes
        projected = rate * np.asarray(proj_minutes, dtype=float)[:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            allow = _ratio(
                _weighted(self.opp_allow, weights), _weighted(self.league_allow, weights)
            )
            pos_allow = _ratio(
                _weighted(self.opp_pos_allow, weights), _weighted(self.league_pos_allow, weights)
            )
        return np.column_stack(
            [
                rate,
                per_game,
                projected,
                _weighted(self.season_pg, weights),
                _weighted(self.previous_rate, weights),
                allow,
                pos_allow,
            ]
        )

    def baseline_season(self, stat: stats.Stat) -> np.ndarray:
        """Baseline A: his season-to-date average (his longer history early in a season)."""
        weights = weights_of(stat).astype(self.rate.dtype)
        season = _weighted(self.season_pg, weights)
        history = (_weighted(self.rate[:, LONG, :], weights)) * self.column("min_l")
        return np.where(np.isnan(season), history, season)

    def baseline_rate(self, stat: stats.Stat) -> np.ndarray:
        """Baseline B: long-run per-minute rate times recent minutes."""
        weights = weights_of(stat).astype(self.rate.dtype)
        return (_weighted(self.rate[:, LONG, :], weights)) * self.column("min_s")


def _weighted(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Exclude unused columns so a missing rebound cannot erase a points target."""
    used = weights != 0
    return values[..., used] @ weights[used]


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.where(denominator > 0, numerator / np.where(denominator > 0, denominator, 1), np.nan)


def build(
    con: duckdb.DuckDBPyConnection,
    *,
    first_season: int = FIRST_SEASON,
    through: date | str | None = None,
    outcomes_through: date | str | None = None,
    extra_rows: list[tuple[int, int, int]] | None = None,
) -> FeatureTable:
    """Run the forward pass, reading no outcomes beyond the requested horizon.

    ``extra_rows`` are ``(game_id, player_id, team_id)`` for players in games that
    have no box score yet: the upcoming games a prediction is wanted for.
    ``through`` limits all dates; ``outcomes_through`` can additionally cap box
    scores while allowing later scheduled games to receive predictions.
    """
    through = date.fromisoformat(str(through)) if through is not None else date.max
    outcomes_through = (
        date.fromisoformat(str(outcomes_through)) if outcomes_through is not None else through
    )
    return _Pass(
        _load(con, first_season, through, min(through, outcomes_through)), extra_rows or []
    ).run()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_BOX_SQL = f"""
SELECT game_id, player_id, team_id, opponent_team_id, game_date, season,
       starter, did_not_play, minutes, position,
       {", ".join(stats.COMPONENTS)}
FROM player_game_stats
WHERE season >= ? AND game_date <= ?
ORDER BY game_date, game_id, team_id, player_id
"""

_CONTEXT_SQL = """
SELECT game_id, team_id, opponent_team_id, game_date, season, season_type, is_home,
       rest_days, is_back_to_back, games_in_prior_6_days, team_game_number,
       opponent_rest_days
FROM team_game_context
WHERE season >= ? AND game_date <= ?
ORDER BY game_date, game_id, team_id
"""

# For each game, the latest injury report published at or before its cutoff.
_PICKED_SQL = """
CREATE OR REPLACE TEMP TABLE feature_reports AS
WITH cutoffs AS (
    SELECT game_id, game_date,
           CAST(game_date AS TIMESTAMP) + to_hours(?) AS cutoff_utc
    FROM games
    WHERE season >= ? AND game_date <= ?
),
reports AS (
    SELECT DISTINCT report_time_utc, game_date FROM injury_report_rows
)
SELECT c.game_id, max(r.report_time_utc) AS report_time_utc
FROM cutoffs c
JOIN reports r ON r.game_date = c.game_date AND r.report_time_utc <= c.cutoff_utc
GROUP BY c.game_id
"""

# That report's rows for the game. A team with nobody listed has no rows, which
# means "everyone available"; a team whose row says the report was not yet
# submitted has a row without a player, which means "unknown".
_LISTED_SQL = """
SELECT p.game_id, i.team_id, i.player_id, i.status,
       i.report_player_name IS NULL AS not_submitted
FROM feature_reports p
JOIN injury_reports i ON i.game_id = p.game_id AND i.report_time_utc = p.report_time_utc
WHERE i.team_id IS NOT NULL
"""


@dataclass
class _Data:
    box: dict[str, np.ndarray]
    context: dict[str, np.ndarray]
    injuries: dict[str, np.ndarray]
    reported_games: np.ndarray  # games a report covered by the cutoff
    team_box: dict[str, np.ndarray]


def _load(
    con: duckdb.DuckDBPyConnection, first_season: int, through: date, outcomes_through: date
) -> _Data:
    box = con.execute(_BOX_SQL, [first_season, outcomes_through]).fetchnumpy()
    context = con.execute(_CONTEXT_SQL, [first_season, through]).fetchnumpy()
    team_box = con.execute(
        f"""SELECT game_id, team_id, {", ".join(stats.COMPONENTS)}
           FROM team_game_stats WHERE season >= ? AND game_date <= ?""",
        [first_season, outcomes_through],
    ).fetchnumpy()
    con.execute(_PICKED_SQL, [CUTOFF_HOUR_UTC, first_season, through])
    reported = con.execute("SELECT game_id FROM feature_reports").fetchnumpy()["game_id"]
    injuries = con.execute(_LISTED_SQL).fetchnumpy()
    con.execute("DROP TABLE feature_reports")
    return _Data(box, context, injuries, np.asarray(reported, dtype=np.int64), team_box)


def _floats(values: np.ndarray) -> np.ndarray:
    """A float array with NaN where the database had NULL."""
    if isinstance(values, np.ma.MaskedArray):
        return np.ma.filled(values.astype(float), np.nan)
    return np.asarray(values, dtype=float)


def _days(values: np.ndarray) -> np.ndarray:
    """Dates as whole days since the epoch."""
    if isinstance(values, np.ma.MaskedArray):
        values = values.filled(np.datetime64("NaT"))
    return np.asarray(values).astype("datetime64[D]").astype(np.int64)


def _texts(values: np.ndarray) -> list:
    if isinstance(values, np.ma.MaskedArray):
        return values.filled(None).tolist()
    return np.asarray(values, dtype=object).tolist()


def _slot_key(game_id: np.ndarray, team_id: np.ndarray) -> np.ndarray:
    """One number per (game, team). Team ids are below 100."""
    return np.asarray(game_id, dtype=np.int64) * 100 + np.asarray(team_id, dtype=np.int64)


# ---------------------------------------------------------------------------
# The forward pass
# ---------------------------------------------------------------------------


class _Pass:
    def __init__(self, data: _Data, extra_rows: list[tuple[int, int, int]]):
        box, context, injuries = data.box, data.context, data.injuries

        # --- one slot per team per game, played or not ---------------------------
        self.slot_key = _slot_key(context["game_id"], context["team_id"])
        order = np.argsort(self.slot_key, kind="stable")
        self._slot_sorted, self._slot_order = self.slot_key[order], order
        self.slot_game = np.asarray(context["game_id"], dtype=np.int64)
        self.slot_team = np.asarray(context["team_id"], dtype=np.int64)
        self.slot_opp = np.asarray(context["opponent_team_id"], dtype=np.int64)
        self.slot_day = _days(context["game_date"])
        self.slot_season = np.asarray(context["season"], dtype=np.int64)
        self.slot_other = self._slots_of(self.slot_game, self.slot_opp)
        season_type = np.asarray(_texts(context["season_type"]), dtype=object)
        self.slot_context = np.column_stack(
            [
                _floats(context["is_home"]),
                _floats(context["rest_days"]),
                _floats(context["is_back_to_back"]),
                _floats(context["games_in_prior_6_days"]),
                _floats(context["team_game_number"]),
                _floats(context["opponent_rest_days"]),
                (season_type != "regular").astype(float),
            ]
        )

        # --- box-score rows -------------------------------------------------------
        self.box_game = np.asarray(box["game_id"], dtype=np.int64)
        self.box_team = np.asarray(box["team_id"], dtype=np.int64)
        self.box_opp = np.asarray(box["opponent_team_id"], dtype=np.int64)
        self.box_day = _days(box["game_date"])
        self.box_season = np.asarray(box["season"], dtype=np.int64)
        self.box_started = _floats(box["starter"])
        self.box_minutes = _floats(box["minutes"])
        self.box_dnp = np.asarray(_floats(box["did_not_play"]) > 0)
        self.box_group = np.array(
            [POSITION_GROUP.get(text, -1) for text in _texts(box["position"])], dtype=np.int64
        )
        components = np.column_stack([_floats(box[name]) for name in stats.COMPONENTS])
        self.box_tracked = np.column_stack([components, _flags(components)])
        # A row counts as played when he was not a DNP and his minutes are recorded.
        self.box_played = ~self.box_dnp & ~np.isnan(self.box_minutes)
        box_player = np.asarray(box["player_id"], dtype=np.int64)
        self.box_slot = self._slots_of(self.box_game, self.box_team)
        if np.any(self.box_slot < 0):
            orphan = self.box_game[self.box_slot < 0][0]
            raise ValueError(f"game {orphan} has a box score but is not on the schedule")
        self.out_row = np.full(len(self.box_game), -1, dtype=np.int64)  # filled as rows are emitted

        # --- upcoming rows asked for ----------------------------------------------
        extra = np.array(extra_rows, dtype=np.int64).reshape(-1, 3)
        existing = set(zip(self.box_game.tolist(), box_player.tolist(), strict=True))
        seen_extra = set()
        for game, player, _ in extra:
            key = (int(game), int(player))
            if key in existing or key in seen_extra:
                raise ValueError(f"duplicate feature row for game {game}, player {player}")
            seen_extra.add(key)
        self.extra_game, extra_player, self.extra_team = extra[:, 0], extra[:, 1], extra[:, 2]
        self.extra_slot = self._slots_of(self.extra_game, self.extra_team)
        if np.any(self.extra_slot < 0):
            missing = extra[self.extra_slot < 0][0]
            raise ValueError(
                f"team {missing[2]} does not play in game {missing[0]}: cannot build a row for it"
            )

        # --- injury listings ------------------------------------------------------
        listed_player = _floats(injuries["player_id"])
        status_code = np.array(
            [
                STATUSES.index(text) if text in STATUSES else -1
                for text in _texts(injuries["status"])
            ],
            dtype=np.int64,
        )
        inj_slot = self._slots_of(
            np.asarray(injuries["game_id"], dtype=np.int64),
            np.asarray(injuries["team_id"], dtype=np.int64),
        )
        # A team is covered when a report for its game existed by the cutoff and did
        # not say "not yet submitted" for it. Covered with nobody listed means healthy.
        self.report_slots = np.isin(self.slot_game, data.reported_games)
        pending = inj_slot[(inj_slot >= 0) & (_floats(injuries["not_submitted"]) > 0)]
        self.report_slots[pending] = False
        usable = (inj_slot >= 0) & ~np.isnan(listed_player) & (status_code >= 0)
        inj_player = listed_player[usable].astype(np.int64)
        self.inj_slot, self.inj_status = inj_slot[usable], status_code[usable]

        # --- players as dense indices ---------------------------------------------
        self.players = np.unique(np.concatenate([box_player, extra_player, inj_player]))
        self.box_p = np.searchsorted(self.players, box_player)
        self.extra_p = np.searchsorted(self.players, extra_player)
        self.inj_p = np.searchsorted(self.players, inj_player)
        self._init_state(len(self.players))
        team_box = data.team_box
        numeric_team_box = {
            name: _floats(values)
            for name, values in team_box.items()
            if name not in ("game_id", "team_id")
        }
        self.team_box = {
            (int(game), int(team)): {name: values[i] for name, values in numeric_team_box.items()}
            for i, (game, team) in enumerate(
                zip(team_box["game_id"], team_box["team_id"], strict=True)
            )
        }

    def _slots_of(self, game_id: np.ndarray, team_id: np.ndarray) -> np.ndarray:
        """Slot index of each (game, team), or -1 where the schedule has no such pair."""
        keys = _slot_key(game_id, team_id)
        if len(self._slot_sorted) == 0:
            return np.full(len(keys), -1, dtype=np.int64)
        position = np.clip(np.searchsorted(self._slot_sorted, keys), 0, len(self._slot_sorted) - 1)
        found = self._slot_sorted[position] == keys
        return np.where(found, self._slot_order[position], -1)

    def _init_state(self, n: int) -> None:
        self.decay = np.array([0.5 ** (1.0 / h) for h in HALF_LIVES])
        h = len(HALF_LIVES)
        # Player history at each half-life.
        self.ew_tracked = np.zeros((h, n, N_TRACKED))
        self.ew_minutes = np.zeros((h, n))
        self.ew_weight = np.zeros((h, n))
        self.ew_starts = np.zeros((h, n))
        self.ew_minutes_sq = np.zeros(n)  # at the medium half-life
        # This season so far.
        self.season_of = np.full(n, -1, dtype=np.int64)
        self.season_games = np.zeros(n)
        self.season_minutes = np.zeros(n)
        self.season_tracked = np.zeros((n, N_TRACKED))
        self.previous_season = np.full(n, -1, dtype=np.int64)
        self.previous_minutes = np.zeros(n)
        self.previous_tracked = np.zeros((n, N_TRACKED))
        # Odds and ends.
        self.career_games = np.zeros(n)
        self.last_day = np.full(n, -1, dtype=np.int64)
        self.last_minutes = np.full(n, np.nan)
        self.last_started = np.full(n, np.nan)
        self.group = np.full(n, -1, dtype=np.int64)
        self.avail_played = np.zeros(n)
        self.avail_weight = np.zeros(n)
        # How short-handed his team was in the games he played (the baseline).
        self.base_weight = np.zeros(n)
        self.base_min_out = np.zeros(n)
        self.base_usage_out = np.zeros(n)
        self.base_pos_out = np.zeros(n)
        # Teams.
        self.team_weight = np.zeros(N_TEAMS)
        self.team_poss = np.zeros(N_TEAMS)
        self.team_points = np.zeros(N_TEAMS)
        self.team_points_allowed = np.zeros(N_TEAMS)
        self.team_allow = np.zeros((N_TEAMS, N_TRACKED))
        self.team_pos_allow = np.zeros((N_TEAMS, N_GROUPS, N_TRACKED))
        # How often each status has meant sitting out, so far.
        self.status_seen = np.zeros(len(STATUSES))
        self.status_sat = np.zeros(len(STATUSES))

    # --- the walk -------------------------------------------------------------

    def run(self) -> FeatureTable:
        n = len(self.box_game) + len(self.extra_game)
        out = _Output(n)
        cursor = 0

        box_bounds = _bounds(self.box_day)
        inj_day = self.slot_day[self.inj_slot]
        inj_order = np.argsort(inj_day, kind="stable")
        inj_bounds = _bounds(inj_day[inj_order])
        extra_day = self.slot_day[self.extra_slot] if len(self.extra_slot) else np.array([], int)
        extra_order = np.argsort(extra_day, kind="stable")
        extra_bounds = _bounds(extra_day[extra_order])

        days = sorted(set(box_bounds) | set(extra_bounds))
        for day in days:
            lo, hi = box_bounds.get(day, (0, 0))
            box_rows = np.arange(lo, hi)
            listed = inj_order[slice(*inj_bounds.get(day, (0, 0)))]
            extras = extra_order[slice(*extra_bounds.get(day, (0, 0)))]

            # 1. Emit: read the state, write feature rows. Nothing is updated here.
            report = self._report(listed)
            cursor = self._emit(out, cursor, box_rows, extras, report, day)

            # 2. Update: only now does the day's outcome enter the state.
            self._learn_statuses(listed, box_rows[~self.box_dnp[box_rows]])
            self._update(box_rows, out, day)

        return out.table(cursor)

    def _report(self, listed: np.ndarray) -> _Report:
        """Per-team sums of what the injury report says is missing, as of the cutoff."""
        report = _Report(len(self.slot_key), len(self.players))
        if len(listed) == 0:
            return report
        slot, p = self.inj_slot[listed], self.inj_p[listed]
        weight = _ratio(self.status_sat, self.status_seen)[self.inj_status[listed]]
        known_weight = np.nan_to_num(weight)
        minutes, usage, assists, rebounds = self._per_game(p)
        group = self.group[p]
        n = len(self.slot_key)
        report.min_out = np.bincount(slot, known_weight * minutes, n)
        report.usage_out = np.bincount(slot, known_weight * usage, n)
        report.ast_out = np.bincount(slot, known_weight * assists, n)
        report.reb_out = np.bincount(slot, known_weight * rebounds, n)
        known = group >= 0
        report.pos_out = np.bincount(
            slot[known] * N_GROUPS + group[known], (known_weight * minutes)[known], n * N_GROUPS
        ).reshape(n, N_GROUPS)
        report.unknown = np.bincount(slot, np.isnan(weight), n)
        report.own_weight = dict(
            zip(zip(slot.tolist(), p.tolist(), strict=True), weight.tolist(), strict=True)
        )
        return report

    def _per_game(self, p: np.ndarray):
        """Recent per-game minutes, usage, assists and rebounds of some players."""
        weight = self.ew_weight[MEDIUM, p]
        safe = np.where(weight > 0, weight, 1.0)
        tracked = self.ew_tracked[MEDIUM, p]
        minutes = np.where(weight > 0, self.ew_minutes[MEDIUM, p] / safe, 0.0)
        usage = np.where(weight > 0, _usage(tracked) / safe, 0.0)
        assists = np.where(weight > 0, tracked[:, _I["assists"]] / safe, 0.0)
        rebounds = np.where(weight > 0, tracked[:, _I["rebounds"]] / safe, 0.0)
        return minutes, usage, assists, rebounds

    def _emit(
        self,
        out: _Output,
        cursor: int,
        played: np.ndarray,
        extras: np.ndarray,
        report: _Report,
        day: int,
    ) -> int:
        count = len(played) + len(extras)
        if count == 0:
            return cursor
        rows = slice(cursor, cursor + count)
        p = np.concatenate([self.box_p[played], self.extra_p[extras]])
        slot = np.concatenate([self.box_slot[played], self.extra_slot[extras]])
        self.out_row[played] = np.arange(cursor, cursor + len(played))
        team, opp = self.slot_team[slot], self.slot_opp[slot]
        season = self.slot_season[slot]

        out.game_id[rows] = self.slot_game[slot]
        out.player_id[rows] = self.players[p]
        out.team_id[rows] = team
        out.opponent_team_id[rows] = opp
        out.day[rows] = day
        out.season[rows] = season
        out.played[rows] = np.concatenate(
            [self.box_played[played], np.zeros(len(extras), dtype=bool)]
        )
        out.minutes[rows] = np.concatenate(
            [
                np.where(self.box_played[played], self.box_minutes[played], np.nan),
                np.full(len(extras), np.nan),
            ]
        )
        out.actual[rows] = np.concatenate(
            [
                np.where(self.box_played[played, None], self.box_tracked[played], np.nan),
                np.full((len(extras), N_TRACKED), np.nan),
            ]
        )

        common = np.full((count, len(COMMON_NAMES)), np.nan)

        def put(name: str, values) -> None:
            common[:, _C[name]] = values

        # --- his own history ---
        weight = self.ew_weight[:, p]  # (3, count)
        seen = weight > 0
        safe = np.where(seen, weight, 1.0)
        minutes = np.where(seen, self.ew_minutes[:, p] / safe, np.nan)  # per game
        put("min_s", minutes[SHORT])
        put("min_m", minutes[MEDIUM])
        put("min_l", minutes[LONG])
        put("min_last", self.last_minutes[p])
        spread = self.ew_minutes_sq[p] / safe[MEDIUM] - np.nan_to_num(minutes[MEDIUM]) ** 2
        put("min_sd", np.where(seen[MEDIUM], np.sqrt(np.maximum(spread, 0.0)), np.nan))
        put("start_s", np.where(seen[SHORT], self.ew_starts[SHORT, p] / safe[SHORT], np.nan))
        put("start_m", np.where(seen[MEDIUM], self.ew_starts[MEDIUM, p] / safe[MEDIUM], np.nan))
        put("start_last", self.last_started[p])

        this_season = self.season_of[p] == season
        games = np.where(this_season, self.season_games[p], 0.0)
        put("gp_season", games)
        put("gp_career", self.career_games[p])
        put(
            "min_season", np.where(games > 0, self.season_minutes[p] / np.maximum(games, 1), np.nan)
        )
        put(
            "days_since_played",
            np.where(self.last_day[p] >= 0, np.minimum(day - self.last_day[p], 365), np.nan),
        )
        put(
            "availability",
            np.where(
                self.avail_weight[p] > 0,
                self.avail_played[p] / np.maximum(self.avail_weight[p], 1e-9),
                np.nan,
            ),
        )

        played_minutes = self.ew_minutes[:, p]
        has_minutes = played_minutes > 0
        safe_minutes = np.where(has_minutes, played_minutes, 1.0)
        rate = np.where(
            has_minutes[:, :, None], self.ew_tracked[:, p] / safe_minutes[:, :, None], np.nan
        )
        out.rate[rows] = np.transpose(rate, (1, 0, 2))
        put(
            "usage_s",
            np.where(
                has_minutes[SHORT], _usage(self.ew_tracked[SHORT, p]) / safe_minutes[SHORT], np.nan
            ),
        )
        put(
            "usage_m",
            np.where(
                has_minutes[MEDIUM],
                _usage(self.ew_tracked[MEDIUM, p]) / safe_minutes[MEDIUM],
                np.nan,
            ),
        )
        out.season_pg[rows] = np.where(
            (games > 0)[:, None], self.season_tracked[p] / np.maximum(games, 1)[:, None], np.nan
        )

        last_season = self.season_of[p] == season - 1
        previous_minutes = np.where(last_season, self.season_minutes[p], self.previous_minutes[p])
        previous = np.where(last_season[:, None], self.season_tracked[p], self.previous_tracked[p])
        has_previous = (last_season | (self.previous_season[p] == season - 1)) & (
            previous_minutes > 0
        )
        out.previous_rate[rows] = np.where(
            has_previous[:, None], previous / np.maximum(previous_minutes, 1)[:, None], np.nan
        )

        # --- schedule ---
        context = self.slot_context[slot]
        for index, name in enumerate(
            (
                "is_home",
                "rest_days",
                "is_back_to_back",
                "games_prior_6_days",
                "team_game_number",
                "opp_rest_days",
                "is_playoff",
            )
        ):
            put(name, context[:, index])
        group = self.group[p]
        put("position", np.where(group >= 0, group, np.nan))

        # --- the two teams ---
        pace, offence, defence = self._team_ratings()
        put("team_pace", pace[team])
        put("opp_pace", pace[opp])
        put("team_off", offence[team])
        put("team_def", defence[team])
        put("opp_off", offence[opp])
        put("opp_def", defence[opp])
        put("net_diff", (offence[team] - defence[team]) - (offence[opp] - defence[opp]))

        known_team = self.team_weight > 0
        safe_team = np.where(known_team, self.team_weight, 1.0)
        allow = np.where(known_team[:, None], self.team_allow / safe_team[:, None], np.nan)
        pos_allow = np.where(
            known_team[:, None, None], self.team_pos_allow / safe_team[:, None, None], np.nan
        )
        with warnings.catch_warnings():
            # Before any team has played there is nothing to average: NaN is the answer.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            league_allow = np.nanmean(allow[1:], axis=0)
            league_pos_allow = np.nanmean(pos_allow[1:], axis=0)  # (groups, tracked)
        out.opp_allow[rows] = allow[opp]
        out.league_allow[rows] = league_allow[None, :]
        has_group = group >= 0
        safe_group = np.where(has_group, group, 0)
        out.opp_pos_allow[rows] = np.where(has_group[:, None], pos_allow[opp, safe_group], np.nan)
        out.league_pos_allow[rows] = np.where(
            has_group[:, None], league_pos_allow[safe_group], np.nan
        )

        # --- the injury report ---
        other = self.slot_other[slot]
        own = np.array(
            [
                report.own_weight.get((int(s), int(player)), 0.0)
                for s, player in zip(slot, p, strict=True)
            ]
        )
        known_own = np.nan_to_num(own)
        unknown_teammate = report.unknown[slot] - np.isnan(own) > 0
        own_minutes, own_usage, own_assists, own_rebounds = self._per_game(p)
        team_min_out = np.where(
            unknown_teammate, np.nan, report.min_out[slot] - known_own * own_minutes
        )
        team_usage_out = np.where(
            unknown_teammate, np.nan, report.usage_out[slot] - known_own * own_usage
        )
        pos_out = np.where(
            has_group,
            np.where(
                unknown_teammate, np.nan, report.pos_out[slot, safe_group] - known_own * own_minutes
            ),
            np.nan,
        )
        covered = self.report_slots[slot]
        put("has_report", covered.astype(float))
        put("own_status", np.where(covered, own, np.nan))
        put("team_min_out", np.where(covered, team_min_out, np.nan))
        put("team_usage_out", np.where(covered, team_usage_out, np.nan))
        put(
            "team_ast_out",
            np.where(
                covered,
                np.where(unknown_teammate, np.nan, report.ast_out[slot] - known_own * own_assists),
                np.nan,
            ),
        )
        put(
            "team_reb_out",
            np.where(
                covered,
                np.where(unknown_teammate, np.nan, report.reb_out[slot] - known_own * own_rebounds),
                np.nan,
            ),
        )
        put("pos_min_out", np.where(covered, pos_out, np.nan))
        put(
            "opp_min_out",
            np.where(
                other >= 0,
                np.where(
                    self.report_slots[other],
                    np.where(report.unknown[other] > 0, np.nan, report.min_out[other]),
                    np.nan,
                ),
                np.nan,
            ),
        )
        has_base = self.base_weight[p] > 0
        safe_base = np.where(has_base, self.base_weight[p], 1.0)
        usual = has_base & covered
        put(
            "min_out_delta",
            np.where(usual, team_min_out - self.base_min_out[p] / safe_base, np.nan),
        )
        put(
            "usage_out_delta",
            np.where(usual, team_usage_out - self.base_usage_out[p] / safe_base, np.nan),
        )
        put(
            "pos_min_out_delta", np.where(usual, pos_out - self.base_pos_out[p] / safe_base, np.nan)
        )

        out.common[rows] = common
        # Kept for the update step: how short-handed his team was in this game.
        out.shorthanded[rows] = np.column_stack(
            [
                np.where(covered, team_min_out, np.nan),
                np.where(covered, team_usage_out, np.nan),
                np.where(covered, pos_out, np.nan),
            ]
        )
        return cursor + count

    def _team_ratings(self):
        known = (self.team_weight > 0) & (self.team_poss > 0)
        safe_weight = np.where(known, self.team_weight, 1.0)
        safe_poss = np.where(known, self.team_poss, 1.0)
        pace = np.where(known, self.team_poss / safe_weight, np.nan)
        offence = np.where(known, 100.0 * self.team_points / safe_poss, np.nan)
        defence = np.where(known, 100.0 * self.team_points_allowed / safe_poss, np.nan)
        return pace, offence, defence

    def _learn_statuses(self, listed: np.ndarray, played: np.ndarray) -> None:
        """Count how the day's listings turned out, for games that were played."""
        if len(listed) == 0:
            return
        took_the_floor = set(
            zip(self.box_slot[played].tolist(), self.box_p[played].tolist(), strict=True)
        )
        final_slots = np.zeros(len(self.slot_key), dtype=bool)
        final_slots[self.box_slot[played]] = True
        in_final = final_slots[self.inj_slot[listed]]
        status = self.inj_status[listed][in_final]
        sat = np.array(
            [
                (int(slot), int(player)) not in took_the_floor
                for slot, player in zip(
                    self.inj_slot[listed][in_final], self.inj_p[listed][in_final], strict=True
                )
            ]
        )
        self.status_seen += np.bincount(status, minlength=len(STATUSES))
        self.status_sat += np.bincount(status, sat.astype(float), len(STATUSES))

    def _update(self, box_rows: np.ndarray, out: _Output, day: int) -> None:
        if len(box_rows) == 0:
            return
        # One game at a time: within a game every player appears once, so plain
        # indexed assignment is safe even where the source dates two games on one day.
        _, starts = np.unique(self.box_game[box_rows], return_index=True)
        ends = np.append(starts[1:], len(box_rows))
        for start, end in zip(starts, ends, strict=True):
            rows = box_rows[start:end]
            self._update_players(rows, out, day)
            self._update_teams(rows[self.box_played[rows]])

    def _update_players(self, rows: np.ndarray, out: _Output, day: int) -> None:
        p_all = self.box_p[rows]
        group = self.box_group[rows]
        self.group[p_all[group >= 0]] = group[group >= 0]

        # Availability: every listing in a box score counts, played or not.
        decay = 0.5 ** (1.0 / AVAILABILITY_HALF_LIFE)
        played_flag = self.box_played[rows]
        self.avail_played[p_all] = self.avail_played[p_all] * decay + played_flag
        self.avail_weight[p_all] = self.avail_weight[p_all] * decay + 1.0

        rows = rows[played_flag]
        if len(rows) == 0:
            return
        p = self.box_p[rows]
        minutes, tracked = self.box_minutes[rows], self.box_tracked[rows]
        started = np.nan_to_num(self.box_started[rows])
        for h, decay in enumerate(self.decay):
            self.ew_tracked[h, p] = self.ew_tracked[h, p] * decay + tracked
            self.ew_minutes[h, p] = self.ew_minutes[h, p] * decay + minutes
            self.ew_weight[h, p] = self.ew_weight[h, p] * decay + 1.0
            self.ew_starts[h, p] = self.ew_starts[h, p] * decay + started
        self.ew_minutes_sq[p] = self.ew_minutes_sq[p] * self.decay[MEDIUM] + minutes**2

        season = self.box_season[rows]
        fresh = self.season_of[p] != season
        pf = p[fresh]
        self.previous_season[pf] = self.season_of[pf]
        self.previous_minutes[pf] = self.season_minutes[pf]
        self.previous_tracked[pf] = self.season_tracked[pf]
        self.season_games[p[fresh]] = 0.0
        self.season_minutes[p[fresh]] = 0.0
        self.season_tracked[p[fresh]] = 0.0
        self.season_of[p] = season
        self.season_games[p] += 1.0
        self.season_minutes[p] += minutes
        self.season_tracked[p] += tracked

        self.career_games[p] += 1.0
        self.last_day[p] = day
        self.last_minutes[p] = minutes
        self.last_started[p] = started

        # The baseline uses what was emitted for these very rows before the update.
        emitted = out.shorthanded[self.out_row[rows]]
        known = ~np.isnan(emitted[:, 0])
        pk = p[known]
        decay = 0.5 ** (1.0 / BASELINE_HALF_LIFE)
        self.base_weight[pk] = self.base_weight[pk] * decay + 1.0
        self.base_min_out[pk] = self.base_min_out[pk] * decay + emitted[known, 0]
        self.base_usage_out[pk] = self.base_usage_out[pk] * decay + emitted[known, 1]
        self.base_pos_out[pk] = self.base_pos_out[pk] * decay + np.nan_to_num(emitted[known, 2])

    def _update_teams(self, rows: np.ndarray) -> None:
        """Fold one played game into the two teams' pace and what each allows."""
        teams = np.unique(self.box_team[rows])
        if len(teams) != 2:
            return  # one side's box score is missing: nothing reliable to learn
        totals, by_group = {}, {}
        for team in teams:
            mine = rows[self.box_team[rows] == team]
            tracked = self.box_tracked[mine]
            totals[team] = tracked.sum(axis=0)
            official = self.team_box.get((int(self.box_game[rows[0]]), int(team)))
            if official is not None:
                # Team totals include team turnovers and survive incomplete player boxes.
                for name in stats.COMPONENTS:
                    totals[team][_I[name]] = official[name]
            groups = np.zeros((N_GROUPS, N_TRACKED))
            for g in range(N_GROUPS):
                groups[g] = tracked[self.box_group[mine] == g].sum(axis=0)
            by_group[team] = groups
        possessions = np.mean([_possessions(totals[team]) for team in teams])
        decay = 0.5 ** (1.0 / TEAM_HALF_LIFE)
        for team, other in ((teams[0], teams[1]), (teams[1], teams[0])):
            self.team_weight[team] = self.team_weight[team] * decay + 1.0
            self.team_poss[team] = self.team_poss[team] * decay + possessions
            self.team_points[team] = self.team_points[team] * decay + totals[team][_I["points"]]
            self.team_points_allowed[team] = (
                self.team_points_allowed[team] * decay + totals[other][_I["points"]]
            )
            self.team_allow[team] = self.team_allow[team] * decay + totals[other]
            self.team_pos_allow[team] = self.team_pos_allow[team] * decay + by_group[other]


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------


class _Report:
    """What the injury report implies for each team slot on one day."""

    def __init__(self, n_slots: int, n_players: int):
        self.min_out = np.zeros(n_slots)
        self.usage_out = np.zeros(n_slots)
        self.ast_out = np.zeros(n_slots)
        self.reb_out = np.zeros(n_slots)
        self.pos_out = np.zeros((n_slots, N_GROUPS))
        self.own_weight: dict[tuple[int, int], float] = {}
        self.unknown = np.zeros(n_slots)


class _Output:
    """Preallocated arrays the pass fills in date order."""

    def __init__(self, n: int):
        self.game_id = np.zeros(n, dtype=np.int64)
        self.player_id = np.zeros(n, dtype=np.int64)
        self.team_id = np.zeros(n, dtype=np.int64)
        self.opponent_team_id = np.zeros(n, dtype=np.int64)
        self.day = np.zeros(n, dtype=np.int64)
        self.season = np.zeros(n, dtype=np.int64)
        self.played = np.zeros(n, dtype=bool)
        self.minutes = np.full(n, np.nan)
        self.actual = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.common = np.full((n, len(COMMON_NAMES)), np.nan, dtype=np.float32)
        self.rate = np.full((n, len(HALF_LIVES), N_TRACKED), np.nan, dtype=np.float32)
        self.season_pg = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.previous_rate = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.opp_allow = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.league_allow = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.opp_pos_allow = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.league_pos_allow = np.full((n, N_TRACKED), np.nan, dtype=np.float32)
        self.shorthanded = np.full((n, 3), np.nan)

    def table(self, n: int) -> FeatureTable:
        return FeatureTable(
            game_id=self.game_id[:n],
            player_id=self.player_id[:n],
            team_id=self.team_id[:n],
            opponent_team_id=self.opponent_team_id[:n],
            game_date=self.day[:n].astype("datetime64[D]"),
            season=self.season[:n],
            played=self.played[:n],
            minutes=self.minutes[:n],
            actual=self.actual[:n],
            common=self.common[:n],
            rate=self.rate[:n],
            season_pg=self.season_pg[:n],
            previous_rate=self.previous_rate[:n],
            opp_allow=self.opp_allow[:n],
            league_allow=self.league_allow[:n],
            opp_pos_allow=self.opp_pos_allow[:n],
            league_pos_allow=self.league_pos_allow[:n],
        )


def _bounds(days: np.ndarray) -> dict[int, tuple[int, int]]:
    """For sorted day numbers, the half-open row range of each day."""
    if len(days) == 0:
        return {}
    values, starts = np.unique(days, return_index=True)
    ends = np.append(starts[1:], len(days))
    return {int(day): (int(lo), int(hi)) for day, lo, hi in zip(values, starts, ends, strict=True)}


def _flags(components: np.ndarray) -> np.ndarray:
    """Double-double and triple-double columns from the box-score columns."""
    tens = sum(
        (components[:, _I[name]] >= 10).astype(float)
        for name in ("points", "rebounds", "assists", "steals", "blocks")
    )
    missing = np.any(
        np.isnan(
            components[
                :, [_I[name] for name in ("points", "rebounds", "assists", "steals", "blocks")]
            ]
        ),
        axis=1,
    )
    flags = np.column_stack([(tens >= 2).astype(float), (tens >= 3).astype(float)])
    flags[missing] = np.nan
    return flags


def _usage(tracked: np.ndarray) -> np.ndarray:
    """Possessions a player used: shots, trips to the line and turnovers."""
    return (
        tracked[..., _I["field_goals_attempted"]]
        + 0.44 * tracked[..., _I["free_throws_attempted"]]
        + tracked[..., _I["turnovers"]]
    )


def _possessions(totals: np.ndarray) -> float:
    return float(
        totals[_I["field_goals_attempted"]]
        + 0.44 * totals[_I["free_throws_attempted"]]
        - totals[_I["offensive_rebounds"]]
        + totals[_I["turnovers"]]
    )


def cutoff_of(game_date: date) -> str:
    """The cutoff for a game date, as text for messages."""
    return f"{game_date.isoformat()} {CUTOFF_HOUR_UTC:02d}:00 UTC"
