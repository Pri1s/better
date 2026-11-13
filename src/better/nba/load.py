"""Turn one season's source files into rows in the canonical tables.

A season is replaced as a whole inside one transaction: its rows are deleted from
every table and written again from the files. Loading is therefore safe to repeat,
and a load that fails leaves the previous rows in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from better.nba import seasons


class SourceSchemaError(RuntimeError):
    """A source file lacks a column it should have, or holds the wrong season."""


class DataIntegrityError(RuntimeError):
    """Loaded rows break an invariant the rest of the project relies on."""


# Tables that hold rows per season and are replaced together.
SEASON_TABLES = (
    "games",
    "player_game_stats",
    "team_game_stats",
    "team_seasons",
    "player_ids",
    "injury_report_rows",
    "season_loads",
)

# ESPN numbers the 30 franchises 1-30. Any other id is an All-Star side, an
# exhibition opponent, or a placeholder for a team that is not yet known.
FRANCHISE = "BETWEEN 1 AND 30"

# ESPN season_type codes that are loaded. Preseason (1) and anything else is skipped.
SEASON_TYPES = {2: "regular", 3: "playoffs", 5: "play_in"}

# Columns read from each source file, and the type each is read as.
# Columns in an *_OPTIONAL set may be absent (older seasons); they load as NULL.
SCHEDULE_COLUMNS = {
    "game_id": "INTEGER",
    "season": "INTEGER",
    "season_type": "INTEGER",
    "game_date": "DATE",
    "game_date_time": "TIMESTAMPTZ",
    "time_valid": "BOOLEAN",
    "status_type_name": "VARCHAR",
    "status_type_completed": "BOOLEAN",
    "home_id": "INTEGER",
    "away_id": "INTEGER",
    "home_score": "INTEGER",
    "away_score": "INTEGER",
    "home_abbreviation": "VARCHAR",
    "home_display_name": "VARCHAR",
    "away_abbreviation": "VARCHAR",
    "away_display_name": "VARCHAR",
    "neutral_site": "BOOLEAN",
    "notes_headline": "VARCHAR",
    "venue_full_name": "VARCHAR",
}
SCHEDULE_OPTIONAL = frozenset({"time_valid", "neutral_site", "notes_headline", "venue_full_name"})

PLAYER_BOX_COLUMNS = {
    "game_id": "INTEGER",
    "season": "INTEGER",
    "athlete_id": "INTEGER",
    "athlete_display_name": "VARCHAR",
    "athlete_position_abbreviation": "VARCHAR",
    "team_id": "INTEGER",
    "starter": "BOOLEAN",
    "did_not_play": "BOOLEAN",
    "reason": "VARCHAR",
    "ejected": "BOOLEAN",
    "minutes": "DOUBLE",
    "points": "INTEGER",
    "rebounds": "INTEGER",
    "assists": "INTEGER",
    "steals": "INTEGER",
    "blocks": "INTEGER",
    "turnovers": "INTEGER",
    "fouls": "INTEGER",
    "field_goals_made": "INTEGER",
    "field_goals_attempted": "INTEGER",
    "three_point_field_goals_made": "INTEGER",
    "three_point_field_goals_attempted": "INTEGER",
    "free_throws_made": "INTEGER",
    "free_throws_attempted": "INTEGER",
    "offensive_rebounds": "INTEGER",
    "defensive_rebounds": "INTEGER",
    "plus_minus": "VARCHAR",
    "team_winner": "BOOLEAN",
}
PLAYER_BOX_OPTIONAL = frozenset(
    {"athlete_position_abbreviation", "reason", "ejected", "plus_minus", "team_winner"}
)

TEAM_BOX_COLUMNS = {
    "game_id": "INTEGER",
    "season": "INTEGER",
    "team_id": "INTEGER",
    "team_winner": "BOOLEAN",
    "team_score": "INTEGER",
    "opponent_team_score": "INTEGER",
    "field_goals_made": "INTEGER",
    "field_goals_attempted": "INTEGER",
    "three_point_field_goals_made": "INTEGER",
    "three_point_field_goals_attempted": "INTEGER",
    "free_throws_made": "INTEGER",
    "free_throws_attempted": "INTEGER",
    "offensive_rebounds": "INTEGER",
    "defensive_rebounds": "INTEGER",
    "total_rebounds": "INTEGER",
    "assists": "INTEGER",
    "steals": "INTEGER",
    "blocks": "INTEGER",
    "total_turnovers": "INTEGER",
    "fouls": "INTEGER",
    "fast_break_points": "INTEGER",
    "points_in_paint": "INTEGER",
    "turnover_points": "INTEGER",
}
TEAM_BOX_OPTIONAL = frozenset(
    {
        "team_winner",
        "opponent_team_score",
        "fast_break_points",
        "points_in_paint",
        "turnover_points",
    }
)

TEAM_CROSSWALK_COLUMNS = {
    "espn_team_id": "INTEGER",
    "nba_team_id": "BIGINT",
    "nba_team_abbreviation": "VARCHAR",
}

PLAYER_CROSSWALK_COLUMNS = {
    "espn_athlete_id": "INTEGER",
    "nba_player_id": "BIGINT",
    "espn_full_name": "VARCHAR",
    "nba_player_name": "VARCHAR",
    "match_method": "VARCHAR",
    "match_confidence": "DOUBLE",
}
PLAYER_CROSSWALK_OPTIONAL = frozenset({"nba_player_name", "match_method", "match_confidence"})

INJURY_COLUMNS = {
    "season": "INTEGER",
    "report_datetime": "TIMESTAMP",  # US Eastern wall-clock time, as printed on the report
    "game_date": "DATE",
    "team": "VARCHAR",
    "report_player_name": "VARCHAR",
    "current_status": "VARCHAR",
    "reason": "VARCHAR",
    "game_id": "VARCHAR",
    "team_id": "BIGINT",
    "person_id": "BIGINT",
}
INJURY_OPTIONAL = frozenset({"game_id", "team_id", "person_id"})

_SEASON_TYPE_CASE = (
    "CASE season_type "
    + " ".join(f"WHEN {code} THEN '{name}'" for code, name in SEASON_TYPES.items())
    + " END"
)
_KEEP_GAME = (
    f"home_id {FRANCHISE} AND away_id {FRANCHISE} "
    f"AND season_type IN ({', '.join(str(code) for code in SEASON_TYPES)})"
)

GAMES_SQL = """
INSERT INTO games BY NAME
SELECT
    game_id,
    {season} AS season,
    {season_type_case} AS season_type,
    game_date,
    CASE WHEN coalesce(time_valid, TRUE) THEN game_date_time AT TIME ZONE 'UTC' END AS tipoff_utc,
    CASE
        WHEN status_type_completed THEN 'final'
        ELSE coalesce(lower(replace(status_type_name, 'STATUS_', '')), 'unknown')
    END AS status,
    home_id AS home_team_id,
    away_id AS away_team_id,
    CASE WHEN status_type_completed THEN home_score END AS home_score,
    CASE WHEN status_type_completed THEN away_score END AS away_score,
    neutral_site,
    nullif(trim(notes_headline), '') AS label,
    nullif(trim(venue_full_name), '') AS venue
