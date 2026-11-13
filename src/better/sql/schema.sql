-- NBA data layer.
--
-- Conventions
--   * game_id, team_id and player_id are ESPN's ids, because the box scores come from ESPN.
--     NBA.com ids are kept alongside wherever a source provides them (nba_* columns).
--   * season is the year a season ends: 2027 means 2026-27.
--   * *_utc columns are UTC timestamps stored without a zone.
--     game_date is the US Eastern calendar date, which is how the league dates games.
--   * Each table is replaced one season at a time, so there are no primary-key indexes.
--     better.nba.load checks uniqueness after every load instead.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

-- One row per game, played or still to come. All-Star games, preseason games and
-- placeholders whose teams are not yet known are left out.
CREATE TABLE IF NOT EXISTS games (
    game_id       INTEGER   NOT NULL,
    season        INTEGER   NOT NULL,
    season_type   VARCHAR   NOT NULL,  -- regular | play_in | playoffs
    game_date     DATE      NOT NULL,
    tipoff_utc    TIMESTAMP,           -- NULL until the league sets a time
    status        VARCHAR   NOT NULL,  -- final | scheduled | postponed | canceled | in_progress | ...
    home_team_id  INTEGER   NOT NULL,
    away_team_id  INTEGER   NOT NULL,
    home_score    INTEGER,             -- NULL until the game is final
    away_score    INTEGER,
    neutral_site  BOOLEAN,
    label         VARCHAR,             -- e.g. 'NBA Cup - Group Play', 'East Finals - Game 4'
    venue         VARCHAR
);

-- One row per player listed in a box score, including players who did not play.
CREATE TABLE IF NOT EXISTS player_game_stats (
    game_id                  INTEGER NOT NULL,
    player_id                INTEGER NOT NULL,
    season                   INTEGER NOT NULL,
    season_type              VARCHAR NOT NULL,
    game_date                DATE    NOT NULL,
    team_id                  INTEGER NOT NULL,
    opponent_team_id         INTEGER NOT NULL,
    is_home                  BOOLEAN NOT NULL,
    player_name              VARCHAR,
    position                 VARCHAR,
    starter                  BOOLEAN NOT NULL,
    did_not_play             BOOLEAN NOT NULL,  -- listed without playing; every stat below is NULL
    dnp_reason               VARCHAR,           -- did_not_play rows only, e.g. 'LEFT ANKLE SPRAIN'
    ejected                  BOOLEAN NOT NULL,
    minutes                  DOUBLE,            -- whole minutes: ESPN does not publish seconds
    points                   INTEGER,
    rebounds                 INTEGER,
    assists                  INTEGER,
    steals                   INTEGER,
    blocks                   INTEGER,
    turnovers                INTEGER,
    fouls                    INTEGER,
    field_goals_made         INTEGER,
    field_goals_attempted    INTEGER,
    three_pointers_made      INTEGER,
    three_pointers_attempted INTEGER,
    free_throws_made         INTEGER,
    free_throws_attempted    INTEGER,
    offensive_rebounds       INTEGER,
    defensive_rebounds       INTEGER,
    plus_minus               INTEGER,
    team_won                 BOOLEAN
);

-- Two rows per played game, one for each team.
CREATE TABLE IF NOT EXISTS team_game_stats (
    game_id                  INTEGER NOT NULL,
    team_id                  INTEGER NOT NULL,
    season                   INTEGER NOT NULL,
    season_type              VARCHAR NOT NULL,
    game_date                DATE    NOT NULL,
    opponent_team_id         INTEGER NOT NULL,
    is_home                  BOOLEAN NOT NULL,
    won                      BOOLEAN,
    points                   INTEGER,
    opponent_points          INTEGER,
    field_goals_made         INTEGER,
    field_goals_attempted    INTEGER,
    three_pointers_made      INTEGER,
    three_pointers_attempted INTEGER,
    free_throws_made         INTEGER,
    free_throws_attempted    INTEGER,
    offensive_rebounds       INTEGER,
    defensive_rebounds       INTEGER,
    rebounds                 INTEGER,
    assists                  INTEGER,
    steals                   INTEGER,
    blocks                   INTEGER,
    turnovers                INTEGER,           -- player turnovers plus team turnovers
    fouls                    INTEGER,
    fast_break_points        INTEGER,
    points_in_paint          INTEGER,
    points_off_turnovers     INTEGER
);

-- Team names per season, with the NBA.com id and abbreviation when the crosswalk has them.
-- ESPN abbreviations differ from the league's (GS, NY, SA, UTAH, WSH, NO).
CREATE TABLE IF NOT EXISTS team_seasons (
    season           INTEGER NOT NULL,
    team_id          INTEGER NOT NULL,
    abbreviation     VARCHAR,
    name             VARCHAR,
    nba_team_id      BIGINT,
    nba_abbreviation VARCHAR
);

-- ESPN athlete id <-> NBA.com player id, as published by sportsdataverse.
CREATE TABLE IF NOT EXISTS player_ids (
    season           INTEGER NOT NULL,
    player_id        INTEGER NOT NULL,
    nba_player_id    BIGINT  NOT NULL,
    player_name      VARCHAR,
    nba_player_name  VARCHAR,
    match_method     VARCHAR,
    match_confidence DOUBLE
);

