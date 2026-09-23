-- 0001_core_schema.sql — the core schema for the shared SLAP sports database.
--
-- Design rules, each of them from SLA-58 (docs/sports-source-evaluation.md in
-- slap-newsletter) and each explained at length in docs/schema.md:
--
--   1. Nothing here is NFL-shaped. Sport and league are dimensions on every
--      fact; ties, overtime losses, shootouts, innings and quarters are all
--      representable without a column that only one sport uses.
--   2. Our own surrogate IDs, always. Source IDs live in *_xref tables, never
--      as primary keys — sources disagree (LAR/LA, OAK/LV) and change.
--   3. A franchise is not a team. `team` is one identity era (Houston Oilers,
--      Tennessee Oilers, Tennessee Titans); `franchise` is the continuity that
--      joins them. A drought query that doesn't know that returns a wrong
--      number confidently.
--   4. Provenance is a column, not a comment. Every fact row names its source.
--   5. Rules are enforced by the database, not by the loader's good intentions:
--      a game cannot reference a team from another league, a final game must
--      have a score, and an ingest run cannot be recorded as succeeded with
--      rows missing.

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- for the xref overlap constraint

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE sport (
    sport_id    text PRIMARY KEY CHECK (sport_id ~ '^[a-z][a-z0-9_]*$'),
    name        text NOT NULL,
    -- 'team' at launch. 'individual' is reserved for tennis/golf/UFC, which
    -- need event-shaped tables of their own rather than a bent version of these.
    kind        text NOT NULL DEFAULT 'team' CHECK (kind IN ('team', 'individual'))
);

CREATE TABLE league (
    league_id     text PRIMARY KEY CHECK (league_id ~ '^[a-z][a-z0-9_]*$'),
    sport_id      text NOT NULL REFERENCES sport,
    name          text NOT NULL,
    level         text NOT NULL CHECK (level IN ('pro', 'college')),
    first_season  int,
    last_season   int,            -- NULL = still active (AFL = 1969)
    regulation_periods smallint,  -- 4 quarters, 9 innings, 3 periods, 2 halves
    CHECK (last_season IS NULL OR first_season IS NULL OR last_season >= first_season)
);

CREATE TABLE source (
    source_id   text PRIMARY KEY CHECK (source_id ~ '^[a-z][a-z0-9_]*$'),
    name        text NOT NULL,
    url         text,
    -- 'backfill' = static files (nflverse, Retrosheet); 'delta' = live APIs
    -- (ESPN, NHL, CFBD); 'curated' = hand-maintained tables (pre-1999 champions).
    lane        text NOT NULL CHECK (lane IN ('backfill', 'delta', 'curated')),
    license     text NOT NULL,          -- 'unstated' is an honest value; NULL is not
    attribution text,                   -- the line we must carry, if any
    notes       text
);

-- One row per load. Lets every fact row say which batch wrote it, and makes
-- a partial backfill impossible to record as a success (see CHECK below).
CREATE TABLE ingest_run (
    ingest_run_id bigserial PRIMARY KEY,
    source_id     text NOT NULL REFERENCES source,
    league_id     text REFERENCES league,
    asset         text,               -- file / endpoint / season range loaded
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    rows_expected int CHECK (rows_expected >= 0),
    rows_written  int NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    error         text,
    -- A backfill that 404s half its seasons and reports success is the
    -- hollow-game_state.json bug again. If we knew how many to expect, a
    -- success must have written them all.
    CONSTRAINT succeeded_means_complete CHECK (
        status <> 'succeeded' OR rows_expected IS NULL OR rows_written = rows_expected),
    CONSTRAINT finished_when_done CHECK (
        (status = 'running') = (finished_at IS NULL))
);