FROM {src}
WHERE game_id IS NOT NULL AND game_date IS NOT NULL AND {keep_game}
QUALIFY row_number() OVER (PARTITION BY game_id ORDER BY game_date_time DESC NULLS LAST) = 1
"""

PLAYER_SQL = """
INSERT INTO player_game_stats BY NAME
SELECT
    p.game_id,
    p.athlete_id AS player_id,
    g.season,
    g.season_type,
    g.game_date,
    p.team_id,
    CASE WHEN p.team_id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END
        AS opponent_team_id,
    p.team_id = g.home_team_id AS is_home,
    p.athlete_display_name AS player_name,
    p.athlete_position_abbreviation AS position,
    coalesce(p.starter, FALSE) AS starter,
    coalesce(p.did_not_play, FALSE) AS did_not_play,
    -- The source fills `reason` with a default for everyone; it only means something on a DNP.
    CASE WHEN p.did_not_play THEN nullif(trim(p.reason), '') END AS dnp_reason,
    coalesce(p.ejected, FALSE) AS ejected,
    p.minutes,
    p.points,
    p.rebounds,
    p.assists,
    p.steals,
    p.blocks,
    p.turnovers,
    p.fouls,
    p.field_goals_made,
    p.field_goals_attempted,
    p.three_point_field_goals_made AS three_pointers_made,
    p.three_point_field_goals_attempted AS three_pointers_attempted,
    p.free_throws_made,
    p.free_throws_attempted,
    p.offensive_rebounds,
    p.defensive_rebounds,
    TRY_CAST(replace(p.plus_minus, '+', '') AS INTEGER) AS plus_minus,
    p.team_winner AS team_won
FROM {src} p
JOIN games g ON g.game_id = p.game_id AND g.season = {season}
WHERE p.athlete_id IS NOT NULL
  AND p.team_id IN (g.home_team_id, g.away_team_id)
