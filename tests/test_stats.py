from __future__ import annotations

from helpers import one, rows, scalar
from sourcefiles import HAUSER, TATUM

from better import stats


def test_every_name_points_at_a_known_stat():
    for key in (*stats.PRIZEPICKS_NAMES.values(), *stats.ODDS_API_MARKETS.values()):
        assert key in stats.BY_KEY
    assert stats.ODDS_API_ALTERNATES <= set(stats.ODDS_API_MARKETS)
    assert len(stats.KEYS) == len(stats.STATS)  # no key is defined twice


def test_stats_are_computed_from_the_box_score(loaded):
    con, _ = loaded
    # Game 101: Tatum had 30 points, 8 rebounds, 5 assists, 4 threes on fixture defaults
    # of 1 steal, 0 blocks, 2 turnovers, 4 field goals made.
    row = one(con, "SELECT * FROM player_prop_stats WHERE game_id = 101 AND player_id = ?", TATUM)
    assert row["points"] == 30
    assert row["pts_rebs_asts"] == 43
    assert row["pts_rebs"] == 38
    assert row["rebs_asts"] == 13
    assert row["blks_stls"] == 1
    assert row["twos_made"] == 0  # 4 field goals made, all of them threes
    assert row["fantasy_score"] == 30 + 1.2 * 8 + 1.5 * 5 + 3 * 1 + 3 * 0 - 2
    assert row["double_double"] == 0


def test_a_player_who_did_not_play_has_no_stats(loaded):
    con, _ = loaded
    assert rows(con, "SELECT * FROM player_prop_stats WHERE player_id = ?", HAUSER) == []
    assert scalar(con, "SELECT count(*) FROM player_stat_values WHERE player_id = ?", HAUSER) == 0


def test_long_view_has_one_row_per_stat(loaded):
    con, _ = loaded
    found = {r[0] for r in rows(
        con, "SELECT stat FROM player_stat_values WHERE game_id = 101 AND player_id = ?", TATUM)}
    assert found == set(stats.KEYS)


def test_double_double_needs_two_categories_at_ten(con):
    sql = f"SELECT {stats.BY_KEY['double_double'].sql}, {stats.BY_KEY['triple_double'].sql} " \
          "FROM (SELECT ? AS points, ? AS rebounds, ? AS assists, 0 AS steals, 0 AS blocks)"
    assert con.execute(sql, [20, 10, 9]).fetchone() == (1, 0)
    assert con.execute(sql, [20, 9, 9]).fetchone() == (0, 0)
    assert con.execute(sql, [10, 10, 10]).fetchone() == (1, 1)


def test_stat_names_view_lists_both_sources(con):
    assert scalar(con, "SELECT stat FROM stat_names WHERE family = 'prizepicks' "
                       "AND stat_name = 'Pts+Rebs+Asts'") == "pts_rebs_asts"
    assert one(con, "SELECT stat, is_alternate FROM stat_names WHERE family = 'odds_api' "
                    "AND stat_name = 'player_points_alternate'") == {
        "stat": "points", "is_alternate": True}
