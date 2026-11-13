# 01. NBA data

**Status:** verified (2026-11-12). Nine seasons loaded, 2018-19 to 2026-27.

## Purpose

Hold game logs, box scores, minutes, injury reports and the schedule with rest days, in
a form every later part can query.

## What exists

Documented in the project [README](../README.md) and in
[schema.sql](../src/better/sql/schema.sql). In short:

- `games`, `player_game_stats`, `team_game_stats`: one season replaced as a whole per load.
- `team_game_context`: rest days and schedule density, known before tip-off.
- `injury_reports` and `injury_report_before_tip(minutes)`: the league's injury report
  as it stood at a cutoff.
- `better nba sync` and `better nba status`.

## Facts later parts rely on

- **R1.1** `game_id`, `team_id`, `player_id` are ESPN ids. NBA.com ids sit alongside.
- **R1.2** `game_date` is the US Eastern date; `tipoff_utc` is UTC.
- **R1.3** A player who was listed without playing has `did_not_play = TRUE` and NULL
  stats. A player missing from the box score entirely was inactive.
- **R1.4** Minutes are whole numbers.
- **R1.5** The source files are rebuilt once a day in the morning (US Eastern) and hold
  nothing from the current day.
- **R1.6** Injury reports start 2018-12-17. From 2021-22 they are published roughly
  hourly; before that, three times a day.

## Limits

Listed in the README under "Known limits". The one that matters most downstream: there
is no same-day data, so a scratch announced in the afternoon is invisible until the next
morning. Spec 08 covers the manual override that fills this gap.
