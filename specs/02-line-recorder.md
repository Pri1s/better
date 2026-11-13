# 02. Line recorder

**Status:** in progress. Schema and stat catalogue built; adapters and loader not yet.

## Purpose

Keep a dated record of every prop line the tool sees: the PrizePicks board, sportsbook
props, and the mirror's history. Lines that are not recorded cannot be recovered, so
this part saves first and interprets second.

## Requirements

- **R2.1** WHEN a source answers, THE SYSTEM SHALL write the response text unchanged to
  a file under `data/raw/lines/` together with the fetch time, before parsing it.
- **R2.2** WHEN the database is emptied, `better lines rebuild` SHALL restore every
  snapshot and line from the raw files alone, with identical `snapshot_id`s.
- **R2.3** WHEN a raw file exists that the database does not know, the next
  `better lines` command SHALL load it. A crash between saving and loading loses nothing.
- **R2.4** WHEN the same snapshot is loaded twice, THE SYSTEM SHALL store it once.
- **R2.5** WHEN a row cannot be read (no line value, no player, no stat name), THE
  SYSTEM SHALL skip it, count it by reason in the snapshot's `detail`, and continue.
- **R2.6** WHEN PrizePicks direct access is not switched on, the PrizePicks adapter
  SHALL refuse to make a request and say how to switch it on and what the risk is.
- **R2.7** WHEN a provider answers 403 or 429, or with something that is not JSON, THE
  SYSTEM SHALL stop and report it. It SHALL NOT retry with different headers.
- **R2.8** WHEN The Odds API is swept, THE SYSTEM SHALL estimate the credit cost first,
  refuse to exceed `--max-credits`, and record the credits actually used.
- **R2.9** An API key SHALL NOT appear in a raw file, the database, a log line or an
  error message.
- **R2.10** WHEN the mirror is imported, THE SYSTEM SHALL create one snapshot per commit
  that changed the NBA file, dated by the mirror's own scrape time, and SHALL skip
  commits already imported.
- **R2.11** A line on two or more players together SHALL be stored with
  `is_combo = TRUE` and never linked to a single player.
- **R2.12** PrizePicks goblin and demon lines SHALL be stored with their `odds_type`.
  For The Odds API, where they arrive as `_alternate` markets, the type SHALL be decided
  by comparing the line with the standard line for the same player and stat, falling
  back to the price (even odds means demon).

## Design

### Raw files

```
data/raw/lines/<source>/<YYYY>/<MM>/<source>_<YYYYMMDDTHHMMSSZ>.json.gz
data/raw/lines/mirror/repo.git          bare clone of the mirror
```

Each file is a gzip-compressed JSON envelope (written with a zero mtime so identical
content gives identical bytes):

```json
{
  "format": 1,
  "source": "prizepicks",
  "fetched_at_utc": "2026-11-13T17:05:01Z",
  "meta": {"league_id": 7},
  "responses": [
    {"url": "https://...", "status": 200, "fetched_at_utc": "...", "body": "<exact text>"}
  ]
}
```

`snapshot_id` is `<source>/<YYYYMMDDTHHMMSSZ>`, so it can be recomputed from the file
name. URLs are stored with `apiKey` removed.

### Modules (`src/better/lines/`)

| Module | Role |
|---|---|
| `raw.py` | `Envelope`, `Response`; write and read envelope files; list them. |
| `rows.py` | `LineRow`, the normalised row every parser returns, and `Parsed` (rows plus drop counts). |
| `prizepicks.py` | Fetch `partner-api.prizepicks.com/projections?league_id=7`; parse JSON:API. |
| `mirror.py` | Clone or fetch the mirror; turn each commit's file into an envelope; parse it. |
| `oddsapi.py` | Events list, per-event odds, credit estimate; parse bookmakers and markets. |
| `store.py` | Load envelopes into `line_snapshots` and `prop_lines`; rebuild; status. |

Every parser is a pure function `parse(envelope) -> Parsed`. Every fetcher takes an
injectable session so tests never touch the network.

### Tables

`line_snapshots` and `prop_lines` in [lines.sql](../src/better/sql/lines.sql).

### Source notes

- **PrizePicks partner endpoint.** JSON:API. `data[]` holds projections with
  `attributes.{line_score, stat_type, odds_type, start_time, status, rank, is_promo,
  updated_at, event_type}` and `relationships.{new_player, game}`. `included[]` holds
  `new_player` (`name`, `team`, `team_name`, `market`, `combo`, `position`), `game`
  (`metadata.game_info.teams.{home,away}.abbreviation`, `start_time`) and `team`.
  `line_score` may be a number or a string. One page returns the whole league.
- **Mirror.** `data/prizepicks-nba.json`: `{scrapedAt, collectionStatus, props[]}`. Row
  fields changed over time; early rows lack `startTimeIso`, `teamCode`, `playerId`.
  `line` is a float, int or string. Rows without a start time are dropped (R2.5).
- **The Odds API.** `GET /v4/sports/basketball_nba/events` (free), then
  `GET /v4/sports/basketball_nba/events/{id}/odds` with `markets`, `oddsFormat=decimal`
  and either `bookmakers=prizepicks` or `regions=us`. Cost is markets returned times
  regions. Outcomes carry `name` (Over/Under), `description` (player), `price`, `point`.
  Quota comes back in `x-requests-remaining`, `x-requests-used`, `x-requests-last`.

### Commands

```
better lines snapshot --source mirror|prizepicks|odds-api [--books ...] [--max-credits N]
better lines rebuild
better lines status
```

## Tasks

- [x] T2.1 Stat catalogue and name tables (`better.stats`). R2.12 groundwork.
- [x] T2.2 `line_snapshots`, `prop_lines` and helper views (`sql/lines.sql`).
- [ ] T2.3 `raw.py` and `rows.py`: envelope round trip, deterministic bytes. R2.1, R2.2.
- [ ] T2.4 `mirror.py`: parser for every row shape seen in history; git import. R2.5,
      R2.10, R2.11.
- [ ] T2.5 `store.py`: load, idempotence, self-healing, rebuild. R2.2, R2.3, R2.4.
- [ ] T2.6 `prizepicks.py`: parser, opt-in gate, refusal handling. R2.6, R2.7, R2.11.
- [ ] T2.7 `oddsapi.py`: sweep, credit guard, key redaction, goblin/demon rule. R2.8,
      R2.9, R2.12.
- [ ] T2.8 CLI `better lines snapshot|rebuild|status`.
- [ ] T2.9 Import the full mirror history into the real database; record counts below.

## Results

Not yet run.

## Limits and open questions

- The PrizePicks adapter has never been run against the live endpoint (decision D2).
- The Odds API adapter has never been run with a key. Its fixtures follow the
  documented response shape.
- Whether The Odds API's free 500 credits include the `us_dfs` books is unverified.