CREATE TABLE season (
    season_id   bigserial PRIMARY KEY,
    league_id   text NOT NULL REFERENCES league,
    year        int  NOT NULL CHECK (year BETWEEN 1850 AND 2200),  -- year the season STARTS
    label       text NOT NULL,     -- '2024' (NFL, MLB) or '2024-25' (NHL, NBA, NCAAMB)
    start_date  date,
    end_date    date,
    -- The league's rules THAT season, as data, so readers never branch on
    -- league_id. They are per season because they change: the NHL had ties
    -- until 2004-05 and only split out OT losses from 1999-00; the NFL had no
    -- regular-season overtime before 1974.
    ties_possible boolean NOT NULL DEFAULT false,
    has_ot_losses boolean NOT NULL DEFAULT false,
    UNIQUE (league_id, year),
    UNIQUE (season_id, league_id),  -- target for league-consistency FKs
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

-- Conferences, divisions, AL/NL — any depth, any era. Realignment is a new
-- row with a new season range, never an UPDATE of an old one.
CREATE TABLE league_group (
    group_id        bigserial PRIMARY KEY,
    league_id       text NOT NULL REFERENCES league,
    parent_group_id bigint REFERENCES league_group,
    kind            text NOT NULL CHECK (kind IN ('conference', 'division', 'league', 'other')),
    name            text NOT NULL,
    abbrev          text,
    first_season    int,
    last_season     int,
    UNIQUE (group_id, league_id),
    CHECK (last_season IS NULL OR first_season IS NULL OR last_season >= first_season)
);

-- ---------------------------------------------------------------------------
-- Identity: franchises, teams, players, and every source's ID for them
-- ---------------------------------------------------------------------------

-- The continuity across relocation and rename. A franchise can cross leagues
-- (AFL -> NFL in 1970, ABA -> NBA, WHA -> NHL), so it carries no league of
-- its own: each `team` era does.
CREATE TABLE franchise (
    franchise_id bigserial PRIMARY KEY,
    name         text NOT NULL,       -- current/last name, for humans only
    notes        text
);

-- One identity ERA of a franchise: Houston Oilers (AFL), Houston Oilers (NFL),
-- Tennessee Oilers, Tennessee Titans are four rows, one franchise.
CREATE TABLE team (
    team_id      bigserial PRIMARY KEY,
    franchise_id bigint NOT NULL REFERENCES franchise,
    league_id    text   NOT NULL REFERENCES league,
    location     text   NOT NULL,     -- 'Tennessee', 'Kansas City', 'Ohio State'
    nickname     text,                -- NULL for colleges known by school name only
    full_name    text   NOT NULL,
    abbrev       text,                -- OUR abbreviation; sources' live in team_xref
    first_season int    NOT NULL,
    last_season  int,                 -- NULL = current identity
    UNIQUE (team_id, league_id),
    CHECK (last_season IS NULL OR last_season >= first_season)
);
CREATE INDEX team_franchise_idx ON team (franchise_id);

-- Membership of a team in a season, and its division that year. This is the
-- backbone: rosters, games, standings, stats and champions all reference it,
-- so a fact cannot name a team for a season (or a league) it did not play in.
CREATE TABLE team_season (
    team_id    bigint NOT NULL,
    season_id  bigint NOT NULL,
    league_id  text   NOT NULL,
    group_id   bigint,            -- the lowest-level group (division) that year
    PRIMARY KEY (team_id, season_id),
    FOREIGN KEY (team_id, league_id)   REFERENCES team (team_id, league_id),
    FOREIGN KEY (season_id, league_id) REFERENCES season (season_id, league_id),
    FOREIGN KEY (group_id, league_id)  REFERENCES league_group (group_id, league_id)
);
CREATE INDEX team_season_season_idx ON team_season (season_id);

-- Source abbreviations are reused over time (nflverse 'LA' is the Rams now;
-- 'STL' was the Rams 1999-2015; ESPN calls them 'LAR'). So an xref is valid
-- for a SEASON RANGE, and two ranges for the same source key may not overlap.
CREATE TABLE team_xref (
    source_id   text      NOT NULL REFERENCES source,
    external_id text      NOT NULL,
    team_id     bigint    NOT NULL REFERENCES team,
    seasons     int4range NOT NULL DEFAULT '(,)',   -- '[1999,2016)' etc.; unbounded = always
    EXCLUDE USING gist (source_id WITH =, external_id WITH =, seasons WITH &&)
);
CREATE INDEX team_xref_team_idx ON team_xref (team_id);

CREATE TABLE player (
    player_id   bigserial PRIMARY KEY,
    full_name   text NOT NULL,
    first_name  text,
    last_name   text,
    birth_date  date,
    debut_year  int,
    notes       text
);
CREATE INDEX player_name_idx ON player (lower(last_name), lower(first_name));

-- nflverse gsis_id, ESPN athlete id, Retrosheet id, Lahman playerID, ...
CREATE TABLE player_xref (
    source_id   text   NOT NULL REFERENCES source,
    external_id text   NOT NULL,
    player_id   bigint NOT NULL REFERENCES player,
    PRIMARY KEY (source_id, external_id)
);
CREATE INDEX player_xref_player_idx ON player_xref (player_id);

-- ---------------------------------------------------------------------------
-- Facts. Every table from here down carries the same four provenance columns.
-- ---------------------------------------------------------------------------

-- R9: which team, which season. Season-level; a traded player has two rows.
CREATE TABLE roster_entry (
    player_id     bigint NOT NULL REFERENCES player,
    team_id       bigint NOT NULL,
    season_id     bigint NOT NULL,
    position      text,          -- the sport's own vocabulary ('QB', 'SS', 'C')
    jersey_number text,          -- text: '00' is not 0
    status        text,          -- source's status ('ACT', 'RES', 'IR', ...)
    first_date    date,          -- when known; NULL for season-only sources
    last_date     date,
    source_id     text NOT NULL REFERENCES source,
    source_ref    text,
    ingest_run_id bigint REFERENCES ingest_run,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, team_id, season_id),
    FOREIGN KEY (team_id, season_id) REFERENCES team_season
);
CREATE INDEX roster_team_season_idx ON roster_entry (team_id, season_id);

