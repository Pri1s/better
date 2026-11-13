from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest
import sourcefiles
from helpers import one, rows, scalar
from sourcefiles import ALVARADO, ATL, BOS, HAUSER, NO, SEASON, TATUM, WHITE, YOUNG

from better.nba import load


def test_only_franchise_regular_and_postseason_games_are_loaded(loaded):
    con, summary = loaded
    assert {r[0] for r in rows(con, "SELECT game_id FROM games")} == {101, 102, 103, 104, 105, 108}
    assert summary.games == 6
    assert summary.games_final == 3
    assert summary.games_scheduled == 2
    assert summary.games_skipped == 3  # All-Star game, placeholder, preseason


def test_game_date_is_eastern_and_tipoff_is_utc(loaded):
    con, _ = loaded
    game = one(con, "SELECT game_date, tipoff_utc, status, home_score, away_score, "
                    "home_team_id, away_team_id FROM games WHERE game_id = 104")
    assert game == {
        "game_date": date(2026, 10, 25),
        "tipoff_utc": datetime(2026, 10, 26, 0, 0),
        "status": "scheduled",
        "home_score": None,  # the source says 0-0 until a game is played
        "away_score": None,
        "home_team_id": NO,
        "away_team_id": ATL,
    }


def test_game_details(loaded):
    con, _ = loaded
    final = one(con, "SELECT status, home_score, away_score, label, venue, season, season_type "
                     "FROM games WHERE game_id = 101")
    assert final == {"status": "final", "home_score": 110, "away_score": 100, "label": None,
                     "venue": "TD Garden", "season": SEASON, "season_type": "regular"}
    assert scalar(con, "SELECT label FROM games WHERE game_id = 102") == "NBA Cup - Group Play"
    assert scalar(con, "SELECT status FROM games WHERE game_id = 105") == "postponed"
    # A game whose time is not set yet has a date but no tip-off.
    assert scalar(con, "SELECT tipoff_utc FROM games WHERE game_id = 108") is None


def test_player_rows_kept_and_dropped(loaded):
    con, summary = loaded
    keys = set(rows(con, "SELECT game_id, player_id FROM player_game_stats"))
    assert keys == {
        (101, TATUM), (101, WHITE), (101, HAUSER), (101, YOUNG),
        (102, YOUNG), (102, 31), (102, ALVARADO),
        (103, TATUM), (103, YOUNG), (103, WHITE),
    }
    assert summary.player_rows == 10
    assert summary.player_rows_dnp == 1


def test_player_line(loaded):
    con, _ = loaded
    line = one(con, """
        SELECT season, season_type, game_date, team_id, opponent_team_id, is_home, player_name,
               position, starter, did_not_play, dnp_reason, minutes, points, rebounds, assists,
               three_pointers_made, plus_minus, team_won
        FROM player_game_stats WHERE game_id = 101 AND player_id = ?""", TATUM)
    assert line == {
        "season": SEASON, "season_type": "regular", "game_date": date(2026, 10, 20),
        "team_id": BOS, "opponent_team_id": ATL, "is_home": True, "player_name": "Jayson Tatum",
        "position": "F", "starter": True, "did_not_play": False,
        "dnp_reason": None,  # the source's default "COACH'S DECISION" is not a reason
        "minutes": 36.0, "points": 30, "rebounds": 8, "assists": 5, "three_pointers_made": 4,
        "plus_minus": 12, "team_won": True,
    }
    away = one(con, "SELECT team_id, opponent_team_id, is_home, plus_minus FROM player_game_stats "
                    "WHERE game_id = 101 AND player_id = ?", YOUNG)
    assert away == {"team_id": ATL, "opponent_team_id": BOS, "is_home": False, "plus_minus": -8}


def test_duplicate_player_line_keeps_the_fuller_one(loaded):
    con, _ = loaded
    line = one(con, "SELECT minutes, points, plus_minus FROM player_game_stats "
                    "WHERE game_id = 101 AND player_id = ?", WHITE)
    assert line == {"minutes": 30.0, "points": 15, "plus_minus": 0}


def test_did_not_play_row_has_a_reason_and_no_stats(loaded):
    con, _ = loaded
    line = one(con, "SELECT did_not_play, dnp_reason, minutes, points, rebounds, plus_minus "
                    "FROM player_game_stats WHERE game_id = 101 AND player_id = ?", HAUSER)
    assert line == {"did_not_play": True, "dnp_reason": "LEFT ANKLE SPRAIN", "minutes": None,
                    "points": None, "rebounds": None, "plus_minus": None}


def test_team_rows(loaded):
    con, summary = loaded
    assert summary.team_rows == 6
    line = one(con, """
        SELECT opponent_team_id, is_home, won, points, opponent_points, rebounds, turnovers,
               fast_break_points, points_in_paint, points_off_turnovers
        FROM team_game_stats WHERE game_id = 102 AND team_id = ?""", NO)
    assert line == {
        "opponent_team_id": ATL, "is_home": False, "won": True, "points": 99,
        "opponent_points": 95, "rebounds": 43,
        "turnovers": 14,           # total_turnovers, not the players-only count
        "fast_break_points": 12,   # text in the source
        "points_in_paint": 48, "points_off_turnovers": 16,
    }


