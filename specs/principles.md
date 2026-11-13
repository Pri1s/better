# Principles

Rules for every part of the project. They exist because breaking any of them produces
results that look fine and are wrong, or exposes the user to a risk they did not choose.

## P1. Sources

- **P1.1** Verify that a source exists and still works before building on it. Record the
  date it was checked. Say plainly when nothing good exists.
- **P1.2** Flag every unofficial endpoint and every terms-of-service risk in the README,
  next to the feature that uses it.
- **P1.3** NBA.com is not used, directly or through `nba_api`. Its terms of use
  (section 9) forbid using its statistics in connection with gambling or fantasy games.
- **P1.4** PrizePicks' own endpoints are called only when the user has switched that
  source on. Its terms (section 16(l)) forbid automated access, and the risk falls on the
  user's account, so the choice is theirs. The code ships with the source off.
- **P1.5** A client identifies itself honestly. No browser User-Agent, no borrowed
  Referer or Origin headers, no proxy rotation, no CAPTCHA or bot-challenge handling. If
  a provider refuses a request, the tool reports that and stops.
- **P1.6** Paid services are used only with a key the user supplies. Nothing in the tool
  buys anything or creates an account. A command that spends metered credits says how
  many before it starts and refuses to exceed a stated ceiling.
- **P1.7** Republished data (sportsdataverse, the PrizePicks mirror) is downloaded as
  published files. The README states who collected it, how, and under what licence.

## P2. Raw first

- **P2.1** Every provider response is saved untouched, with the time it was fetched,
  before anything parses it.
- **P2.2** Database tables built from those files are an index, not the record. Any of
  them can be dropped and rebuilt from `data/raw/` without the network.
- **P2.3** A parser never guesses silently. A row it cannot read is counted by reason and
  reported; the raw file still has it.

## P3. No lookahead

- **P3.1** Every prediction has a cutoff time. It may use only what was knowable before
  that time: games finished earlier, injury reports published earlier, lines fetched
  earlier.
- **P3.2** Features are built by one forward pass that emits a row's features before it
  reads that row's outcome. Training and live prediction use the same code path.
- **P3.3** A test proves it: changing any outcome on or after a date must leave every
  feature and prediction for that date unchanged.
- **P3.4** Models, dispersion fits and calibration maps used for a date are fitted only on
  earlier dates.

## P4. One definition

- **P4.1** A stat is defined once, in `better.stats`. Grading, modelling and entry
  building all read that definition.
- **P4.2** Payout multipliers and lineup rules live in one configuration file with the
  date and source of each value. No multiplier appears in code.
- **P4.3** `game_id`, `team_id` and `player_id` are ESPN's ids everywhere.
- **P4.4** Times are UTC without a zone in `*_utc` columns. `game_date` is the US Eastern
  calendar date.

## P5. Honest evaluation

- **P5.1** Every hit rate is reported with its sample size and an interval. Picks from
  the same night are not independent, so intervals resample whole nights.
- **P5.2** A holdout period is set before any backtest is run. Reading it is a deliberate,
  logged act, and nothing is tuned afterwards against it.
- **P5.3** Every result is shown beside the break-even rate and beside a naive baseline.
- **P5.4** A null result is a result. When the numbers do not show an edge, the report
  and the README say so in plain words.

## P6. Tests

- **P6.1** Tests never touch the network. Clients take an injectable session or fetch
  function; fixtures are small files shaped like the real responses, quirks included.
- **P6.2** `uv run pytest` and `uv run ruff check .` pass before every commit.
- **P6.3** A number that came from real data and matters (a match rate, a calibration
  slope) is recorded in the spec's Results, with the date and the command that produced it.

## P7. Scope

- **P7.1** The tool analyses. It never places an entry, signs in to PrizePicks or a
  sportsbook, or moves money.
- **P7.2** Outputs are probabilities, expected values and their uncertainty. Deciding
  whether and how much to play is the user's.
- **P7.3** Secrets come from the environment or `.env` (ignored by git). They are never
  written to raw files, logs, error messages or the database.

## P8. Code

- **P8.1** Python 3.13, `uv`, DuckDB for data, numpy and scipy for numerics,
  scikit-learn for boosted trees. A new dependency needs a reason in decisions.md.
- **P8.2** Data work is SQL where SQL is clear; numeric work is numpy. No pandas.
- **P8.3** Modules are small and say in their docstring what they are for. Comments
  explain why, not what.
- **P8.4** Derived tables carry enough to trace a number back: the snapshot a line came
  from, the model version and training cutoff a prediction came from.
