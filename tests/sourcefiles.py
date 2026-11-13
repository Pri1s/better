"""Small source-shaped parquet files for the tests.

Column names and types mirror the real sportsdataverse files as inspected on
2026-11-12, including their quirks: ids stored as text in the crosswalk,
plus_minus as '+12', team stats stored as text, and a default ``reason`` on
every box-score row.

The story they tell (season 2027, i.e. 2026-27):

    101  Oct 20  ATL @ BOS   final 100-110
    102  Oct 21  NO  @ ATL   final 99-95     (ATL on a back-to-back)
    103  Oct 23  BOS @ ATL   final 118-120   (ATL's third game in four nights)
    104  Oct 25  ATL @ NO    scheduled, 8 pm Eastern
    105  Oct 22  NO  @ BOS   postponed
    108  Nov 20  NO  @ BOS   scheduled, time not set
    106  All-Star game, 107 placeholder with unknown teams, 109 preseason: all skipped
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from better.nba.load import SeasonFiles

SEASON = 2027

ATL, BOS, NO = 1, 2, 3
ALL_STAR_HOME, ALL_STAR_AWAY = 132375, 132374
NBA_TEAM = {ATL: 1610612737, BOS: 1610612738, NO: 1610612740}
TEAM_NAME = {ATL: "Atlanta Hawks", BOS: "Boston Celtics", NO: "New Orleans Pelicans"}
TEAM_ABBR = {ATL: "ATL", BOS: "BOS", NO: "NO"}

TATUM, WHITE, HAUSER, YOUNG, MURRAY, ALVARADO = 11, 12, 13, 21, 31, 32
NBA_PLAYER = {TATUM: 1628369, WHITE: 1628401, HAUSER: 1630573, YOUNG: 1629027, MURRAY: 1627749}


def _write(
    path: Path,
    columns: dict[str, str],
    rows: list[dict],
    defaults: dict | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    kept = {name: sql_type for name, sql_type in columns.items() if name not in drop}
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE TABLE t (" + ", ".join(f'"{n}" {t}' for n, t in kept.items()) + ")")
    for row in rows:
        full = {**(defaults or {}), **row}
        unknown = set(full) - set(columns)
        assert not unknown, f"unknown fixture columns: {unknown}"
        con.execute(
            f"INSERT INTO t VALUES ({', '.join('?' for _ in kept)})",
            [full.get(name) for name in kept],
        )
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    con.close()
    return path


# --- schedule ---------------------------------------------------------------

SCHEDULE_COLUMNS = {
    "id": "INTEGER",  # not read by the loader
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


def _game(game_id, day, tip_utc, home, away, status="STATUS_FINAL", score=(0, 0), **extra):
    names = {**TEAM_NAME, ALL_STAR_HOME: "Team Stripes", ALL_STAR_AWAY: "Team Stars"}
    abbrs = {**TEAM_ABBR, ALL_STAR_HOME: "STRIPES", ALL_STAR_AWAY: "STARS"}
    return {
        "id": game_id,
        "game_id": game_id,
        "game_date": day,
        "game_date_time": tip_utc + "+00",
        "status_type_name": status,
        "status_type_completed": status == "STATUS_FINAL",
        "home_id": home,
        "away_id": away,
        "home_score": score[0],
        "away_score": score[1],
        "home_abbreviation": abbrs.get(home, "TBD"),
        "home_display_name": names.get(home, "TBD"),
        "away_abbreviation": abbrs.get(away, "TBD"),
        "away_display_name": names.get(away, "TBD"),
        **extra,
    }


SCHEDULE_ROWS = [
    _game(101, date(2026, 10, 20), "2026-10-20 23:30:00", BOS, ATL, score=(110, 100),
          venue_full_name="TD Garden"),
    _game(102, date(2026, 10, 21), "2026-10-21 23:30:00", ATL, NO, score=(95, 99),
          notes_headline="NBA Cup - Group Play"),
    _game(103, date(2026, 10, 23), "2026-10-23 23:30:00", ATL, BOS, score=(120, 118)),
    # 8 pm Eastern on the 25th, which is already the 26th in UTC.
    _game(104, date(2026, 10, 25), "2026-10-26 00:00:00", NO, ATL, status="STATUS_SCHEDULED"),
    _game(105, date(2026, 10, 22), "2026-10-22 23:30:00", BOS, NO, status="STATUS_POSTPONED"),
    _game(106, date(2027, 2, 14), "2027-02-15 01:00:00", ALL_STAR_HOME, ALL_STAR_AWAY,
          status="STATUS_SCHEDULED"),
    _game(107, date(2026, 12, 8), "2026-12-09 00:00:00", -1, -2, status="STATUS_SCHEDULED"),
    _game(108, date(2026, 11, 20), "2026-11-20 05:00:00", BOS, NO, status="STATUS_SCHEDULED",
          time_valid=False),
    _game(109, date(2026, 10, 5), "2026-10-05 23:30:00", ATL, BOS, score=(90, 80), season_type=1),
]
SCHEDULE_DEFAULTS = {
    "season": SEASON, "season_type": 2, "time_valid": True, "neutral_site": False,
    "notes_headline": "", "venue_full_name": "",
}


def schedule(path: Path, rows=SCHEDULE_ROWS, drop: tuple[str, ...] = ()) -> Path:
    return _write(path, SCHEDULE_COLUMNS, rows, SCHEDULE_DEFAULTS, drop)


# --- player box scores -------------------------------------------------------

PLAYER_BOX_COLUMNS = {
    "game_id": "INTEGER",
    "season": "INTEGER",
    "athlete_id": "INTEGER",
    "athlete_display_name": "VARCHAR",
    "athlete_position_abbreviation": "VARCHAR",
    "team_id": "INTEGER",
    "home_away": "VARCHAR",  # not read by the loader
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
STAT_COLUMNS = (
    "minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls",
    "field_goals_made", "field_goals_attempted", "three_point_field_goals_made",
    "three_point_field_goals_attempted", "free_throws_made", "free_throws_attempted",
    "offensive_rebounds", "defensive_rebounds", "plus_minus",
)
PLAYER_BOX_DEFAULTS = {
    "season": SEASON, "athlete_position_abbreviation": "G", "home_away": "home",
    "starter": False, "did_not_play": False, "reason": "COACH'S DECISION", "ejected": False,
    "minutes": 20.0, "points": 10, "rebounds": 4, "assists": 3, "steals": 1, "blocks": 0,
    "turnovers": 2, "fouls": 2, "field_goals_made": 4, "field_goals_attempted": 9,
    "three_point_field_goals_made": 1, "three_point_field_goals_attempted": 3,
    "free_throws_made": 1, "free_throws_attempted": 2, "offensive_rebounds": 1,
    "defensive_rebounds": 3, "plus_minus": "+1", "team_winner": True,
}
NO_STATS = dict.fromkeys(STAT_COLUMNS)


def _line(game_id, athlete_id, name, team_id, **extra):
    return {"game_id": game_id, "athlete_id": athlete_id, "athlete_display_name": name,
            "team_id": team_id, **extra}


PLAYER_BOX_ROWS = [
    _line(101, TATUM, "Jayson Tatum", BOS, athlete_position_abbreviation="F", starter=True,
          minutes=36.0, points=30, rebounds=8, assists=5, three_point_field_goals_made=4,
          plus_minus="+12"),
    _line(101, WHITE, "Derrick White", BOS, minutes=30.0, points=15, plus_minus="0"),
    # The same player twice in one game: the loader keeps the fuller line.
    _line(101, WHITE, "Derrick White", BOS, minutes=5.0, points=2),
    _line(101, HAUSER, "Sam Hauser", BOS, did_not_play=True, reason="LEFT ANKLE SPRAIN",
          **NO_STATS),
    _line(101, YOUNG, "Trae Young", ATL, starter=True, minutes=34.0, points=25,
          plus_minus="-8", team_winner=False),
    _line(101, None, "TEAM", ATL),  # no athlete id: dropped
    _line(102, YOUNG, "Trae Young", ATL, minutes=35.0, points=22, plus_minus="-4",
          team_winner=False),
    _line(102, MURRAY, "Dejounte Murray", NO, starter=True, minutes=33.0, points=20),
    _line(102, ALVARADO, "José Alvarado", NO, minutes=18.0, points=9),
    _line(102, TATUM, "Jayson Tatum", BOS),  # BOS did not play in game 102: dropped
    _line(103, TATUM, "Jayson Tatum", BOS, starter=True, minutes=38.0, points=28,
          team_winner=False),
    _line(103, YOUNG, "Trae Young", ATL, starter=True, minutes=36.0, points=31),
    _line(103, WHITE, "Derrick White", BOS, minutes=None, points=0, team_winner=False),
    _line(106, TATUM, "Jayson Tatum", ALL_STAR_HOME),  # All-Star game: dropped
]


def player_box(path: Path, rows=PLAYER_BOX_ROWS, drop: tuple[str, ...] = ()) -> Path:
    return _write(path, PLAYER_BOX_COLUMNS, rows, PLAYER_BOX_DEFAULTS, drop)


# --- team box scores ---------------------------------------------------------

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
    "turnovers": "INTEGER",  # not read: the loader takes total_turnovers
    "total_turnovers": "INTEGER",
    "fouls": "INTEGER",
    "fast_break_points": "VARCHAR",
    "points_in_paint": "VARCHAR",
    "turnover_points": "VARCHAR",
}
TEAM_BOX_DEFAULTS = {
    "season": SEASON, "field_goals_made": 40, "field_goals_attempted": 88,
    "three_point_field_goals_made": 12, "three_point_field_goals_attempted": 35,
    "free_throws_made": 18, "free_throws_attempted": 22, "offensive_rebounds": 10,
    "defensive_rebounds": 33, "total_rebounds": 43, "assists": 25, "steals": 7, "blocks": 5,
    "turnovers": 13, "total_turnovers": 14, "fouls": 20, "fast_break_points": "12",
    "points_in_paint": "48", "turnover_points": "16",
}


def _team_line(game_id, team_id, score, opponent_score):
    return {"game_id": game_id, "team_id": team_id, "team_score": score,
            "opponent_team_score": opponent_score, "team_winner": score > opponent_score}


TEAM_BOX_ROWS = [
    _team_line(101, BOS, 110, 100), _team_line(101, ATL, 100, 110),
    _team_line(102, ATL, 95, 99), _team_line(102, NO, 99, 95),
    _team_line(103, ATL, 120, 118), _team_line(103, BOS, 118, 120),
    _team_line(106, ALL_STAR_HOME, 50, 48), _team_line(106, ALL_STAR_AWAY, 48, 50),
]


def team_box(path: Path, rows=TEAM_BOX_ROWS, drop: tuple[str, ...] = ()) -> Path:
    return _write(path, TEAM_BOX_COLUMNS, rows, TEAM_BOX_DEFAULTS, drop)


# --- crosswalks --------------------------------------------------------------

TEAM_CROSSWALK_COLUMNS = {
    "season": "INTEGER",
    "espn_team_id": "INTEGER",
    "espn_abbreviation": "VARCHAR",  # not read
    "nba_team_id": "VARCHAR",        # text in the real file
    "nba_team_abbreviation": "VARCHAR",
}
TEAM_CROSSWALK_ROWS = [
    {"espn_team_id": ATL, "espn_abbreviation": "ATL", "nba_team_id": str(NBA_TEAM[ATL]),
     "nba_team_abbreviation": "ATL"},
    {"espn_team_id": BOS, "espn_abbreviation": "BOS", "nba_team_id": str(NBA_TEAM[BOS]),
     "nba_team_abbreviation": "BOS"},
    {"espn_team_id": NO, "espn_abbreviation": "NO", "nba_team_id": str(NBA_TEAM[NO]),
     "nba_team_abbreviation": "NOP"},
]


def team_crosswalk(path: Path, rows=TEAM_CROSSWALK_ROWS) -> Path:
    return _write(path, TEAM_CROSSWALK_COLUMNS, rows, {"season": SEASON})


PLAYER_CROSSWALK_COLUMNS = {
    "season": "INTEGER",
    "espn_team_id": "INTEGER",        # not read
    "espn_athlete_id": "VARCHAR",     # text in the real file
    "espn_full_name": "VARCHAR",
    "nba_player_id": "VARCHAR",       # text in the real file
    "nba_player_name": "VARCHAR",
    "match_method": "VARCHAR",
    "match_confidence": "DOUBLE",
}


def _id_row(athlete_id, name, nba_id, team_id, confidence=1.0):
    return {
        "espn_team_id": team_id,
        "espn_athlete_id": None if athlete_id is None else str(athlete_id),
        "espn_full_name": name,
        "nba_player_id": None if nba_id is None else str(nba_id),
        "nba_player_name": name,
        "match_method": "exact_name",
        "match_confidence": confidence,
    }


PLAYER_CROSSWALK_ROWS = [
    _id_row(TATUM, "Jayson Tatum", NBA_PLAYER[TATUM], BOS),
    _id_row(TATUM, "Jayson Tatum", NBA_PLAYER[TATUM], ATL, confidence=0.9),  # one row per team
    _id_row(WHITE, "Derrick White", NBA_PLAYER[WHITE], BOS),
    _id_row(HAUSER, "Sam Hauser", NBA_PLAYER[HAUSER], BOS),
    _id_row(YOUNG, "Trae Young", NBA_PLAYER[YOUNG], ATL),
    _id_row(MURRAY, "Dejounte Murray", NBA_PLAYER[MURRAY], NO),
    _id_row(99, "No Nba Id", None, NO),      # unmatched upstream: dropped
    _id_row(None, "No Espn Id", 555, NO),    # unmatched upstream: dropped
    # José Alvarado is deliberately absent, to exercise the name fallback.
]


def player_crosswalk(path: Path, rows=PLAYER_CROSSWALK_ROWS) -> Path:
    return _write(path, PLAYER_CROSSWALK_COLUMNS, rows, {"season": SEASON})


# --- injury reports ----------------------------------------------------------

INJURY_COLUMNS = {
    "season": "BIGINT",
    "report_datetime": "TIMESTAMP",  # US Eastern wall-clock time
    "game_date": "DATE",
    "game_time": "VARCHAR",          # not read
    "team": "VARCHAR",
    "report_player_name": "VARCHAR",
    "current_status": "VARCHAR",
    "reason": "VARCHAR",
    "game_id": "VARCHAR",
    "team_id": "BIGINT",
    "person_id": "BIGINT",
}


def _listing(report_et, game_day, team_id, player, status, person_id=None,
             reason="Injury/Illness - Left Ankle Sprain"):
    return {
        "report_datetime": report_et,
        "game_date": game_day,
        "game_time": "07:30 (ET)",
        "team": TEAM_NAME[team_id],
        "report_player_name": player,
        "current_status": status,
        "reason": reason,
        "game_id": None,
        "team_id": NBA_TEAM[team_id],
        "person_id": person_id,
    }


OCT_23, OCT_25 = date(2026, 10, 23), date(2026, 10, 25)
TATUM_ID = NBA_PLAYER[TATUM]
INJURY_ROWS = [
    # Game 103 (Oct 23, tip-off 7:30 pm Eastern = 23:30 UTC).
    _listing("2026-10-22 17:30:00", OCT_23, BOS, "Tatum, Jayson", "Questionable", TATUM_ID),
    _listing("2026-10-23 13:30:00", OCT_23, BOS, "Tatum, Jayson", "Out", TATUM_ID),
    _listing("2026-10-23 13:30:00", OCT_23, BOS, "Tatum, Jayson", "Out", TATUM_ID),  # printed twice
    _listing("2026-10-23 13:30:00", OCT_23, ATL, None, None, reason="NOT YET SUBMITTED"),
    _listing("2026-10-23 18:45:00", OCT_23, BOS, "Tatum, Jayson", "Available", TATUM_ID),
    _listing("2026-10-23 18:45:00", OCT_23, ATL, "Young, Trae", "Probable", NBA_PLAYER[YOUNG]),
    # Published after tip-off: must never be visible to a pre-game query.
    _listing("2026-10-23 20:00:00", OCT_23, BOS, "Tatum, Jayson", "Out", TATUM_ID),
    # Game 104 (Oct 25). Neither player has an NBA.com id in the source.
    _listing("2026-10-25 13:30:00", OCT_25, NO, "Alvarado, Jose", "Out"),
    _listing("2026-10-25 13:30:00", OCT_25, NO, "Unknown, Guy", "Doubtful"),
    # A date on which Boston has no game.
    _listing("2026-10-26 17:30:00", date(2026, 10, 27), BOS, "Tatum, Jayson", "Out", TATUM_ID),
    # After the clocks go back: Eastern is UTC-5 instead of UTC-4.
    _listing("2026-11-19 17:30:00", date(2026, 11, 20), BOS, "White, Derrick", "Questionable",
             NBA_PLAYER[WHITE]),
]


def injuries(path: Path, rows=INJURY_ROWS) -> Path:
    return _write(path, INJURY_COLUMNS, rows, {"season": SEASON})


def build(directory: Path) -> SeasonFiles:
    """Write the whole season's files into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    return SeasonFiles(
        schedule=schedule(directory / "nba_schedule_2027.parquet"),
        player_box=player_box(directory / "player_box_2027.parquet"),
        team_box=team_box(directory / "team_box_2027.parquet"),
        team_crosswalk=team_crosswalk(directory / "nba_team_crosswalk_2027.parquet"),
        player_crosswalk=player_crosswalk(directory / "nba_player_crosswalk_2027.parquet"),
        injuries=injuries(directory / "injury_report_2027.parquet"),
    )