-- Postseason structure. A single-game NFL round is a best-of-1 series, so
-- one model covers the Super Bowl and a seven-game Stanley Cup Final.
CREATE TABLE postseason_round (
    round_id    bigserial PRIMARY KEY,
    season_id   bigint NOT NULL REFERENCES season,
    name        text   NOT NULL,        -- 'Wild Card', 'Super Bowl', 'World Series'
    round_order smallint NOT NULL CHECK (round_order >= 1),   -- 1 = first round
    best_of     smallint CHECK (best_of >= 1),                -- NULL = unknown/mixed
    is_final    boolean NOT NULL DEFAULT false,               -- the title round
    UNIQUE (season_id, name),
    UNIQUE (round_id, season_id)
);

CREATE TABLE postseason_series (
    series_id      bigserial PRIMARY KEY,
    round_id       bigint NOT NULL,
    season_id      bigint NOT NULL,
    team_a_id      bigint NOT NULL,     -- higher seed where known
    team_b_id      bigint NOT NULL,
    team_a_wins    smallint CHECK (team_a_wins >= 0),
    team_b_wins    smallint CHECK (team_b_wins >= 0),
    winner_team_id bigint,
    source_id      text NOT NULL REFERENCES source,
    source_ref     text,
    ingest_run_id  bigint REFERENCES ingest_run,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    -- A series, its round, its teams and (below) its games are all one season.
    UNIQUE (series_id, season_id),
    FOREIGN KEY (round_id, season_id)  REFERENCES postseason_round (round_id, season_id),
    FOREIGN KEY (team_a_id, season_id) REFERENCES team_season,
    FOREIGN KEY (team_b_id, season_id) REFERENCES team_season,
    CHECK (team_a_id <> team_b_id),
    CHECK (winner_team_id IS NULL OR winner_team_id IN (team_a_id, team_b_id))
);

