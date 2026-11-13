"""DuckDB connection and schema."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb

from better import config, stats

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
    package = resources.files("better")
    con.execute(package.joinpath("sql/schema.sql").read_text(encoding="utf-8"))
    # The stat views are generated from the catalogue in better.stats, so that a stat
    # is defined in exactly one place. lines.sql builds on them.
    con.execute(stats.stat_views_sql())
    con.execute(f"CREATE OR REPLACE VIEW stat_names AS SELECT * FROM {stats.name_table_sql()}")
    con.execute(package.joinpath("sql/lines.sql").read_text(encoding="utf-8"))
