"""The stats a prop line can be written on, and how each is read from a box score.

Every other part of the project refers to a stat by its ``key``. This module is
the only place that knows what PrizePicks or The Odds API call it, and how it is
computed from ``player_game_stats``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Box-score columns a stat can be built from.
COMPONENTS = (
    "points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls",
    "field_goals_made", "field_goals_attempted",
    "three_pointers_made", "three_pointers_attempted",
    "free_throws_made", "free_throws_attempted",
    "offensive_rebounds", "defensive_rebounds",
)

COUNT = "count"  # a whole number of something, or a sum of such numbers
SCORE = "score"  # a weighted sum that is not a whole number (fantasy score)
FLAG = "flag"    # 1 when something happened, 0 when it did not (double-double)


@dataclass(frozen=True)
class Stat:
    key: str
    label: str
    kind: str
    # Weight of each box-score column, for stats that are a weighted sum of columns.
    weights: tuple[tuple[str, float], ...] = ()
    # SQL over player_game_stats, for stats that are not a weighted sum.
    custom_sql: str | None = None

    @property
    def sql(self) -> str:
        """An expression over ``player_game_stats`` columns that yields the stat."""
        if self.custom_sql is not None:
            return self.custom_sql
        terms = []
        for column, weight in self.weights:
            if weight == 1:
                terms.append(f"+ {column}")
            elif weight == -1:
                terms.append(f"- {column}")
            else:
                terms.append(f"+ {weight!r} * {column}")
        return " ".join(terms).removeprefix("+ ")

    @property
    def is_linear(self) -> bool:
        return self.custom_sql is None


def _sum(key: str, label: str, *columns: str) -> Stat:
    return Stat(key, label, COUNT, tuple((column, 1.0) for column in columns))


def _tens(count: int) -> str:
    """SQL for "reached ten in at least ``count`` of the five counting categories"."""
    reached = " + ".join(
        f"CAST({column} >= 10 AS INTEGER)"
        for column in ("points", "rebounds", "assists", "steals", "blocks")
    )
    return f"CAST(({reached}) >= {count} AS INTEGER)"


# PrizePicks scores an NBA fantasy point as: point 1, rebound 1.2, assist 1.5,
# block 3, steal 3, turnover -1.
FANTASY_WEIGHTS = (
    ("points", 1.0), ("rebounds", 1.2), ("assists", 1.5),
    ("blocks", 3.0), ("steals", 3.0), ("turnovers", -1.0),
)

STATS: tuple[Stat, ...] = (
    _sum("points", "Points", "points"),
    _sum("rebounds", "Rebounds", "rebounds"),
    _sum("assists", "Assists", "assists"),
    _sum("threes_made", "3-Pointers Made", "three_pointers_made"),
    _sum("threes_attempted", "3-Pointers Attempted", "three_pointers_attempted"),
    _sum("steals", "Steals", "steals"),
    _sum("blocks", "Blocks", "blocks"),
    _sum("turnovers", "Turnovers", "turnovers"),
    _sum("fouls", "Personal Fouls", "fouls"),
    _sum("fg_made", "Field Goals Made", "field_goals_made"),
    _sum("fg_attempted", "Field Goals Attempted", "field_goals_attempted"),
    _sum("ft_made", "Free Throws Made", "free_throws_made"),
    _sum("ft_attempted", "Free Throws Attempted", "free_throws_attempted"),
    Stat("twos_made", "2-Pointers Made", COUNT,
         (("field_goals_made", 1.0), ("three_pointers_made", -1.0))),
    Stat("twos_attempted", "2-Pointers Attempted", COUNT,
         (("field_goals_attempted", 1.0), ("three_pointers_attempted", -1.0))),
    _sum("off_rebounds", "Offensive Rebounds", "offensive_rebounds"),
    _sum("def_rebounds", "Defensive Rebounds", "defensive_rebounds"),
    _sum("pts_rebs_asts", "Points + Rebounds + Assists", "points", "rebounds", "assists"),
    _sum("pts_rebs", "Points + Rebounds", "points", "rebounds"),
    _sum("pts_asts", "Points + Assists", "points", "assists"),
    _sum("rebs_asts", "Rebounds + Assists", "rebounds", "assists"),
    _sum("blks_stls", "Blocks + Steals", "blocks", "steals"),
    Stat("fantasy_score", "Fantasy Score", SCORE, FANTASY_WEIGHTS),
    Stat("double_double", "Double-Double", FLAG, custom_sql=_tens(2)),
    Stat("triple_double", "Triple-Double", FLAG, custom_sql=_tens(3)),
)

BY_KEY: dict[str, Stat] = {stat.key: stat for stat in STATS}
KEYS: tuple[str, ...] = tuple(BY_KEY)

# What PrizePicks prints on the board for each stat. Names missing from this table
# (Dunks, quarter and half stats, "Points - 1st 3 Minutes") cannot be read from a
# box score; their lines are stored but never graded.
PRIZEPICKS_NAMES: dict[str, str] = {
    "Points": "points",
    "Rebounds": "rebounds",
    "Assists": "assists",
    "3-PT Made": "threes_made",
    "3-PT Attempted": "threes_attempted",
    "Steals": "steals",
    "Blocked Shots": "blocks",
    "Turnovers": "turnovers",
    "Personal Fouls": "fouls",
    "FG Made": "fg_made",
    "FG Attempted": "fg_attempted",
    "Free Throws Made": "ft_made",
    "Free Throws Attempted": "ft_attempted",
    "Two Pointers Made": "twos_made",
    "Two Pointers Attempted": "twos_attempted",
    "Offensive Rebounds": "off_rebounds",
    "Defensive Rebounds": "def_rebounds",
    "Pts+Rebs+Asts": "pts_rebs_asts",
    "Pts+Rebs": "pts_rebs",
    "Pts+Asts": "pts_asts",
    "Rebs+Asts": "rebs_asts",
    "Blks+Stls": "blks_stls",
    "Fantasy Score": "fantasy_score",
    "Double-Double": "double_double",
    "Triple-Double": "triple_double",
}

# The Odds API market keys (https://the-odds-api.com/sports-odds-data/betting-markets.html).
# An "_alternate" suffix marks the same stat at another line. Yes/No markets
# (double-double, first basket) and quarter markets are not listed: they carry no line.
ODDS_API_MARKETS: dict[str, str] = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes_made",
    "player_blocks": "blocks",
    "player_steals": "steals",
    "player_turnovers": "turnovers",
    "player_blocks_steals": "blks_stls",
    "player_points_rebounds_assists": "pts_rebs_asts",
    "player_points_rebounds": "pts_rebs",
    "player_points_assists": "pts_asts",
    "player_rebounds_assists": "rebs_asts",
    "player_field_goals": "fg_made",
    "player_frees_made": "ft_made",
    "player_frees_attempts": "ft_attempted",
    # Every fantasy site scores differently. The key is only trusted for PrizePicks,
    # whose scoring is FANTASY_WEIGHTS; see better.lines.mapping.
    "player_fantasy_points": "fantasy_score",
}
ALTERNATE_SUFFIX = "_alternate"
# Markets that exist with the "_alternate" suffix, per the same page.
ODDS_API_ALTERNATES: frozenset[str] = frozenset({
    "player_points", "player_rebounds", "player_assists", "player_blocks", "player_steals",
    "player_turnovers", "player_threes", "player_points_assists", "player_points_rebounds",
    "player_rebounds_assists", "player_points_rebounds_assists", "player_fantasy_points",
})


def stat_views_sql() -> str:
    """SQL that (re)creates the views exposing every stat for every player game.

    ``player_prop_stats`` has one row per player who played and one column per
    stat key. ``player_stat_values`` is the same data with one row per stat.
    Players who did not play are absent from both.
    """
    columns = ",\n    ".join(f"CAST({stat.sql} AS DOUBLE) AS {stat.key}" for stat in STATS)
    keys = ", ".join(KEYS)
    return f"""
