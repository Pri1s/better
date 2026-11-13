"""Spec 03: every line is tied to a game, a player and a stat, or left alone.

The fixture season (see ``sourcefiles``) has three teams. The lines here are aimed at:

    game 103  Oct 23  BOS @ ATL  final      Tatum 28 pts, Young 31 pts, White played
    game 102  Oct 21  NO  @ ATL  final      Young, Murray, Alvarado
    game 104  Oct 25  ATL @ NO   scheduled
    game 105  Oct 22  NO  @ BOS  postponed
"""

from __future__ import annotations

from datetime import datetime

import pytest
from helpers import one, rows, scalar
from sourcefiles import ALVARADO, ATL, BOS, MURRAY, NO, TATUM, WHITE, YOUNG

from better import cli
from better.lines import mapping, store
from better.lines.rows import LineRow

FETCHED = datetime(2026, 10, 23, 12, 6, 28)
GAME_102 = datetime(2026, 10, 21, 23, 30)
GAME_103 = datetime(2026, 10, 23, 23, 30)
GAME_104 = datetime(2026, 10, 26, 0, 0)   # 8 pm Eastern on the 25th
GAME_105 = datetime(2026, 10, 22, 23, 30)
NO_GAME_DAY = datetime(2026, 10, 27, 23, 30)


def line(player="Jayson Tatum", stat="Points", value=27.5, *, start=GAME_103, team="BOS",
         **more) -> LineRow:
    return LineRow(book=more.pop("book", "prizepicks"), league="NBA", player_name=player,
                   stat_name=stat, line=value, start_time_utc=start, team_code=team, **more)


def add(con, lines: list[LineRow], snapshot_id="mirror/20261023T120628Z", source="mirror"):
    store.insert_lines(con, snapshot_id, source, FETCHED, lines)


def linked(con, tmp_path, lines: list[LineRow], **options) -> list[dict]:
    """Insert the lines, map them, and return their link columns in order."""
    add(con, lines, **options)
    mapping.map_lines(con, local_aliases=tmp_path / "no-aliases.csv")
    cursor = con.execute(
        "SELECT game_id, team_id, player_id, stat, link_status, player_match "
        "FROM prop_lines ORDER BY snapshot_id, line_no")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def add_box_score_row(con, game_id, player_id, name, team_id, opponent_id, day):
    """A player the fixture files do not have, written straight into the box scores."""
    con.execute(
        "INSERT INTO player_game_stats BY NAME SELECT ? AS game_id, ? AS player_id, 2027 AS season,"
        " 'regular' AS season_type, CAST(? AS DATE) AS game_date, ? AS team_id,"
        " ? AS opponent_team_id, TRUE AS is_home, ? AS player_name, FALSE AS starter,"
        " FALSE AS did_not_play, FALSE AS ejected, 20.0 AS minutes, 7 AS points",
        [game_id, player_id, day, team_id, opponent_id, name],
    )


# --- R3.1 to R3.4: the ordinary ways a line resolves ---------------------------


def test_a_line_resolves_to_its_game_player_and_stat(loaded, tmp_path):
    con, _ = loaded
    assert linked(con, tmp_path, [line()]) == [{
        "game_id": 103, "team_id": BOS, "player_id": TATUM, "stat": "points",
        "link_status": "linked", "player_match": "team"}]


@pytest.mark.parametrize(
    ("printed", "team", "start", "expected"),
    [
        ("Jose Alvarado", "NOP", GAME_102, (102, NO, ALVARADO)),   # box score: 'José Alvarado'
        ("JAYSON TATUM", "bos", GAME_103, (103, BOS, TATUM)),
        ("Trae Young", "ATL", GAME_104, (104, ATL, YOUNG)),        # a game not yet played
    ],
)
def test_names_and_team_codes_are_matched_loosely(loaded, tmp_path, printed, team, start,
                                                  expected):
    con, _ = loaded
    found = linked(con, tmp_path, [line(printed, team=team, start=start)])[0]
    assert (found["game_id"], found["team_id"], found["player_id"]) == expected
    assert found["link_status"] == "linked"


def test_the_league_abbreviation_and_espn_abbreviation_both_work(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [
        line("Dejounte Murray", team="NOP", start=GAME_102),   # the league's code
        line("Dejounte Murray", team="NO", start=GAME_102),    # ESPN's code
    ])
    assert [f["player_id"] for f in found] == [MURRAY, MURRAY]


def test_a_team_given_by_name_instead_of_code(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [
        line("Derrick White", team=None, team_name="Boston Celtics"),
        line("Derrick White", team=None, team_name="Celtics"),
        line("Derrick White", team=None, team_name="Boston Boston Celtics"),  # seen in the mirror
    ])
    assert [(f["game_id"], f["player_id"], f["link_status"]) for f in found] == [
        (103, WHITE, "linked")] * 3