QUALIFY row_number() OVER (
    PARTITION BY p.game_id, p.athlete_id ORDER BY p.minutes DESC NULLS LAST
) = 1
"""

TEAM_SQL = """
INSERT INTO team_game_stats BY NAME
SELECT
    t.game_id,
    t.team_id,
    g.season,
    g.season_type,
    g.game_date,
    CASE WHEN t.team_id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END
        AS opponent_team_id,
    t.team_id = g.home_team_id AS is_home,
    t.team_winner AS won,
    t.team_score AS points,
    coalesce(
        t.opponent_team_score,
        CASE WHEN t.team_id = g.home_team_id THEN g.away_score ELSE g.home_score END
    ) AS opponent_points,
    t.field_goals_made,
    t.field_goals_attempted,
    t.three_point_field_goals_made AS three_pointers_made,
    t.three_point_field_goals_attempted AS three_pointers_attempted,
    t.free_throws_made,
    t.free_throws_attempted,
    t.offensive_rebounds,
    t.defensive_rebounds,
    t.total_rebounds AS rebounds,
    t.assists,
    t.steals,
    t.blocks,
    t.total_turnovers AS turnovers,
    t.fouls,
    t.fast_break_points,
    t.points_in_paint,
    t.turnover_points AS points_off_turnovers
FROM {src} t
JOIN games g ON g.game_id = t.game_id AND g.season = {season}
WHERE t.team_id IN (g.home_team_id, g.away_team_id)
QUALIFY row_number() OVER (
    PARTITION BY t.game_id, t.team_id ORDER BY t.team_score DESC NULLS LAST
) = 1
"""

TEAM_SEASONS_SQL = """
INSERT INTO team_seasons BY NAME
WITH listed AS (
    SELECT home_id AS team_id, home_abbreviation AS abbreviation,
           home_display_name AS name, game_date_time
    FROM {schedule}
    UNION ALL
    SELECT away_id, away_abbreviation, away_display_name, game_date_time
    FROM {schedule}
),
espn AS (
    SELECT team_id,
           arg_max(abbreviation, game_date_time) AS abbreviation,
           arg_max(name, game_date_time) AS name
    FROM listed
    WHERE team_id {franchise}
    GROUP BY team_id
),
nba AS (
    SELECT espn_team_id, nba_team_id, nba_team_abbreviation
    FROM {crosswalk}
    WHERE espn_team_id IS NOT NULL AND nba_team_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY espn_team_id ORDER BY nba_team_id) = 1
)
SELECT {season} AS season, e.team_id, e.abbreviation, e.name,
       n.nba_team_id, n.nba_team_abbreviation AS nba_abbreviation
FROM espn e
LEFT JOIN nba n ON n.espn_team_id = e.team_id
"""

NO_TEAM_CROSSWALK = (
    "(SELECT CAST(NULL AS INTEGER) AS espn_team_id, CAST(NULL AS BIGINT) AS nba_team_id, "
    "CAST(NULL AS VARCHAR) AS nba_team_abbreviation WHERE FALSE)"
)

PLAYER_IDS_SQL = """
INSERT INTO player_ids BY NAME
SELECT
    {season} AS season,
    espn_athlete_id AS player_id,
    nba_player_id,
    espn_full_name AS player_name,
    nba_player_name,
    match_method,
    match_confidence
FROM {src}
WHERE espn_athlete_id IS NOT NULL AND nba_player_id IS NOT NULL
-- The file has one row per player per team. Keep one row per id on each side.
QUALIFY row_number() OVER (
            PARTITION BY espn_athlete_id ORDER BY match_confidence DESC NULLS LAST, nba_player_id
        ) = 1
    AND row_number() OVER (
            PARTITION BY nba_player_id ORDER BY match_confidence DESC NULLS LAST, espn_athlete_id
        ) = 1
"""

INJURY_SQL = """
INSERT INTO injury_report_rows BY NAME
SELECT
    {season} AS season,
    -- Report times are US Eastern wall-clock times. Store them as UTC.
    (report_datetime AT TIME ZONE 'America/New_York') AT TIME ZONE 'UTC' AS report_time_utc,
    game_date,
    team AS team_name,
    report_player_name,
    current_status AS status,
    reason,
    game_id AS nba_game_id,
    team_id AS nba_team_id,
    person_id AS nba_player_id
FROM {src}
WHERE report_datetime IS NOT NULL AND game_date IS NOT NULL
-- A few rows are printed twice in a report. Keep one.
QUALIFY row_number() OVER (
    PARTITION BY report_datetime, game_date, team, report_player_name
    ORDER BY current_status NULLS LAST, reason NULLS LAST
) = 1
"""


@dataclass(frozen=True)
class SeasonFiles:
    """Local paths of one season's source files. The last three are optional."""

    schedule: Path
    player_box: Path
    team_box: Path
    team_crosswalk: Path | None = None
    player_crosswalk: Path | None = None
    injuries: Path | None = None


