# Schema

The design, table by table, and the reasoning behind each choice. The source
of truth is `migrations/`; this file explains it. If the two disagree, the
migration is right and this file is a bug.

Built on SLA-58's source evaluation (`docs/sports-source-evaluation.md` in
slap-newsletter), not on any one provider. The short version of that
evaluation: **no single source covers the launch sports**. Six launch leagues
use six different primary sources, so nothing here takes its shape from
nflverse, ESPN or anyone else.

---

## The shape in one picture

```
sport ── league ── season ─────────────┐
            │         │                │
            │    league_group          │  (conferences / divisions, by era)
            │                          │
franchise ── team ── team_season ◄─────┘  ← the backbone: every fact below
               │         │                   references a (team, season) pair
          team_xref      ├── roster_entry ── player ── player_xref
                         ├── game ── game_period_score, game_xref
                         │     └── postseason_series ── postseason_round
                         ├── standing
                         ├── champion
                         ├── player_season_stat ┐
                         └── team_season_stat  ─┴── stat_definition (registry)

source ── ingest_run      (every fact row names both)
```

## What the ticket asked for, and where it lives

| Ticket scope | Tables | Views |
|---|---|---|
| Teams | `franchise`, `team`, `team_season`, `league_group`, `team_xref` | `v_team_season` |
| Rosters | `player`, `player_xref`, `roster_entry` | `v_player_team_history` |
| Games / results | `game`, `game_period_score`, `game_xref` | — |
| Standings | `standing` | `v_team_season_record` |
| Playoff / postseason results + champions | `postseason_round`, `postseason_series`, `champion` | `v_champion` |
| Historical records | season records from games and standings, titles by franchise, careers | `v_team_season_record`, `v_champion`, `v_player_career_stat` |
| Season-level stats | `player_season_stat`, `team_season_stat`, `stat_definition` | `v_player_career_stat`, `v_unknown_stat_keys` |

The SLA-58 rubric items this covers: R1 results, R2 champions, R4 records, R5
schedule (a `game` with `status = 'scheduled'`), R6 stable team identity, R7
stable player IDs, R8 season stats, R9 player–team history, R14 careers. R10
box scores are partly covered by the linescore (`game_period_score`); player
game lines, play-by-play (R11) and awards/draft (R12) are left for later
tables. They are nice-to-haves, and adding them later changes nothing that
exists now.

---

## Design decisions

### 1. Nothing is NFL-shaped

The ticket's main requirement: football first, but other sports must fit
later without a rewrite. The tests prove it by loading an MLB doubleheader
with extra innings, an NHL shootout, an 1869 college football game between
two schools with no nicknames, and an NBA season. None of them needed a
schema change.

What makes that work:

- **`sport` and `league` are dimensions on everything.** A league belongs to
  a sport, and every season, team, game and champion belongs to a league.
- **Sport-specific rules are data, not code.** Ties, OT losses and regulation
  periods are columns (see §5), so a query never needs
  `CASE WHEN league_id = 'nhl'`.
- **The columns are general.** `period` covers quarters, innings and
  periods; `week` is nullable because baseball has no weeks; `game_number`
  covers doubleheaders; `decided_in` handles `regulation`/`overtime`/
  `shootout`.
- **Stats go in JSONB with a registry instead of a column per stat** (§7).

**Out at launch, on purpose: individual sports.** SLA-58 §5.4 decided team
sports only. Tennis and golf are shaped around players and events, with no
standings and no seasons in the league sense. Forcing them into these tables
would bend the schema for the six sports that fit. `sport.kind` reserves the
value `'individual'` so they can get their own event/participant tables
later, alongside these rather than inside them. A test fails if one appears
before that design exists.

### 2. Our own IDs; every source's IDs live in `*_xref`

Every primary key is our own surrogate. Source IDs go in `team_xref`,
`player_xref` and `game_xref`. Sources disagree with each other today: SLAP
Fantasy already carries a five-entry patch just to reconcile Sleeper and
nflverse team abbreviations. Sources also change their IDs, so keying on
them would mean a migration every time one does.