def test_an_unknown_stat_is_marked_and_the_rest_still_resolves(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [line(stat="Dunks", value=0.5)])[0]
    assert found == {"game_id": 103, "team_id": BOS, "player_id": TATUM, "stat": None,
                     "link_status": "no_stat", "player_match": "team"}


@pytest.mark.parametrize(
    ("stat_name", "expected"),
    [("Pts+Rebs+Asts", "pts_rebs_asts"), ("3-PT Made", "threes_made"),
     ("Blocked Shots", "blocks"), ("Fantasy Score", "fantasy_score"),
     ("Two Pointers Made", "twos_made"), ("Double-Double", "double_double")],
)
def test_prizepicks_stat_names_map_to_the_catalogue(loaded, tmp_path, stat_name, expected):
    con, _ = loaded
    assert linked(con, tmp_path, [line(stat=stat_name)])[0]["stat"] == expected


def test_a_suffix_the_source_drops_or_adds_is_tolerated_within_the_roster(loaded, tmp_path):
    con, _ = loaded
    add_box_score_row(con, 103, 77, "Larry Nance Jr.", ATL, BOS, "2026-10-23")
    found = linked(con, tmp_path, [
        line("Larry Nance", team="ATL"),          # the source drops the suffix
        line("Jayson Tatum Jr.", team="BOS"),     # the source adds one
    ])
    assert [(f["player_id"], f["link_status"], f["player_match"]) for f in found] == [
        (77, "linked", "team"), (TATUM, "linked", "team")]


def test_an_exact_name_beats_a_suffix_free_one(loaded, tmp_path):
    con, _ = loaded
    # Father and son on the same roster: 'Gary Payton' must mean the one printed that way.
    add_box_score_row(con, 103, 81, "Gary Payton", ATL, BOS, "2026-10-23")
    add_box_score_row(con, 103, 82, "Gary Payton II", ATL, BOS, "2026-10-23")
    found = linked(con, tmp_path, [line("Gary Payton", team="ATL"),
                                   line("Gary Payton II", team="ATL")])
    assert [f["player_id"] for f in found] == [81, 82]


def test_an_alias_file_bridges_a_name_the_source_prints_differently(loaded, tmp_path):
    con, _ = loaded
    add(con, [line("Tre Young", team="ATL")])
    aliases = tmp_path / "player_aliases.csv"

    mapping.map_lines(con, local_aliases=aliases)
    assert scalar(con, "SELECT link_status FROM prop_lines") == "no_player"

    aliases.write_text("alias,player\nTre Young,Trae Young\n# a comment,ignored\n,\n")
    summary = mapping.map_lines(con, local_aliases=aliases)
    assert summary.aliases >= 1
    assert one(con, "SELECT player_id, link_status FROM prop_lines") == {
        "player_id": YOUNG, "link_status": "linked"}
    assert rows(con, "SELECT alias, player, origin FROM player_aliases WHERE origin = 'local'") == [
        ("Tre Young", "Trae Young", "local")]


def test_a_player_not_on_either_roster_is_found_across_the_league(loaded, tmp_path):
    con, _ = loaded
    # Murray has only played for New Orleans; the source lists him with Atlanta (a trade).
    found = linked(con, tmp_path, [line("Dejounte Murray", team="ATL")])[0]
    assert found == {"game_id": 103, "team_id": ATL, "player_id": MURRAY, "stat": "points",
                     "link_status": "linked", "player_match": "league"}


def test_last_seasons_rosters_cover_a_seasons_first_night(loaded, tmp_path):
    con, _ = loaded
    # Someone who has not appeared in a 2026-27 box score yet, but did in 2025-26.
    con.execute(
        "INSERT INTO player_game_stats BY NAME SELECT 9001 AS game_id, 55 AS player_id,"
        " 2026 AS season, 'regular' AS season_type, DATE '2026-04-01' AS game_date,"
        " 2 AS team_id, 1 AS opponent_team_id, TRUE AS is_home, 'Al Horford' AS player_name,"
        " FALSE AS starter, FALSE AS did_not_play, FALSE AS ejected")
    found = linked(con, tmp_path, [line("Al Horford", team="BOS")])[0]
    assert (found["player_id"], found["player_match"]) == (55, "league")


# --- R3.5, R3.6: when not to link ---------------------------------------------


def test_two_players_who_fit_leave_the_line_unlinked(loaded, tmp_path):
    con, _ = loaded
    add_box_score_row(con, 102, 99, "Jayson Tatum", ATL, NO, "2026-10-21")  # a namesake
    found = linked(con, tmp_path, [line("Jayson Tatum")])[0]
    assert found["link_status"] == "ambiguous_player"
    assert found["player_id"] is None and found["game_id"] == 103


