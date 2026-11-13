"""Spec 02, requirements R2.2 to R2.5: the database is an index of the raw files."""

from __future__ import annotations

import json
from datetime import datetime

import linefiles
from helpers import one, rows, scalar

from better import cli
from better.lines import mirror, raw, store

FIRST = mirror.Commit("1" * 40, datetime(2026, 10, 22, 12, 7, 10))
SECOND = mirror.Commit("2" * 40, datetime(2026, 10, 23, 12, 7, 40))


def save(raw_root, props, commit, **times) -> raw.Envelope:
    envelope = mirror.capture(linefiles.document(props, **times).encode(), commit)
    raw.write(envelope, raw_root)
    return envelope


def two_days(raw_root) -> None:
    save(raw_root, [linefiles.current_row(line="28.5"), linefiles.middle_row()], FIRST,
         scraped_at="2026-10-22T12:07:00.000Z", provider_at="2026-10-22T12:06:00.000Z")
    save(raw_root, linefiles.MIXED_PROPS, SECOND)


def test_new_files_are_loaded_with_their_lines(con, tmp_path):
    two_days(tmp_path)
    loaded = store.load_pending(con, tmp_path)
    assert [(item.snapshot_id, item.rows_seen, item.rows_kept) for item in loaded] == [
        ("mirror/20261022T120600Z", 2, 2), ("mirror/20261023T120628Z", 11, 7)]

    snapshot = one(con, "SELECT * FROM line_snapshots WHERE snapshot_id = ?",
                   "mirror/20261023T120628Z")
    assert snapshot["source"] == "mirror"
    assert snapshot["fetched_at_utc"] == datetime(2026, 10, 23, 12, 6, 28)
    assert snapshot["raw_path"] == "lines/mirror/2026/10/mirror_20261023T120628Z.json.gz"
    assert snapshot["raw_sha256"] == raw.sha256(tmp_path / snapshot["raw_path"])
    assert (snapshot["rows_seen"], snapshot["rows_kept"]) == (11, 7)
    detail = json.loads(snapshot["detail"])
    assert detail["commit"] == "2" * 40
    assert detail["dropped"] == {
        "no_start_time": 1, "bad_line": 1, "no_player": 1, "other_league": 1}

    line = one(con, "SELECT * FROM prop_lines WHERE snapshot_id = ? AND line_no = 1",
               "mirror/20261023T120628Z")
    assert (line["player_name"], line["stat_name"], line["line"]) == (
        "Jayson Tatum", "Points", 27.5)
    assert line["fetched_at_utc"] == datetime(2026, 10, 23, 12, 6, 28)
    assert line["start_time_utc"] == datetime(2026, 10, 23, 23, 30)
    assert (line["book"], line["odds_type"], line["is_combo"]) == ("prizepicks", "standard", False)
    assert line["game_id"] is None and line["link_status"] is None  # mapping has not run


def test_line_numbers_follow_the_order_of_the_file(con, tmp_path):
    two_days(tmp_path)
    store.load_pending(con, tmp_path)
    numbered = rows(con, "SELECT line_no, player_name FROM prop_lines "
                         "WHERE snapshot_id = 'mirror/20261023T120628Z' ORDER BY line_no")
    assert [number for number, _ in numbered] == [1, 2, 3, 4, 5, 6, 7]
    assert numbered[3][1] == "Jayson Tatum + Trae Young"


def test_loading_twice_stores_everything_once(con, tmp_path):
    two_days(tmp_path)
    store.load_pending(con, tmp_path)
    assert store.load_pending(con, tmp_path) == []
    assert scalar(con, "SELECT count(*) FROM line_snapshots") == 2
    assert scalar(con, "SELECT count(*) FROM prop_lines") == 9


def test_a_file_saved_but_never_loaded_is_picked_up_later(con, tmp_path):
    save(tmp_path, [linefiles.current_row()], FIRST,
         scraped_at="2026-10-22T12:07:00.000Z", provider_at="2026-10-22T12:06:00.000Z")
    store.load_pending(con, tmp_path)
    # A crash here, after the file was written and before it was loaded, loses nothing.
    save(tmp_path, linefiles.MIXED_PROPS, SECOND)
    assert [item.snapshot_id for item in store.load_pending(con, tmp_path)] == [
        "mirror/20261023T120628Z"]