**Team abbreviations get reused, so a `team_xref` row is valid for a season
range.** In nflverse, `STL` meant the Rams from 1999 to 2015, and `LA` means
the Rams from 2016 on. ESPN calls the same team `LAR`. The `seasons` column
is an `int4range`, and an exclusion constraint stops one source key from
meaning two teams in the same season.

Loaders resolve every team, player and game through the xref tables and never
by name. Matching on the nickname "Rams" is exactly how one franchise turns
into three.

### 3. A franchise is not a team

`team` is one identity **era**: a location plus a name over a range of
seasons. `franchise` is what connects those eras. The Houston Oilers (AFL),
Houston Oilers (NFL), Tennessee Oilers and Tennessee Titans are four `team`
rows and one `franchise`.

This matters most for the questions SLAP actually writes about. "First title
since…", "longest drought", "franchise record" all need the whole history.
If a query doesn't know the Oilers became the Titans, it returns a wrong
answer with full confidence. That is the RULE 3 failure the database exists
to eliminate.

**Franchises that change league.** A franchise has no league of its own;
each `team` era has one. So the 1960 AFL Houston Oilers are a team in league
`afl`, and their titles still count toward the Titans franchise
(`v_champion` by `franchise_id`). The same pattern covers ABA→NBA and
WHA→NHL when those arrive.

### 4. `team_season` is the backbone, and league mismatches can't be stored

Rosters, games (both sides), standings, champions and season stats all have a
foreign key to `team_season(team_id, season_id)`. `team_season` itself has
composite foreign keys to `team(team_id, league_id)` and
`season(season_id, league_id)`.

As a result, the database itself refuses:

- a game or roster entry for a team in a season it didn't play;
- an AFL-era team placed in an NFL season;
- a champion that wasn't in the league that season.

This follows the repo's standing rule: a rule is enforced where it can't be
skipped. A loader that tries any of these gets an error. It can't record the
bad row and carry on.

`team_season.group_id` records the team's division **that season**.
Realignment adds new `league_group` rows with new season ranges and never
edits old ones, so the 2001 AFC Central still exists for 2001.

### 5. Rules like ties and OT losses belong to the season, not the league

`season.ties_possible` and `season.has_ot_losses` hold the rules because
those rules change over time. The NHL had ties until 2003-04 and only
separated out OT losses from 1999-00. The NFL had no regular-season overtime
before 1974. MLB has had called ties. A league-level flag would be wrong for
some era of almost every league.

- A trigger refuses a final tie in a season that doesn't allow ties, since a
  tie in the NBA can only be a loader bug. (The trigger compares the scores
  directly: Postgres computes generated columns after BEFORE triggers run,
  so `is_tie` isn't available yet at that point.)
- `v_team_season_record` counts overtime and shootout losses as `ot_losses`
  only when that season has the column.

### 6. The database derives who won

`game.winner_team_id` and `game.is_tie` are generated from the score and
can't be loaded. If a source's "winner" field contradicts its own score, it
can't reach the database. Other constraints: a `final` game must have a
score, `decided_in` is only allowed on a finished game, and a team can't play
itself.

### 7. Stats: JSONB plus a registry

nflverse's player-season file alone has 148 columns, and MLB's columns have
nothing in common with it. One column per stat would mean one table per
sport, or a migration for every new sport. So each
`player_season_stat`/`team_season_stat` row holds a JSONB `stats` object.

What keeps that from becoming a junk drawer:

- **`stat_definition`** registers each key per sport and scope, with its
  label, category and, most importantly, its **aggregation**. `sum` stats
  get career totals. `rate` stats (passer rating, batting average) are never
  summed or averaged; they have to be recomputed from their parts.
- **`v_unknown_stat_keys`** lists every key a loader wrote that isn't
  registered. It must stay empty after each load, and loaders check it
  instead of trusting their own column mapping.
- **One row per player, team, season and season type.** A player traded
  midseason gets one row per team. Sources' "TOT" total rows are not stored;
  totals are computed. That keeps the per-team split, and the rows can't
  double-count.

`v_player_career_stat` provides R14 (career totals) for every `sum` stat,
counting regular season only and grouped per league. That way a two-sport
athlete's NFL and MLB careers stay separate.

Keys use SLAP's own canonical names (`passing_yards`,
`interceptions_thrown`), not a source's column names. Each adapter maps its
source onto them. Migration 0002 registers a starter football set; SLA-59
registers the rest when it maps nflverse.

