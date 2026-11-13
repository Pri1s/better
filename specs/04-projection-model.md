# 04. Projection model

**Status:** not started.

## Purpose

For any player, game and stat, give a full predictive distribution as of a cutoff, so
that the probability of going over any line can be read off it. Accuracy is checked
against box scores alone; whether the probabilities beat real lines is spec 05's job.

## Requirements

- **R4.1** Features for a game SHALL depend only on games dated before it and on injury
  reports published at or before the cutoff (12:00 UTC on the game date by default).
- **R4.2** WHEN any outcome on or after a date is altered, every feature and prediction
  for that date SHALL be unchanged. (The leak test.)
- **R4.3** Training and live prediction SHALL use the same feature code path. A game not
  yet played is simply a row whose outcome is never read.
- **R4.4** Every prediction SHALL be made by models fitted only on earlier dates, and
  SHALL record `trained_through`, the last game date its models saw.
- **R4.5** Minutes fed to a stat model as a feature SHALL themselves be out-of-sample
  predictions, in training as well as in prediction.
- **R4.6** For each stat the model SHALL output a mean and a spread, and from them
  `p_over`, `p_under`, `p_push` for any line. The three sum to 1. `p_push` is zero
  unless the line is a value the stat can take.
- **R4.7** Probabilities SHALL be calibrated on out-of-sample predictions from earlier
  blocks only.
- **R4.8** `better model evaluate` SHALL report, by stat and season, with no lines
  involved: mean absolute error against two baselines, the log score, coverage of the
  central 50% and 80% intervals, and a calibration table of predicted against observed
  frequency.
- **R4.9** The fitted state SHALL be stored with a version, so that a prediction can be
  traced to the code and data that made it and re-made.
- **R4.10** A player with no history SHALL still get a prediction (wide, and built from
  league-level context), never an error.

## Design

### Overview

```
box scores, schedule, injury reports
        |  one forward pass by date
        v
   feature rows  (one per player per game, as of the cutoff)
        |
        +--> minutes model  ---> proj_minutes (out of sample)
        |                              |
        +------------------------------+--> stat mean models (one per stat)
                                               |
                         dispersion fit  <-----+   (per stat, on earlier blocks)
                         calibration map <-----+   (per stat, on earlier blocks)
                                               v
                                         predictions table
```

### Features (`src/better/model/features.py`)

One pass over game dates in order. For each date: **emit** a feature row for every
player in that date's games from the current state, **then update** the state with that
date's box scores. Nothing from a date can reach its own rows, by construction (R4.1 to
R4.3). State is held in numpy arrays indexed by player and by team.

Player state (updated only by games he played, unless noted):

- Exponentially weighted sums of each box-score component and of minutes, at two
  half-lives (6 and 30 games), giving per-minute rates.
- Exponentially weighted minutes, minutes squared (for volatility) and starts.
- Season-to-date totals; previous-season per-minute rates.
- Last game: date, minutes, started.
- Share of his team's recent games he played (updated by DNP rows too).
- Exponentially weighted "minutes his teammates were missing" in the games he played,
  the baseline his current situation is compared against.

Team state:

- Exponentially weighted possessions per game (`FGA + 0.44 FTA - OREB + TOV`), points
  scored and allowed per 100 possessions.
- Exponentially weighted totals allowed to opponents for each component, as a ratio to
  the league average at that time.
- The same allowed totals split by the opponent's position group (G, F, C) for points,
  rebounds, assists and threes.

Injury features, from the report as of the cutoff. Each listed status carries a weight,
the historical share of players with that status who then did not play, measured from
this database rather than assumed:

- `own_status`: the player's own weight (0 when not listed).
- `team_min_out`, `team_usage_out`, `team_ast_out`, `team_reb_out`: sums over listed
  teammates of weight times their recent per-game minutes, usage, assists, rebounds.
- `pos_min_out`: the same for teammates in his position group.
- `min_out_delta`, `usage_out_delta`, `pos_min_out_delta`: each minus the player's
  recent baseline. A star who has been out for a month is already in the baseline; one
  who was scratched today is not.
- `opp_min_out`: the opponent's vacated minutes.

Context: home, team rest days, back-to-back, games in the prior six days, team game
number, playoffs, the gap between the two teams' net ratings.

Missing values stay missing (NaN); the trees handle them.

### Models (`src/better/model/`)

| Module | Role |
|---|---|
| `features.py` | The forward pass. `build(con, through=None, extra_rows=None) -> FeatureTable`. |
| `blocks.py` | Splits dates into walk-forward blocks. |
| `minutes.py` | Minutes mean model. |
| `statmodel.py` | Per-stat mean models. |
| `dist.py` | Distribution families: `p_over`, `cdf`, `pit`, log likelihood. |
| `dispersion.py` | Spread as a function of a few features, by maximum likelihood. |
| `calibrate.py` | Monotone map from model CDF to calibrated CDF. |
| `pipeline.py` | Runs blocks in order; writes `predictions`; saves fitted state. |
| `evaluate.py` | The line-free accuracy report. |

