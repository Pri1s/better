# Decisions

Choices that were not obvious. Newest last. Each says what was decided, why, and what
was turned down, so that nobody reverses one without knowing what it was protecting.

## D1. Line history comes from the mirror's git history (2026-11-13)

**Decision.** Historical PrizePicks lines are imported from
`enkday/prizepicks-data-mirror`, a public GitHub repository that commits one scrape of
the PrizePicks board per day. The importer clones it and reads every past version of
`data/prizepicks-nba.json`.

**Why.** It is the only free history of real PrizePicks lines found: 306 commits, about
80,000 standard NBA lines, 2025-12-06 onward. Without it the backtest has nothing to run
on until months of own snapshots accumulate or a purchase is made.

**Costs accepted.** No licence (personal use only, never redistribute). One snapshot a
day near 12:05 UTC, so no line movement. Standard single-stat lines only after the first
days: no combos, no fantasy score, no goblins or demons. It is collected upstream by an
unofficial scraper of PrizePicks' API; PrizePicks' terms bind whoever collects it, in the
same way ESPN's terms bind sportsdataverse. The repository could vanish, so everything
read from it is kept in `data/raw/` (which is never committed).

**Turned down.** Buying The Odds API history (the user's call, see STATUS.md). Waiting
for own snapshots only.

## D2. The PrizePicks adapter ships switched off (2026-11-13)

**Decision.** `better.lines.prizepicks` can fetch the partner endpoint, but only when
`BETTER_PRIZEPICKS_DIRECT=yes` is set. It was not run during the autonomous build.

**Why.** PrizePicks' terms forbid automated access and the account at risk is the
user's. The user asked for such risks to be flagged; flagging and then proceeding anyway
would make the flag meaningless. The parser is tested against a fixture built from real
`included` objects (captured in the mirror's master file) and from the field names the
mirror's scraper reads successfully every day.

**Turned down.** Running it once "to verify". One request is still the user's decision.

## D3. Boosted trees from scikit-learn, not NGBoost or LightGBMLSS (2026-11-13)

**Decision.** Means come from `HistGradientBoostingRegressor` with a Poisson loss.
Spread comes from a small dispersion regression fitted by maximum likelihood, then a
calibration map fitted on out-of-sample predictions.

**Why.** scikit-learn wheels need nothing else installed (LightGBM on macOS wants
`libomp`; LightGBMLSS pulls in PyTorch). It fits 200,000 rows by 60 features in about
1.5 seconds, which makes monthly walk-forward refits cheap. Separating mean, spread and
calibration keeps each piece inspectable. NGBoost fits both at once but is far slower at
this size.

**Revisit if.** The calibration check in spec 04 shows spread depends on features the
dispersion regression cannot express.

## D4. Vig removal is written here, not imported from penaltyblog (2026-11-13)

**Decision.** `better.market.devig` implements the multiplicative, additive, power and
Shin methods directly.

**Why.** Each is a few lines with a closed form or a one-dimensional root. penaltyblog
is a football modelling package; depending on it for four formulas adds a large surface
for no gain. Tests pin each method to hand-computed values.

## D5. The daily cutoff is 12:00 UTC on the game date (2026-11-13)

**Decision.** Every game's features are built from what was known at 12:00 UTC on its
US Eastern game date: box scores from earlier dates, and the latest injury report
published at or before that moment.

**Why.** It is just before the mirror's daily scrape (about 12:05 UTC), so a model
trained at this cutoff knows no more than the lines it is tested against. It also
matches how the tool is used: the source files are rebuilt each morning.

**Cost.** News after the morning (a late scratch) is not seen. Live use can pass a later
cutoff; the training cutoff stays fixed so the two stay comparable.

## D6. The holdout is everything from 2026-10-01 (2026-11-13)

**Decision.** Lines for games on or after 2026-10-01 (the 2026-27 season) are held out.
Development uses 2025-12-06 to 2026-06-30.

**Why.** A season boundary is a natural break, the held-out set is the one the user will
actually play in, and it grows every day new snapshots are recorded.

**Rule.** `better backtest run` refuses holdout dates without `--holdout`, and each use
of that flag is written to `holdout_log`. Nothing is tuned after reading it.

## D7. numpy arrays move between DuckDB and Python, no pandas (2026-11-13)

**Decision.** Query results come back with `fetchnumpy()`; arrays go back in as a dict
that DuckDB scans by name.

**Why.** Verified on DuckDB 2.0: a dict of numpy arrays is scanned directly and three
million rows insert in 20 ms. That removes pandas and pyarrow as dependencies.

## D8. Line links are columns on prop_lines (2026-11-13)

**Decision.** `game_id`, `team_id`, `player_id`, `stat` and `link_status` are columns of
`prop_lines`, filled by a mapping step that can be run again at any time.

**Why.** Everything downstream joins through these ids, so they should be one plain
column away. Mapping improves as aliases are added and as rookies appear in box scores;
re-running it over all rows takes seconds.

**Turned down.** Views that resolve links at query time (slow to audit, no record of how
a match was made). A separate link table (an extra join everywhere).

## D9. Small pull requests into main, merged often (2026-11-13)

**Decision.** Each group of tasks gets its own branch (`feat/...`, `fix/...`,
`docs/...`), is pushed, opened as a pull request against `main` and merged with a merge
commit as soon as its tests pass. STATUS.md is true in every pull request. Commit
messages and pull request descriptions carry no AI attribution.

**Why.** The project owner asked for this workflow. Small merged pull requests keep
`main` resumable at any moment, and each description is a readable record of one step.

**Note.** The repository is public, so nothing under `data/`, no `.env` and no API key
is ever committed. The two earliest commits predate the attribution rule and were
already published; they are left as they are rather than rewriting public history.

## D10. Fantasy score needs support below zero (2026-11-13)

**Decision.** Fantasy score uses a normal predictive family with fitted variance and
a CDF calibration map, evaluated on a grid of tenths. Counts retain the negative
binomial family, and flags retain Bernoulli.

**Why.** The stat definition subtracts turnovers. A player can finish with a negative
fantasy score, which a gamma distribution cannot represent. Rounding boundaries
determine probability mass and pushes; a line between grid points cannot push.

**Cost.** The normal family can place mass on implausibly low scores. Earlier-block
calibration and the line-free log score expose that limitation.