### 8. Postseason: rounds, series, champions

Every playoff matchup is a `postseason_series`. A single NFL game is a
best-of-1, so the Super Bowl and a seven-game Stanley Cup Final use the same
model. `game.series_id` and `series_game_number` tie games to their series.
A check constraint stops the series winner from being anyone but one of the
two teams in it, and composite foreign keys keep a round, its series, the
series' games and a champion all in the same season.

`champion` stands on its own. It can point at a series, but it doesn't have
to. SLA-58 §5.1 depends on that: nflverse only goes back to 1999, so the
~106 pre-1999 NFL/AFL titles come from a curated table with **no game rows
behind them**. `title` separates titles that share a season: the NFL
Championship and AFL Championship before the merger, or the AP and Coaches
national titles in college football.

### 9. Provenance and honest loads

Every fact table carries `source_id` (required), `source_ref` (the file or
endpoint), `ingest_run_id` and `ingested_at`. Facts come from different
sources depending on the sport and the era. A 1994 NFL result and a 2024 one
will have different origins, and when they disagree we need to know which is
which.

`source` records each source's **licence and attribution line**, as SLA-58
found them. `unstated` is an honest value (ESPN, NHL); NULL is not allowed.
Retrosheet's attribution line is stored here so every output that uses it can
carry it. See slap-newsletter's CLAUDE.md, Known Issues, for the licensing
trigger: Lahman and MLB Stats API get re-checked if SLAP is ever monetised.

`ingest_run` has the constraint `succeeded_means_complete`. If a load said
how many rows it expected, it can't be recorded as `succeeded` with fewer. A
backfill that 404s on half its seasons and reports success is the same bug
as the newsletter's hollow `game_state.json`, and here the database refuses
it.

---

## Adding a sport

SLA-58 §4 puts the same five questions to every candidate source (is there a
purpose-built open project; how far back does it go, measured; what's the
licence; static files or a live API; does a backfill fit the quota). Once a
sport passes those, adding it takes **no migration**:

1. Add a `sport` row if the sport is new, and a `league` row.
2. Add `source` rows for its sources, with licence and attribution.
3. Register its stat keys in `stat_definition`.
4. Write an adapter (`sports_db/adapters/`) for each (source, league) pair,
   following the contract in `adapters/base.py`, and add it to
   `adapters/registry.py`. A test fails if the registry names a source or
   league that isn't seeded.
5. Load seasons, setting `ties_possible` and `has_ot_losses` to that era's
   rules.

A migration is only needed for something the model genuinely can't
represent yet. The known case is individual sports (§1).

## Changing the schema

Add a new numbered file in `migrations/`. Never edit an applied one:
`sports_db/migrate.py` records a checksum for each applied file and refuses
to continue if one changes. Line endings are normalised first, so a Windows
checkout doesn't count as a change. Each migration runs in its own
transaction, so a failure leaves the database at the last good migration.

## Sizing

SLA-58 §7.2 estimated about 270 MB of data for all six launch leagues, call
it 1 GB with indexes and headroom. NFL alone (~45 MB) fits any free tier
easily. The full set may outgrow Supabase's 500 MB free tier by the time
every sport is loaded; that is the point to choose between Neon's free tier
and a paid plan. No storage optimisation was attempted, per
that doc's advice.