def test_teams_and_players_views(loaded):
    con, _ = loaded
    team = one(con, "SELECT abbreviation, name, nba_team_id, nba_abbreviation FROM teams "
                    "WHERE team_id = ?", NO)
    assert team == {"abbreviation": "NO", "name": "New Orleans Pelicans",
                    "nba_team_id": 1610612740, "nba_abbreviation": "NOP"}
    assert scalar(con, "SELECT count(*) FROM teams") == 3  # All-Star sides are not teams

    player = one(con, "SELECT player_name, team_id, first_game_date, last_game_date, "
                      "games_played, nba_player_id FROM players WHERE player_id = ?", TATUM)
    assert player == {"player_name": "Jayson Tatum", "team_id": BOS,
                      "first_game_date": date(2026, 10, 20), "last_game_date": date(2026, 10, 23),
                      "games_played": 2, "nba_player_id": 1628369}
    # Listed twice in the crosswalk (once per team) but stored once.
    assert scalar(con, "SELECT count(*) FROM player_ids WHERE player_id = ?", TATUM) == 1
    assert scalar(con, "SELECT count(*) FROM player_ids") == 5
    assert scalar(con, "SELECT nba_player_id FROM players WHERE player_id = ?", ALVARADO) is None


def test_oddities_are_reported_as_warnings(loaded):
    _, summary = loaded
    assert summary.warnings == [
        "1 player row(s) for someone who played have no minutes",
        "1 game(s) have no tip-off time",
    ]
    assert summary.missing == []


def test_loading_twice_changes_nothing(loaded, source_files):
    con, first = loaded
    second = load.load_season(con, SEASON, source_files)
    assert dataclasses.asdict(second) == dataclasses.asdict(first)
    assert scalar(con, "SELECT count(*) FROM games") == 6
    assert scalar(con, "SELECT count(*) FROM player_game_stats") == 10
    assert scalar(con, "SELECT count(*) FROM season_loads") == 1


def test_a_failed_load_keeps_the_previous_rows(loaded, source_files, tmp_path):
    con, _ = loaded
    # Two ESPN teams claiming one NBA.com id would make injury rows match twice.
    clash = [dict(sourcefiles.TEAM_CROSSWALK_ROWS[0], espn_team_id=team) for team in (ATL, BOS)]
    bad = dataclasses.replace(
        source_files, team_crosswalk=sourcefiles.team_crosswalk(tmp_path / "bad.parquet", clash)
    )
    with pytest.raises(load.DataIntegrityError, match="nba_team_id"):
        load.load_season(con, SEASON, bad)
    assert scalar(con, "SELECT count(*) FROM games") == 6
    assert scalar(con, "SELECT count(*) FROM player_game_stats") == 10
    assert scalar(con, "SELECT count(*) FROM team_seasons WHERE nba_team_id IS NOT NULL") == 3


def test_a_file_for_another_season_is_rejected(con, source_files):
    with pytest.raises(load.SourceSchemaError, match="expected only 2026"):
        load.load_season(con, 2026, source_files)
    assert scalar(con, "SELECT count(*) FROM games") == 0


def test_a_missing_required_column_is_named(con, source_files, tmp_path):
    broken = dataclasses.replace(
        source_files, schedule=sourcefiles.schedule(tmp_path / "s.parquet", drop=("home_id",))
    )
    with pytest.raises(load.SourceSchemaError, match="home_id"):
        load.load_season(con, SEASON, broken)


def test_optional_columns_and_files_may_be_absent(con, source_files, tmp_path):
    older = load.SeasonFiles(
        schedule=sourcefiles.schedule(
            tmp_path / "s.parquet",
            drop=("time_valid", "neutral_site", "notes_headline", "venue_full_name"),
        ),
        player_box=sourcefiles.player_box(
            tmp_path / "p.parquet", drop=("plus_minus", "reason", "ejected")
        ),
        team_box=sourcefiles.team_box(tmp_path / "t.parquet", drop=("fast_break_points",)),
    )
    summary = load.load_season(con, SEASON, older)
    assert summary.missing == ["team_crosswalk", "player_crosswalk", "injuries"]
    assert summary.games == 6
    assert scalar(con, "SELECT count(*) FROM games WHERE label IS NOT NULL") == 0
    # Without time_valid every listed time is taken at face value.
    assert scalar(con, "SELECT count(*) FROM games WHERE tipoff_utc IS NULL") == 0
    assert scalar(con, "SELECT count(*) FROM player_game_stats WHERE plus_minus IS NOT NULL") == 0
    assert scalar(con, "SELECT count(*) FROM team_seasons WHERE nba_team_id IS NOT NULL") == 0
    assert scalar(con, "SELECT count(*) FROM injury_report_rows") == 0