def test_rebuild_restores_the_same_tables_from_the_files_alone(con, tmp_path):
    two_days(tmp_path)
    store.load_pending(con, tmp_path)
    snapshots = "SELECT snapshot_id, raw_sha256, rows_kept FROM line_snapshots ORDER BY 1"
    lines = ("SELECT snapshot_id, line_no, player_name, stat_name, line FROM prop_lines "
             "ORDER BY 1, 2")
    before = (rows(con, snapshots), rows(con, lines))

    con.execute("DELETE FROM prop_lines")
    con.execute("DELETE FROM line_snapshots")
    assert len(store.rebuild(con, tmp_path)) == 2
    assert (rows(con, snapshots), rows(con, lines)) == before


def test_a_file_that_cannot_be_parsed_is_recorded_and_not_retried(con, tmp_path):
    broken = mirror.capture(b"<html>rate limited</html>", FIRST)
    raw.write(broken, tmp_path)
    save(tmp_path, linefiles.MIXED_PROPS, SECOND)

    loaded = store.load_pending(con, tmp_path)
    assert [bool(item.error) for item in loaded] == [True, False]  # the good file still loads
    assert "JSONDecodeError" in loaded[0].error
    stored = one(con, "SELECT rows_seen, rows_kept, detail FROM line_snapshots "
                      "WHERE snapshot_id = ?", broken.snapshot_id)
    assert (stored["rows_seen"], stored["rows_kept"]) == (0, 0)
    assert "error" in json.loads(stored["detail"])
    assert store.load_pending(con, tmp_path) == []
    assert (tmp_path / broken.relative_path).exists()  # the raw file is never thrown away


def test_a_source_without_a_parser_is_recorded_as_unreadable(con, tmp_path):
    moment = datetime(2026, 10, 23, 12, 0, 0)
    unknown = raw.Envelope("somewhere_else", moment, (raw.Response("u", 200, moment, "{}"),))
    raw.write(unknown, tmp_path)
    loaded = store.load_pending(con, tmp_path)
    assert loaded[0].error == "no parser for source 'somewhere_else'"
    assert scalar(con, "SELECT count(*) FROM prop_lines") == 0


def test_known_commits_are_read_back_from_the_database(con, tmp_path):
    two_days(tmp_path)
    store.load_pending(con, tmp_path)
    assert store.known_meta(con, "mirror", "commit") == {"1" * 40, "2" * 40}
    assert store.known_meta(con, "prizepicks", "commit") == set()


def test_snapshots_from_an_older_parser_are_counted(con, tmp_path):
    two_days(tmp_path)
    store.load_pending(con, tmp_path)
    assert store.stale_snapshots(con) == 0
    con.execute("UPDATE line_snapshots SET parser_version = 0 "
                "WHERE snapshot_id = 'mirror/20261022T120600Z'")
    assert store.stale_snapshots(con) == 1


# --- command line -------------------------------------------------------------


def test_status_before_anything_is_recorded(data_dir, capsys):
    assert cli.main(["lines", "status"]) == 0
    assert "No lines recorded yet" in capsys.readouterr().out


def test_snapshot_imports_the_mirror_and_status_reports_it(data_dir, mirror_repo, monkeypatch,
                                                           capsys):
    monkeypatch.setattr(mirror, "REPO_URL", str(mirror_repo))
    assert cli.main(["lines", "snapshot"]) == 0
    out = capsys.readouterr().out
    assert "Loaded 3 snapshot(s): 10 lines, 4 rows skipped." in out

    assert cli.main(["lines", "snapshot"]) == 0
    assert "Nothing new to load." in capsys.readouterr().out

    assert cli.main(["lines", "status"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["source", "snapshots", "lines", "first", "(UTC)", "latest",
                                "(UTC)", "unreadable"]
    assert lines[1].split() == ["mirror", "3", "10", "2026-10-21", "12:06", "2026-10-23",
                                "12:06", "0"]


def test_rebuild_from_the_command_line(data_dir, mirror_repo, monkeypatch, capsys):
    monkeypatch.setattr(mirror, "REPO_URL", str(mirror_repo))
    cli.main(["lines", "snapshot"])
    capsys.readouterr()
    assert cli.main(["lines", "rebuild"]) == 0
    assert "Loaded 3 snapshot(s): 10 lines" in capsys.readouterr().out


def test_the_mirror_can_be_switched_off(data_dir, monkeypatch, capsys):
    monkeypatch.setenv("BETTER_MIRROR", "no")
    assert cli.main(["lines", "snapshot"]) == 1
    assert "switched off" in capsys.readouterr().err