**Minutes.** `HistGradientBoostingRegressor`, squared loss, on rows where the player
played. Target: minutes.

**Stat means.** One `HistGradientBoostingRegressor` per stat. Poisson loss for counts,
squared loss for fantasy score. Inputs: the shared feature matrix, `proj_minutes`, and
two stat-specific columns: recent per-minute rate times `proj_minutes` at each
half-life (computed from the stat's weights in `better.stats`). Flags (double-double)
use `HistGradientBoostingClassifier`.

**Baselines** kept for comparison: (a) season-to-date average; (b) long-half-life
per-minute rate times weighted recent minutes.

**Spread.**

- Counts: negative binomial, variance `mean + alpha * mean^2`, with
  `log(alpha) = g0 + g1 * log(mean) + g2 * minutes_cv + g3 * started_share`.
- Fantasy score: gamma with the same form for `log(1 / shape)`.
- Fitted per stat by maximum likelihood (L-BFGS) on out-of-sample means.

**Calibration.** For each stat, compute the randomised probability integral transform of
out-of-sample outcomes under the fitted distribution. If the distribution were right
these would be uniform. The calibration map is the monotone function that makes them
uniform (isotonic fit of their empirical CDF, stored as knots). Calibrated
`CDF*(x) = G(CDF(x))`. This needs no lines and applies to any line.

**Probabilities.** For a count stat and line `L`: `p_over = 1 - CDF*(floor(L))`,
`p_push = CDF*(L) - CDF*(L - 1)` when `L` is a whole number, else 0.

### Walk-forward blocks

- Feature state warms up on 2018-19.
- Minutes model: first predicts 2020-21, trained on everything before.
- Stat models: train on rows that have out-of-sample minutes; first predict 2021-22.
- Dispersion and calibration: fitted on out-of-sample stat predictions; first applied
  to 2022-23. Evaluation is reported from 2022-23.
- Blocks are three per season up to 2024-25 and monthly from 2025-26, where real lines
  exist. Each block's models see only earlier blocks.

### Storage

```sql
CREATE TABLE IF NOT EXISTS predictions (
    model_version   VARCHAR  NOT NULL,
    game_id         INTEGER  NOT NULL,
    player_id       INTEGER  NOT NULL,
    stat            VARCHAR  NOT NULL,
    game_date       DATE     NOT NULL,
    cutoff_utc      TIMESTAMP NOT NULL,
    trained_through DATE     NOT NULL,
    block           VARCHAR  NOT NULL,
    family          VARCHAR  NOT NULL,   -- negbin | gamma | bernoulli
    proj_minutes    DOUBLE,
    mean            DOUBLE   NOT NULL,
    dispersion      DOUBLE,
    baseline_mean   DOUBLE
);
```

Calibration knots per (model_version, block, stat) go in `calibration_maps`. Fitted
models for the latest block are saved with joblib under `data/models/<version>/`.

### Commands

```
better model fit [--through DATE]     walk forward, write predictions for every block
better model evaluate [--from SEASON] the line-free accuracy report
better model predict [--date DATE]    predictions for upcoming games on the board
```

## Tasks

- [ ] T4.1 `dist.py` with tests against scipy and by simulation. R4.6.
- [ ] T4.2 `features.py` forward pass on the fixture season; hand-checked values. R4.1,
      R4.3, R4.10.
- [ ] T4.3 The leak test: alter outcomes from a date on, features before unchanged. R4.2.
- [ ] T4.4 Status weights measured from the database; injury features. R4.1.
- [ ] T4.5 `blocks.py`, `minutes.py`, `statmodel.py`; pipeline on a small synthetic
      league where the true rates are known. R4.4, R4.5.
- [ ] T4.6 `dispersion.py`, `calibrate.py` recover known parameters from simulated data.
      R4.7.
- [ ] T4.7 `pipeline.py` writes `predictions`; `better model fit`. R4.9.
- [ ] T4.8 `evaluate.py` and `better model evaluate`. R4.8.
- [ ] T4.9 Run on the real database; record accuracy and calibration below; fix what the
      calibration table shows.
- [ ] T4.10 `better model predict` for upcoming games.

## Results

Not yet run.

## Limits and open questions

- Minutes are whole numbers, which blurs per-minute rates for low-minute players.
- No play-by-play: no on/off splits, no lineup data. Role change is inferred from the
  injury report and from who was missing in past games.
- The morning cutoff means a late scratch is not seen (decision D5).
- Whether boosted means beat the per-minute baseline by enough to matter is an empirical
  question T4.9 answers.
