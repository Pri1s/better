from __future__ import annotations

import dataclasses
from datetime import date

import sourcefiles
from helpers import rows, scalar
from sourcefiles import ATL, BOS, NO, SEASON

from better.nba import load

CONTEXT = """
    SELECT game_id, rest_days, is_back_to_back, is_third_in_four_days,
           games_in_prior_3_days, games_in_prior_6_days, opponent_rest_days, rest_advantage
    FROM team_game_context WHERE team_id = ? ORDER BY game_date, game_id
"""


def test_rest_and_density_for_a_busy_team(loaded):
    con, _ = loaded
    assert rows(con, CONTEXT, ATL) == [
        # Oct 20: season opener for both teams.
        (101, None, False, False, 0, 0, None, None),
        # Oct 21: second night of a back-to-back, against a team playing its opener.
        (102, 0, True, False, 1, 1, None, None),
        # Oct 23: third game in four nights. Boston last played on the 20th.
        (103, 1, False, True, 2, 2, 2, -1),
        # Oct 25: not yet played, but its rest is already known from the schedule.
        (104, 1, False, False, 1, 3, 3, -2),
    ]


def test_postponed_games_are_ignored(loaded):
    con, _ = loaded
    # Boston's game on the 22nd was postponed, so its rest before the 23rd counts from the 20th.
    assert rows(con, CONTEXT, BOS) == [
        (101, None, False, False, 0, 0, None, None),
        (103, 2, False, False, 1, 1, 1, 1),
        (108, 27, False, False, 0, 0, 25, 2),
    ]
    assert scalar(con, "SELECT count(*) FROM team_game_context WHERE game_id = 105") == 0


def test_every_counted_game_has_one_row_per_team(loaded):
    con, _ = loaded
    per_game = rows(con, "SELECT game_id, count(*) FROM team_game_context GROUP BY 1 ORDER BY 1")
    assert per_game == [(101, 2), (102, 2), (103, 2), (104, 2), (108, 2)]
    home = rows(con, "SELECT team_id, opponent_team_id, is_home, status FROM team_game_context "
                     "WHERE game_id = 104 ORDER BY is_home DESC")
    assert home == [(NO, ATL, True, "scheduled"), (ATL, NO, False, "scheduled")]


def test_two_games_dated_the_same_day_do_not_break_anything(con, source_files, tmp_path):
    # The source misdates the odd game, which makes a team appear to play twice in a day.
    misdated = sourcefiles._game(110, date(2026, 10, 23), "2026-10-23 20:00:00", NO, ATL,
                                 score=(101, 99))
    files = dataclasses.replace(
        source_files,
        schedule=sourcefiles.schedule(
            tmp_path / "s.parquet", rows=[*sourcefiles.SCHEDULE_ROWS, misdated]
        ),
    )
    summary = load.load_season(con, SEASON, files)
    assert any("two games dated the same day" in warning for warning in summary.warnings)

    rest = rows(con, "SELECT game_id, rest_days, is_back_to_back FROM team_game_context "
                     "WHERE team_id = ? AND game_date = DATE '2026-10-23' ORDER BY game_id", ATL)
    assert rest == [(103, 1, False), (110, 0, True)]  # never negative

    # An injury report for that date resolves to one game: the later tip-off.
    load.check_links(con)
    linked = rows(con, "SELECT DISTINCT game_id FROM injury_reports "
                       "WHERE game_date = DATE '2026-10-23' AND team_id = ?", ATL)
    assert linked == [(103,)]
