# 07. Entry builder

**Status:** not started.

## Purpose

Combine picks into PrizePicks entries and say, for each entry type, the chance of any
payout and the expected value, then rank entries from low to high risk.

## Requirements

- **R7.1** Payout multipliers and lineup rules SHALL be read from `payouts.toml`. Each
  value there carries its source and the date it was checked.
- **R7.2** For legs with independent outcomes, THE SYSTEM SHALL compute the exact
  distribution of outcomes, with each leg able to win, lose, tie or be voided.
- **R7.3** A voided leg (did not play) SHALL remove the pick and step the entry down one
  size. A tied leg SHALL step the entry down one size. The step-down table, including
  what a 3-pick Flex and a 2-pick Power become, SHALL come from `payouts.toml`.
- **R7.4** WHEN voids leave fewer than two picks, or leave every remaining pick on one
  team, the entry SHALL be refunded (returns its stake).
- **R7.5** WHEN two or more legs come from the same game, THE SYSTEM SHALL estimate the
  outcome distribution by simulation with correlated legs, and SHALL say that it did.
- **R7.6** For each entry THE SYSTEM SHALL report: expected value per unit staked,
  chance of any payout, chance of the top payout, and the standard deviation of the
  return.
- **R7.7** Entries SHALL obey PrizePicks' construction rules: 2 to 6 picks, one pick per
  player, picks from at least two teams.
- **R7.8** By default only standard lines are used: PrizePicks does not publish goblin,
  demon or same-game payout rates.
- **R7.9** Entries SHALL be ranked into tiers by chance of any payout, with the tier
  thresholds stated in the output.
- **R7.10** The output SHALL state the per-pick break-even for each entry type and SHALL
  carry the backtest's caution when the model has not shown an edge.

## Design

### Payout configuration (`src/better/entries/payouts.toml`)

```toml
checked = "2026-11-12"
source = "PrizePicks help centre, payouts page"

[power]            # all picks must hit
2 = [3.0]
3 = [6.0]
4 = [10.0]
5 = [20.0]
6 = [37.5]

[flex]             # multipliers for all correct, one miss, two misses
3 = [3.0, 1.0]
4 = [6.0, 1.5]
5 = [10.0, 2.0, 0.4]
6 = [25.0, 2.0, 0.4]

[step_down]        # what an entry becomes when one pick ties or is voided
power = { 6 = "power-5", 5 = "power-4", 4 = "power-3", 3 = "power-2", 2 = "single" }
flex  = { 6 = "flex-5", 5 = "flex-4", 4 = "flex-3", 3 = "power-2" }

[single]           # an entry reduced to one live pick
after_void = "refund"
after_tie_win = 1.5      # reported, not confirmed on an official page
after_tie_loss = 0.0
```

### Exact evaluation (`src/better/entries/ev.py`)

Each leg has probabilities `(win, loss, tie, void)` summing to 1. A dynamic programme
over legs tracks `(voids, ties, wins)`; the entry's payout for each end state is looked
up after applying the step-down table `voids + ties` times. Refund rules (R7.4) are
applied to void counts. Expected value, payout chance and variance follow from the
state distribution.

### Correlation (`src/better/entries/correlate.py`)

- **Estimating.** From out-of-sample predictions, turn each outcome into a normal score
  through its calibrated CDF. For pairs of players in the same game, estimate the
  correlation of scores by relation (teammates, opponents) and stat pair, shrunk toward
  zero by sample size. Stored in `leg_correlations`.
- **Using.** Gaussian copula: draw correlated normals, cut each at the leg's cumulative
  probabilities to get win, tie or loss (an under pick flips the sign), then score the
  entry. Voids are drawn independently. 20,000 draws by default with a fixed seed.

### Building (`src/better/entries/build.py`)

1. Candidate legs: today's standard PrizePicks lines with a prediction, side chosen by
   the larger probability, kept when that probability clears a floor (default 0.54).
2. Take the top `K` legs (default 14) by probability. Enumerate every legal entry of
   2 to 6 picks from them for Power and Flex.
3. Score all by exact evaluation assuming independence, keep the best per type and
   size, re-score any with same-game legs by simulation.
4. Tier by chance of any payout: low risk at 45% or more, medium 25 to 45%, high below
   25%. Report the top entries per tier with every R7.6 number.

### Commands

```
better entries build [--date DATE] [--min-prob 0.54] [--top 14]
better entries evaluate "<player> over 24.5 points" ...     score a hand-built entry
```

## Tasks

- [ ] T7.1 `payouts.toml` loader and break-even solver; tests against hand calculation.
      R7.1.
- [ ] T7.2 Exact evaluation: binomial special case, known tie and void cases, the
      3-pick Flex and 2-pick Power step-downs, refund rules. R7.2 to R7.4, R7.6.
- [ ] T7.3 Copula simulation: zero correlation matches the exact answer; positive
      correlation raises the all-hit chance. R7.5.
- [ ] T7.4 Correlation estimate from predictions; `leg_correlations`.
- [ ] T7.5 Builder: construction rules, tiers. R7.7 to R7.10.
- [ ] T7.6 `better entries build|evaluate`.

## Results

Not yet run.

## Limits and open questions

- The step-down for a 2-pick entry with a tie (1.5x on a win) comes from secondary
  sources. Confirm in the app before relying on it; it is one line in `payouts.toml`.
- Reboots are not modelled.
- Sizing a stake is out of scope (principle P7.2).