@dataclass
class SeasonSummary:
    season: int
    games: int = 0
    games_final: int = 0
    games_scheduled: int = 0
    games_skipped: int = 0  # All-Star, preseason and placeholder rows left out
    player_rows: int = 0
    player_rows_dnp: int = 0
    team_rows: int = 0
    injury_rows: int = 0
    injury_reports: int = 0
    missing: list[str] = field(default_factory=list)  # optional files that were not available
    warnings: list[str] = field(default_factory=list)

    def line(self) -> str:
        parts = [
            f"{self.games:,} games ({self.games_final:,} final, {self.games_scheduled:,} to come)",
            f"{self.player_rows:,} player rows",
            f"{self.injury_reports:,} injury reports",
        ]
        return f"{seasons.label(self.season)}  " + " · ".join(parts)


def load_season(
    con: duckdb.DuckDBPyConnection, season: int, files: SeasonFiles
) -> SeasonSummary:
    """Replace everything stored for ``season`` with the contents of ``files``."""
    season = int(season)
    summary = SeasonSummary(season=season)

    schedule = _source(con, files.schedule, SCHEDULE_COLUMNS, SCHEDULE_OPTIONAL)
    player_box = _source(con, files.player_box, PLAYER_BOX_COLUMNS, PLAYER_BOX_OPTIONAL)
    team_box = _source(con, files.team_box, TEAM_BOX_COLUMNS, TEAM_BOX_OPTIONAL)
    for src, path in ((schedule, files.schedule), (player_box, files.player_box),
                      (team_box, files.team_box)):
        _check_season(con, src, season, path)

    team_crosswalk = NO_TEAM_CROSSWALK
    if files.team_crosswalk is not None:
        team_crosswalk = _source(con, files.team_crosswalk, TEAM_CROSSWALK_COLUMNS)
    else:
        summary.missing.append("team_crosswalk")

    con.execute("BEGIN TRANSACTION")
    try:
        for table in SEASON_TABLES:
            con.execute(f"DELETE FROM {table} WHERE season = ?", [season])

        con.execute(
            GAMES_SQL.format(
                season=season, src=schedule,
                season_type_case=_SEASON_TYPE_CASE, keep_game=_KEEP_GAME,
            )
        )
        con.execute(
            TEAM_SEASONS_SQL.format(
                season=season, schedule=schedule, crosswalk=team_crosswalk, franchise=FRANCHISE
            )
        )
        con.execute(PLAYER_SQL.format(season=season, src=player_box))
        con.execute(TEAM_SQL.format(season=season, src=team_box))

        if files.player_crosswalk is not None:
            src = _source(
                con, files.player_crosswalk, PLAYER_CROSSWALK_COLUMNS, PLAYER_CROSSWALK_OPTIONAL
            )
            con.execute(PLAYER_IDS_SQL.format(season=season, src=src))
        else:
            summary.missing.append("player_crosswalk")

        if files.injuries is not None:
            src = _source(con, files.injuries, INJURY_COLUMNS, INJURY_OPTIONAL)
            _check_season(con, src, season, files.injuries)
            con.execute(INJURY_SQL.format(season=season, src=src))
        else:
            summary.missing.append("injuries")

        _summarise(con, season, schedule, summary)
        _validate(con, season, summary)
        con.execute(
            "INSERT INTO season_loads VALUES (?, ?, ?, ?, ?, ?)",
            [
                season,
                datetime.now(UTC).replace(tzinfo=None),
                summary.games,
                summary.player_rows,
                summary.team_rows,
                summary.injury_rows,
            ],
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return summary


def check_links(con: duckdb.DuckDBPyConnection) -> None:
    """Make sure the injury_reports view returns each stored row exactly once."""
    stored, linked = con.execute(
        "SELECT (SELECT count(*) FROM injury_report_rows), (SELECT count(*) FROM injury_reports)"
    ).fetchone()
    if stored != linked:
        raise DataIntegrityError(
            f"injury_reports returns {linked:,} rows for {stored:,} stored rows: "
            "a team or game is matching more than once"
        )


def _source(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    columns: dict[str, str],
    optional: frozenset[str] = frozenset(),
) -> str:
    """SQL for a subquery that exposes ``columns`` of a parquet file with fixed types.

    Reading through this keeps the loader independent of type drift between
    seasons (a score stored as text one year and as a number the next).
    """
    cursor = con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [str(path)])
    available = {column[0] for column in cursor.description}
    missing = sorted(name for name in columns if name not in available and name not in optional)
    if missing:
        raise SourceSchemaError(f"{path.name} is missing expected columns: {', '.join(missing)}")
    parts = [
        f'TRY_CAST("{name}" AS {sql_type}) AS "{name}"'
        if name in available
        else f'CAST(NULL AS {sql_type}) AS "{name}"'
        for name, sql_type in columns.items()
    ]
    literal = str(path).replace("'", "''")
    return f"(SELECT {', '.join(parts)} FROM read_parquet('{literal}'))"