CREATE TABLE game (
    game_id           bigserial PRIMARY KEY,
    league_id         text   NOT NULL,
    season_id         bigint NOT NULL,
    season_type       text   NOT NULL
                      CHECK (season_type IN ('preseason', 'regular', 'play_in', 'postseason', 'exhibition')),
    game_date         date   NOT NULL,       -- local date; start_time when known
    start_time        timestamptz,
    week              smallint,              -- NFL/CFB week; NULL where the sport has none
    game_number       smallint NOT NULL DEFAULT 1,   -- doubleheaders
    home_team_id      bigint NOT NULL,
    away_team_id      bigint NOT NULL,
    neutral_site      boolean NOT NULL DEFAULT false,
    venue             text,
    status            text   NOT NULL DEFAULT 'scheduled'
                      CHECK (status IN ('scheduled', 'in_progress', 'final',
                                        'postponed', 'suspended', 'cancelled', 'forfeit')),
    home_score        smallint CHECK (home_score >= 0),
    away_score        smallint CHECK (away_score >= 0),
    decided_in        text CHECK (decided_in IN ('regulation', 'overtime', 'shootout')),
    periods_played    smallint CHECK (periods_played >= 1),   -- innings/quarters/periods incl. OT
    series_id         bigint,
    series_game_number smallint,
    attendance        int CHECK (attendance >= 0),
    -- Derived, never loaded, so a source can't hand us a winner that
    -- contradicts the score.
    winner_team_id    bigint GENERATED ALWAYS AS (
        CASE WHEN status IN ('final', 'forfeit') AND home_score > away_score THEN home_team_id
             WHEN status IN ('final', 'forfeit') AND away_score > home_score THEN away_team_id
        END) STORED,
    is_tie            boolean GENERATED ALWAYS AS (
        status = 'final' AND home_score = away_score) STORED,
    source_id         text NOT NULL REFERENCES source,
    source_ref        text,
    ingest_run_id     bigint REFERENCES ingest_run,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (season_id, league_id)    REFERENCES season (season_id, league_id),
    FOREIGN KEY (home_team_id, season_id) REFERENCES team_season,
    FOREIGN KEY (away_team_id, season_id) REFERENCES team_season,
    FOREIGN KEY (series_id, season_id)    REFERENCES postseason_series (series_id, season_id),
    CHECK (home_team_id <> away_team_id),
    CONSTRAINT final_has_score CHECK (
        status NOT IN ('final', 'forfeit') OR (home_score IS NOT NULL AND away_score IS NOT NULL)),
    CONSTRAINT decided_only_when_final CHECK (
        decided_in IS NULL OR status IN ('final', 'forfeit')),
    CONSTRAINT series_game_needs_series CHECK (
        series_game_number IS NULL OR series_id IS NOT NULL),
    UNIQUE (league_id, game_date, home_team_id, away_team_id, game_number)
);
CREATE INDEX game_season_idx ON game (season_id, season_type);
CREATE INDEX game_home_idx   ON game (home_team_id, season_id);
CREATE INDEX game_away_idx   ON game (away_team_id, season_id);
CREATE INDEX game_date_idx   ON game (game_date);

-- A final tie must be one the season's rules allow. Enforced by trigger
-- rather than trusted: a tie in the NBA is a loader bug, not a result.
-- (Tests the scores directly: generated columns are not yet computed when a
-- BEFORE trigger runs, so NEW.is_tie would read NULL here.)
CREATE FUNCTION game_tie_allowed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'final' AND NEW.home_score = NEW.away_score AND NOT
       (SELECT ties_possible FROM season WHERE season_id = NEW.season_id) THEN
        RAISE EXCEPTION 'game %: % season % does not allow ties (% - %)',
            NEW.game_id, NEW.league_id, NEW.season_id, NEW.home_score, NEW.away_score
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER game_tie_allowed BEFORE INSERT OR UPDATE ON game
    FOR EACH ROW EXECUTE FUNCTION game_tie_allowed();

CREATE TABLE game_xref (
    source_id   text   NOT NULL REFERENCES source,
    external_id text   NOT NULL,        -- nflverse game_id, ESPN event id, Retrosheet game id
    game_id     bigint NOT NULL REFERENCES game ON DELETE CASCADE,
    PRIMARY KEY (source_id, external_id)
);
CREATE INDEX game_xref_game_idx ON game_xref (game_id);

