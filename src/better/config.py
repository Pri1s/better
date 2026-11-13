"""Where the project keeps its data."""

from __future__ import annotations

import os
from pathlib import Path

# src/better/config.py -> the project root is two levels above the package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Directory for downloaded files and the database.

    Defaults to ``<project>/data``. Set ``BETTER_DATA_DIR`` to keep it somewhere else.
    """
    override = os.environ.get("BETTER_DATA_DIR")
    return Path(override).expanduser() if override else PROJECT_ROOT / "data"


def db_path() -> Path:
    return data_dir() / "better.duckdb"


def raw_dir() -> Path:
    """Downloaded source files, kept so the database can be rebuilt without the network."""
    return data_dir() / "raw"
