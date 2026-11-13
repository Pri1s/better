from __future__ import annotations

import shutil

import linefiles
import pytest
import sourcefiles

from better import config, db
from better.nba import load

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep the developer's own .env and environment out of every test."""
    monkeypatch.setenv("BETTER_ENV_FILE", str(tmp_path / "no-such.env"))
    for name in (config.ODDS_API_KEY, config.PRIZEPICKS_DIRECT, config.MIRROR):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def source_files(tmp_path_factory) -> load.SeasonFiles:
    """One season of source-shaped files, written once for the whole test run."""
    return sourcefiles.build(tmp_path_factory.mktemp("sources"))


@pytest.fixture
def con():
    connection = db.connect(db.MEMORY)
    yield connection
    connection.close()


@pytest.fixture
def loaded(con, source_files):
    """A fresh in-memory database with the fixture season loaded."""
    summary = load.load_season(con, sourcefiles.SEASON, source_files)
    return con, summary


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A throwaway data directory for tests that go through the command line."""
    monkeypatch.setenv("BETTER_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def mirror_repo(tmp_path):
    """A local git repository that stands in for the mirror. No network involved."""
    return linefiles.mirror_repo(tmp_path)