-- Linescore: quarters, innings, periods — `period` beyond the league's
-- regulation_periods is overtime / extra innings.
CREATE TABLE game_period_score (
    game_id  bigint   NOT NULL REFERENCES game ON DELETE CASCADE,
    team_id  bigint   NOT NULL REFERENCES team,
    period   smallint NOT NULL CHECK (period >= 1),
    score    smallint NOT NULL CHECK (score >= 0),
    PRIMARY KEY (game_id, team_id, period)
);

CREATE FUNCTION period_score_team_in_game() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM game g WHERE g.game_id = NEW.game_id
                   AND NEW.team_id IN (g.home_team_id, g.away_team_id)) THEN
        RAISE EXCEPTION 'team % did not play in game %', NEW.team_id, NEW.game_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER period_score_team_in_game BEFORE INSERT OR UPDATE ON game_period_score
    FOR EACH ROW EXECUTE FUNCTION period_score_team_in_game();

-- R4: a standings snapshot as a source published it. `is_final` marks the
-- end-of-regular-season row. v_team_season_record (below) computes the same
-- record from games, so the two can be checked against each other.
CREATE TABLE standing (
    team_id         bigint NOT NULL,
    season_id       bigint NOT NULL,
    as_of_date      date   NOT NULL,
    is_final        boolean NOT NULL DEFAULT false,
    wins            smallint NOT NULL CHECK (wins >= 0),
    losses          smallint NOT NULL CHECK (losses >= 0),
    ties            smallint CHECK (ties >= 0),
    ot_losses       smallint CHECK (ot_losses >= 0),
    win_pct         numeric(5,4) CHECK (win_pct BETWEEN 0 AND 1),
    points          smallint,                 -- NHL points
    points_for      int,
    points_against  int,
    games_back      numeric(5,1),
    group_rank      smallint,                 -- within the team_season's division
    conference_rank smallint,
    league_rank     smallint,
    streak          text,                     -- 'W3'
    clinched        text,                     -- source's clinch code ('z', 'y', 'x', '*')
    extra           jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(extra) = 'object'),
    source_id       text NOT NULL REFERENCES source,
    source_ref      text,
    ingest_run_id   bigint REFERENCES ingest_run,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season_id, as_of_date),
    FOREIGN KEY (team_id, season_id) REFERENCES team_season
);
-- At most one final row per team-season.
CREATE UNIQUE INDEX standing_one_final ON standing (team_id, season_id) WHERE is_final;

-- R2: champions, including seasons with no game rows at all (the ~106
-- curated pre-1999 NFL/AFL titles). `title` separates the NFL Championship
-- from the AFL Championship, and the AP from the Coaches poll title in CFB.
CREATE TABLE champion (
    champion_id        bigserial PRIMARY KEY,
    league_id          text   NOT NULL,
    season_id          bigint NOT NULL,
    title              text   NOT NULL,    -- 'Super Bowl LIX', 'Stanley Cup', 'AP national title'
    team_id            bigint NOT NULL,
    runner_up_team_id  bigint,
    series_id          bigint,
    source_id          text NOT NULL REFERENCES source,
    source_ref         text,
    ingest_run_id      bigint REFERENCES ingest_run,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league_id, season_id, title),
    FOREIGN KEY (season_id, league_id)         REFERENCES season (season_id, league_id),
    FOREIGN KEY (team_id, season_id)           REFERENCES team_season,
    FOREIGN KEY (runner_up_team_id, season_id) REFERENCES team_season,
    FOREIGN KEY (series_id, season_id)         REFERENCES postseason_series (series_id, season_id),
    CHECK (runner_up_team_id IS NULL OR runner_up_team_id <> team_id)
);

-- ---------------------------------------------------------------------------
-- Season-level stats (R8) and careers (R14).
--
-- Stats are a JSONB object per row, not a column per stat: nflverse's player
-- season file alone has 148 columns, MLB's differ entirely, and a new sport
-- must not need a migration. What keeps that from becoming a junk drawer is
-- the registry below plus v_unknown_stat_keys, which lists any key a loader
-- wrote that nobody registered. Loaders must keep it empty.
-- ---------------------------------------------------------------------------

