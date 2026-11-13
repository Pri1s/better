# better

A personal research tool for PrizePicks NBA player props: record lines, project player
stats, backtest the projections against real lines, and build entries.

## Start here

This project is built spec-first. Before changing anything, read, in this order:

1. [specs/STATUS.md](specs/STATUS.md): what is built, what is in progress, and the exact
   task to resume from.
2. [specs/principles.md](specs/principles.md): rules every part of the project follows.
3. The spec named under "Resume here" in STATUS.md. Its first unchecked task is the next
   thing to do.

[specs/README.md](specs/README.md) explains how the specs are organised and the working
loop (spec, tests, code, status, pull request). [specs/decisions.md](specs/decisions.md)
records why things are the way they are; read it before reversing a choice.

## Commands

```bash
uv sync                  # install
uv run pytest            # tests: no network, a few seconds
uv run ruff check .      # lint
uv run better --help     # the command line
```

## Workflow

- One short-lived branch per group of tasks, cut from an up-to-date `main` and named
  `feat/<topic>`, `fix/<topic>` or `docs/<topic>`.
- Commit as tasks finish. `uv run pytest` and `uv run ruff check .` pass before each one.
- Push the branch, open a pull request into `main`, merge it with a merge commit, delete
  the branch and pull `main`. Do this often: one pull request per finished group of
  tasks, never one large one at the end.
- `specs/STATUS.md` is true in every pull request.
- Commit messages and pull request descriptions carry no AI attribution: no co-author
  trailers and no "generated with" footers.
- The repository is public. Nothing under `data/`, no `.env` and no API key is committed.

## Rules that are easy to break by accident

- Never call PrizePicks directly unless the user has switched that source on
  (`BETTER_PRIZEPICKS_DIRECT=yes` in `.env`). Never disguise the client or work around a
  block. See principles.md, "Sources".
- Never use NBA.com data (`nba_api`, `cdn.nba.com`): its terms forbid gambling use.
- Never read the backtest holdout to make a modelling choice. See specs/05-backtest.md.
- Never place an entry, log in to PrizePicks or move money. This tool only analyses.
- Never write an API key into a file under version control, a raw snapshot or a log.
