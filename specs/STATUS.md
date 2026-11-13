# Status

Last updated: 2026-11-13.

## Resume here

**Spec:** [04-projection-model.md](04-projection-model.md), task **T4.5**.

**State of the tree:** Model distribution families, the forward feature pass, injury
weights, dispersion and calibration are built and tested (T4.1–T4.4, T4.6). The
walk-forward pipeline and CLI are being completed next.

**Next three steps:**

1. Finish the walk-forward pipeline, accuracy report and live prediction commands
   (T4.5, T4.7–T4.10), using only outcomes before 2026-10-01.
2. Backtest the development window with the holdout sealed.
3. Entry builder, adapters, sportsbook comparison and daily operations.

**Local data** (not in git; rebuild with `better nba sync --from 2019` and
`better lines snapshot`): nine seasons of box scores, 306 line snapshots, all mapped.

**The number to beat:** on standard lines the under wins 51.8% of decided picks with
no model at all (spec 03, Results).

## Board

| # | Spec | Status | Notes |
|---|---|---|---|
| 01 | [NBA data](01-nba-data.md) | verified | 2018-19 to 2026-27 loaded |
| 02 | [Line recorder](02-line-recorder.md) | in progress | recorder and mirror import verified; two adapters to write |
| 03 | [Mapping and grading](03-mapping-grading.md) | verified | 96.7% of lines linked; links audited |
| 04 | [Projection model](04-projection-model.md) | in progress | features and distribution/calibration maths built |
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

- 2026-11-13: Resumed interrupted model branch. Completed forward-pass features,
  empirical injury weights and leakage tests; added spread fitting and calibration.
  Fantasy score now supports negative values (D10).

- 2026-11-13: Mapping and grading merged. 78,211 of 80,891 lines linked; 52,172 unique
  graded standard picks over 186 nights. Unders win 51.8% untouched.
- 2026-11-13: Line recorder merged: raw snapshot files, loader, mirror importer,
  `better lines snapshot|rebuild|status`. Real import: 306 snapshots, 80,891 lines.
- 2026-11-13: Specs written for every part. Work now goes through small pull requests
  into `main` (decision D9).
- 2026-11-13: Stat catalogue and lines schema committed (`20d159b`).
- 2026-11-13: Sources researched: mirror history surveyed (306 commits, about 80,000
  standard lines), The Odds API endpoints confirmed from its documentation.
- 2026-11-12: NBA data layer committed (`fb1bb0f`).