CREATE TABLE stat_definition (
    sport_id    text NOT NULL REFERENCES sport,
    scope       text NOT NULL CHECK (scope IN ('player', 'team')),
    stat_key    text NOT NULL CHECK (stat_key ~ '^[a-z][a-z0-9_]*$'),
    label       text NOT NULL,
    category    text,          -- 'passing', 'batting', 'goaltending'
    -- How a career (or multi-team season) combines it. 'sum' stats get a
    -- career total in v_player_career_stat; rates must be recomputed from
    -- their components, never averaged.
    aggregation text NOT NULL CHECK (aggregation IN ('sum', 'max', 'min', 'rate', 'none')),
    unit        text,
    description text,
    PRIMARY KEY (sport_id, scope, stat_key)
);

CREATE TABLE player_season_stat (
    player_id     bigint NOT NULL REFERENCES player,
    team_id       bigint NOT NULL,   -- one row PER TEAM; no source 'TOT' rows
    season_id     bigint NOT NULL,
    season_type   text   NOT NULL CHECK (season_type IN ('regular', 'postseason')),
    stats         jsonb  NOT NULL CHECK (jsonb_typeof(stats) = 'object'),
    source_id     text NOT NULL REFERENCES source,
    source_ref    text,
    ingest_run_id bigint REFERENCES ingest_run,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, team_id, season_id, season_type),
    FOREIGN KEY (team_id, season_id) REFERENCES team_season
);
CREATE INDEX player_season_stat_season_idx ON player_season_stat (season_id);

CREATE TABLE team_season_stat (
    team_id       bigint NOT NULL,
    season_id     bigint NOT NULL,
    season_type   text   NOT NULL CHECK (season_type IN ('regular', 'postseason')),
    stats         jsonb  NOT NULL CHECK (jsonb_typeof(stats) = 'object'),
    source_id     text NOT NULL REFERENCES source,
    source_ref    text,
    ingest_run_id bigint REFERENCES ingest_run,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season_id, season_type),
    FOREIGN KEY (team_id, season_id) REFERENCES team_season
);

-- ---------------------------------------------------------------------------
-- Views: the questions the writers actually ask
-- ---------------------------------------------------------------------------

-- Every team season with its identity era and its franchise. The join most
-- queries start from.
CREATE VIEW v_team_season AS
SELECT ts.team_id, ts.season_id, ts.league_id, s.year, s.label AS season_label,
       t.full_name, t.abbrev, t.franchise_id, f.name AS franchise_name,
       ts.group_id, g.name AS group_name
FROM team_season ts
JOIN team t      ON t.team_id = ts.team_id
JOIN franchise f ON f.franchise_id = t.franchise_id
JOIN season s    ON s.season_id = ts.season_id
LEFT JOIN league_group g ON g.group_id = ts.group_id;

-- Historical records computed from game results (R1 -> R4), independent of
-- any standings feed. Regular season only; OT losses split out where that
-- season's rules have them. A season with standings but no games simply has
-- no row here; use `standing` for those.
CREATE VIEW v_team_season_record AS
WITH sides AS (
    SELECT g.league_id, g.season_id, g.home_team_id AS team_id, g.home_score AS pf,
           g.away_score AS pa, g.winner_team_id, g.is_tie, g.decided_in
    FROM game g WHERE g.status IN ('final', 'forfeit') AND g.season_type = 'regular'
    UNION ALL
    SELECT g.league_id, g.season_id, g.away_team_id, g.away_score,
           g.home_score, g.winner_team_id, g.is_tie, g.decided_in
    FROM game g WHERE g.status IN ('final', 'forfeit') AND g.season_type = 'regular'
)
SELECT s.team_id, s.season_id, s.league_id,
       count(*)                                                      AS games,
       count(*) FILTER (WHERE s.winner_team_id = s.team_id)          AS wins,
       count(*) FILTER (WHERE s.winner_team_id <> s.team_id
                        AND NOT (sn.has_ot_losses AND s.decided_in IN ('overtime', 'shootout')))
                                                                     AS losses,
       count(*) FILTER (WHERE s.is_tie)                              AS ties,
       count(*) FILTER (WHERE sn.has_ot_losses AND s.winner_team_id <> s.team_id
                        AND s.decided_in IN ('overtime', 'shootout')) AS ot_losses,
       sum(s.pf) AS points_for, sum(s.pa) AS points_against
