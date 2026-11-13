# 08. Operations

**Status:** not started.

## Purpose

Make the parts usable day to day: one command for the morning routine, a schedule the
user can install, a way to tell the tool about late injury news, and documentation that
matches what exists.

## Requirements

- **R8.1** `better daily` SHALL run, in order: NBA sync, line snapshot from every source
  that is switched on, mapping, prediction for upcoming games, and print a summary. A
  failure in one step SHALL be reported and SHALL NOT stop the steps that do not depend
  on it.
- **R8.2** `better status` SHALL show, in one screen: data freshness, snapshots by
  source and day, link rate, graded results, the model version in use, and anything
  that needs attention.
- **R8.3** THE SYSTEM SHALL read settings from the environment and from `.env` in the
  project root, the environment winning. Unknown keys are ignored.
- **R8.4** WHEN the user records an injury override for a player and date, predictions
  made after that SHALL treat it as the player's status, ahead of the stored report.
- **R8.5** The project SHALL ship a schedule the user can install with one command and
  remove with one command. Nothing installs it automatically.
- **R8.6** The README SHALL describe every command, every source with its terms and
  risks, and the latest backtest result with its caveats.

## Design

### Settings (`src/better/config.py`)

| Key | Meaning | Default |
|---|---|---|
| `BETTER_DATA_DIR` | where data lives | `<project>/data` |
| `ODDS_API_KEY` | The Odds API key | unset: that source is off |
| `BETTER_PRIZEPICKS_DIRECT` | `yes` to allow calling PrizePicks | unset: off |
| `BETTER_MIRROR` | `no` to skip the mirror | on |

### Injury overrides

```sql
CREATE TABLE IF NOT EXISTS injury_overrides (
    game_date  DATE      NOT NULL,
    player_id  INTEGER   NOT NULL,
    status     VARCHAR   NOT NULL,   -- Out | Doubtful | Questionable | Probable | Available
    noted_at_utc TIMESTAMP NOT NULL,
    note       VARCHAR
);
```

```
better nba injury "Player Name" out [--date DATE]
better nba injury --list
```

The feature builder reads overrides for dates on or after today and lets them replace
the report's status for that player. They never affect past dates, so a backtest cannot
be changed by them.

### Schedule

`better schedule print` writes a launchd property list (macOS) that runs `better daily`
each morning and a line-snapshot command at set times. `better schedule install` copies
it to `~/Library/LaunchAgents` and loads it; `better schedule remove` undoes that. Both
are run by the user.

## Tasks

- [ ] T8.1 `.env` reader and settings table. R8.3.
- [ ] T8.2 `better status`. R8.2.
- [ ] T8.3 Injury overrides: table, command, feature hook, test that past dates ignore
      them. R8.4.
- [ ] T8.4 `better daily`. R8.1.
- [ ] T8.5 Schedule commands. R8.5.
- [ ] T8.6 README rewrite. R8.6.

## Results

Not yet run.

## Limits and open questions

- Installing a schedule changes the user's machine, so it stays a command they run.
- A licensed same-day injury feed (BALLDONTLIE, paid) would replace manual overrides.
