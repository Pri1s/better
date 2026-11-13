"""Spec 02, requirements R2.5, R2.10 and R2.11: the mirror's history becomes snapshots."""

from __future__ import annotations

from datetime import datetime

import linefiles
import pytest
from conftest import needs_git

from better.lines import mirror, raw

COMMIT = mirror.Commit("a" * 40, datetime(2026, 10, 23, 12, 7, 40))


def parse(props: list[dict]):
    envelope = mirror.capture(linefiles.document(props).encode(), COMMIT)
    return mirror.parse(envelope)


def test_every_row_shape_in_the_history_is_read():
    parsed = parse(linefiles.MIXED_PROPS)
    by_player = {(row.player_name, row.stat_name): row for row in parsed.rows}

    current = by_player["Jayson Tatum", "Points"]
    assert current.line == 27.5  # sent as the text "27.5"
    assert current.start_time_utc == datetime(2026, 10, 23, 23, 30)
    assert (current.team_code, current.team_name) == ("BOS", "Boston Celtics")
    assert (current.opponent_code, current.opponent_name) == ("ATL", "Atlanta Hawks")
    assert (current.source_line_id, current.source_player_id, current.source_game_id) == (
        "16915616", "188005", "195018")
    assert (current.book, current.league, current.odds_type, current.status) == (
        "prizepicks", "NBA", "standard", "pre_game")
    assert current.board_rank == 101
    assert current.is_combo is False

    middle = by_player["Trae Young", "Assists"]
    assert middle.line == 10.5
    assert middle.source_line_id is None and middle.status is None

    # No ISO time and no team code: the Central wall-clock time and the full name are used.
    early = by_player["Derrick White", "3-PT Made"]
    assert early.start_time_utc == datetime(2026, 10, 23, 23, 30)
    assert (early.team_code, early.team_name) == (None, "Boston Celtics")

    assert by_player["Dejounte Murray", "Points"].line == 18.0  # sent as the integer 18
    assert by_player["Dejounte Murray", "Points"].start_time_utc == datetime(2026, 10, 26, 0, 0)

    # The second day of the mirror: an ISO time under "startTime" and no team at all.
    teamless = by_player["Trae Young", "Points"]
    assert teamless.start_time_utc == datetime(2026, 10, 23, 23, 30)
    assert (teamless.team_code, teamless.team_name, teamless.odds_type) == (None, None, "demon")


def test_rows_that_cannot_be_read_are_counted_by_reason():
    parsed = parse(linefiles.MIXED_PROPS)
    assert parsed.seen == len(linefiles.MIXED_PROPS) == 11
    assert len(parsed.rows) == 7
    assert dict(parsed.dropped) == {
        "no_start_time": 1, "bad_line": 1, "no_player": 1, "other_league": 1,
    }


def test_a_line_on_two_players_is_marked_as_a_combo():
    parsed = parse(linefiles.MIXED_PROPS)
    combos = [row for row in parsed.rows if row.is_combo]
    assert [(row.player_name, row.team_name) for row in combos] == [
        ("Jayson Tatum + Trae Young", "BOS/ATL")]


def test_goblin_and_demon_lines_keep_their_type():
    parsed = parse(linefiles.MIXED_PROPS)
    assert [(row.stat_name, row.line, row.odds_type) for row in parsed.rows
            if row.odds_type != "standard"] == [
        ("Rebounds", 6.5, "goblin"), ("Points", 31.5, "demon")]


def test_central_time_is_read_with_the_right_offset_in_winter_and_summer():
    winter = parse([linefiles.early_row(startTime="12/06/25 04:10 PM CST")]).rows[0]
    assert winter.start_time_utc == datetime(2025, 12, 6, 22, 10)  # CST is UTC-6
    # The mirror prints "CST" all year; in April Chicago is on daylight time, UTC-5.
    spring = parse([linefiles.early_row(startTime="04/10/26 07:00 PM CST")]).rows[0]
    assert spring.start_time_utc == datetime(2026, 4, 11, 0, 0)