CREATE OR REPLACE VIEW player_prop_stats AS
SELECT
    game_id, player_id, season, season_type, game_date, team_id, opponent_team_id,
    is_home, starter, minutes,
    {columns}
FROM player_game_stats
WHERE NOT did_not_play;

CREATE OR REPLACE VIEW player_stat_values AS
UNPIVOT (SELECT game_id, player_id, {keys} FROM player_prop_stats)
ON {keys}
INTO NAME stat VALUE value;
"""


def name_table_sql() -> str:
    """SQL for a relation mapping (family, stat_name) to (stat, is_alternate).

    ``family`` is ``prizepicks`` for names printed on the PrizePicks board and
    ``odds_api`` for The Odds API market keys.
    """
    rows = [("prizepicks", name, key, False) for name, key in PRIZEPICKS_NAMES.items()]
    for market, key in ODDS_API_MARKETS.items():
        rows.append(("odds_api", market, key, False))
        if market in ODDS_API_ALTERNATES:
            rows.append(("odds_api", market + ALTERNATE_SUFFIX, key, True))
    values = ",\n    ".join(
        f"('{family}', '{name}', '{key}', {str(alternate).upper()})"
        for family, name, key, alternate in rows
    )
    return f"(VALUES\n    {values}\n) AS stat_names(family, stat_name, stat, is_alternate)"
