from __future__ import annotations

from datetime import datetime

import pytest
from helpers import rows, scalar
from sourcefiles import ALVARADO, ATL, BOS, NO, TATUM, WHITE, YOUNG

from better.nba import load

BEFORE_TIP = """
    SELECT report_time_utc, team_id, player_id, player_name, status
    FROM injury_report_before_tip(?)
    WHERE game_id = ?
    ORDER BY team_id, player_name NULLS FIRST
"""


def test_report_times_are_converted_from_eastern_to_utc(loaded):
    con, _ = loaded
    times = [r[0] for r in rows(con, "SELECT DISTINCT report_time_utc FROM injury_report_rows "
                                     "ORDER BY 1")]
    assert times[0] == datetime(2026, 10, 22, 21, 30)   # 5:30 pm EDT is UTC-4
    assert times[-1] == datetime(2026, 11, 19, 22, 30)  # 5:30 pm EST is UTC-5


def test_rows_printed_twice_are_stored_once(loaded):
    con, summary = loaded
    assert summary.injury_rows == 10
    assert summary.injury_reports == 7
    assert scalar(con, "SELECT count(*) FROM injury_report_rows WHERE status = 'Out' "
                       "AND report_time_utc = TIMESTAMP '2026-10-23 17:30:00'") == 1


def test_rows_are_linked_to_games_teams_and_players(loaded):
    con, _ = loaded
    load.check_links(con)  # one view row per stored row
    linked = rows(con, """
        SELECT game_id, team_id, player_id, player_name, player_match
        FROM injury_reports
        WHERE report_time_utc IN (TIMESTAMP '2026-10-23 22:45:00', TIMESTAMP '2026-10-25 17:30:00')
        ORDER BY game_id, player_name""")
    assert linked == [
        (103, BOS, TATUM, "Jayson Tatum", "crosswalk"),
        (103, ATL, YOUNG, "Trae Young", "crosswalk"),
        # No NBA.com id in the source: matched by name against the team's box scores,
        # accent and all ('Alvarado, Jose' on the report, 'José Alvarado' in the box score).
        (104, NO, None, "Guy Unknown", None),
        (104, NO, ALVARADO, "Jose Alvarado", "name"),
    ]


def test_a_date_without_a_game_is_left_unlinked(loaded):
    con, _ = loaded
    row = rows(con, "SELECT game_id, team_id, player_id FROM injury_reports "
                    "WHERE game_date = DATE '2026-10-27'")
    assert row == [(None, BOS, TATUM)]


def test_not_yet_submitted_row_is_kept_without_a_player(loaded):
    con, _ = loaded
    row = rows(con, "SELECT team_id, player_id, player_name, status, reason, player_match "
                    "FROM injury_reports WHERE report_player_name IS NULL")
    assert row == [(ATL, None, None, None, "NOT YET SUBMITTED", None)]


@pytest.mark.parametrize(
    ("lead_minutes", "expected"),
    [
        # Tip-off is 23:30 UTC. Reports for the game: 21:30 the day before
        # (Questionable), 17:30 (Out), 22:45 (Available), and 00:00 after tip-off (Out).
        (3000, []),
        (600, [(datetime(2026, 10, 22, 21, 30), BOS, TATUM, "Jayson Tatum", "Questionable")]),
        (60, [(datetime(2026, 10, 23, 17, 30), ATL, None, None, None),
              (datetime(2026, 10, 23, 17, 30), BOS, TATUM, "Jayson Tatum", "Out")]),
        (30, [(datetime(2026, 10, 23, 22, 45), ATL, YOUNG, "Trae Young", "Probable"),
              (datetime(2026, 10, 23, 22, 45), BOS, TATUM, "Jayson Tatum", "Available")]),
        (0, [(datetime(2026, 10, 23, 22, 45), ATL, YOUNG, "Trae Young", "Probable"),
             (datetime(2026, 10, 23, 22, 45), BOS, TATUM, "Jayson Tatum", "Available")]),
    ],
)
def test_report_before_tip_shows_only_what_was_known_then(loaded, lead_minutes, expected):
    con, _ = loaded
    assert rows(con, BEFORE_TIP, lead_minutes, 103) == expected


def test_report_before_tip_never_returns_a_later_report(loaded):
    con, _ = loaded
    for lead_minutes in (0, 30, 60, 600):
        late = scalar(con, "SELECT count(*) FROM injury_report_before_tip(?) "
                           "WHERE report_time_utc > cutoff_utc", lead_minutes)
        assert late == 0


def test_report_before_tip_skips_games_without_a_tipoff_time(loaded):
    con, _ = loaded
    # Game 108 has a report (Derrick White, questionable) but no tip-off time to count back from.
    assert scalar(con, "SELECT count(*) FROM injury_reports WHERE game_id = 108 "
                       "AND player_id = ?", WHITE) == 1
    assert rows(con, BEFORE_TIP, 60, 108) == []


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("McConnell, T.J.", "T.J. McConnell"),
        ("Udeh, Jr., Ernest", "Ernest Udeh Jr."),
        ("Nene", "Nene"),
        (None, None),
    ],
)
def test_first_last(con, printed, expected):
    assert scalar(con, "SELECT first_last(?)", printed) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("T.J. McConnell", "TJ McConnell"),
        ("José Alvarado", "Jose Alvarado"),
        ("De'Aaron Fox", "DeAaron Fox"),
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        ("Nikola Jokić", "  NIKOLA   JOKIC "),
    ],
)
def test_name_key_ignores_spelling_noise(con, a, b):
    assert scalar(con, "SELECT name_key(?) = name_key(?)", a, b) is True


def test_name_key_keeps_generational_suffixes(con):
    # Fathers and sons are different players.
    assert scalar(con, "SELECT name_key('Gary Payton') = name_key('Gary Payton II')") is False
