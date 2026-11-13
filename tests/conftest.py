from __future__ import annotations

import pytest
import sourcefiles

from better import db
from better.nba import load


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