FROM sides s JOIN season sn ON sn.season_id = s.season_id
GROUP BY s.team_id, s.season_id, s.league_id;

-- Titles by franchise, across every name it has played under: "the Titans'
-- franchise has 2 titles" counts the 1960 and 1961 AFL Houston Oilers.
CREATE VIEW v_champion AS
SELECT c.champion_id, c.league_id, s.year, s.label AS season_label, c.title,
       c.team_id, t.full_name AS team_name, t.franchise_id, f.name AS franchise_name,
       c.runner_up_team_id, ru.full_name AS runner_up_name, c.source_id
FROM champion c
JOIN season s    ON s.season_id = c.season_id
JOIN team t      ON t.team_id = c.team_id
JOIN franchise f ON f.franchise_id = t.franchise_id
LEFT JOIN team ru ON ru.team_id = c.runner_up_team_id;

-- R9: a player's team history, with the franchise so "his third stint with
-- the franchise" survives a relocation.
CREATE VIEW v_player_team_history AS
SELECT r.player_id, p.full_name AS player_name, s.league_id, s.year, s.label AS season_label,
       r.team_id, t.full_name AS team_name, t.franchise_id, r.position, r.source_id
FROM roster_entry r
JOIN player p ON p.player_id = r.player_id
JOIN season s ON s.season_id = r.season_id
JOIN team t   ON t.team_id = r.team_id;

-- R14: career totals for every 'sum' stat, regular season, per league
-- (a two-sport athlete's NFL and MLB careers stay separate).
CREATE VIEW v_player_career_stat AS
SELECT pss.player_id, s.league_id, kv.key AS stat_key,
       sum(kv.value::text::numeric)     AS career_total,
       count(DISTINCT pss.season_id)    AS seasons,
       min(s.year) AS first_year, max(s.year) AS last_year
FROM player_season_stat pss
JOIN season s  ON s.season_id = pss.season_id
JOIN league l  ON l.league_id = s.league_id
CROSS JOIN LATERAL jsonb_each(pss.stats) kv
JOIN stat_definition d ON d.sport_id = l.sport_id AND d.scope = 'player'
                      AND d.stat_key = kv.key AND d.aggregation = 'sum'
WHERE pss.season_type = 'regular' AND jsonb_typeof(kv.value) = 'number'
GROUP BY pss.player_id, s.league_id, kv.key;

-- Keys written by a loader that no stat_definition registers. Must be empty;
-- tests and loaders check it rather than trusting the mapping.
CREATE VIEW v_unknown_stat_keys AS
SELECT DISTINCT 'player' AS scope, l.sport_id, kv.key AS stat_key, pss.source_id
FROM player_season_stat pss
JOIN season s ON s.season_id = pss.season_id
JOIN league l ON l.league_id = s.league_id
CROSS JOIN LATERAL jsonb_object_keys(pss.stats) kv(key)
WHERE NOT EXISTS (SELECT 1 FROM stat_definition d
                  WHERE d.sport_id = l.sport_id AND d.scope = 'player' AND d.stat_key = kv.key)
UNION
SELECT DISTINCT 'team', l.sport_id, kv.key, tss.source_id
FROM team_season_stat tss
JOIN season s ON s.season_id = tss.season_id
JOIN league l ON l.league_id = s.league_id
CROSS JOIN LATERAL jsonb_object_keys(tss.stats) kv(key)
WHERE NOT EXISTS (SELECT 1 FROM stat_definition d
                  WHERE d.sport_id = l.sport_id AND d.scope = 'team' AND d.stat_key = kv.key);
