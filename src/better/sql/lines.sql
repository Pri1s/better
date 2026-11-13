-- Prop lines: what PrizePicks and the sportsbooks offered, when, and how it turned out.
--
-- Conventions
--   * The untouched provider responses live in files under data/raw/lines/. These
--     tables are an index of those files and can be rebuilt from them at any time
--     (`better lines rebuild`), so a parsing mistake never loses a recorded line.
--   * A snapshot is one capture of a board: one request to PrizePicks, one sweep of
--     The Odds API, or one day's file from the mirror.
--   * fetched_at_utc is when the provider answered. Nothing recorded in a snapshot
--     was knowable before that moment, which is what a backtest keys on.
--   * player_stat_values and stat_names are created by better.db from better.stats.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS line_snapshots (
    snapshot_id    VARCHAR   NOT NULL,  -- '<source>/<UTC time>', e.g. 'prizepicks/20261113T170501Z'
    source         VARCHAR   NOT NULL,  -- prizepicks | odds_api | mirror
    fetched_at_utc TIMESTAMP NOT NULL,
    raw_path       VARCHAR   NOT NULL,  -- relative to the raw directory
    raw_sha256     VARCHAR   NOT NULL,
    parser_version INTEGER   NOT NULL,
    rows_seen      INTEGER   NOT NULL,  -- line rows in the response
    rows_kept      INTEGER   NOT NULL,  -- rows stored in prop_lines
    loaded_at_utc  TIMESTAMP NOT NULL,
    detail         VARCHAR              -- JSON: rows dropped by reason, API credits used, ...
);

-- One row per line per snapshot. A line that stays on the board is stored again in
-- every snapshot, so its history is simply its rows in time order.
CREATE TABLE IF NOT EXISTS prop_lines (
    snapshot_id      VARCHAR   NOT NULL,
    line_no          INTEGER   NOT NULL,  -- position within the snapshot
    source           VARCHAR   NOT NULL,
    fetched_at_utc   TIMESTAMP NOT NULL,
    book             VARCHAR   NOT NULL,  -- prizepicks, draftkings, fanduel, ...
    league           VARCHAR   NOT NULL,
    source_line_id   VARCHAR,             -- the provider's id for the line (PrizePicks projection id)
    source_player_id VARCHAR,
    source_game_id   VARCHAR,
    player_name      VARCHAR   NOT NULL,  -- as the source prints it
    team_code        VARCHAR,             -- the player's team as the source abbreviates it
    team_name        VARCHAR,
    opponent_code    VARCHAR,
    opponent_name    VARCHAR,
    home_team_name   VARCHAR,             -- The Odds API names the two teams, not the player's
    away_team_name   VARCHAR,
    start_time_utc   TIMESTAMP,
    stat_name        VARCHAR   NOT NULL,  -- as the source names it: 'Pts+Rebs+Asts', 'player_points'
    line             DOUBLE    NOT NULL,
    odds_type        VARCHAR   NOT NULL,  -- standard | goblin | demon (PrizePicks); alternate (books)
    over_price       DOUBLE,              -- decimal odds; NULL where the source quotes none
    under_price      DOUBLE,
    status           VARCHAR,             -- the provider's own status: pre_game, live, ...
    is_combo         BOOLEAN   NOT NULL,  -- one line on two or more players together
    is_promo         BOOLEAN,
    board_rank       INTEGER,             -- PrizePicks' own ordering of its board
    updated_at_utc   TIMESTAMP,           -- the provider's last-update time for the line
    -- Filled in by better.lines.mapping. NULL until `better lines map` has run.
    game_id          INTEGER,
    team_id          INTEGER,
    player_id        INTEGER,
    stat             VARCHAR,             -- a key from better.stats
    link_status      VARCHAR,             -- linked | combo | no_stat | no_game | no_player | ambiguous_player
    player_match     VARCHAR              -- how the player was found: team | league
);

-- Names a source prints differently from the box scores ('Nic Claxton' for
-- 'Nicolas Claxton'). Reloaded on every mapping run from the alias files.
CREATE TABLE IF NOT EXISTS player_aliases (
    alias     VARCHAR NOT NULL,  -- the name as a source prints it
    player    VARCHAR NOT NULL,  -- the name as the box scores print it
    origin    VARCHAR NOT NULL   -- builtin | local
);

-- ---------------------------------------------------------------------------
-- Name helpers
-- ---------------------------------------------------------------------------