def test_a_namesake_elsewhere_in_the_league_does_not_disturb_a_roster_match(loaded, tmp_path):
    con, _ = loaded
    # The namesake plays for New Orleans, which is not in game 103.
    add_box_score_row(con, 102, 99, "Jayson Tatum", NO, ATL, "2026-10-21")
    assert linked(con, tmp_path, [line("Jayson Tatum")])[0]["player_id"] == TATUM


def test_a_combo_line_is_never_given_a_player(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [
        line("Jayson Tatum + Trae Young", team=None, team_name="BOS/ATL", is_combo=True)])[0]
    assert found == {"game_id": None, "team_id": None, "player_id": None, "stat": "points",
                     "link_status": "combo", "player_match": None}


@pytest.mark.parametrize(
    "changes",
    [
        {"start": NO_GAME_DAY},                       # Boston does not play that day
        {"team": "XYZ"},                              # not a team
        {"opponent_code": "NOP"},                     # Boston plays Atlanta, not New Orleans
        {"start": None},                              # no start time
    ],
)
def test_no_game_means_no_link(loaded, tmp_path, changes):
    con, _ = loaded
    found = linked(con, tmp_path, [line(**changes)])[0]
    assert found["link_status"] == "no_game"
    assert found["game_id"] is None and found["player_id"] is None


def test_an_unknown_player_is_left_unlinked(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [line("Nobody Atall")])[0]
    assert (found["game_id"], found["player_id"], found["link_status"]) == (103, None, "no_player")


# --- R3.2 (both-teams form) and R3.12 (no team at all) ---------------------------


def odds_api_line(player="Jayson Tatum", market="player_points", book="draftkings", **more):
    return LineRow(book=book, league="NBA", player_name=player, stat_name=market, line=27.5,
                   start_time_utc=GAME_103, home_team_name="Atlanta Hawks",
                   away_team_name="Boston Celtics", **more)


def test_a_line_that_names_both_teams_finds_the_game_and_the_players_team(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [odds_api_line(), odds_api_line("Trae Young")],
                   snapshot_id="odds_api/20261023T120628Z", source="odds_api")
    assert [(f["game_id"], f["team_id"], f["player_id"], f["stat"], f["link_status"])
            for f in found] == [(103, BOS, TATUM, "points", "linked"),
                                (103, ATL, YOUNG, "points", "linked")]


def test_home_and_away_must_be_the_right_way_round(loaded, tmp_path):
    con, _ = loaded
    swapped = LineRow(book="draftkings", league="NBA", player_name="Jayson Tatum",
                      stat_name="player_points", line=27.5, start_time_utc=GAME_103,
                      home_team_name="Boston Celtics", away_team_name="Atlanta Hawks")
    found = linked(con, tmp_path, [swapped], snapshot_id="odds_api/20261023T120628Z",
                   source="odds_api")[0]
    assert found["link_status"] == "no_game"


def test_alternate_markets_and_fantasy_points_from_the_odds_api(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [
        odds_api_line(market="player_points_alternate"),
        odds_api_line(market="player_fantasy_points", book="prizepicks"),
        odds_api_line(market="player_fantasy_points", book="underdog"),   # scored differently
        odds_api_line(market="Points"),                                    # not an Odds API key
    ], snapshot_id="odds_api/20261023T120628Z", source="odds_api")
    assert [(f["stat"], f["link_status"]) for f in found] == [
        ("points", "linked"), ("fantasy_score", "linked"), (None, "no_stat"), (None, "no_stat")]


def test_a_line_with_no_team_is_linked_through_the_player(loaded, tmp_path):
    con, _ = loaded
    found = linked(con, tmp_path, [
        line("Trae Young", team=None),                     # Atlanta plays on the 23rd
        line("Dejounte Murray", team=None),                # New Orleans does not
        line("Nobody Atall", team=None),
    ])
    assert found[0] == {"game_id": 103, "team_id": ATL, "player_id": YOUNG, "stat": "points",
                        "link_status": "linked", "player_match": "league"}
    assert (found[1]["link_status"], found[1]["game_id"]) == ("no_game", None)
    assert (found[2]["link_status"], found[2]["player_id"]) == ("no_game", None)


def test_a_traded_player_with_no_team_stated_resolves_to_the_team_he_was_on(loaded, tmp_path):
    con, _ = loaded
    # Young moves to Boston and first appears for them on the 28th. Both Atlanta and
    # Boston play on the 23rd (each other), so only the dates can say which side he was on.
    add_box_score_row(con, 9002, YOUNG, "Trae Young", BOS, NO, "2026-10-28")
    found = linked(con, tmp_path, [line("Trae Young", team=None)])[0]
    assert (found["game_id"], found["team_id"], found["link_status"]) == (103, ATL, "linked")


