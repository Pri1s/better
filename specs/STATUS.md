# Status

Last updated: 2026-11-13.

## Resume here

**Spec:** [03-mapping-grading.md](03-mapping-grading.md), task **T3.1**.

**State of the tree:** `main` holds everything finished. Tests pass (`uv run pytest`:
121). Work in progress, if any, is on a `feat/...` branch named in the log below.

**Next three steps:**

1. Spec 03: map the 80,891 imported lines to players, games and stats, and grade them.
2. Spec 02, T2.6 and T2.7: the PrizePicks and Odds API adapters (neither blocks the
   model or the backtest, which run on the mirror's lines).
3. Spec 04: the projection model.

**Local data** (not in git; rebuild with `better nba sync --from 2019` and
`better lines snapshot`): nine seasons of box scores, 306 line snapshots.

## Board

| # | Spec | Status | Notes |
|---|---|---|---|
| 01 | [NBA data](01-nba-data.md) | verified | 2018-19 to 2026-27 loaded |
| 02 | [Line recorder](02-line-recorder.md) | in progress | recorder and mirror import verified; two adapters to write |
| 03 | [Mapping and grading](03-mapping-grading.md) | not started | views exist |
| 04 | [Projection model](04-projection-model.md) | not started | |
| 05 | [Backtest](05-backtest.md) | not started | |
| 06 | [Sportsbook comparison](06-market-comparison.md) | not started | optional; needs a key to run live |
| 07 | [Entry builder](07-entry-builder.md) | not started | |
| 08 | [Operations](08-operations.md) | not started | |

Build order: 02, 03, 04, 05, 07, 06, 08. The entry builder comes before the sportsbook
comparison because it needs nothing the user has not already got.

## Waiting on the user

None of these block the build. Each is one setting or one command when decided.

| Decision | What it unlocks | Where |
|---|---|---|
| Switch on direct PrizePicks access (`BETTER_PRIZEPICKS_DIRECT=yes`)? It breaks their terms; the risk is to the user's account. | Full board several times a day, with goblins, demons, combos and fantasy score. | Spec 02, decision D2 |
| Buy an Odds API plan and put `ODDS_API_KEY` in `.env`? | A sanctioned PrizePicks feed, sportsbook comparison, and three seasons of sportsbook history if the larger plan is bought. | Specs 02 and 06 |
| Install the daily schedule? | Snapshots and predictions without remembering to run them. | Spec 08 |
| Open the holdout? | An unbiased read on the 2026-27 season, once and only once per model. | Spec 05, decision D6 |

## Log

Newest first. One line per finished task or notable event.

- 2026-11-13: Line recorder merged: raw snapshot files, loader, mirror importer,
  `better lines snapshot|rebuild|status`. Real import: 306 snapshots, 80,891 lines.
- 2026-11-13: Specs written for every part. Work now goes through small pull requests
  into `main` (decision D9).
- 2026-11-13: Stat catalogue and lines schema committed (`20d159b`).
- 2026-11-13: Sources researched: mirror history surveyed (306 commits, about 80,000
  standard lines), The Odds API endpoints confirmed from its documentation.
- 2026-11-12: NBA data layer committed (`fb1bb0f`).
