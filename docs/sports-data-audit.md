# Sports Data Audit — What Exists Before Building an NFL Game Results Feed

Scoped to [SLA-41](https://linear.app/slap-sports/issue/SLA-41/audit-existing-sports-data-sourcespipes-before-building-nfl-game).
Answers one question: what does SLAP already have that a current-season NFL
game-results feed (for standings computation — [SLA-42](https://linear.app/slap-sports/issue/SLA-42/build-current-season-nfl-game-results-feed-for-standings-computation))
can build on, and what's missing. Not a full historical-backfill design for
the Shared Sports Database epic.

## 1. Existing data source: `fetch_sports_data.py` (SLAP Newsletter)

Single source, ESPN's undocumented JSON API (`site.api.espn.com` /
`site.web.api.espn.com`), no API key. One `LEAGUES` config entry per sport
(`mlb`, `nba`, `nhl`, `wnba`, `nfl`, `ncaafb`, `ncaamb`, `wc`) drives a shared
fetch loop in `main()`. **NFL is already a configured league key** — it is
not a new sport being onboarded, it's a sport whose fetch is already running
daily but whose *output* is incomplete for standings purposes (see §3).

Per active sport, per day, `main()` fetches:

| Data | Function | Notes |
|---|---|---|
| Yesterday's + today's games | `fetch_scoreboard()` → `parse_game()` | date-scoped scoreboard endpoint, `?dates=YYYYMMDD` |
| Standings | `fetch_standings()` | see §3 — this is the gap |
| Box scores (completed games only) | `fetch_game_summary()` → `parse_box_score()` | dispatches per sport; NFL/CFB handled by `_parse_football_box()` |
| League leaders | `fetch_league_leaders()` | not relevant to standings |
| CFB rankings (AP/CFP poll) | `fetch_cfb_rankings()` | NFL has no equivalent poll-based fetch, and doesn't need one |

Everything lands in `game_state.json` under `sports.<key>`, fetched once
daily as part of the existing pipeline cadence (2:17 AM EDT). **No separate
cadence is needed for NFL** — it already runs inside this loop every day the
current month is in NFL's `months` window (`[9,10,11,12,1,2]`).

Retry/proxy handling (`fetch_url()`, ESPN 403 → browser UA → residential
proxy) is shared infrastructure, already applies to NFL's calls with zero
NFL-specific work.

### `parse_game()` — per-game fields already captured (all sports, incl. NFL)

```
game_id, date, matchup, home_team, home_abbr, away_team, away_abbr,
home_score, away_score, winner, loser, completed, status, overtime,
playoffs, series, home_rank, away_rank
```

This already satisfies most of SLA-42's "required fields per game" list:
season/week come from the scoreboard's date-scoped fetch (not stored on the
game dict itself, but derivable — see gaps below), home/away team + ESPN
abbreviation, home/away score, and completed-vs-in-progress status are all
present today. `home_rank`/`away_rank` exist but are CFB-oriented
(`_competitor_rank`, poll-based) — meaningless for NFL, which doesn't rank by
AP poll.

### `_parse_football_box()` — per-game box score (NFL + CFB, added 2026-09-15)

Passing/rushing/receiving stat lines per player, quarter linescore, scoring
plays (`_parse_football_scoring`). This is player-level detail for the box
score images, not team-level results — not what standings computation needs,
but confirms the football-specific parsing path is real and already
shipping (see the 2026-09-15 changelog entry in `CLAUDE.md`).

## 2. Existing data source: SLAP Fantasy repo (`../slap-fantasy`)

**Not directly reusable for this ticket.** SLAP Fantasy's NFL data layer
(`nfl_data.py`) pulls from a completely different source and shape than
ESPN's JSON API:

- Source: `nflverse-data` GitHub release assets (play-by-play and
  weekly-aggregate **parquet files**, downloaded once and cached to disk),
  plus a DynastyProcess player-ID CSV crosswalk. Loaded via `pandas`.
- Grain: play-by-play and per-player weekly stats — built for fantasy
  scoring and recap narration, not team standings. There is no team-level
  won/lost/tied result table, and no division/conference mapping anywhere
  in the repo (`grep`'d for `AFC`/`NFC`/`division`/`conference` across all
  `.py` files — the only hits are fantasy-league constructs like "conference"
  meaning a fantasy sub-league, not the real NFL AFC/NFC).
- `fetch_yahoo.py` / `fetch_sleeper.py` pull *fantasy* league/roster/matchup
  data (Yahoo/Sleeper APIs), not real NFL game results.

Standings computation needs final scores, TD/point differentials, and a
fixed 32-team division/conference map — none of which live in this repo's
grain or format. Pulling nflverse parquet data in just to get scores that
ESPN's JSON API already gives us for free would mean shipping a second stack
(pandas/parquet) for a strict subset of what `fetch_sports_data.py` already
produces. **Nothing here is reusable for SLA-42.**

## 3. The actual gap: standings has no division/conference structure for NFL

`fetch_standings()` in `fetch_sports_data.py` has two code paths:

- **MLB** (`sport_key == "mlb"`): `_extract_divisions()` walks ESPN's
  conference→division tree and returns `{division_name: [team, ...]}`,
  preserving grouping. This is the shape `box_score/build_box_score.py`
  renders (`render_mlb_standings_html`, `_mi_std_table`) — division labels,
  trimmed to `Team/W/L/Pct/GB/Strk` for the 400px-wide mobile image.
- **Everything else, including NFL**: `_extract_divisions()` is still called
  internally, but its per-division results are immediately flattened —
  `all_teams = [t for v in divisions.values() for t in v]` — sorted by win
  percentage, and returned as one flat list with **no division or conference
  field on the team dict at all** (`_parse_entries()`'s output has no
  `division`/`conference` key; the division name only exists as the dict key
  during the walk, then gets discarded).

So today, NFL standings in `game_state.json` are a flat 32-team list sorted
by win %, with **no AFC/NFC or East/North/South/West grouping preserved**,
and no DIV/CONF-record fields either (`_parse_entries()`'s stat aliasing
covers `home_record`/`away_record`/`last_ten` but has no division-record or
conference-record alias set — ESPN's standings entries do carry these under
names like `vs. Div.` / `vs. Conf.`, they're just never asked for).

This is exactly the gap SLA-43 flags with "Conference is top-level, division
is second-level" and DIV/CONF columns, and exactly what SLA-42's "Division
and conference for each team (fixed 32-team mapping)" field is asking
`fetch_sports_data.py` to add. **Nothing renders NFL standings today either**
— `box_score/build_box_score.py` has `_mi_simple_standings()` (flat, for
NBA/NHL/WNBA) and `_mi_rankings()` (AP poll, replacing standings for CFB),
but `_render_football_sections()` only emits game box scores
(passing/rushing/receiving/scoring), never a standings table.

## 4. What's directly reusable for NFL

- **Fetch/retry/proxy plumbing** (`fetch_url`, `_fetch_via_proxy`,
  `FETCH_HEALTH`) — sport-agnostic, zero new work.
- **Scoreboard → `parse_game()`** — already gives per-game home/away score,
  completed flag, ESPN abbreviations, and season/date context for NFL. Needs
  only: (a) explicit season-type filtering to exclude preseason (see gap
  below), (b) a week number, which the scoreboard payload carries but
  `parse_game()` doesn't currently pull out.
- **Standings fetch plumbing** (`fetch_standings()` → ESPN's
  `/standings` endpoints, `_drill_for_entries`/`_extract_divisions` tree
  walk) — the walk already groups by division; it just needs to *keep* that
  grouping for non-MLB sports instead of flattening it, and to carry the
  division/group's parent conference name up too (MLB's `_extract_divisions`
  doesn't preserve conference either, since MLB's own box score only needs
  AL/NL prefixes on preset division names — NFL's ticket wants real
  conference-then-division nesting, one level deeper than MLB currently
  needs).
- **Mobile-rendering pattern to mirror** (per SLA-43's explicit ask): not a
  CSS breakpoint — the box score images render at a **fixed 400px width**
  (`MOBILE_IMG_WIDTH` in `build_box_score.py`) via a headless-browser
  screenshot, so "responsive" here means *column-priority trimming into a
  hardcoded fixed-width table*, not a media query. MLB's pattern
  (`_mi_std_table`) trims to exactly `Team/W/L/Pct/GB/Strk`. SLA-43's 9-column
  priority list is the same technique applied to a wider NFL column set.

## 5. What's new / not reusable

- A fixed 32-team NFL division/conference mapping (doesn't exist anywhere in
  either repo today — needs to be added, e.g. as a static dict, since it's
  realignment-stable and doesn't need a live fetch).
- Preseason exclusion — `parse_game()` has no season-type field at all today;
  ESPN's scoreboard payload does carry season-type, it's just never read.
- Week number extraction from the scoreboard payload into the flat game dict.
- Division/conference-record fields (`vs. Div.` / `vs. Conf.` ESPN stat
  names) in `_parse_entries()`'s stat-name aliasing.
- Any NFL standings *rendering* — `_render_football_sections()` has no
  standings table today; SLA-43 is building that from scratch, informed by
  the MLB column-cut pattern above, not by any existing NFL rendering code
  (there isn't any).
- Real head-to-head/common-games/SOV/SOS tiebreaker computation — nothing in
  either repo computes NFL's multi-step tiebreaker procedure; ESPN's
  standings payload may already resolve ties server-side (worth confirming
  once SLA-42's feed exists — if ESPN's own ordering already applies the
  official tiebreaker, SLA-43 may not need to reimplement it from raw game
  results at all, only from-scratch it if ESPN's stat ordering turns out to
  be approximate).

## Bottom line for SLA-42

Build the NFL game-results feed as an **extension of `fetch_sports_data.py`**,
not a new pipeline: reuse `fetch_url`/proxy handling and the scoreboard fetch
as-is, extend `parse_game()` with week + season-type (regular-season-only)
filtering, and extend `fetch_standings()`/`_extract_divisions()` to preserve
conference→division grouping (and division/conference records) for NFL
instead of flattening it — rather than building any of this fresh. SLAP
Fantasy's data layer is a dead end for this specific need; it solves a
different problem (fantasy scoring/recap) on a different stack
(nflverse parquet, no division map) and has nothing standings-shaped to
borrow.