def test_two_namesakes_with_no_team_stated_are_left_alone(loaded, tmp_path):
    con, _ = loaded
    add_box_score_row(con, 102, 99, "Trae Young", NO, ATL, "2026-10-21")
    found = linked(con, tmp_path, [line("Trae Young", team=None)])[0]
    assert found["link_status"] == "ambiguous_player" and found["player_id"] is None


# --- R3.7, R3.8: mapping is repeatable and never changes the row count ------------


def test_mapping_twice_gives_the_same_links(loaded, tmp_path):
    con, _ = loaded
    first = linked(con, tmp_path, [line(), line("Trae Young", team="ATL"), line("Nobody Atall"),
                                   line("Tatum + Young", is_combo=True)])
    mapping.map_lines(con, local_aliases=tmp_path / "none.csv")
    again = rows(con, "SELECT game_id, team_id, player_id, stat, link_status, player_match "
                      "FROM prop_lines ORDER BY line_no")
    assert again == [tuple(f.values()) for f in first]
    assert scalar(con, "SELECT count(*) FROM prop_lines") == 4


def test_a_player_becomes_linkable_once_he_appears_in_a_box_score(loaded, tmp_path):
    con, _ = loaded
    assert linked(con, tmp_path, [line("Rookie Newcomer")])[0]["link_status"] == "no_player"
    add_box_score_row(con, 103, 88, "Rookie Newcomer", BOS, ATL, "2026-10-23")
    summary = mapping.map_lines(con, local_aliases=tmp_path / "none.csv")
    assert summary.linked == 1
    assert scalar(con, "SELECT player_id FROM prop_lines") == 88


def test_mapping_can_be_limited_to_some_snapshots(loaded, tmp_path):
    con, _ = loaded
    add(con, [line()], snapshot_id="mirror/20261022T120600Z")
    add(con, [line("Trae Young", team="ATL")], snapshot_id="mirror/20261023T120628Z")
    summary = mapping.map_lines(con, ["mirror/20261023T120628Z"],
                                local_aliases=tmp_path / "none.csv")
    assert (summary.lines, summary.linked) == (1, 1)
    assert rows(con, "SELECT snapshot_id, link_status FROM prop_lines ORDER BY 1") == [
        ("mirror/20261022T120600Z", None), ("mirror/20261023T120628Z", "linked")]
    assert mapping.map_lines(con, [], local_aliases=tmp_path / "none.csv").lines == 0


def test_the_summary_counts_every_outcome(loaded, tmp_path):
    con, _ = loaded
    add(con, [line(), line("Nobody Atall"), line(stat="Dunks"), line(start=NO_GAME_DAY),
              line("A + B", is_combo=True), line("Dejounte Murray", team="ATL")])
    summary = mapping.map_lines(con, local_aliases=tmp_path / "none.csv")
    assert summary.lines == 6
    assert summary.by_status == {"linked": 2, "no_player": 1, "no_stat": 1, "no_game": 1,
                                 "combo": 1}
    assert summary.by_match == {"team": 2, "league": 1}  # the Dunks line still found Tatum
    assert summary.line().startswith("Linked 2 of 6 lines (33.3%)")


# --- R3.9: the report that makes an alias easy to write ---------------------------


def test_unmatched_groups_lines_and_suggests_close_names(loaded, tmp_path):
    con, _ = loaded
    add(con, [line("Jason Tatum"), line("Jason Tatum", stat="Rebounds"), line("Nobody Atall"),
              line(stat="Dunks"), line()])
    mapping.map_lines(con, local_aliases=tmp_path / "none.csv")
    groups = mapping.unmatched(con)
    assert [(g["link_status"], g["player_name"], g["lines"]) for g in groups] == [
        ("no_player", "Jason Tatum", 2), ("no_player", "Nobody Atall", 1),
        ("no_stat", "Jayson Tatum", 1)]
    assert groups[0]["closest"] == ["Jayson Tatum"]
    assert groups[1]["closest"] is None


def test_map_and_unmatched_from_the_command_line(data_dir, source_files, capsys):
    import sourcefiles

    from better import db
    from better.nba import load

    con = db.connect()
    load.load_season(con, sourcefiles.SEASON, source_files)
    add(con, [line(), line("Jason Tatum")])
    con.close()

    assert cli.main(["lines", "map"]) == 0
    assert "Linked 1 of 2 lines (50.0%); 1 no_player." in capsys.readouterr().out

    assert cli.main(["lines", "unmatched"]) == 0
    out = capsys.readouterr().out
    assert "no_player  Jason Tatum" in out and "Jayson Tatum" in out
    assert "player_aliases.csv" in out
