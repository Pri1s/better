"""The ``better lines`` commands."""

from __future__ import annotations

import argparse
import sys

import duckdb

from better import config
from better.lines import mapping, mirror, store

MIRROR = "mirror"
SOURCES = (MIRROR,)


def register(groups: argparse._SubParsersAction) -> None:
    lines = groups.add_parser("lines", help="prop lines: record, link and grade them")
    commands = lines.add_subparsers(dest="command", required=True, metavar="COMMAND")

    snapshot = commands.add_parser(
        "snapshot",
        help="record lines from a source",
        description=(
            "Record lines from a source. The response is saved untouched under the raw "
            "directory before anything reads it."
        ),
    )
    snapshot.add_argument(
        "--source", choices=SOURCES, default=MIRROR,
        help="where to read lines from (default: mirror)",
    )
    snapshot.set_defaults(handler=_snapshot)

    rebuild = commands.add_parser(
        "rebuild", help="empty the line tables and load them again from the raw files"
    )
    rebuild.set_defaults(handler=_rebuild)

    map_parser = commands.add_parser(
        "map",
        help="link lines to games, players and stats",
        description=(
            "Link every recorded line to a game, a player and a stat. Safe to repeat: run "
            "it again after adding an alias or loading new box scores."
        ),
    )
    map_parser.set_defaults(handler=_map)

    unmatched = commands.add_parser(
        "unmatched", help="list lines that could not be linked, and why"
    )
    unmatched.add_argument("--limit", type=int, default=30, metavar="N",
                           help="how many groups to show (default: 30)")
    unmatched.set_defaults(handler=_unmatched)

    status = commands.add_parser("status", help="show what has been recorded")
    status.set_defaults(handler=_status)


def _snapshot(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    raw_root = config.raw_dir()
    if args.source == MIRROR:
        if not config.flag(config.MIRROR, default=True):
            print(f"error: the mirror is switched off ({config.MIRROR}=no)", file=sys.stderr)
            return 1
        try:
            mirror.import_new(raw_root, store.known_meta(con, mirror.SOURCE, "commit"))
        except mirror.MirrorError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return _loaded(con, store.load_pending(con, raw_root))


def _rebuild(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    return _loaded(con, store.rebuild(con, config.raw_dir()))


def _loaded(con: duckdb.DuckDBPyConnection, loaded: list[store.Loaded]) -> int:
    """Report what was loaded and link it."""
    code = _report(loaded)
    if loaded:
        print(mapping.map_lines(con, [item.snapshot_id for item in loaded]).line())
    return code


def _map(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    store.load_pending(con, config.raw_dir())
    summary = mapping.map_lines(con)
    print(summary.line())
    if summary.by_match.get("league"):
        print(f"{summary.by_match['league']:,} of the linked lines found their player outside "
              "the two teams' rosters (a line with no team, a trade, or a season's first night).")
    return 0


def _unmatched(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    groups = mapping.unmatched(con, args.limit)
    if not groups:
        print("Every line is linked, or mapping has not run yet (better lines map).")
        return 0
    header = ("reason", "player", "team", "lines", "first", "last", "example stat", "closest names")
    table = [header] + [
        (
            group["link_status"], group["player_name"], group["team"] or "",
            f"{group['lines']:,}", str(group["first_date"]), str(group["last_date"]),
            group["example_stat"], ", ".join(group["closest"] or []),
        )
        for group in groups
    ]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(header))]
    for row in table:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True))
              .rstrip())
    print(f"\nTo fix a name, add a line to {config.data_dir() / mapping.LOCAL_ALIAS_FILE} "
          "(header: alias,player) and run: better lines map")
    return 0


def _report(loaded: list[store.Loaded]) -> int:
    if not loaded:
        print("Nothing new to load.")
        return 0
    # A first import brings hundreds of snapshots; list a handful and total the rest.
    shown = loaded if len(loaded) <= 12 else loaded[:3] + loaded[-3:]
    for index, item in enumerate(shown):
        if len(loaded) > 12 and index == 3:
            print(f"  ... {len(loaded) - 6:,} more ...")
        print(f"  {item.line()}")
    lines = sum(item.rows_kept for item in loaded)
    skipped = sum(sum(item.dropped.values()) for item in loaded)
    print(f"Loaded {len(loaded):,} snapshot(s): {lines:,} lines, {skipped:,} rows skipped.")
    failed = [item for item in loaded if item.error]
    for item in failed:
        print(f"  could not read {item.snapshot_id}: {item.error}", file=sys.stderr)
    return 1 if failed else 0


def _status(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    pending = store.load_pending(con, config.raw_dir())
    if pending:
        print(f"Loaded {len(pending):,} snapshot file(s) the database did not have yet.")

    rows = con.execute(
        """
        SELECT s.source,
               count(*),
               sum(s.rows_kept),
               strftime(min(s.fetched_at_utc), '%Y-%m-%d %H:%M'),
               strftime(max(s.fetched_at_utc), '%Y-%m-%d %H:%M'),
               count(*) FILTER (WHERE json_extract_string(s.detail, '$.error') IS NOT NULL)
        FROM line_snapshots s
        GROUP BY s.source
        ORDER BY s.source
        """
    ).fetchall()
    if not rows:
        print("No lines recorded yet. Start with: better lines snapshot")
        return 0

    header = ("source", "snapshots", "lines", "first (UTC)", "latest (UTC)", "unreadable")
    table = [header] + [
        (source, f"{snapshots:,}", f"{lines:,}", first, latest, f"{bad:,}")
        for source, snapshots, lines, first, latest, bad in rows
    ]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(header))]
    for row in table:
        cells = [str(row[0]).ljust(widths[0])]
        cells += [str(cell).rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)]
        print("  ".join(cells))

    links = con.execute(
        "SELECT coalesce(link_status, 'not mapped yet'), count(*) FROM prop_lines "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    total = sum(count for _, count in links)
    if total:
        print("Links:  " + ", ".join(f"{count:,} {status}" for status, count in links))
        results = con.execute(
            "SELECT result, count(*) FROM graded_lines WHERE link_status = 'linked' "
            "GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        if results:
            print("Graded: " + ", ".join(f"{count:,} {result}" for result, count in results))

    stale = store.stale_snapshots(con)
    if stale:
        print(f"{stale:,} snapshot(s) were stored by an older parser. "
              "Run: better lines rebuild")
    return 0