-- name_key without a generational suffix, for sources that drop or add 'Jr.'.
-- Only used after the exact key has failed, and only within one team's roster.
CREATE OR REPLACE MACRO name_key_no_suffix(name) AS
    trim(regexp_replace(name_key(name), ' (jr|sr|ii|iii|iv|v)$', ''));

-- Every way a team can be written, as a name_key: ESPN's abbreviation, the league's,
-- a few abbreviations other sites use, the full name and the nickname.
CREATE OR REPLACE VIEW team_keys AS
WITH named AS (
    SELECT team_id, abbreviation, name, nba_abbreviation FROM teams
),
other_codes(nba_abbreviation, code) AS (
    VALUES ('BKN', 'BRK'), ('CHA', 'CHO'), ('PHX', 'PHO'), ('GSW', 'GS'), ('NOP', 'NO'),
           ('NOP', 'NOR'), ('NYK', 'NY'), ('SAS', 'SA'), ('UTA', 'UTAH'), ('UTA', 'UTH'),
           ('WAS', 'WSH')
),
other_names(nba_abbreviation, alias) AS (
    VALUES ('LAC', 'Los Angeles Clippers'), ('LAC', 'LA Clippers'), ('LAL', 'LA Lakers')
),
keys AS (
    SELECT team_id, name_key(abbreviation) AS key FROM named
    UNION
    SELECT team_id, name_key(nba_abbreviation) FROM named
    UNION
    SELECT team_id, name_key(name) FROM named
    UNION
    -- The nickname: 'Trail Blazers' for Portland, the last word for everyone else.
    SELECT team_id,
           name_key(CASE WHEN name LIKE '% Trail Blazers' THEN 'Trail Blazers'
                         ELSE regexp_extract(name, '([^ ]+)$', 1) END)
    FROM named
    UNION
    SELECT n.team_id, name_key(o.code) FROM named n JOIN other_codes o USING (nba_abbreviation)
    UNION
    SELECT n.team_id, name_key(o.alias) FROM named n JOIN other_names o USING (nba_abbreviation)
)
SELECT key, min(team_id) AS team_id
FROM keys
WHERE key IS NOT NULL AND key <> ''
GROUP BY key
HAVING count(DISTINCT team_id) = 1;

-- Who has been on each team's box scores in each season, keyed for name matching.
CREATE OR REPLACE VIEW roster_names AS
SELECT DISTINCT season, team_id, player_id,
       name_key(player_name) AS name_key,
       name_key_no_suffix(player_name) AS name_key_no_suffix
FROM player_game_stats
WHERE player_name IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Grading
-- ---------------------------------------------------------------------------

-- Every line with what actually happened.
--   result   over | under | push  the stat against the line, for a player who played
--            void                 the player did not play, or the game was not played
--            pending              the game is not final yet
--            unlinked             the line could not be tied to a player, game and stat
-- PrizePicks treats a push and a void alike: the pick drops out of the entry.
CREATE OR REPLACE VIEW graded_lines AS
SELECT
    l.*,
    g.game_date,
    g.season,
    g.tipoff_utc,
    g.status AS game_status,
    v.value  AS actual,
    CASE
        WHEN l.link_status IS DISTINCT FROM 'linked' THEN 'unlinked'
        WHEN g.status IN ('postponed', 'canceled') THEN 'void'
        WHEN g.status <> 'final' THEN 'pending'
        WHEN v.value IS NULL THEN 'void'
        WHEN v.value > l.line THEN 'over'
        WHEN v.value < l.line THEN 'under'
        ELSE 'push'
    END AS result
FROM prop_lines l
LEFT JOIN games g ON g.game_id = l.game_id
LEFT JOIN player_stat_values v
       ON v.game_id = l.game_id AND v.player_id = l.player_id AND v.stat = l.stat;

-- The PrizePicks board as of the most recent snapshot that carried PrizePicks
-- lines, limited to games that had not started when it was taken.
CREATE OR REPLACE VIEW current_board AS
SELECT l.*
FROM prop_lines l
WHERE l.book = 'prizepicks'
  AND l.snapshot_id = (
        SELECT arg_max(snapshot_id, fetched_at_utc) FROM prop_lines WHERE book = 'prizepicks')
  AND (l.start_time_utc IS NULL OR l.start_time_utc > l.fetched_at_utc);
