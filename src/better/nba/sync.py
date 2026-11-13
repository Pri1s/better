"""Download whatever is needed and load it, one season at a time."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Protocol

import duckdb

from better.nba import load, seasons, sources


class SyncError(RuntimeError):
    """One or more seasons could not be loaded. The others were."""


class Fetch(Protocol):
    def __call__(
        self, dataset: sources.Dataset, season: int, raw_root: Path, *, refresh: bool
    ) -> Path | None: ...


def sync(
    con: duckdb.DuckDBPyConnection,
    *,
    raw_root: Path,
    first: int | None = None,
    last: int | None = None,
    refresh: bool = False,
    today: date | None = None,
    fetch: Fetch = sources.fetch,
    log: Callable[[str], None] = print,
) -> list[load.SeasonSummary]:
    """Bring seasons ``first``..``last`` up to date and return what was loaded.

    The season in progress is downloaded again on every call, because its files
    change daily. Finished seasons are loaded once and then left alone unless
    ``refresh`` is set. With no ``first``, the range starts at the earliest
    season already loaded, or at the current season on a fresh database.

    A season whose files are malformed is reported and skipped so that one bad
    season does not block a long backfill; ``SyncError`` is raised at the end.
    A failed download stops the run at once, since the next one would fail too.
    """
    current = seasons.current_season(today)
    last = current if last is None else last
    loaded = {row[0] for row in con.execute("SELECT season FROM season_loads").fetchall()}
    if first is None:
        first = min(loaded | {last})
    if first > last:
        raise ValueError(f"--from {first} is after --to {last}")

    summaries: list[load.SeasonSummary] = []
    failures: list[str] = []
    for season in range(first, last + 1):
        name = seasons.label(season)
        in_progress = season >= current
        if season in loaded and not (refresh or in_progress):
            log(f"{name}  already loaded")
            continue

        paths = {
            dataset.key: fetch(dataset, season, raw_root, refresh=refresh or in_progress)
            for dataset in sources.DATASETS
        }
        unavailable = [d.key for d in sources.DATASETS if d.required and paths[d.key] is None]
        if unavailable:
            log(f"{name}  skipped: no {', '.join(unavailable)} file is published")
            continue

        try:
            summary = load.load_season(con, season, load.SeasonFiles(**paths))
        except (load.SourceSchemaError, load.DataIntegrityError) as exc:
            log(f"{name}  FAILED: {exc}")
            failures.append(f"{name} ({exc})")
            continue
        summaries.append(summary)
        log(summary.line())
        for dataset_key in summary.missing:
            log(f"         no {dataset_key} file for this season")
        for warning in summary.warnings:
            log(f"         note: {warning}")

    if summaries:
        load.check_links(con)
    if failures:
        raise SyncError(f"{len(failures)} season(s) could not be loaded: " + "; ".join(failures))
    return summaries
