# 03. Mapping and grading

**Status:** not started. Views exist (`team_keys`, `roster_names`, `graded_lines`).

## Purpose

Tie every recorded line to a `game_id`, a `player_id` and a stat key, then say how it
turned out. Everything downstream joins through these links, so a wrong one corrupts
results without any error. This part prefers leaving a line unlinked to guessing.

## Requirements

- **R3.1** WHEN a line's stat name is in the catalogue for its source, THE SYSTEM SHALL
  set `stat`. Otherwise `link_status = 'no_stat'`.
- **R3.2** WHEN a line names the player's team (code or name) and a start time, THE
  SYSTEM SHALL find the game that team plays on that US Eastern date. WHEN a line names
  both teams instead (The Odds API), it SHALL find the game by home and away team on
  that date. No game found means `link_status = 'no_game'`.
- **R3.3** WHEN the game is found, THE SYSTEM SHALL match the player by `name_key`
  within the rosters of the two teams for that season, trying in order: the exact key,
  an alias, the key without a generational suffix. `player_match = 'team'`.
- **R3.4** WHEN no roster match exists, THE SYSTEM SHALL match on the exact key or alias
  across the whole league for that season, and accept it only if exactly one player has
  that key. `player_match = 'league'`.
- **R3.5** WHEN two or more players fit, THE SYSTEM SHALL leave the line unlinked with
  `link_status = 'ambiguous_player'`. It SHALL NOT pick one.
- **R3.6** WHEN a line is a combo, `link_status = 'combo'` and no player is linked.
- **R3.7** Mapping SHALL be repeatable: running it again over all lines gives the same
  links, and picks up new aliases and players who have since appeared in a box score.
- **R3.8** Mapping SHALL never change the number of rows in `prop_lines`.
- **R3.9** `better lines unmatched` SHALL list unlinked lines grouped by reason and
  name, with the number of lines affected and the closest roster names, so that an
  alias can be written.
- **R3.10** Aliases SHALL come from a file shipped with the package and a local file in
  the data directory, both reloaded on every run.
- **R3.11** `graded_lines.result` SHALL be `over`, `under` or `push` for a player who
  played in a final game; `void` when he did not play or the game was not played;
  `pending` before the game is final; `unlinked` otherwise.

## Design

### Mapping (`src/better/lines/mapping.py`)

`map_lines(con, snapshot_ids=None) -> MappingSummary`. One transaction:

1. Reload `player_aliases` from `lines/aliases.csv` (package) and
   `<data>/player_aliases.csv` (local). Columns: `alias,player`.
2. Clear the link columns of the rows being mapped.
3. **Stat.** Join `stat_names` on family (`prizepicks` when `book = 'prizepicks'` and the
   source is not `odds_api`; else `odds_api`). `player_fantasy_points` only counts when
   `book = 'prizepicks'`: other sites score fantasy points differently.
4. **Game.** `game_date = (start_time_utc AT TIME ZONE 'America/New_York')::DATE`.
   Team text resolves through `team_keys`; the game through `team_game_dates`.
5. **Player.** Candidate keys per line: `name_key(player_name)`, the alias target's key,
   and `name_key_no_suffix`. Join `roster_names` for the game's season and two teams.
   Count distinct `player_id`; exactly one links. Then the league-wide fallback.
6. **Status.** `linked` only when stat, game and player are all set.

Each step is an `UPDATE ... FROM (subquery with one row per line)`, so no step can
duplicate a row (R3.8). Steps record how many rows they resolved.

### Why the checks are strict

- A name alone is not enough across the league in general (fathers and sons, shared
  names), so the roster of the two teams in the game is the first filter.
- The suffix-free key is only tried inside those two rosters.
- The mirror and PrizePicks print NBA.com-style team codes (`GSW`, `NYK`, `NOP`), ESPN
  prints `GS`, `NY`, `NO`. `team_keys` holds every spelling of every team.

### Grading

`graded_lines` in [lines.sql](../src/better/sql/lines.sql). `player_stat_values` has no
row for a player who did not play, which is what makes a DNP a void.

PrizePicks also "reboots" a pick when a player leaves hurt in the first half and does
not return. Box scores cannot show that, so such a pick is graded on the stat line.

### Commands

```
better lines map [--all]
better lines unmatched [--limit N]
```

`better lines snapshot` and `better lines rebuild` run the mapping for what they loaded.

## Tasks

- [ ] T3.1 Fixture lines covering every branch (exact, alias, suffix, league fallback,
      ambiguous, combo, no game, no stat, both-teams form). Tests for R3.1 to R3.8.
- [ ] T3.2 `mapping.py` to pass them.
- [ ] T3.3 `better lines map` and `better lines unmatched`. R3.9, R3.10.
- [ ] T3.4 Grading tests on the fixture season: over, under, push, void by DNP, void by
      postponement, pending. R3.11.
- [ ] T3.5 Map the imported mirror history; read the unmatched report; add verified
      aliases to `aliases.csv`; record the link rate below.
- [ ] T3.6 Sanity checks on real graded lines, recorded below: over rate by stat near
      50%; mean of actual over mean of line near 1; fantasy-score ratio in line with the
      other stats (this confirms the scoring weights in `better.stats`).

## Results

Not yet run.

## Limits and open questions

- Reboots are not detected (see above).
- A player traded on the day of a game may only resolve through the league fallback
  until he appears in a box score for his new team.
- The fantasy-score weights (1, 1.2, 1.5, 3, 3, -1) are from memory of PrizePicks'
  published scoring and could not be re-confirmed by search on 2026-11-13. T3.6 checks
  them against real lines.
