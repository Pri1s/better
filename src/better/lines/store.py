"""Load raw snapshot files into the database.

The files under ``data/raw/lines/`` are the record; ``line_snapshots`` and
``prop_lines`` are an index of them. Loading is driven by what is on disk: any file
the database does not know yet is parsed and stored, so a crash between saving a
snapshot and loading it loses nothing, and the tables can be emptied and rebuilt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

from better.lines import mirror, raw, rows

Parser = Callable[[raw.Envelope], rows.Parsed]

# source -> (parser, parser version). The version is stored with every snapshot so
# that `better lines status` can say when a rebuild would change something.
PARSERS: dict[str, tuple[Parser, int]] = {
    mirror.SOURCE: (mirror.parse, mirror.PARSER_VERSION),
}

_TEXT = (
    "book", "league", "player_name", "stat_name", "odds_type", "source_line_id",
    "source_player_id", "source_game_id", "team_code", "team_name", "opponent_code",
    "opponent_name", "home_team_name", "away_team_name", "status",
)
_NUMBER = ("line", "over_price", "under_price")
_TIME = ("start_time_utc", "updated_at_utc")

_INSERT = """
INSERT INTO prop_lines BY NAME
SELECT
    CAST(snapshot_id AS VARCHAR) AS snapshot_id,
    CAST(line_no AS INTEGER) AS line_no,
    CAST(source AS VARCHAR) AS source,
    CAST(fetched_at_utc AS TIMESTAMP) AS fetched_at_utc,
    {text},
    {number},
    {time},
    CAST(is_combo AS BOOLEAN) AS is_combo,
    CAST(is_promo AS BOOLEAN) AS is_promo,
    CAST(board_rank AS INTEGER) AS board_rank
