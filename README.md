# better

A personal research tool for NBA player props. The NBA data layer is built. The
other six parts are still to come (see [Roadmap](#roadmap)).

## Quick start

```bash
uv sync
uv run better nba sync --from 2019   # first run: about 12 MB of downloads
uv run better nba status
```

After that, `uv run better nba sync` refreshes the season in progress. Run it once a day.

Seasons are named by the year they end: `2027` is 2026-27 (`--from 2018-19` also works).
Everything downloaded or built lives in `data/`, which git ignores: the source files
under `data/raw/` and the DuckDB database at `data/better.duckdb`. Set `BETTER_DATA_DIR`
to keep it somewhere else.

## What is in the database

| Table or view | One row per | Notes |
|---|---|---|
| `games` | game, played or scheduled | `game_date` is the US Eastern date; `tipoff_utc` is UTC |
| `player_game_stats` | player listed in a box score | `starter` flag; did-not-play rows carry `dnp_reason` and no stats |
| `team_game_stats` | team in a played game | |
| `team_game_context` (view) | team in a game, upcoming games included | `rest_days`, `is_back_to_back`, `is_third_in_four_days`, opponent rest |
| `injury_reports` (view) | player in a published injury report | linked to `game_id`, `team_id` and `player_id` |
| `injury_report_before_tip(minutes)` | | the report as it stood that many minutes before each tip-off |
| `teams`, `players` (views) | | ESPN ids, with NBA.com ids and abbreviations alongside |

`game_id`, `team_id` and `player_id` are ESPN's ids, because the box scores come from
ESPN. Column-level notes are in [schema.sql](src/better/sql/schema.sql).

```python
from better import db

con = db.connect()

# A player's recent games with his team's rest.
con.sql("""
    SELECT s.game_date, o.abbreviation AS opp, s.minutes, s.points, s.rebounds, s.assists,
           c.rest_days, c.is_back_to_back
    FROM player_game_stats s
    JOIN teams o ON o.team_id = s.opponent_team_id
    JOIN team_game_context c ON c.game_id = s.game_id AND c.team_id = s.team_id
    WHERE s.player_name = 'Jalen Brunson' AND NOT s.did_not_play
    ORDER BY s.game_date DESC
    LIMIT 10
""").show()

# Who was listed on the injury report an hour before the most recent game tipped off.
con.sql("""
    SELECT t.abbreviation AS team, i.player_name, i.status, i.reason, i.report_time_utc
    FROM injury_report_before_tip(60) i
    JOIN teams t USING (team_id)
    WHERE i.game_id = (SELECT max(game_id) FROM games WHERE status = 'final')
    ORDER BY team, i.player_name
""").show()

con.close()  # DuckDB allows one writer: close this before running `better nba sync`
```

`injury_report_before_tip` is the piece that keeps a backtest honest about injuries: it
returns only the latest report published at or before the cutoff, never a later one.

## Where the data comes from

Every file is a prebuilt parquet published by the
[sportsdataverse](https://github.com/sportsdataverse/sportsdataverse-data) project (MIT)
as a GitHub release asset. This project downloads those files and nothing else.

| Data | Release | Collected upstream from |
|---|---|---|
| Schedule, player and team box scores | `espn_nba_schedules`, `espn_nba_player_boxscores`, `espn_nba_team_boxscores` | ESPN's undocumented API |
| Injury reports | `nba_stats_injury_report` | the injury report PDFs the NBA publishes |
| ESPN to NBA.com ids | `nba_crosswalk` | sportsdataverse's own matching |

Things to know about that choice (checked 2026-11-12):

- **The upstream collection is unofficial.** Disney's terms of use (section 2.B) prohibit
  automated access to ESPN. That binds whoever collects the data, which is
  sportsdataverse, not this project. The MIT licence is sportsdataverse's own label and
  is not a licence from ESPN or the NBA.
- **Why not NBA.com through `nba_api`.** NBA.com's terms of use (section 9, last updated
  2026-07-13) say its statistics may not be used in connection with gambling or fantasy
  games, which is what this tool is for. Its CDN also answered HTTP 403 from this machine.
- **A licensed alternative exists.** [BALLDONTLIE](https://docs.balldontlie.io/) is a
  documented paid API (player stats and injuries from $9.99 a month). The tables here do
  not depend on the source, so a second loader could fill them.

## Known limits

- **Not same-day.** On 2026-11-12 the files had been rebuilt that morning (US Eastern) and
  held nothing from that day: box scores ran through the night before, injury reports
  through midnight. Treat it as a once-a-day feed. For who is out tonight you need another
  source.
- **Whole minutes.** ESPN does not publish seconds played.
- **Use 2012-13 onward for minutes.** Between 2005-06 and 2011-12, 20 to 29% of player
  rows have no minutes. Plus-minus starts in 2008-09 and is complete from 2012-13.
- **Injury reports start on 2018-12-17.** In finished seasons up to 3% of listed players
  cannot be matched to a `player_id`. Early in a season it is about 10%, mostly players
  who have not yet appeared in a box score.
- **Left out on purpose:** All-Star games, preseason games, and schedule placeholders
  whose teams are not yet known (NBA Cup knockout rounds).
- **One misdated game.** ESPN dates Golden State at Phoenix on 2020-03-01 instead of
  2020-02-29. `sync` prints a note for oddities like this rather than hiding them.

## Development

```bash
uv run pytest
uv run ruff check .
```

```
src/better/
  cli.py            command line
  config.py         where data lives
  db.py             DuckDB connection, applies the schema
  sql/schema.sql    tables, views and macros
  nba/sources.py    which files exist and how they are downloaded
  nba/load.py       source files -> tables, one season per transaction
  nba/sync.py       decides which seasons to download and load
tests/              run against small files shaped like the real ones; no network
```

A season is always replaced as a whole, so `sync` is safe to repeat, and the database
can be rebuilt from `data/raw/` without the network (`--refresh` forces new downloads).

## Roadmap

1. **NBA data** (game logs, box scores, minutes, injuries, schedule and rest). Built.
2. **PrizePicks board snapshots.** Not started. Start this next: line history that is not
   recorded cannot be recovered later. The board has no official API and PrizePicks'
   terms prohibit automated access, so the source is a decision to make first.
3. **Historical prop lines** for backtesting. Not started.
4. **Projection model** with an over/under probability per line. Not started.
5. **Backtest** without lookahead, grouped by size of disagreement. Not started.
6. **Sportsbook odds with the vig removed.** Not started.
7. **Entry builder** (Power and Flex, 2 to 6 picks, risk tiers). Not started.