def _check_season(con: duckdb.DuckDBPyConnection, src: str, season: int, path: Path) -> None:
    found = [
        row[0]
        for row in con.execute(
            f"SELECT DISTINCT season FROM {src} WHERE season IS NOT NULL ORDER BY season"
        ).fetchall()
    ]
    if found and found != [season]:
        raise SourceSchemaError(f"{path.name} holds seasons {found}, expected only {season}")


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, *params: object) -> int:
    return con.execute(sql, list(params)).fetchone()[0]


def _summarise(
    con: duckdb.DuckDBPyConnection, season: int, schedule: str, summary: SeasonSummary
) -> None:
    summary.games, summary.games_final, summary.games_scheduled = con.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE status = 'final'),
               count(*) FILTER (WHERE status = 'scheduled')
        FROM games WHERE season = ?
        """,
        [season],
    ).fetchone()
    summary.games_skipped = _scalar(con, f"SELECT count(*) FROM {schedule}") - summary.games
    summary.player_rows, summary.player_rows_dnp = con.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE did_not_play)
        FROM player_game_stats WHERE season = ?
        """,
        [season],
    ).fetchone()
    summary.team_rows = _scalar(
        con, "SELECT count(*) FROM team_game_stats WHERE season = ?", season
    )
    summary.injury_rows, summary.injury_reports = con.execute(
        """
        SELECT count(*), count(DISTINCT report_time_utc)
        FROM injury_report_rows WHERE season = ?
        """,
        [season],
    ).fetchone()


def _validate(con: duckdb.DuckDBPyConnection, season: int, summary: SeasonSummary) -> None:
    """Raise on broken keys; record softer oddities as warnings."""
    duplicates = {
        "games.game_id": "SELECT game_id FROM games GROUP BY ALL HAVING count(*) > 1",
        "player_game_stats (game_id, player_id)": (
            "SELECT game_id, player_id FROM player_game_stats GROUP BY ALL HAVING count(*) > 1"
        ),
        "team_game_stats (game_id, team_id)": (
            "SELECT game_id, team_id FROM team_game_stats GROUP BY ALL HAVING count(*) > 1"
        ),
        "team_seasons (season, team_id)": (
            "SELECT season, team_id FROM team_seasons GROUP BY ALL HAVING count(*) > 1"
        ),
        "team_seasons (season, nba_team_id)": (
            "SELECT season, nba_team_id FROM team_seasons WHERE nba_team_id IS NOT NULL "
            "GROUP BY ALL HAVING count(*) > 1"
        ),
    }
    for key, sql in duplicates.items():
        count = _scalar(con, f"SELECT count(*) FROM ({sql})")
        if count:
            raise DataIntegrityError(
                f"{count} duplicate key(s) in {key} after loading {seasons.label(season)}"
            )

    checks = {
        "team(s) have two games dated the same day (a dating error in the source)": """
            SELECT count(*) FROM (
                SELECT game_date, team_id FROM team_game_context
                WHERE season = ? GROUP BY ALL HAVING count(*) > 1
            )
        """,
        "final game(s) have no player box score": """
            SELECT count(*) FROM games g
            WHERE g.season = ? AND g.status = 'final'
              AND NOT EXISTS (SELECT 1 FROM player_game_stats p WHERE p.game_id = g.game_id)
        """,
        "final game(s) do not have exactly two team rows": """
            SELECT count(*) FROM games g
            WHERE g.season = ? AND g.status = 'final'
              AND (SELECT count(*) FROM team_game_stats t WHERE t.game_id = g.game_id) <> 2
        """,
        "player row(s) for someone who played have no minutes": """
            SELECT count(*) FROM player_game_stats
            WHERE season = ? AND NOT did_not_play AND minutes IS NULL
        """,
        "game(s) have no tip-off time": """
            SELECT count(*) FROM games WHERE season = ? AND tipoff_utc IS NULL
        """,
    }
    for message, sql in checks.items():
        count = _scalar(con, sql, season)
        if count:
            summary.warnings.append(f"{count:,} {message}")
