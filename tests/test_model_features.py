"""Hand-computed and leakage checks on a small, synthetic season."""

from datetime import date

import numpy as np
import pytest
import sourcefiles as sf

from better import stats
from better.model import features


def row(table, game, player):
    return np.flatnonzero((table.game_id == game) & (table.player_id == player)).item()


def report(con, day, player, status, *, team=sf.BOS, time=None):
    con.execute(
        """INSERT INTO injury_report_rows
        (season, report_time_utc, game_date, team_name, report_player_name,
         status, nba_team_id, nba_player_id)
        VALUES (2027, ?, ?, ?, ?, ?, ?, ?)""",
        [
            time or f"{day} 10:00:00",
            day,
            sf.TEAM_NAME[team],
            "Synthetic Player",
            status,
            sf.NBA_TEAM[team],
            sf.NBA_PLAYER[player],
        ],
    )


def test_forward_values_and_dnp_rows(loaded):
    con, _ = loaded
    table = features.build(con)
    assert len(table) == 10
    i = row(table, 101, sf.HAUSER)
    assert not table.played[i]
    assert np.isnan(table.minutes[i])
    assert np.isnan(table.target(stats.BY_KEY["points"])[i])
    i = row(table, 102, sf.YOUNG)
    assert table.column("min_s")[i] == 34
    assert table.column("start_s")[i] == 1
    assert table.column("gp_career")[i] == 1
    assert table.column("days_since_played")[i] == 1
    assert table.column("rest_days")[i] == 0
    assert table.column("is_back_to_back")[i] == 1
    assert table.baseline_season(stats.BY_KEY["points"])[i] == 25
    i = row(table, 103, sf.YOUNG)
    decay = 0.5 ** (1 / 6)
    assert table.column("min_s")[i] == pytest.approx((34 * decay + 35) / (decay + 1))
    assert table.rate[i, 0, features.TRACKED.index("points")] == pytest.approx(
        (25 * decay + 22) / (34 * decay + 35)
    )
    assert table.baseline_season(stats.BY_KEY["points"])[i] == 23.5
    assert table.column("start_s")[i] == pytest.approx(decay / (decay + 1))


def test_through_limits_outcome_reads(loaded):
    con, _ = loaded
    table = features.build(con, through=date(2026, 10, 21))
    assert set(table.game_id) == {101, 102}
    assert len(table) == 7
    # An invalid future row would fail _Pass validation if it were loaded.
    con.execute("UPDATE player_game_stats SET game_id=9999 WHERE game_id=103")
    earlier = features.build(con, through="2026-10-21")
    np.testing.assert_equal(earlier.common, table.common)


def test_outcomes_on_or_after_date_cannot_change_features(loaded):
    con, _ = loaded
    before = features.build(con)
    con.execute("""UPDATE player_game_stats SET minutes=1, points=100,
        rebounds=99, assists=88, starter=NOT starter, did_not_play=NOT did_not_play,
        position='C' WHERE game_date >= DATE '2026-10-21'""")
    con.execute("""UPDATE team_game_stats SET points=200, opponent_points=1,
        field_goals_attempted=200 WHERE game_date >= DATE '2026-10-21'""")
    after = features.build(con)
    keep = before.game_date <= np.datetime64("2026-10-21")
    np.testing.assert_equal(before.game_id[keep], after.game_id[keep])
    for name in (
        "common",
        "rate",
        "season_pg",
        "opp_allow",
        "league_allow",
        "opp_pos_allow",
        "league_pos_allow",
    ):
        np.testing.assert_equal(getattr(before, name)[keep], getattr(after, name)[keep])


def test_upcoming_rows_share_historical_path_and_unknown_players_work(loaded):
    con, _ = loaded
    historical = features.build(con)
    i = row(historical, 103, sf.YOUNG)
    con.execute("DELETE FROM player_game_stats WHERE game_id=103")
    live = features.build(con, extra_rows=[(103, sf.YOUNG, sf.ATL), (104, 99999, sf.ATL)])
    j = row(live, 103, sf.YOUNG)
    np.testing.assert_equal(historical.common[i], live.common[j])
    np.testing.assert_equal(historical.rate[i], live.rate[j])
    assert not live.played[j]
    assert np.isnan(live.target(stats.BY_KEY["points"])[j])
    rookie = row(live, 104, 99999)
    assert live.column("gp_career")[rookie] == 0
    assert np.isnan(live.column("min_s")[rookie])
    assert np.isfinite(live.column("opp_pace")[rookie])


