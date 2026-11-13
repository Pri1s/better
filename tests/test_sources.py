from __future__ import annotations

import pytest
import requests

from better.nba import sources

PARQUET = b"PAR1" + b"\x00" * 16 + b"PAR1"


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class FakeSession:
    """Hands out canned responses (or raises canned errors) and records the URLs asked for."""

    def __init__(self, *results):
        self.results = list(results)
        self.urls: list[str] = []

    def get(self, url, *, timeout):
        self.urls.append(url)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_urls_and_local_paths(tmp_path):
    assert sources.PLAYER_BOX.url(2027) == (
        "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
        "espn_nba_player_boxscores/player_box_2027.parquet"
    )
    assert sources.INJURIES.local_path(2019, tmp_path) == (
        tmp_path / "sportsdataverse" / "nba_stats_injury_report" / "injury_report_2019.parquet"
    )
    # Every dataset key is a SeasonFiles field, which is how sync hands files to the loader.
    assert [d.key for d in sources.DATASETS if d.required] == ["schedule", "player_box", "team_box"]


def test_download_is_saved(tmp_path):
    session = FakeSession(FakeResponse(200, PARQUET))
    path = sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session)
    assert path == sources.SCHEDULE.local_path(2027, tmp_path)
    assert path.read_bytes() == PARQUET
    assert session.urls == [sources.SCHEDULE.url(2027)]
    assert [p.name for p in path.parent.iterdir()] == ["nba_schedule_2027.parquet"]


def test_file_on_disk_is_reused_without_a_request(tmp_path):
    sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=FakeSession(FakeResponse(200, PARQUET)))
    session = FakeSession()  # any request would fail: there is nothing to hand out
    assert sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session) is not None
    assert session.urls == []


def test_refresh_downloads_again(tmp_path):
    sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=FakeSession(FakeResponse(200, PARQUET)))
    newer = b"PAR1" + b"\x01" * 16 + b"PAR1"
    session = FakeSession(FakeResponse(200, newer))
    path = sources.fetch(sources.SCHEDULE, 2027, tmp_path, refresh=True, session=session)
    assert path.read_bytes() == newer


def test_file_that_is_not_published_returns_none(tmp_path):
    session = FakeSession(FakeResponse(404))
    assert sources.fetch(sources.INJURIES, 2010, tmp_path, session=session) is None
    assert len(session.urls) == 1  # a 404 is an answer, not something to retry


def test_file_that_disappears_upstream_keeps_the_local_copy(tmp_path):
    path = sources.fetch(
        sources.SCHEDULE, 2027, tmp_path, session=FakeSession(FakeResponse(200, PARQUET))
    )
    again = sources.fetch(
        sources.SCHEDULE, 2027, tmp_path, refresh=True, session=FakeSession(FakeResponse(404))
    )
    assert again == path
    assert path.read_bytes() == PARQUET


def test_a_response_that_is_not_parquet_is_rejected(tmp_path):
    session = FakeSession(FakeResponse(200, b"<html>rate limited</html>"))
    with pytest.raises(sources.DownloadError, match="did not return a parquet file"):
        sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session)
    assert not sources.SCHEDULE.local_path(2027, tmp_path).parent.exists()


def test_server_errors_are_retried(tmp_path):
    session = FakeSession(
        FakeResponse(503), requests.ConnectionError("reset"), FakeResponse(200, PARQUET)
    )
    assert sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session, pause=0) is not None
    assert len(session.urls) == 3


def test_giving_up_after_repeated_failures(tmp_path):
    session = FakeSession(FakeResponse(500), FakeResponse(500), FakeResponse(500))
    with pytest.raises(sources.DownloadError, match="HTTP 500"):
        sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session, pause=0)
    assert len(session.urls) == sources.ATTEMPTS


def test_client_errors_are_not_retried(tmp_path):
    session = FakeSession(FakeResponse(403))
    with pytest.raises(sources.DownloadError, match="HTTP 403"):
        sources.fetch(sources.SCHEDULE, 2027, tmp_path, session=session, pause=0)
    assert len(session.urls) == 1
