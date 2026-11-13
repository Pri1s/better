"""DuckDB connection and schema."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb

from better import config

MEMORY = ":memory:"


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Open the project database, creating it and its schema when needed.

    Pass ``":memory:"`` for a throwaway database (the tests do).
    """
    target = str(path) if path is not None else str(config.db_path())
    if target != MEMORY:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(target)
    # Stored timestamps are UTC without a zone. Pinning the session zone keeps any
    # zone-aware expression from depending on the machine it runs on.
    con.execute("SET TimeZone = 'UTC'")
    apply_schema(con)
    return con


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create missing tables and (re)create views and macros. Safe to run every time."""
    sql = resources.files("better").joinpath("sql/schema.sql").read_text(encoding="utf-8")
    con.execute(sql)
