"""Spec 03, requirement R3.11: how each line turned out.

Box scores in the fixture season:
    game 101 (final)      Tatum 30 pts, 8 reb, 5 ast, 4 threes; Hauser listed, did not play
    game 103 (final)      Tatum 28 pts; Young 31 pts
    game 104 (scheduled)  Atlanta at New Orleans
    game 105 (postponed)  New Orleans at Boston
"""

from __future__ import annotations

from datetime import datetime

import pytest
from helpers import rows
from test_lines_mapping import GAME_103, GAME_104, GAME_105, add, line

from better.lines import mapping

GAME_101 = datetime(2026, 10, 20, 23, 30)


def graded(con, tmp_path, lines) -> list[tuple]:
    add(con, lines)
    mapping.map_lines(con, local_aliases=tmp_path / "none.csv")
    return rows(con, "SELECT result, actual FROM graded_lines ORDER BY line_no")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(27.5, "over"), (28.5, "under"), (28.0, "push"), (0.5, "over"), (99.5, "under")],
)
def test_a_stat_is_compared_with_its_line(loaded, tmp_path, value, expected):
    con, _ = loaded
    assert graded(con, tmp_path, [line(value=value)]) == [(expected, 28.0)]  # Tatum scored 28


def test_combined_and_derived_stats_are_graded_from_the_same_box_score(loaded, tmp_path):
    con, _ = loaded
    results = graded(con, tmp_path, [
        line(stat="Pts+Rebs+Asts", value=42.5, start=GAME_101),   # 30 + 8 + 5 = 43
        line(stat="Pts+Rebs+Asts", value=43.5, start=GAME_101),
        line(stat="3-PT Made", value=3.5, start=GAME_101),        # 4
        line(stat="Rebs+Asts", value=13.0, start=GAME_101),       # 8 + 5 = 13
        line(stat="Fantasy Score", value=48.0, start=GAME_101),   # 30 + 9.6 + 7.5 + 3 - 2 = 48.1
        line(stat="Double-Double", value=0.5, start=GAME_101),    # only points reached ten
    ])
    assert [result for result, _ in results] == ["over", "under", "over", "push", "over",
                                                 "under"]
    assert results[4][1] == pytest.approx(48.1)
    assert results[5][1] == 0.0


def test_a_player_who_did_not_play_is_a_void(loaded, tmp_path):
    con, _ = loaded
    assert graded(con, tmp_path, [line("Sam Hauser", start=GAME_101)]) == [("void", None)]


def test_a_player_missing_from_the_box_score_is_a_void(loaded, tmp_path):
    con, _ = loaded
    # Linked through the league fallback, but he was not in the game.
    assert graded(con, tmp_path, [line("Dejounte Murray", team="ATL")]) == [("void", None)]


def test_a_postponed_game_is_a_void(loaded, tmp_path):
    con, _ = loaded
    assert graded(con, tmp_path, [line(start=GAME_105)]) == [("void", None)]


def test_a_game_not_yet_played_is_pending(loaded, tmp_path):
    con, _ = loaded
    assert graded(con, tmp_path, [line("Trae Young", team="ATL", start=GAME_104)]) == [
        ("pending", None)]


def test_a_line_that_was_not_linked_is_not_graded(loaded, tmp_path):
    con, _ = loaded
    results = graded(con, tmp_path, [line("Nobody Atall"), line(stat="Dunks"),
                                     line("Tatum + Young", is_combo=True)])
    assert results == [("unlinked", None)] * 3


def test_grading_returns_each_line_exactly_once(loaded, tmp_path):
    con, _ = loaded
    graded(con, tmp_path, [line(), line("Trae Young", team="ATL"), line(start=GAME_105),
                           line("Sam Hauser", start=GAME_101), line("Nobody Atall")])
    assert rows(con, "SELECT count(*), count(DISTINCT line_no) FROM graded_lines") == [(5, 5)]


def test_grading_keeps_the_lines_own_columns_and_adds_the_games(loaded, tmp_path):
    con, _ = loaded
    graded(con, tmp_path, [line()])
    row = rows(con, "SELECT player_name, stat, line, game_date, season, game_status, tipoff_utc "
                    "FROM graded_lines")[0]
    assert row == ("Jayson Tatum", "points", 27.5, datetime(2026, 10, 23).date(), 2027, "final",
                   GAME_103)
