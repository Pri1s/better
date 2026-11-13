from __future__ import annotations

import dataclasses
from datetime import date

import pytest
import sourcefiles
from helpers import scalar

from better.nba import sync as nba_sync

IN_PROGRESS = date(2026, 11, 12)  # 2026-27 is being played
FINISHED = date(2027, 11, 1)      # 2026-27 is over, 2027-28 has begun


class Fetcher:
    """Stands in for the downloader: serves fixture files and records what was asked for."""

    def __init__(self, files_by_season):
        self.files_by_season = files_by_season
        self.calls: list[tuple[str, int, bool]] = []

    def __call__(self, dataset, season, raw_root, *, refresh):
        self.calls.append((dataset.key, season, refresh))
        files = self.files_by_season.get(season)
        return getattr(files, dataset.key) if files else None


def run(con, fetch, tmp_path, **options):
    lines: list[str] = []
    summaries = nba_sync.sync(con, raw_root=tmp_path, fetch=fetch, log=lines.append, **options)
    return summaries, lines


def test_fresh_database_loads_only_the_current_season(con, source_files, tmp_path):
    fetch = Fetcher({2027: source_files})
    summaries, lines = run(con, fetch, tmp_path, today=IN_PROGRESS)
    assert [s.season for s in summaries] == [2027]
    assert {season for _, season, _ in fetch.calls} == {2027}
    assert lines[0].startswith("2026-27  6 games (3 final, 2 to come)")


def test_season_in_progress_is_downloaded_again_every_time(con, source_files, tmp_path):
    fetch = Fetcher({2027: source_files})
    run(con, fetch, tmp_path, today=IN_PROGRESS)
    summaries, _ = run(con, fetch, tmp_path, today=IN_PROGRESS)
    assert [s.season for s in summaries] == [2027]
    assert all(refresh for _, _, refresh in fetch.calls)
    assert scalar(con, "SELECT count(*) FROM games") == 6  # reloaded, not doubled


def test_finished_season_is_loaded_once(con, source_files, tmp_path):
    fetch = Fetcher({2027: source_files})
    run(con, fetch, tmp_path, first=2027, last=2027, today=FINISHED)
    assert not any(refresh for _, _, refresh in fetch.calls)  # files on disk are good enough

    fetch.calls.clear()
    summaries, lines = run(con, fetch, tmp_path, first=2027, last=2027, today=FINISHED)
    assert summaries == []
    assert fetch.calls == []
    assert lines == ["2026-27  already loaded"]

    summaries, _ = run(con, fetch, tmp_path, first=2027, last=2027, today=FINISHED, refresh=True)
    assert [s.season for s in summaries] == [2027]
    assert all(refresh for _, _, refresh in fetch.calls)


def test_default_range_starts_at_the_earliest_loaded_season(con, source_files, tmp_path):
    fetch = Fetcher({2027: source_files})
    run(con, fetch, tmp_path, first=2027, last=2027, today=FINISHED)
    fetch.calls.clear()
    _, lines = run(con, fetch, tmp_path, today=FINISHED)  # current season is now 2028
    assert lines == [
        "2026-27  already loaded",
        "2027-28  skipped: no schedule, player_box, team_box file is published",
    ]
    assert {season for _, season, _ in fetch.calls} == {2028}


def test_one_bad_season_does_not_block_the_others(con, source_files, tmp_path):
    broken = dataclasses.replace(
        source_files, schedule=sourcefiles.schedule(tmp_path / "s.parquet", drop=("home_id",))
    )
    fetch = Fetcher({2026: broken, 2027: source_files})
    lines: list[str] = []
    with pytest.raises(nba_sync.SyncError, match="1 season.*2025-26.*home_id"):
        nba_sync.sync(con, raw_root=tmp_path, fetch=fetch, log=lines.append,
                      first=2026, today=IN_PROGRESS)
    assert lines[0].startswith("2025-26  FAILED: s.parquet is missing expected columns: home_id")
    assert scalar(con, "SELECT list(season) FROM season_loads") == [2027]


def test_range_must_run_forwards(con, tmp_path):
    with pytest.raises(ValueError, match="after"):
        run(con, Fetcher({}), tmp_path, first=2027, last=2020, today=IN_PROGRESS)
