-- 0002_reference_data.sql — the dimensions every loader needs before it can
-- write a single fact: sports, the launch leagues, and the sources SLA-58
-- recommended, with their licences as that evaluation found them.
--
-- This is reference data, not population. Franchises, teams, seasons, games
-- and players arrive through the backfill tickets (SLA-6, SLA-59, SLA-60) and
-- the recurring feed (SLA-7). NFL populates first.
--
-- Licence text is what SLA-58 recorded (docs/sports-source-evaluation.md in
-- slap-newsletter). 'unstated' means the source publishes no licence, which is
-- a finding, not a gap in this file.

INSERT INTO sport (sport_id, name, kind) VALUES
    ('football',   'Football',   'team'),
    ('baseball',   'Baseball',   'team'),
    ('hockey',     'Hockey',     'team'),
    ('basketball', 'Basketball', 'team');

-- Tier A at launch: NFL, MLB, NHL, NCAAF, NCAAMB. Tier B: NBA.
-- AFL is here because pre-merger AFL champions (1960-69) are part of the
-- curated pre-1999 champions table (SLA-6b), and those teams' franchises
-- continue into the NFL — see docs/schema.md, "Franchises that change league".
INSERT INTO league (league_id, sport_id, name, level, first_season, last_season, regulation_periods) VALUES
    ('nfl',    'football',   'National Football League',           'pro',     1920, NULL, 4),
    ('afl',    'football',   'American Football League',           'pro',     1960, 1969, 4),
    ('ncaaf',  'football',   'NCAA Division I FBS Football',       'college', 1869, NULL, 4),
    ('mlb',    'baseball',   'Major League Baseball',              'pro',     1871, NULL, 9),
    ('nhl',    'hockey',     'National Hockey League',             'pro',     1917, NULL, 3),
    ('nba',    'basketball', 'National Basketball Association',    'pro',     1946, NULL, 4),
    ('ncaamb', 'basketball', 'NCAA Division I Men''s Basketball',  'college', 1938, NULL, 2);

INSERT INTO source (source_id, name, url, lane, license, attribution, notes) VALUES
    ('nflverse', 'nflverse-data releases', 'https://github.com/nflverse/nflverse-data',
     'backfill', 'CC-BY 4.0 (FTN charting data CC-BY-SA 4.0)', 'Data from nflverse (nflverse.com).',
     'NFL primary. Games/stats 1999+, rosters 1920+. Pin asset names; the deprecated player_stats release 404s for 2025+ (SLA-64).'),
    ('espn', 'ESPN site API', 'https://site.api.espn.com',
     'delta', 'unstated', NULL,
     'Same-day scores for every launch sport. Undocumented; IP-blocked GitHub Actions 8/14-9/4/2026, so proxy-aware.'),
    ('retrosheet', 'Retrosheet game logs', 'https://www.retrosheet.org',
     'backfill', 'Free use including commercial, with attribution',
     'The information used here was obtained free of charge from and is copyrighted by Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org.',
     'MLB results 1871+.'),
    ('lahman', 'Lahman Baseball Database', 'https://sabr.org/lahman-database/',
     'backfill', 'CC BY-SA 3.0', 'Lahman Baseball Database, CC BY-SA 3.0.',
     'MLB season stats 1871+. ShareAlike binds if the DATABASE is ever distributed, not the newsletter prose (CLAUDE.md Known Issues).'),
    ('mlb_stats_api', 'MLB Stats API', 'https://statsapi.mlb.com',
     'delta', 'Individual, non-commercial, non-bulk use only without MLBAM authorization', NULL,
     'Live delta ONLY. A historical backfill is bulk use regardless of money.'),
    ('nhl_api', 'NHL official API', 'https://api-web.nhle.com',
     'delta', 'unstated', NULL,
     'NHL primary, history to 1917-18 (measured, SLA-63). Undocumented.'),
    ('cfbd', 'CollegeFootballData.com', 'https://collegefootballdata.com',
     'delta', 'API terms; free tier 1,000 calls/month', NULL,
     'NCAAF primary, history to 1869. Call pool is SHARED with cbbd on one key.'),
    ('cbbd', 'CollegeBasketballData.com', 'https://collegebasketballdata.com',
     'delta', 'API terms; free tier shared with cfbd', NULL,
     'NCAAMB primary, games from 1949. 3,000-row response cap.'),
    ('nba_api', 'NBA stats (via nba_api)', 'https://github.com/swar/nba_api',
     'delta', 'unstated', NULL,
     'NBA primary (Tier B). stats.nba.com blocks datacenter IPs; needs the proxy.'),
    ('hoopr', 'hoopR-data releases', 'https://github.com/sportsdataverse',
     'backfill', 'see upstream', NULL,
     'NBA/NCAAMB fallback, 2002+, static files, no proxy needed.'),
    ('curated', 'SLAP curated tables', NULL,
     'curated', 'Facts compiled by SLAP; no third-party database copied', NULL,
     'Hand-maintained rows, e.g. the ~106 pre-1999 NFL/AFL champions. Never scraped from Sports-Reference (its terms forbid it, SLA-58 §5.3).');

-- A starter set of football stats so the career view has something to
-- aggregate. SLA-59 registers the full set when it maps nflverse's columns;
-- keys are SLAP's canonical names and each adapter maps its source onto them.
INSERT INTO stat_definition (sport_id, scope, stat_key, label, category, aggregation, unit) VALUES
    ('football', 'player', 'games',             'Games played',          'general',   'sum',  NULL),
    ('football', 'player', 'passing_yards',     'Passing yards',         'passing',   'sum',  'yards'),
    ('football', 'player', 'passing_tds',       'Passing touchdowns',    'passing',   'sum',  NULL),
    ('football', 'player', 'interceptions_thrown', 'Interceptions thrown', 'passing', 'sum',  NULL),
    ('football', 'player', 'completions',       'Completions',           'passing',   'sum',  NULL),
    ('football', 'player', 'attempts',          'Pass attempts',         'passing',   'sum',  NULL),
    ('football', 'player', 'rushing_yards',     'Rushing yards',         'rushing',   'sum',  'yards'),
    ('football', 'player', 'rushing_tds',       'Rushing touchdowns',    'rushing',   'sum',  NULL),
    ('football', 'player', 'receptions',        'Receptions',            'receiving', 'sum',  NULL),
    ('football', 'player', 'receiving_yards',   'Receiving yards',       'receiving', 'sum',  'yards'),
    ('football', 'player', 'receiving_tds',     'Receiving touchdowns',  'receiving', 'sum',  NULL),
    ('football', 'player', 'sacks',             'Sacks',                 'defense',   'sum',  NULL),
    ('football', 'player', 'passer_rating',     'Passer rating',         'passing',   'rate', NULL),
    ('football', 'team',   'points_for',        'Points scored',         'scoring',   'sum',  NULL),
    ('football', 'team',   'points_against',    'Points allowed',        'scoring',   'sum',  NULL),
    ('football', 'team',   'total_yards',       'Total yards',           'offense',   'sum',  'yards'),
    ('football', 'team',   'turnovers',         'Turnovers',             'offense',   'sum',  NULL);
