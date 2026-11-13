"""Where the NBA files come from, and how they are downloaded.

Every file is a prebuilt parquet that the sportsdataverse project publishes as a
GitHub release asset (https://github.com/sportsdataverse/sportsdataverse-data, MIT).
This module only downloads those files; it never calls ESPN or NBA.com.

Upstream, sportsdataverse collects the box scores and schedules from ESPN's
undocumented API and parses the injury reports from the PDFs the NBA publishes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

RELEASE_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
USER_AGENT = "better-nba-data/0.1"
PARQUET_MAGIC = b"PAR1"
TIMEOUT = (10, 120)  # seconds to connect, seconds to read
ATTEMPTS = 3


class DownloadError(RuntimeError):
    """A file could not be downloaded, or what came back was not a parquet file."""


@dataclass(frozen=True)
class Dataset:
    key: str        # also the SeasonFiles field the file is passed to
    release: str    # release tag in sportsdataverse-data
    filename: str   # asset name, with {season}
    required: bool  # a season cannot be loaded without it

    def file_name(self, season: int) -> str:
        return self.filename.format(season=season)

    def url(self, season: int) -> str:
        return f"{RELEASE_BASE}/{self.release}/{self.file_name(season)}"

    def local_path(self, season: int, raw_root: Path) -> Path:
        return raw_root / "sportsdataverse" / self.release / self.file_name(season)


SCHEDULE = Dataset("schedule", "espn_nba_schedules", "nba_schedule_{season}.parquet", True)
PLAYER_BOX = Dataset("player_box", "espn_nba_player_boxscores", "player_box_{season}.parquet", True)
TEAM_BOX = Dataset("team_box", "espn_nba_team_boxscores", "team_box_{season}.parquet", True)
TEAM_CROSSWALK = Dataset(
    "team_crosswalk", "nba_crosswalk", "nba_team_crosswalk_{season}.parquet", False
)
PLAYER_CROSSWALK = Dataset(
    "player_crosswalk", "nba_crosswalk", "nba_player_crosswalk_{season}.parquet", False
)
INJURIES = Dataset("injuries", "nba_stats_injury_report", "injury_report_{season}.parquet", False)

DATASETS = (SCHEDULE, PLAYER_BOX, TEAM_BOX, TEAM_CROSSWALK, PLAYER_CROSSWALK, INJURIES)


class Session(Protocol):
    def get(self, url: str, *, timeout: tuple[int, int]) -> requests.Response: ...


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def fetch(
    dataset: Dataset,
    season: int,
    raw_root: Path,
    *,
    refresh: bool = False,
    session: Session | None = None,
    pause: float = 1.0,
) -> Path | None:
    """Return the local copy of a file, downloading it when needed.

    A file already on disk is reused unless ``refresh`` is set. Returns ``None``
    when the release has no file for that season and none is on disk.
    """
    path = dataset.local_path(season, raw_root)
    if path.exists() and not refresh:
        return path

    session = session or new_session()
    url = dataset.url(season)
    failure = "no response"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            failure = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 404:
                # Not published for this season. Keep using an older copy if there is one.
                return path if path.exists() else None
            if response.status_code == 200:
                return _save(path, response.content, url)
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                break  # a client error will not fix itself
        if attempt < ATTEMPTS:
            time.sleep(pause * attempt)
    raise DownloadError(f"could not download {url} ({failure})")


def _save(path: Path, content: bytes, url: str) -> Path:
    if len(content) < 12 or content[:4] != PARQUET_MAGIC or content[-4:] != PARQUET_MAGIC:
        raise DownloadError(f"{url} did not return a parquet file ({len(content)} bytes)")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(content)
    os.replace(partial, path)  # never leave a half-written file where the loader looks
    return path
