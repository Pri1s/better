"""Spec 02, requirements R2.1 and R2.2: raw files are exact, stable and re-readable."""

from __future__ import annotations

from datetime import datetime

import pytest

from better.lines import raw

MOMENT = datetime(2026, 11, 13, 17, 5, 1)
# A body with everything a careless writer would mangle: non-ASCII text, escapes,
# significant whitespace and a number that must not be reformatted.
BODY = '{"data": [ {"name": "Nikola Jokić", "line_score": 24.50, "note": "a\\"b\\n"} ]}\n'


def envelope(body: str = BODY, moment: datetime = MOMENT, source: str = "prizepicks"):
    return raw.Envelope(
        source=source,
        fetched_at_utc=moment,
        meta={"league_id": 7},
        responses=(raw.Response("https://example.test/projections", 200, moment, body),),
    )


def test_a_snapshot_is_named_by_its_source_and_time(tmp_path):
    saved = envelope()
    assert saved.snapshot_id == "prizepicks/20261113T170501Z"
    path = raw.write(saved, tmp_path)
    assert path == tmp_path / "lines/prizepicks/2026/11/prizepicks_20261113T170501Z.json.gz"
    assert raw.snapshot_id_of(path) == saved.snapshot_id


def test_the_body_comes_back_exactly_as_it_was_saved(tmp_path):
    path = raw.write(envelope(), tmp_path)
    restored = raw.read(path)
    assert restored.responses[0].body == BODY
    assert restored == envelope()


def test_the_same_snapshot_always_gives_the_same_bytes(tmp_path):
    first = raw.write(envelope(), tmp_path / "a").read_bytes()
    second = raw.write(envelope(), tmp_path / "b").read_bytes()
    assert first == second
    assert raw.sha256(tmp_path / "a" / envelope().relative_path) == raw.sha256(
        tmp_path / "b" / envelope().relative_path
    )


def test_saving_twice_is_harmless(tmp_path):
    path = raw.write(envelope(), tmp_path)
    before = path.stat().st_mtime_ns
    assert raw.write(envelope(), tmp_path) == path
    assert path.stat().st_mtime_ns == before  # not rewritten
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no stray .part file


def test_a_different_snapshot_in_the_same_second_is_refused(tmp_path):
    raw.write(envelope(), tmp_path)
    with pytest.raises(raw.RawConflictError):
        raw.write(envelope(body='{"data": []}'), tmp_path)
    assert raw.read(tmp_path / envelope().relative_path).responses[0].body == BODY


def test_files_are_listed_oldest_first_and_strays_are_ignored(tmp_path):
    late = raw.write(envelope(moment=datetime(2026, 11, 14, 9, 0, 0)), tmp_path)
    early = raw.write(envelope(moment=datetime(2026, 10, 30, 9, 0, 0)), tmp_path)
    other = raw.write(envelope(source="mirror"), tmp_path)
    (tmp_path / "lines" / "prizepicks" / "notes.txt").write_text("not a snapshot")
    (late.parent / "prizepicks_20261114T090000Z.json.gz.part").write_bytes(b"half written")
    assert raw.list_files(tmp_path) == [other, early, late]
    assert raw.list_files(tmp_path / "nothing-here") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-12T19:00:00.000-05:00", datetime(2026, 11, 13, 0, 0, 0)),
        ("2026-11-12T12:07:33.431Z", datetime(2026, 11, 12, 12, 7, 33, 431000)),
        ("2026-11-12T12:07:33", datetime(2026, 11, 12, 12, 7, 33)),
        ("yesterday", None),
        ("", None),
        (None, None),
        (20261112, None),
    ],
)
def test_times_are_read_as_utc(text, expected):
    assert raw.parse_time(text) == expected


def test_an_unknown_format_is_refused(tmp_path):
    import gzip
    import json

    path = tmp_path / "prizepicks_20261113T170501Z.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"format": 99}).encode()))
    with pytest.raises(ValueError, match="unknown raw format"):
        raw.read(path)