def test_status_weights_are_measured_only_from_earlier_games(loaded):
    con, _ = loaded
    # On Oct 20, one Questionable player plays and one sits: empirical sit rate 1/2.
    report(con, "2026-10-20", sf.TATUM, "Questionable")
    report(con, "2026-10-20", sf.HAUSER, "Questionable")
    table = features.build(con, extra_rows=[(108, sf.WHITE, sf.BOS)])
    first = row(table, 101, sf.TATUM)
    assert np.isnan(table.column("own_status")[first])  # no invented status prior
    third = row(table, 103, sf.TATUM)
    assert table.column("own_status")[third] == 0.5
    white = row(table, 103, sf.WHITE)
    assert table.column("team_min_out")[white] == 18
    assert table.column("team_ast_out")[white] == 2.5
    assert table.column("team_reb_out")[white] == 4
    final = row(table, 108, sf.WHITE)
    assert table.column("own_status")[final] == pytest.approx(1 / 3)


def test_late_reports_and_not_submitted(loaded):
    con, _ = loaded
    report(con, "2026-10-20", sf.HAUSER, "Questionable")
    before = features.build(con)
    con.execute(
        "UPDATE injury_report_rows SET status='Out' WHERE report_time_utc > '2026-10-23 12:00:00'"
    )
    after = features.build(con)
    np.testing.assert_equal(before.common, after.common)
    con.execute(
        """INSERT INTO injury_report_rows
        (season, report_time_utc, game_date, team_name, nba_team_id, reason)
        VALUES (2027, '2026-10-23 10:00:00', '2026-10-23', 'Atlanta Hawks', ?,
        'NOT YET SUBMITTED')""",
        [sf.NBA_TEAM[sf.ATL]],
    )
    table = features.build(con)
    i = row(table, 103, sf.YOUNG)
    assert table.column("has_report")[i] == 0
    assert np.isnan(table.column("own_status")[i])
    assert np.isnan(table.column("team_min_out")[i])


def test_empty_database(con):
    table = features.build(con)
    assert len(table) == 0
    assert table.common.shape == (0, len(features.COMMON_NAMES))


def test_unknown_game_and_duplicate_extra_rejected(loaded):
    con, _ = loaded
    with pytest.raises(ValueError, match="does not play"):
        features.build(con, extra_rows=[(104, sf.TATUM, sf.BOS)])
    with pytest.raises(ValueError, match="duplicate"):
        features.build(con, extra_rows=[(104, sf.YOUNG, sf.ATL)] * 2)
    with pytest.raises(ValueError, match="duplicate"):
        features.build(con, extra_rows=[(103, sf.YOUNG, sf.ATL)])


def test_previous_season_rate_and_team_totals(loaded):
    con, _ = loaded
    con.execute("""UPDATE games SET season=2026 WHERE game_id=101""")
    con.execute("""UPDATE player_game_stats SET season=2026 WHERE game_id=101""")
    table = features.build(con)
    i = row(table, 102, sf.YOUNG)
    assert table.column("gp_season")[i] == 0
    assert table.previous_rate[i, features.TRACKED.index("points")] == pytest.approx(25 / 34)
    assert table.column("team_pace")[i] == pytest.approx(88 + 0.44 * 22 - 10 + 14)
    assert table.column("team_off")[i] == pytest.approx(100 * 100 / 101.68)
    j = row(table, 103, sf.YOUNG)
    assert table.previous_rate[j, features.TRACKED.index("points")] == pytest.approx(25 / 34)


def test_future_schedule_can_exclude_recent_outcomes(loaded):
    con, _ = loaded
    table = features.build(
        con,
        through="2026-10-25",
        outcomes_through="2026-10-20",
        extra_rows=[(104, sf.YOUNG, sf.ATL)],
    )
    i = row(table, 104, sf.YOUNG)
    assert table.column("gp_career")[i] == 1
    assert table.column("min_last")[i] == 34
    assert set(table.game_id) == {101, 104}


def test_stat_ignores_unrelated_missing_components(loaded):
    con, _ = loaded
    con.execute("UPDATE player_game_stats SET rebounds=NULL WHERE player_id=?", [sf.YOUNG])
    table = features.build(con)
    i = row(table, 102, sf.YOUNG)
    assert table.target(stats.BY_KEY["points"])[i] == 22
    assert table.baseline_season(stats.BY_KEY["points"])[i] == 25
    assert np.isnan(table.target(stats.BY_KEY["rebounds"])[i])


def test_persistent_absence_becomes_teammate_baseline(loaded):
    con, _ = loaded
    report(con, "2026-10-20", sf.HAUSER, "Out")
    report(con, "2026-10-23", sf.WHITE, "Out")
    con.execute(
        "UPDATE player_game_stats SET did_not_play=TRUE WHERE game_id=103 AND player_id=?",
        [sf.WHITE],
    )
    con.execute("UPDATE player_game_stats SET position='F' WHERE player_id=?", [sf.TATUM])
    report(con, "2026-11-20", sf.WHITE, "Out")
    table = features.build(con, extra_rows=[(108, sf.TATUM, sf.BOS)])
    third = row(table, 103, sf.TATUM)
    future = row(table, 108, sf.TATUM)
    assert table.column("team_min_out")[third] == 30
    assert table.column("team_min_out")[future] == 30
    assert table.column("min_out_delta")[future] == 0
    assert table.column("pos_min_out")[future] == 0  # White is G; Tatum is F.