def test_a_snapshot_is_dated_by_when_the_mirror_fetched_the_board():
    text = linefiles.document([linefiles.current_row()])
    assert mirror.capture(text.encode(), COMMIT).fetched_at_utc == datetime(2026, 10, 23, 12, 6, 28)
    # Older files have no per-league fetch time: the file's own scrape time is used.
    older = linefiles.document([linefiles.middle_row()], provider_at=None)
    assert mirror.capture(older.encode(), COMMIT).fetched_at_utc == datetime(
        2026, 10, 23, 12, 7, 33)
    # A file that says nothing usable is dated by its commit and kept all the same.
    broken = mirror.capture(b"<html>not json</html>", COMMIT)
    assert broken.fetched_at_utc == COMMIT.committed_at_utc
    assert broken.responses[0].body == "<html>not json</html>"
    with pytest.raises(ValueError):
        mirror.parse(broken)


def test_the_snapshot_records_where_it_came_from():
    envelope = mirror.capture(linefiles.document([linefiles.current_row()]).encode(), COMMIT)
    assert envelope.meta == {"commit": "a" * 40, "path": "data/prizepicks-nba.json"}
    assert envelope.responses[0].url == (
        "https://github.com/enkday/prizepicks-data-mirror/blob/" + "a" * 40
        + "/data/prizepicks-nba.json")
    parsed = mirror.parse(envelope)
    assert parsed.detail["commit"] == "a" * 40
    assert parsed.detail["upstream_projections"] == 3051


@needs_git
def test_one_snapshot_is_saved_per_commit_that_changed_the_file(tmp_path, mirror_repo):
    raw_root = tmp_path / "raw"
    written = mirror.import_new(raw_root, url=str(mirror_repo))
    # Three commits changed the NBA file; the commit that only touched the README did not.
    assert [raw.snapshot_id_of(path) for path in written] == [
        "mirror/20261021T120600Z", "mirror/20261022T120600Z", "mirror/20261023T120628Z"]
    newest = raw.read(written[-1])
    assert newest.responses[0].body == linefiles.document(linefiles.MIXED_PROPS)
    assert len(newest.meta["commit"]) == 40


@needs_git
def test_importing_again_saves_only_what_is_new(tmp_path, mirror_repo):
    raw_root = tmp_path / "raw"
    first = mirror.import_new(raw_root, url=str(mirror_repo))
    assert mirror.import_new(raw_root, url=str(mirror_repo)) == []

    linefiles.commit_feed(
        mirror_repo,
        linefiles.document([linefiles.current_row(line="29.5")], "2026-10-24T12:07:00.000Z",
                           "2026-10-24T12:06:00.000Z"),
        "2026-10-24T12:07:10+00:00",
    )
    known = {raw.read(path).meta["commit"] for path in first}
    again = mirror.import_new(raw_root, known, url=str(mirror_repo))
    assert [raw.snapshot_id_of(path) for path in again] == ["mirror/20261024T120600Z"]


@needs_git
def test_two_commits_with_one_scrape_time_keep_the_first_and_say_so(tmp_path, mirror_repo):
    # The same scrape time as the newest commit, but different lines.
    linefiles.commit_feed(
        mirror_repo, linefiles.document([linefiles.current_row(line="1.5")]),
        "2026-10-23T12:30:00+00:00",
    )
    notes: list[str] = []
    written = mirror.import_new(tmp_path / "raw", url=str(mirror_repo), log=notes.append)
    assert len(written) == 3
    assert len(notes) == 1 and "kept the first" in notes[0]
    assert raw.read(written[-1]).responses[0].body == linefiles.document(linefiles.MIXED_PROPS)


def test_a_missing_repository_is_reported_plainly(tmp_path):
    with pytest.raises(mirror.MirrorError, match="git clone failed"):
        mirror.import_new(tmp_path / "raw", url=str(tmp_path / "no-such-repo"))
