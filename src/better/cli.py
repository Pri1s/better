"""Command line entry point: ``better nba sync`` and ``better nba status``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import duckdb

from better import config, db
from better.nba import load, seasons, sources
from better.nba import sync as nba_sync


def _season(text: str) -> int:
    try:
        return seasons.parse(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="better", description="Personal research tool for NBA player props."
    )
    groups = parser.add_subparsers(dest="group", required=True, metavar="GROUP")

    nba = groups.add_parser("nba", help="NBA schedule, box scores and injury reports")
    commands = nba.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sync = commands.add_parser(
        "sync",
        help="download and load NBA data",
        description=(
            "Download and load NBA data. With no options this refreshes the season in "
            "progress and fills any gap back to the earliest season already loaded."
        ),
    )
    sync.add_argument(
        "--from", dest="first", type=_season, metavar="SEASON",
        help="first season to load, named by the year it ends (2019 or 2018-19)",
    )
    sync.add_argument(
        "--to", dest="last", type=_season, metavar="SEASON",
        help="last season to load (default: the current season)",
    )
    sync.add_argument(
        "--refresh", action="store_true",
        help="download again and reload seasons that are already loaded",
    )

    commands.add_parser("status", help="show what is loaded and how fresh it is")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    con = db.connect()
    try:
        if args.command == "sync":
            return _sync(con, args)
        return _status(con)
    finally:
        con.close()


def _sync(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> int:
    try:
        summaries = nba_sync.sync(
            con,
            raw_root=config.raw_dir(),
            first=args.first,
            last=args.last,
            refresh=args.refresh,
        )
    except (sources.DownloadError, nba_sync.SyncError, load.DataIntegrityError,
            ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Loaded {len(summaries)} season(s) into {config.db_path()}")
    return 0


def _status(con: duckdb.DuckDBPyConnection) -> int:
    rows = con.execute(
        """
        SELECT
            l.season,
            coalesce(g.games, 0),
            coalesce(g.final, 0),
            coalesce(g.scheduled, 0),
            coalesce(p.played, 0),
            coalesce(p.dnp, 0),
            coalesce(i.reports, 0),
            strftime(l.loaded_at_utc, '%Y-%m-%d %H:%M')
        FROM season_loads l
        LEFT JOIN (
            SELECT season, count(*) AS games,
                   count(*) FILTER (WHERE status = 'final') AS final,
                   count(*) FILTER (WHERE status = 'scheduled') AS scheduled
            FROM games GROUP BY season
        ) g USING (season)
        LEFT JOIN (
            SELECT season,
                   count(*) FILTER (WHERE NOT did_not_play) AS played,
                   count(*) FILTER (WHERE did_not_play) AS dnp
            FROM player_game_stats GROUP BY season
        ) p USING (season)
        LEFT JOIN (
            SELECT season, count(DISTINCT report_time_utc) AS reports
            FROM injury_report_rows GROUP BY season
        ) i USING (season)
        ORDER BY l.season
        """
    ).fetchall()

    print(f"Database: {config.db_path()}")
    if not rows:
        first = seasons.FIRST_INJURY_SEASON
        print(f"Nothing is loaded yet. Start with: better nba sync --from {first}")
        return 0

    header = (
        "season", "games", "final", "to come", "played", "dnp", "inj. reports", "loaded (UTC)"
    )
    table = [header] + [
        (seasons.label(season), *(f"{n:,}" for n in counts), loaded)
        for season, *counts, loaded in rows
    ]
    widths = [max(len(str(row[i])) for row in table) for i in range(len(header))]
    for row in table:
        cells = [str(row[0]).ljust(widths[0])]
        cells += [str(cell).rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)]
        print("  ".join(cells))

    last_final, first_unplayed, last_report = con.execute(
        """
        SELECT
            (SELECT max(game_date) FROM games WHERE status = 'final'),
            (SELECT strftime(min(tipoff_utc), '%Y-%m-%d %H:%M') FROM games
              WHERE status = 'scheduled'),
            (SELECT strftime(max(report_time_utc), '%Y-%m-%d %H:%M') FROM injury_report_rows)
        """
    ).fetchone()

    def shown(value: object, suffix: str = "") -> str:
        return "none" if value is None else f"{value}{suffix}"

    # The source files are rebuilt once a day, so the first game not yet final can
    # be a few hours in the past: that gap is how stale the data is.
    print(f"Latest final game:         {shown(last_final)}")
    print(f"First game not yet final:  {shown(first_unplayed, ' UTC')}")
    print(f"Latest injury report:      {shown(last_report, ' UTC')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