-- The league's official injury report, one row per player per published report.
-- Rows keep the league's own ids; the injury_reports view adds game_id, team_id and player_id.
CREATE TABLE IF NOT EXISTS injury_report_rows (
    season             INTEGER   NOT NULL,
    report_time_utc    TIMESTAMP NOT NULL,
    game_date          DATE      NOT NULL,
    team_name          VARCHAR,
    report_player_name VARCHAR,   -- 'Last, First' as printed; NULL on a "not yet submitted" row
    status             VARCHAR,   -- Out | Doubtful | Questionable | Probable | Available
    reason             VARCHAR,
    nba_game_id        VARCHAR,
    nba_team_id        BIGINT,
    nba_player_id      BIGINT
);

-- When each season was last loaded.
CREATE TABLE IF NOT EXISTS season_loads (
    season        INTEGER   NOT NULL,
    loaded_at_utc TIMESTAMP NOT NULL,
    games         INTEGER   NOT NULL,
    player_rows   INTEGER   NOT NULL,
    team_rows     INTEGER   NOT NULL,
    injury_rows   INTEGER   NOT NULL
);

-- ---------------------------------------------------------------------------
-- Name helpers
-- ---------------------------------------------------------------------------

-- A comparison key for player names: lower case, no accents, no apostrophes or
-- periods, other punctuation as spaces. 'T.J. McConnell' and 'TJ McConnell' agree.
CREATE OR REPLACE MACRO name_key(name) AS
    trim(regexp_replace(
        regexp_replace(lower(strip_accents(name)), '[''’.]', '', 'g'),
        '[^a-z0-9]+', ' ', 'g'));

-- The injury report prints 'Last, First' ('Udeh, Jr., Ernest'). Return 'First Last'.
CREATE OR REPLACE MACRO first_last(report_name) AS
    CASE
        WHEN report_name IS NULL THEN NULL
        WHEN contains(report_name, ',') THEN
            trim(regexp_extract(report_name, ',([^,]*)$', 1)) || ' ' ||
            trim(replace(regexp_replace(report_name, ',[^,]*$', ''), ',', ''))
        ELSE trim(report_name)
    END;

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- Current identity of each team.
CREATE OR REPLACE VIEW teams AS
SELECT
    team_id,
    arg_max(abbreviation, season)     AS abbreviation,
    arg_max(name, season)             AS name,
    arg_max(nba_team_id, season)      AS nba_team_id,
    arg_max(nba_abbreviation, season) AS nba_abbreviation,
    min(season)                       AS first_season,
    max(season)                       AS last_season
FROM team_seasons
GROUP BY team_id;

