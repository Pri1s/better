"""Tie each line to a game, a player and a stat.

Everything downstream joins through these links, so a wrong one corrupts results
without raising anything. The rules here therefore prefer leaving a line unlinked
to guessing: a player is matched inside the rosters of the two teams in the game
before anywhere else, and anything that fits two players is left alone.

Mapping only fills the link columns of ``prop_lines``. It can be run again at any
time, and should be after aliases are added or new box scores are loaded: a rookie
becomes linkable once he has appeared in a box score.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import duckdb

from better import config

LINKED = "linked"
COMBO = "combo"
NO_STAT = "no_stat"
NO_GAME = "no_game"
NO_PLAYER = "no_player"
AMBIGUOUS = "ambiguous_player"
STATUSES = (LINKED, COMBO, NO_STAT, NO_GAME, NO_PLAYER, AMBIGUOUS)

LOCAL_ALIAS_FILE = "player_aliases.csv"


class MappingError(RuntimeError):
    """A mapping step produced more than one answer for a line."""


@dataclass
class MappingSummary:
    lines: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_match: dict[str, int] = field(default_factory=dict)
    aliases: int = 0

    @property
    def linked(self) -> int:
        return self.by_status.get(LINKED, 0)

    def line(self) -> str:
        if not self.lines:
            return "No lines to map."
        share = self.linked / self.lines
        rest = ", ".join(
            f"{count:,} {status}" for status, count in self.by_status.items() if status != LINKED
        )
        text = f"Linked {self.linked:,} of {self.lines:,} lines ({share:.1%})"
        return f"{text}; {rest}." if rest else f"{text}."


# One row per line in scope, with every key the matching needs. Team text resolves
# through team_keys; the game date is the US Eastern date of the start time, which
# is how games are dated.
_WORK = """
CREATE OR REPLACE TEMP TABLE link_work AS
WITH alias_keys AS (
    -- One target per alias; a local alias wins over a built-in one.
    SELECT name_key(alias) AS alias_key, arg_max(name_key(player), origin = 'local') AS key
    FROM player_aliases
    GROUP BY name_key(alias)
)
SELECT
    l.snapshot_id,
    l.line_no,
    l.is_combo,
    l.start_time_utc,
    CAST((l.start_time_utc AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York' AS DATE)
        AS game_date,
    n.stat,
    coalesce(team_code.team_id, team_name.team_id) AS stated_team_id,
    coalesce(opp_code.team_id, opp_name.team_id)   AS stated_opponent_id,
    home.team_id AS home_id,
    away.team_id AS away_id,
    -- Whether the source named any team, resolved or not. A team that is named but
    -- not recognised must show up as unlinked, never fall through to a looser rule.
    (l.team_code IS NOT NULL OR l.team_name IS NOT NULL
     OR l.home_team_name IS NOT NULL OR l.away_team_name IS NOT NULL) AS names_a_team,
    name_key(l.player_name)           AS key,
    name_key_no_suffix(l.player_name) AS key_no_suffix,
    a.key                             AS alias_key
FROM prop_lines l
LEFT JOIN stat_names n
       ON n.family = CASE WHEN l.source = 'odds_api' THEN 'odds_api' ELSE 'prizepicks' END
      AND n.stat_name = l.stat_name
      -- Every fantasy site scores differently; only PrizePicks' scoring is known here.
      AND (n.stat <> 'fantasy_score' OR l.book = 'prizepicks')
LEFT JOIN team_keys team_code ON team_code.key = name_key(l.team_code)
LEFT JOIN team_keys team_name ON team_name.key = name_key(l.team_name)
LEFT JOIN team_keys opp_code  ON opp_code.key = name_key(l.opponent_code)
LEFT JOIN team_keys opp_name  ON opp_name.key = name_key(l.opponent_name)
LEFT JOIN team_keys home      ON home.key = name_key(l.home_team_name)
LEFT JOIN team_keys away      ON away.key = name_key(l.away_team_name)
LEFT JOIN alias_keys a        ON a.alias_key = name_key(l.player_name)
{scope}
"""

# The game: the one the stated team plays that day (against the stated opponent,
# when there is one), or the one between the stated home and away teams. If a
# postponed game and its replacement share a date, the one that was played wins.
_GAMES = """
CREATE OR REPLACE TEMP TABLE link_games AS
SELECT w.snapshot_id, w.line_no, g.game_id, g.season, g.home_team_id, g.away_team_id
FROM link_work w
JOIN games g
  ON g.game_date = w.game_date
 AND (
        (w.stated_team_id IN (g.home_team_id, g.away_team_id)
         AND (w.stated_opponent_id IS NULL
              OR w.stated_opponent_id IN (g.home_team_id, g.away_team_id)))
     OR (w.stated_team_id IS NULL
         AND w.home_id = g.home_team_id AND w.away_id = g.away_team_id)
     )
QUALIFY row_number() OVER (
    PARTITION BY w.snapshot_id, w.line_no
    ORDER BY g.status IN ('postponed', 'canceled'),
             abs(epoch(g.tipoff_utc) - epoch(w.start_time_utc)) NULLS LAST,
             g.game_id
) = 1
"""

# The names to look a line's player up under: the printed name's key and, when an
# alias exists for it, the alias target's key. Kept as rows so that every lookup
# below is a plain equality join.
_KEYS = """
CREATE OR REPLACE TEMP TABLE link_keys AS
SELECT snapshot_id, line_no, key AS name_key FROM link_work WHERE NOT is_combo
UNION
SELECT snapshot_id, line_no, alias_key FROM link_work
WHERE NOT is_combo AND alias_key IS NOT NULL
"""

_ROSTERS = "CREATE OR REPLACE TEMP TABLE link_rosters AS SELECT * FROM roster_names"

# Players on the rosters of the two teams whose name fits. Level 1 is the exact key
# or an alias; level 2 is the key without 'Jr.' or 'III', used only when level 1
# finds nobody.
_ROSTER = """
CREATE OR REPLACE TEMP TABLE link_roster AS
WITH candidates AS (
    SELECT k.snapshot_id, k.line_no, r.player_id, r.team_id, r.first_game_date, 1 AS level
    FROM link_keys k
    JOIN link_games g USING (snapshot_id, line_no)
    JOIN link_rosters r
      ON r.name_key = k.name_key AND r.season = g.season
     AND r.team_id IN (g.home_team_id, g.away_team_id)
    UNION ALL
    SELECT w.snapshot_id, w.line_no, r.player_id, r.team_id, r.first_game_date, 2 AS level
    FROM link_work w
    JOIN link_games g USING (snapshot_id, line_no)
    JOIN link_rosters r
      ON r.name_key_no_suffix = w.key_no_suffix AND r.season = g.season
     AND r.team_id IN (g.home_team_id, g.away_team_id)
    WHERE NOT w.is_combo
),
best AS (
    SELECT c.*, w.game_date, w.stated_team_id,
           min(c.level) OVER (PARTITION BY c.snapshot_id, c.line_no) AS best_level
    FROM candidates c
    JOIN link_work w USING (snapshot_id, line_no)
)
SELECT snapshot_id, line_no,
       count(DISTINCT player_id) AS players,
       min(player_id) AS player_id,
       -- His team: the stated one; else, of the two, the one he most recently joined
       -- before this game.
       coalesce(
           any_value(stated_team_id),
           arg_max(team_id, first_game_date) FILTER (WHERE first_game_date <= game_date),
           arg_min(team_id, first_game_date)
       ) AS team_id
FROM best
WHERE level = best_level
GROUP BY snapshot_id, line_no
"""

# Nobody on either roster: look across the league, this season and last. A player
# who has just changed teams, or a season's first night, resolves this way. The
# exact key or alias is tried first and the suffix-free key second; either must
# fit exactly one player in the league.
_LEAGUE = """
CREATE OR REPLACE TEMP TABLE link_league AS
WITH candidates AS (
    SELECT k.snapshot_id, k.line_no, r.player_id, 1 AS level
    FROM link_keys k
    JOIN link_games g USING (snapshot_id, line_no)
    JOIN link_rosters r ON r.name_key = k.name_key
    WHERE r.season IN (g.season, g.season - 1)
    UNION ALL
    SELECT w.snapshot_id, w.line_no, r.player_id, 2 AS level
    FROM link_work w
    JOIN link_games g USING (snapshot_id, line_no)
    JOIN link_rosters r ON r.name_key_no_suffix = w.key_no_suffix
    WHERE r.season IN (g.season, g.season - 1) AND NOT w.is_combo
),
best AS (
    SELECT *, min(level) OVER (PARTITION BY snapshot_id, line_no) AS best_level
    FROM candidates
)
SELECT b.snapshot_id, b.line_no,
       count(DISTINCT b.player_id) AS players,
       min(b.player_id) AS player_id,
       any_value(w.stated_team_id) AS team_id
FROM best b
JOIN link_work w USING (snapshot_id, line_no)
WHERE b.level = b.best_level
  AND NOT EXISTS (
        SELECT 1 FROM link_roster x
        WHERE x.snapshot_id = b.snapshot_id AND x.line_no = b.line_no)
GROUP BY b.snapshot_id, b.line_no
"""

# A line that names no team at all: find the player first, then his team's game
# that day. The player must be the only one in the league that season with that
# name (exact key or alias first, suffix-free key second). His team is the one he
# had most recently joined by that date, so a player traded mid-season resolves to
# the right side of the trade.
_TEAMLESS = """
CREATE OR REPLACE TEMP TABLE link_teamless AS
WITH lines AS (
    SELECT snapshot_id, line_no, game_date, key_no_suffix
    FROM link_work
    WHERE NOT is_combo AND game_date IS NOT NULL AND NOT names_a_team
),
seasons AS (
    SELECT DISTINCT game_date, season FROM games
),
candidates AS (
    SELECT l.snapshot_id, l.line_no, l.game_date, r.player_id, r.team_id, r.season,
           r.first_game_date, 1 AS level
    FROM lines l
    JOIN link_keys k USING (snapshot_id, line_no)
    JOIN seasons s ON s.game_date = l.game_date
    JOIN link_rosters r ON r.name_key = k.name_key AND r.season = s.season
    UNION ALL
    SELECT l.snapshot_id, l.line_no, l.game_date, r.player_id, r.team_id, r.season,
           r.first_game_date, 2 AS level
    FROM lines l
    JOIN seasons s ON s.game_date = l.game_date
    JOIN link_rosters r ON r.name_key_no_suffix = l.key_no_suffix AND r.season = s.season
),
best AS (
    SELECT *, min(level) OVER (PARTITION BY snapshot_id, line_no) AS best_level
    FROM candidates
),
placed AS (
    SELECT snapshot_id, line_no, game_date, season,
           count(DISTINCT player_id) AS players,
           min(player_id) AS player_id,
           coalesce(
               arg_max(team_id, first_game_date) FILTER (WHERE first_game_date <= game_date),
               arg_min(team_id, first_game_date)
           ) AS team_id
    FROM best
    WHERE level = best_level
    GROUP BY ALL
)
SELECT p.snapshot_id, p.line_no, p.players, p.player_id, p.team_id,
       count(d.game_id) AS games,
       min(d.game_id)   AS game_id
FROM placed p
LEFT JOIN team_game_dates d
       ON d.team_id = p.team_id AND d.season = p.season AND d.game_date = p.game_date
GROUP BY ALL
"""

_RESULT = f"""
CREATE OR REPLACE TEMP TABLE link_result AS
WITH joined AS (
    SELECT
        w.snapshot_id, w.line_no, w.is_combo, w.stat,
        coalesce(g.game_id, CASE WHEN t.players = 1 AND t.games = 1 THEN t.game_id END)
            AS game_id,
        CASE
            WHEN r.players = 1 THEN r.player_id
            WHEN r.players IS NULL AND l.players = 1 THEN l.player_id
            WHEN t.players = 1 AND t.games = 1 THEN t.player_id
        END AS player_id,
        CASE
            WHEN r.players = 1 THEN r.team_id
            WHEN r.players IS NULL AND l.players = 1 THEN l.team_id
            WHEN t.players = 1 AND t.games = 1 THEN t.team_id
        END AS team_id,
        CASE
            WHEN r.players = 1 THEN 'team'
            WHEN r.players IS NULL AND l.players = 1 THEN 'league'
            WHEN t.players = 1 AND t.games = 1 THEN 'league'
        END AS player_match,
        coalesce(r.players, l.players, t.players, 0) AS players_found
    FROM link_work w
    LEFT JOIN link_games g USING (snapshot_id, line_no)
    LEFT JOIN link_roster r USING (snapshot_id, line_no)
    LEFT JOIN link_league l USING (snapshot_id, line_no)
    LEFT JOIN link_teamless t USING (snapshot_id, line_no)
)
SELECT
    snapshot_id, line_no, stat,
    CASE WHEN is_combo THEN NULL ELSE game_id END AS game_id,
    CASE WHEN is_combo THEN NULL ELSE team_id END AS team_id,
    CASE WHEN is_combo THEN NULL ELSE player_id END AS player_id,
    CASE WHEN is_combo THEN NULL ELSE player_match END AS player_match,
    CASE
        WHEN is_combo THEN '{COMBO}'
        WHEN stat IS NULL THEN '{NO_STAT}'
        WHEN game_id IS NULL AND players_found > 1 THEN '{AMBIGUOUS}'
        WHEN game_id IS NULL THEN '{NO_GAME}'
        WHEN player_id IS NULL AND players_found > 1 THEN '{AMBIGUOUS}'
        WHEN player_id IS NULL THEN '{NO_PLAYER}'
        ELSE '{LINKED}'
    END AS link_status
FROM joined
"""

_APPLY = """
UPDATE prop_lines AS l
SET game_id = r.game_id,
    team_id = r.team_id,
    player_id = r.player_id,
    stat = r.stat,
    link_status = r.link_status,
    player_match = r.player_match
FROM link_result r
WHERE l.snapshot_id = r.snapshot_id AND l.line_no = r.line_no
"""


def map_lines(
    con: duckdb.DuckDBPyConnection,
    snapshot_ids: Sequence[str] | None = None,
    *,
    local_aliases: Path | None = None,
) -> MappingSummary:
    """Link the lines of the given snapshots, or of every snapshot when none are given."""
    summary = MappingSummary(aliases=load_aliases(con, local_aliases))
    if snapshot_ids is not None and not snapshot_ids:
        return summary

    scope, params = "", []
    if snapshot_ids is not None:
        scope = f"WHERE l.snapshot_id IN ({', '.join('?' for _ in snapshot_ids)})"
        params = list(snapshot_ids)

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(_WORK.format(scope=scope), params)
        for step in (_GAMES, _KEYS, _ROSTERS, _ROSTER, _LEAGUE, _TEAMLESS, _RESULT):
            con.execute(step)
        lines, distinct = con.execute(
            "SELECT count(*), count(DISTINCT (snapshot_id, line_no)) FROM link_result"
        ).fetchone()
        if lines != distinct:
            raise MappingError(
                f"mapping produced {lines:,} rows for {distinct:,} lines: a line matched twice"
            )
        con.execute(_APPLY)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise

    summary.lines = lines
    summary.by_status = dict(con.execute(
        "SELECT link_status, count(*) FROM link_result GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall())
    summary.by_match = dict(con.execute(
        "SELECT player_match, count(*) FROM link_result WHERE player_match IS NOT NULL "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall())
    for table in ("link_work", "link_games", "link_keys", "link_rosters", "link_roster",
                  "link_league", "link_teamless", "link_result"):
        con.execute(f"DROP TABLE IF EXISTS {table}")
    return summary


def load_aliases(con: duckdb.DuckDBPyConnection, local_file: Path | None = None) -> int:
    """Reload ``player_aliases`` from the built-in file and the local one. Returns the count."""
    builtin = resources.files("better").joinpath("lines/aliases.csv").read_text(encoding="utf-8")
    entries = [(alias, player, "builtin") for alias, player in _read_aliases(builtin)]
    local_file = local_file or config.data_dir() / LOCAL_ALIAS_FILE
    if local_file.exists():
        text = local_file.read_text(encoding="utf-8")
        entries += [(alias, player, "local") for alias, player in _read_aliases(text)]
    con.execute("DELETE FROM player_aliases")
    if entries:
        con.executemany("INSERT INTO player_aliases VALUES (?, ?, ?)", entries)
    return len(entries)


def _read_aliases(text: str) -> list[tuple[str, str]]:
    """``alias,player`` rows of a CSV file with that header. Incomplete rows are skipped."""
    pairs = []
    for row in csv.DictReader(text.splitlines()):
        alias = (row.get("alias") or "").strip()
        player = (row.get("player") or "").strip()
        if alias and player and not alias.startswith("#"):
            pairs.append((alias, player))
    return pairs


def unmatched(con: duckdb.DuckDBPyConnection, limit: int = 40) -> list[dict]:
    """Unlinked lines grouped by reason and name, most lines first.

    For a name that found no player, ``closest`` lists the nearest names on the
    stated team's roster that season, which is usually enough to write an alias.
    """
    cursor = con.execute(
        """
        WITH groups AS (
            SELECT link_status, player_name,
                   coalesce(team_code, team_name) AS team,
                   count(*) AS lines,
                   min(CAST(start_time_utc AS DATE)) AS first_date,
                   max(CAST(start_time_utc AS DATE)) AS last_date,
                   any_value(stat_name) AS example_stat
            FROM prop_lines
            WHERE link_status IS DISTINCT FROM 'linked' AND link_status IS NOT NULL
            GROUP BY ALL
            ORDER BY lines DESC, link_status, player_name
            LIMIT ?
        ),
        team_of AS (
            SELECT g.*, k.team_id
            FROM groups g
            LEFT JOIN team_keys k ON k.key = name_key(g.team)
        ),
        near AS (
            SELECT t.link_status, t.player_name, t.team,
                   list(DISTINCT p.player_name ORDER BY p.player_name) AS closest
            FROM team_of t
            JOIN player_game_stats p
              ON p.team_id = t.team_id
             AND p.game_date BETWEEN t.first_date - INTERVAL 200 DAY
                                 AND t.last_date + INTERVAL 200 DAY
             AND jaro_winkler_similarity(name_key(p.player_name), name_key(t.player_name)) >= 0.85
            WHERE t.link_status IN ('no_player', 'ambiguous_player')
            GROUP BY ALL
        )
        SELECT g.link_status, g.player_name, g.team, g.lines, g.first_date, g.last_date,
               g.example_stat, n.closest
        FROM groups g
        LEFT JOIN near n
               ON n.link_status = g.link_status AND n.player_name = g.player_name
              AND n.team IS NOT DISTINCT FROM g.team
        ORDER BY g.lines DESC, g.link_status, g.player_name
        """,
        [limit],
    )
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
