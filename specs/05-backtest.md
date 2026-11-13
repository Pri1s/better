# 05. Backtest

**Status:** not started.

## Purpose

Answer one question honestly: had these probabilities been used to pick sides on real
PrizePicks lines, how often would the picks have won, and is that more than the
break-even rate by more than chance?

## Requirements

- **R5.1** A pick SHALL pair a line with a prediction whose cutoff is not later than the
  moment the line was fetched, and whose models were fitted before the game date.
- **R5.2** Only lines fetched at or after the game's cutoff and before its tip-off SHALL
  be used. WHEN several snapshots hold the same player, game, stat and odds type, the
  earliest SHALL be used (closest to what the model knew).
- **R5.3** Games on or after the holdout start SHALL be excluded unless `--holdout` is
  given. Every use of `--holdout` SHALL be written to `holdout_log` with the time, the
  model version and the date range read.
- **R5.4** Each pick SHALL record: side taken, probability given to that side, the
  model's mean, the line, the result (`win`, `loss`, `push`, `void`), and the snapshot
  and model version it came from.
- **R5.5** The report SHALL give hit rate (wins over wins plus losses) overall and by:
  size of disagreement (probability bucket), stat, side, odds type and month.
- **R5.6** Every hit rate SHALL carry its number of picks, number of distinct nights,
  and a 95% interval from resampling whole nights.
- **R5.7** The report SHALL show, beside the model: the share of overs among graded
  lines, the hit rate of always picking under, and each entry type's break-even rate
  taken from the payout configuration.
- **R5.8** The report SHALL include a calibration table: for each probability bucket,
  the mean probability given and the share that won.
- **R5.9** WHEN fewer than a stated minimum of picks fall in a bucket, the report SHALL
  mark it as too small to read rather than print a bare percentage.

## Design

### Pick selection (`src/better/backtest/picks.py`)

1. From `graded_lines`: `book = 'prizepicks'`, `link_status = 'linked'`, the requested
   odds types (standard by default), `status` not live.
2. Keep rows with `cutoff(game_date) <= fetched_at_utc < tipoff_utc`, where
   `cutoff(d) = d 12:00 UTC`. Take the earliest per (game, player, stat, odds type).
3. Join `predictions` on (game, player, stat) for the chosen model version, requiring
   `trained_through < game_date` (R5.1).
4. Compute `p_over`, `p_under`, `p_push` at the line. Side is the larger of over and
   under. Goblin and demon lines can only be played as over.
5. Result: `win` or `loss` against the side taken; `push` and `void` pass through.

Picks are written to `backtest_picks` under a `run_id`; the run's settings and headline
numbers go to `backtest_runs`.

### Report (`src/better/backtest/report.py`)

- **Disagreement buckets** on the probability of the chosen side:
  50-52, 52-54, 54-56, 56-58, 58-60, 60-65, 65+.
- **Night-level bootstrap.** Resample game dates with replacement (2,000 draws), recompute
  the hit rate, take the 2.5th and 97.5th percentiles. With `N` nights the report also
  states `N`, because that, not the number of picks, is the effective sample.
- **Break-evens** computed from `payouts.toml`: the per-pick win rate at which each entry
  type returns exactly its stake when picks are independent and equally likely to win.
- **Cumulative selection.** "Picks at 56% or better" as well as per-bucket rows, since a
  rule one would actually follow is a threshold.

### Holdout

`HOLDOUT_START = 2026-10-01` in `better.backtest.config` (decision D6). The development
window is 2025-12-06 to 2026-06-30.

### Commands

```
better backtest run [--from DATE] [--to DATE] [--odds-type standard] [--holdout]
better backtest report [--run ID]
```

## Tasks

- [ ] T5.1 Pick-selection tests on fixture lines and predictions: cutoff rule, earliest
      snapshot, trained-through rule, sides, goblin and demon over-only. R5.1, R5.2, R5.4.
- [ ] T5.2 Holdout gate and log. R5.3.
- [ ] T5.3 Report maths: bucket rates, night bootstrap on a constructed case with a
      known answer, break-evens against hand calculation. R5.5 to R5.9.
- [ ] T5.4 `better backtest run|report`.
- [ ] T5.5 Run on the development window; record the report below and in the README.

## Results

Not yet run.

## Limits and open questions

- The mirror scrapes once, early in the day. Lines move afterwards; this tests playing
  the early line with early information.
- Standard lines only until own snapshots hold enough goblins and demons.
- Telling a 56% hit rate from a 54% break-even needs roughly 2,500 independent picks.
  Same-night picks are not independent, which is why R5.6 counts nights.
