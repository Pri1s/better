# 06. Sportsbook comparison (optional)

**Status:** not started. Needs a paid Odds API key to run on real data; the code and
tests do not.

## Purpose

Turn sportsbook prices into a vig-free probability at the PrizePicks line. Sportsbooks
are sharper than a home-built model, so this is both the toughest benchmark for the
model and a signal in its own right.

## Requirements

- **R6.1** Given the two prices of an over/under market, THE SYSTEM SHALL return fair
  probabilities that sum to 1, by any of four methods: multiplicative, additive, power,
  Shin.
- **R6.2** WHEN the book's line differs from the PrizePicks line, THE SYSTEM SHALL
  convert the fair probability to the PrizePicks line using the stat's distribution
  family and the model's spread for that player, and SHALL mark the result as shifted.
- **R6.3** WHEN several books quote the same player and stat, THE SYSTEM SHALL give one
  consensus probability (the median) and the number of books behind it.
- **R6.4** A book's line SHALL only be compared with a PrizePicks line fetched within a
  stated window of it (default 30 minutes).
- **R6.5** `better market estimate` SHALL compute, without calling the API, the credits
  a live sweep or a historical backfill would cost, from the schedule in the database.
- **R6.6** WHEN sportsbook lines exist for backtest picks, the backtest report SHALL
  show the market's hit rate and calibration beside the model's.

## Design

### Vig removal (`src/better/market/devig.py`)

For implied probabilities `q_over = 1/price_over`, `q_under = 1/price_under`:

- **Multiplicative:** `p_i = q_i / (q_over + q_under)`.
- **Additive:** `p_i = q_i - (q_over + q_under - 1) / 2`.
- **Power:** find `k` with `q_over^k + q_under^k = 1`; `p_i = q_i^k`.
- **Shin:** for two outcomes, `z` solved in closed form;
  `p_i = (sqrt(z^2 + 4 (1 - z) q_i^2 / S) - z) / (2 (1 - z))`, `S = q_over + q_under`.

Default: power. Tests pin each to hand-computed values and to the property that a
market with no vig is returned unchanged.

### Line shift (`src/better/market/shift.py`)

Find the mean `m` such that the stat's distribution with the player's fitted spread
gives the book's fair `p_over` at the book's line (one-dimensional root). Evaluate that
distribution at the PrizePicks line. This assumes the book and the model agree on the
shape and differ only in the mean, which is a fair reading of a half-point move.

### Consensus (`src/better/market/view.py`)

`market_view(con, window_minutes=30)` returns, per PrizePicks line: `market_p_over`,
`n_books`, `max_shift` (largest line difference bridged). Written to `market_probs`.

### Cost estimate (`src/better/market/cost.py`)

- Live sweep: events on the date × markets × regions.
- Historical: 10 × markets × regions per event per snapshot, plus 1 per events listing.

### Commands

```
better lines snapshot --source odds-api --region us       record sportsbook props
better market view [--date DATE]                          PrizePicks lines beside the market
better market estimate --from DATE --to DATE [--snapshots N]
```

## Tasks

- [ ] T6.1 `devig.py` with pinned values and properties. R6.1.
- [ ] T6.2 `shift.py`: round trip (shift there and back), monotone in the line. R6.2.
- [ ] T6.3 `view.py` and `market_probs` on fixture lines from two books. R6.3, R6.4.
- [ ] T6.4 `cost.py` and `better market estimate`. R6.5.
- [ ] T6.5 Market columns in the backtest report. R6.6.

## Results

Not yet run on real data (no key).

## Limits and open questions

- DFS prices in The Odds API are indicative, not real odds. Only sportsbook rows
  (`regions=us`) are de-vigged.
- Whether to pay for the API is the user's decision; see STATUS.md.