FROM incoming_lines
""".format(
    text=", ".join(f"CAST({name} AS VARCHAR) AS {name}" for name in _TEXT),
    number=", ".join(f"CAST({name} AS DOUBLE) AS {name}" for name in _NUMBER),
    time=", ".join(f"CAST({name} AS TIMESTAMP) AS {name}" for name in _TIME),
)


@dataclass
class Loaded:
    """What became of one raw file."""

    snapshot_id: str
    source: str
    fetched_at_utc: datetime
    rows_seen: int = 0
    rows_kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    error: str | None = None  # the file could not be parsed at all

    def line(self) -> str:
        text = f"{self.snapshot_id}  {self.rows_kept:,} lines"
        if self.dropped:
            reasons = ", ".join(f"{count:,} {reason}" for reason, count in self.dropped.items())
            text += f" ({reasons} skipped)"
        if self.error:
            text += f"  COULD NOT BE READ: {self.error}"
        return text


def load_pending(con: duckdb.DuckDBPyConnection, raw_root: Path) -> list[Loaded]:
    """Load every snapshot file the database does not hold yet, oldest first."""
    known = {row[0] for row in con.execute("SELECT snapshot_id FROM line_snapshots").fetchall()}
    loaded = []
    for path in raw.list_files(raw_root):
        if raw.snapshot_id_of(path) not in known:
            loaded.append(_load_file(con, path, raw_root))
    return loaded


def rebuild(con: duckdb.DuckDBPyConnection, raw_root: Path) -> list[Loaded]:
    """Empty the line tables and load them again from the raw files."""
    con.execute("DELETE FROM prop_lines")
    con.execute("DELETE FROM line_snapshots")
    return load_pending(con, raw_root)


def known_meta(con: duckdb.DuckDBPyConnection, source: str, key: str) -> set[str]:
    """Values of one ``detail`` key across a source's snapshots (the mirror's commits)."""
    found = con.execute(
        "SELECT DISTINCT json_extract_string(detail, ?) FROM line_snapshots WHERE source = ?",
        [f"$.{key}", source],
    ).fetchall()
    return {row[0] for row in found if row[0] is not None}


def stale_snapshots(con: duckdb.DuckDBPyConnection) -> int:
    """Snapshots stored by an older parser than the one in the code now."""
    count = 0
    for source, (_, version) in PARSERS.items():
        count += con.execute(
            "SELECT count(*) FROM line_snapshots WHERE source = ? AND parser_version <> ?",
            [source, version],
        ).fetchone()[0]
    return count


def _load_file(con: duckdb.DuckDBPyConnection, path: Path, raw_root: Path) -> Loaded:
    envelope = raw.read(path)
    result = Loaded(envelope.snapshot_id, envelope.source, envelope.fetched_at_utc)
    parser, version = PARSERS.get(envelope.source, (None, 0))
    parsed = rows.Parsed()
    if parser is None:
        result.error = f"no parser for source {envelope.source!r}"
    else:
        try:
            parsed = parser(envelope)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            # A file that cannot be read is recorded as such, so that it is not retried
            # on every command and so that it shows up in the status report.
            result.error = f"{type(exc).__name__}: {exc}"
            parsed = rows.Parsed()
    result.rows_seen = parsed.seen
    result.rows_kept = len(parsed.rows)
    result.dropped = dict(parsed.dropped)

    detail = dict(parsed.detail)
    if result.dropped:
        detail["dropped"] = result.dropped
    if result.error:
        detail["error"] = result.error

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            "INSERT INTO line_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                envelope.snapshot_id,
                envelope.source,
                envelope.fetched_at_utc,
                path.relative_to(raw_root).as_posix(),
                raw.sha256(path),
                version,
                result.rows_seen,
                result.rows_kept,
                raw.utcnow(),
                json.dumps(detail, sort_keys=True),
            ],
        )
        insert_lines(con, envelope.snapshot_id, envelope.source, envelope.fetched_at_utc,
                     parsed.rows)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return result


def insert_lines(
    con: duckdb.DuckDBPyConnection,
    snapshot_id: str,
    source: str,
    fetched_at_utc: datetime,
    lines: list[rows.LineRow],
) -> None:
    """Append one snapshot's rows to ``prop_lines``, numbered in the order given."""
    if not lines:
        return
    con.register("incoming_lines", _columns(snapshot_id, source, fetched_at_utc, lines))
    try:
        con.execute(_INSERT)
    finally:
        con.unregister("incoming_lines")


def _columns(
    snapshot_id: str, source: str, fetched_at_utc: datetime, lines: list[rows.LineRow]
) -> dict[str, np.ndarray]:
    """The rows as one array per column, which DuckDB reads far faster than row inserts.

    Times and flags travel as text and every column is cast in SQL, so a column that
    happens to be entirely NULL in one snapshot still loads with the right type.
    """
    count = len(lines)

    def objects(values: list) -> np.ndarray:
        array = np.empty(count, dtype=object)
        array[:] = values
        return array

    def moment(value: datetime | None) -> str | None:
        return None if value is None else value.strftime("%Y-%m-%d %H:%M:%S")

    def flag(value: bool | None) -> str | None:
        return None if value is None else ("true" if value else "false")

    columns: dict[str, np.ndarray] = {
        "snapshot_id": objects([snapshot_id] * count),
        "line_no": np.arange(1, count + 1, dtype=np.int64),
        "source": objects([source] * count),
        "fetched_at_utc": objects([moment(fetched_at_utc)] * count),
    }
    for name in _TEXT:
        columns[name] = objects([getattr(line, name) for line in lines])
    for name in _NUMBER:
        columns[name] = np.array(
            [np.nan if getattr(line, name) is None else getattr(line, name) for line in lines],
            dtype=np.float64,
        )
    for name in _TIME:
        columns[name] = objects([moment(getattr(line, name)) for line in lines])
    columns["is_combo"] = objects([flag(line.is_combo) for line in lines])
    columns["is_promo"] = objects([flag(line.is_promo) for line in lines])
    columns["board_rank"] = np.array(
        [np.nan if line.board_rank is None else line.board_rank for line in lines],
        dtype=np.float64,
    )
    return columns