-- NBA.com player id -> ESPN athlete id (the most recent season's answer wins).
CREATE OR REPLACE VIEW player_link AS
SELECT nba_player_id, arg_max(player_id, season) AS player_id
FROM player_ids
GROUP BY nba_player_id;

-- Everyone who has appeared in a loaded box score, with their latest name and team.
CREATE OR REPLACE VIEW players AS
SELECT
    s.player_id,
    arg_max(s.player_name, s.game_date)        AS player_name,
    arg_max(s.position, s.game_date)           AS position,
    arg_max(s.team_id, s.game_date)            AS team_id,
    min(s.game_date)                           AS first_game_date,
    max(s.game_date)                           AS last_game_date,
    count(*) FILTER (WHERE NOT s.did_not_play) AS games_played,
    any_value(n.nba_player_id)                 AS nba_player_id
FROM player_game_stats s
LEFT JOIN (
    SELECT player_id, arg_max(nba_player_id, season) AS nba_player_id
    FROM player_ids
    GROUP BY player_id
) n USING (player_id)
GROUP BY s.player_id;

-- Rest and schedule density for each team in each game, upcoming games included.
-- Everything here is known before tip-off: it is computed from game dates alone.
-- Postponed and canceled games are ignored. Counts restart every season, so a
-- team's first game of a season has rest_days NULL.
CREATE OR REPLACE VIEW team_game_context AS
WITH team_games AS (
    SELECT game_id, season, season_type, game_date, tipoff_utc, status,
           home_team_id AS team_id, away_team_id AS opponent_team_id, TRUE AS is_home
    FROM games
    WHERE status NOT IN ('postponed', 'canceled')
    UNION ALL
    SELECT game_id, season, season_type, game_date, tipoff_utc, status,
           away_team_id AS team_id, home_team_id AS opponent_team_id, FALSE AS is_home
    FROM games
    WHERE status NOT IN ('postponed', 'canceled')
),
spaced AS (
    SELECT
        *,
        row_number() OVER by_game AS team_game_number,
        -- Full days off since the previous game: 0 on the second night of a back-to-back.
        -- Never negative, even where the source dates two of a team's games on one day.
        CASE
            WHEN lag(game_date) OVER by_game IS NULL THEN NULL
            ELSE greatest(CAST(game_date - lag(game_date) OVER by_game AS INTEGER) - 1, 0)
        END AS rest_days,
        count(*) OVER (
            PARTITION BY team_id, season ORDER BY game_date
            RANGE BETWEEN INTERVAL 3 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
        ) AS games_in_prior_3_days,
        count(*) OVER (
            PARTITION BY team_id, season ORDER BY game_date
            RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
        ) AS games_in_prior_6_days
    FROM team_games
    WINDOW by_game AS (PARTITION BY team_id, season ORDER BY game_date, game_id)
)
SELECT
    t.game_id,
    t.team_id,
    t.opponent_team_id,
    t.season,
    t.season_type,
    t.game_date,
    t.tipoff_utc,
    t.status,
    t.is_home,
    t.team_game_number,
    t.rest_days,
    coalesce(t.rest_days = 0, FALSE) AS is_back_to_back,
    t.games_in_prior_3_days >= 2     AS is_third_in_four_days,
    t.games_in_prior_3_days,
    t.games_in_prior_6_days,
    o.rest_days                      AS opponent_rest_days,
    t.rest_days - o.rest_days        AS rest_advantage
FROM spaced t
JOIN spaced o
  ON o.game_id = t.game_id AND o.team_id = t.opponent_team_id;

-- The game a team plays on a date. A team plays at most once a day, but the source
-- occasionally misdates a game (Golden State has two on 2020-03-01); the later
-- tip-off wins so that a date never resolves to two games.
CREATE OR REPLACE VIEW team_game_dates AS
SELECT season, game_date, team_id, game_id
FROM team_game_context
QUALIFY row_number() OVER (
    PARTITION BY season, team_id, game_date ORDER BY tipoff_utc DESC NULLS LAST, game_id DESC
) = 1;

-- Names seen on each team's box scores in a season. Used to match an injury-report
-- player whose NBA.com id is missing from the crosswalk. Ambiguous names are left out.
CREATE OR REPLACE VIEW season_player_names AS
SELECT season, team_id, name_key(player_name) AS name_key, min(player_id) AS player_id
FROM player_game_stats
WHERE player_name IS NOT NULL
GROUP BY season, team_id, name_key(player_name)
HAVING count(DISTINCT player_id) = 1;

-- Injury-report rows linked to this database's games, teams and players.
--   game_id   NULL when the team has no scheduled game that day (postponements, summer league).
--   player_id NULL when the player could not be matched, and on "not yet submitted" rows.
-- A player who is absent from a report is simply not listed: absence means available,
-- unless that team's row says the report was not yet submitted.
CREATE OR REPLACE VIEW injury_reports AS
SELECT
    r.season,
    r.report_time_utc,
    r.game_date,
    g.game_id,
    t.team_id,
    coalesce(x.player_id, n.player_id) AS player_id,
    first_last(r.report_player_name)   AS player_name,
    r.status,
    r.reason,
    CASE
        WHEN r.report_player_name IS NULL THEN NULL
        WHEN x.player_id IS NOT NULL THEN 'crosswalk'
        WHEN n.player_id IS NOT NULL THEN 'name'
    END AS player_match,
    r.team_name,
    r.report_player_name,
    r.nba_game_id,
    r.nba_team_id,
    r.nba_player_id
FROM injury_report_rows r
LEFT JOIN team_seasons t
       ON t.season = r.season AND t.nba_team_id = r.nba_team_id
LEFT JOIN team_game_dates g
       ON g.season = r.season AND g.game_date = r.game_date AND g.team_id = t.team_id
LEFT JOIN player_link x
       ON x.nba_player_id = r.nba_player_id
LEFT JOIN season_player_names n
       ON x.player_id IS NULL
      AND n.season = r.season AND n.team_id = t.team_id
      AND n.name_key = name_key(first_last(r.report_player_name));

-- What the injury report said `lead_minutes` before each game's tip-off.
--
--   SELECT * FROM injury_report_before_tip(60) WHERE game_id = 401909947;
--
-- For every game it takes the latest report that (a) was published at or before
-- the cutoff and (b) covers that game's date, and returns that report's rows for
-- the game. Nothing published after the cutoff can appear, which is what keeps a
-- backtest honest. A game with no rows had nobody listed, or no report yet covered
-- its date at the cutoff.
CREATE OR REPLACE MACRO injury_report_before_tip(lead_minutes) AS TABLE
WITH report_dates AS (
    SELECT DISTINCT report_time_utc, game_date
    FROM injury_report_rows
),
cutoffs AS (
    SELECT game_id, game_date, tipoff_utc - to_minutes(lead_minutes) AS cutoff_utc
    FROM games
    WHERE tipoff_utc IS NOT NULL
),
picked AS (
    SELECT c.game_id, c.cutoff_utc, d.report_time_utc
    FROM cutoffs c
    ASOF JOIN report_dates d
      ON c.game_date = d.game_date AND c.cutoff_utc >= d.report_time_utc
)
SELECT p.cutoff_utc, i.*
FROM picked p
JOIN injury_reports i
  ON i.game_id = p.game_id AND i.report_time_utc = p.report_time_utc;
