# 03. Mapping and grading

**Status:** verified (2026-11-13). 96.7% of the imported lines link; the rest are
stats a box score cannot show and two-player combos.

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
- **R3.4** WHEN no roster match exists, THE SYSTEM SHALL look across the whole league,
  this season and the one before, trying the exact key or alias first and the
  suffix-free key second, and accept a match only if exactly one player fits.
  `player_match = 'league'`. (The season before covers a season's first night, when
  nobody has a box score yet.)
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
- **R3.12** WHEN a line names no team at all, THE SYSTEM SHALL find the player across the
  league for the season of the start date (exact key or alias first, suffix-free key
  second, exactly one player), place him on the team he had most recently joined by
  that date, and link that team's game on that date. A line that names a team the
  system does not recognise SHALL NOT use this rule: it stays `no_game` so that the
  unknown team is noticed.
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
   Count distinct `player_id`; exactly one links. Then the league-wide fallback, then
   the no-team rule.
6. **Status.** `linked` only when stat, game and player are all set. A line with an
   unsupported stat still gets its game and player; its status says `no_stat`.

The steps build temporary tables holding at most one row per line, and one final
`UPDATE` copies the result into `prop_lines`. The row count of the result is checked
against the number of lines before anything is written (R3.8). Every lookup is an
equality join, so all 80,891 lines map in under half a second.

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

- [x] T3.1 Fixture lines covering every branch (exact, alias, suffix, league fallback,
      ambiguous, combo, no game, no stat, both-teams form, no team). Tests for R3.1 to
      R3.8 and R3.12: `tests/test_lines_mapping.py`.
- [x] T3.2 `mapping.py` to pass them.
- [x] T3.3 `better lines map` and `better lines unmatched`. R3.9, R3.10.
- [x] T3.4 Grading tests on the fixture season: over, under, push, void by DNP, void by
      postponement, pending. R3.11: `tests/test_lines_grading.py`.
- [x] T3.5 Map the imported mirror history; read the unmatched report; add verified
      aliases to `aliases.csv`; record the link rate below.
- [x] T3.6 Sanity checks on real graded lines, recorded below.

## Results

**Mapping the mirror history, 2026-11-13** (`better lines map`, 0.4 seconds).

| | Lines |
|---|---|
| Linked | 78,211 of 80,891 (96.7%) |
| `no_stat` | 1,999 (Dunks, "Points - 1st 3 Minutes") |
| `combo` | 681 |
| `no_game`, `no_player`, `ambiguous_player` | 0 |

- 72,350 linked lines found their player on the roster of one of the two teams; 5,861
  through the league rule, nearly all of them the December 2025 lines that name no team.
- Two aliases were needed and are in `aliases.csv`: "Tristan Silva" and
  "Yanic Niederhäuser".
- **Audit of the links.** 36 distinct pairs exist where the printed name differs from
  the box-score name. All were read: 34 are a generational suffix the source drops or
  adds ("Jabari Smith" for "Jabari Smith Jr."), 2 are the aliases. No wrong match found.
- **Start time against tip-off.** 77,487 linked lines are within 30 minutes of the
  game's tip-off. 714 are 2 to 12 hours off: playoff and afternoon games whose times
  moved after the line was posted, same teams and date.

**Grading, same day.** All linked lines: 38,124 under, 33,683 over, 4,676 void, 1,420
push, 308 pending (the night of 2026-11-12, not yet in the box scores).

Taking one line per player, game and stat (the earliest snapshot), standard lines only:
**52,172 graded picks over 186 nights, 1,142 games and 438 players.** The over won
48.2% of the decided ones, so "always under" wins 51.8% before any model. That is the
baseline spec 05 has to beat.

| Stat | Picks | Over % | Push % | Mean line | Mean actual | Actual / line |
|---|---|---|---|---|---|---|
| Points | 13,932 | 48.5 | 0.5 | 14.11 | 14.27 | 1.011 |
| Rebounds | 8,358 | 47.6 | 5.7 | 5.66 | 5.70 | 1.007 |
| Assists | 5,023 | 48.0 | 4.4 | 4.24 | 4.34 | 1.023 |
| FG attempted | 2,925 | 48.4 | 1.1 | 11.68 | 11.69 | 1.001 |
| Turnovers | 2,756 | 48.4 | 0.0 | 1.73 | 1.84 | 1.063 |
| 3-pointers made | 2,379 | 48.8 | 0.5 | 1.76 | 1.92 | 1.088 |
| Steals | 2,138 | 46.5 | 0.0 | 0.68 | 0.83 | 1.235 |
| Fantasy score | 72 | 44.4 | 0.0 | 27.97 | 28.13 | 1.006 |

Lines track the median, and low-count stats are right-skewed, so a mean ratio above 1
for steals and threes is expected. The fantasy-score ratio matches the other stats,
which is consistent with the scoring weights in `better.stats`; with 72 lines that is
weak confirmation, not proof.

Voids are 5.5% of linked lines: players with a morning line who then did not play.

## Limits and open questions

- Reboots are not detected (see above).
- A player traded on the day of a game may only resolve through the league fallback
  until he appears in a box score for his new team.
- The fantasy-score weights (1, 1.2, 1.5, 3, 3, -1) are from memory of PrizePicks'
  published scoring and could not be re-confirmed by search on 2026-11-13. T3.6 checks
  them against real lines.
