# Sports Data Source Evaluation — One Recommendation Per Sport

Scoped to [SLA-58](https://linear.app/slap-sports/issue/SLA-58/source-evaluation-spike-pick-best-data-source-per-sport).
Blocks [SLA-5](https://linear.app/slap-sports/issue/SLA-5) (repo + schema),
[SLA-6](https://linear.app/slap-sports/issue/SLA-6) (backfill phase 1),
[SLA-59](https://linear.app/slap-sports/issue/SLA-59) (backfill phase 2),
[SLA-60](https://linear.app/slap-sports/issue/SLA-60) (rosters) and
[SLA-7](https://linear.app/slap-sports/issue/SLA-7) (recurring feed).

This is an evaluation, not an implementation. Nothing here has been built.

Builds on [SLA-41](https://linear.app/slap-sports/issue/SLA-41)'s
[`docs/sports-data-audit.md`](sports-data-audit.md), which covers what SLAP
already runs. That audit is not repeated; this doc picks up where it stops.

---

## 0. How this was verified, and where it wasn't

Three findings in this doc are **measured** — files were downloaded and their
contents counted. Everything else is documentation- or vendor-stated and is
labelled as such. The distinction matters, because the single most
consequential finding here (§5.1) contradicts an assumption written into
SLA-6 and SLA-59, and it would have been easy to restate that assumption
instead of checking it.

**What I could measure.** This session's egress proxy allows
`github.com` release-asset downloads and `raw.githubusercontent.com` for
nflverse. So nflverse — the one source the ticket names — was measured
directly: asset existence probed by HTTP status, and two files downloaded and
parsed.

**What I could not measure.** Every live sports API is denied by the egress
policy at CONNECT time (HTTP 403 from the gateway, `curl` reports 000):

```
statsapi.mlb.com          api-web.nhle.com
api.collegefootballdata.com    site.api.espn.com    stats.nba.com
```

`api.github.com` is scope-limited to this session's repos, and
`raw.githubusercontent.com` and the release-asset host are allowlisted to
**nflverse only** — verified by control: `sportsdataverse/hoopR-mbb-data`,
`hoopR-nba-data` and `cfbfastR-data` assets all 404 while an nflverse asset
fetched in the same batch returns 200. So a probe of, say,
`JeffSackmann/tennis_atp` returns 404 whether or not the file exists.
**No claim in this doc rests on one of those 404s.** Where a source could not
be probed, its row says "documented, unverified".

The fetch service is blocked for the same hosts, so there is no second route
and no indirect one. The three checks this leaves open are tracked in §6 with
a runnable script attached to the ticket.

That limitation is itself a finding, not just an inconvenience — see §5.2.

---

## 1. Step 1 — What the writers actually consume

Read from the live prompt and runner files in both repos, not assumed.

### 1.1 SLAP Newsletter

**The ground-truth block is thin.** `runner_common.format_game_state_summary()`
(`runner_common.py:171–226`) is the entire factual payload Pass 1, Pass 2 and
Pass 6 receive. Per completed game it emits exactly:

```
{away_team} {away_score}, {home_team} {home_score} (OT) — {winner} wins
    PLAYOFFS — {series summary}. Next: Game {n}. {team} eliminated.
```

That is yesterday's games and playoff series state. Nothing else. No records,
no season context, no history.

**`claim_validator.py` (Pass 3) polices four claim types** — game numbers,
series scores, elimination language, series-over language — plus
"defending champion" (`claim_validator.py:65`), which it can only flag,
never resolve, because no source in the pipeline knows who won last year's
title.

**The prompts want far more than the pipeline can source.** This is the gap
the Shared Sports Database exists to close, and it is stated most plainly in
`prompts/rolling_feedback.txt` RULE 3:

> Numerical historical claims — "X-year drought," "first since YYYY," "most
> since Z," "longest streak in N years" — are the #1 source of factual errors
> in this newsletter. The model fills them from training memory under pressure
> and gets them wrong.

RULE 3's canonical failure is the 6/1/2026 lead conflating the Knicks'
53-year *championship* drought with their 27-year *appearance* drought. The
rule's remedy is rule 3.3: when the number can't be verified, **write it
vague**. Editor Check 8 enforces that by downgrading specifics to relative
framing.

So today the newsletter deliberately writes worse prose than it wants to,
because it has no source. Meanwhile `prompts/pass1_story_selector.txt`
(lines ~638–670) instructs the selector to build exactly these angles:

- "Rank this drought against comparable droughts in major North American
  sports. Give the reader a top 5."
- Dominance streak endings: "name the players, name the years, name what it
  took to end it."
- A coach who won a title as a player with the same franchise.

**Every one of those is a cross-sport, full-history query.** That is the
product requirement, and it is the requirement that decides this evaluation:
a source covering the last 20 seasons does not answer "longest drought in
major North American sports."

**Two consumers need more than prose.** `nfl_standings.py` (SLA-43) needs a
full season game log with home/away, scores and division/conference
classification to run the official tiebreakers.
`box_score/build_box_score.py` needs player stat lines, linescores, scoring
summaries, standings, leaders and the CFB poll — all already served by ESPN.

### 1.2 SLAP Fantasy

Same cardinal rule, stated independently (`writer.py:~1205`):

> **(A) GROUNDING** — Every stat, score, play, projection, margin, rank, and
> points value you write MUST be copied from the fact pack for THIS week. […]
> Fabricating a stat, score, or play is the cardinal sin.

Its real-NFL needs are narrower and already met: play-by-play with scoring
events and timestamps, weekly player stats, the weekly schedule (for
opponent lookup and defense-vs-position), and two ID crosswalks —
Sleeper↔nflverse team abbreviations (`nfl_context.py:41`) and the
DynastyProcess player-ID CSV.

**Fantasy already runs on nflverse** (`nfl_data.py`), pulling release assets
by direct URL rather than through `nfl_data_py` — deliberately, because
`nfl_data_py` pins old pandas/numpy with no Python 3.14 wheels. That choice
turns out to be strategically right for reasons beyond wheels (§5.2).

### 1.3 The rubric

Must-haves are what the writers are *currently blocked on*. Nice-to-haves are
real but already served, or wanted later.

| # | Data point | Why | Priority |
|---|---|---|---|
| R1 | Game results — date, teams, final score, OT/SO flag, winner | The base fact of every recap | **MUST** |
| R2 | Postseason results + champions per season | RULE 3.4 "defending champion"; `claim_validator.py:65` can only flag it today | **MUST** |
| R3 | **Full-league-history depth** for R1/R2 | "first since YYYY", "longest in N years", cross-sport drought ranking. The discriminating requirement | **MUST** |
| R4 | Standings / season records (W-L-T, div, conf, pct) | `nfl_standings.py`; "X is 7-0" | **MUST** |
| R5 | Schedule — scheduled and in-progress | Tier 1 calendar; "Next: Game n" | **MUST** |
| R6 | Stable team identity across relocation/rename | Oilers→Titans, Sonics→Thunder, Bullets→Wizards. A drought query silently breaks without it | **MUST** |
| R7 | Stable player IDs across sources and eras | Joins season stats to rosters to results | **MUST** |
| R8 | Season-level team + player stats | SLA-59 | Nice |
| R9 | Rosters, current and historical | SLA-60 | Nice |
| R10 | Box scores / player game lines | SLA-41: already served by ESPN | Nice (served) |
| R11 | Play-by-play | Fantasy needs it for NFL only; no other sport does | Nice (NFL only) |
| R12 | Awards, leaders, draft | Colour | Nice |
| R13 | Betting lines, weather, venue, tracking | Nothing consumes these | Out of scope |

R3 and R6 are the two the naive answer ("just use ESPN") fails, and they are
why this ticket exists.

---

## 2. Step 2/3 — Per-sport evaluation

### 2.1 NFL — **nflverse primary, ESPN for the daily delta, pre-1999 unsolved**

Measured, not claimed. Every row below is an HTTP status against
`github.com/nflverse/nflverse-data/releases/download/…` on 2026-09-19:

| Dataset | Earliest | Verified how |
|---|---|---|
| `schedules/games.parquet` | **1999** | Downloaded: 7,548 rows, 46 cols, seasons 1999–2026 contiguous, 27 Super Bowls |
| `pbp/play_by_play_YYYY` | **1999** | 1999 ✅ 200 · 1990 ❌ 404 · 2026 ✅ 200 |
| `stats_player/stats_player_reg_YYYY` | **1999** | 1999 ✅ · 1980 ❌ · 1970 ❌ |
| `stats_team/stats_team_reg_YYYY` | **1999** | 1999 ✅ · 1970 ❌ |
| `rosters/roster_YYYY` | **1920** | Downloaded `roster_1920.parquet`: 369 players, 30 cols, 14 teams (AKR, CAN, DEC, …) |
| `weekly_rosters/roster_weekly_YYYY` | **2002** | 2002 ✅ · 2025 ✅ |
| `draft_picks`, `players`, `combine`, `contracts`, `officials`, `injuries` (2009+), `depth_charts` (2001+), `snap_counts` (2012+), `ftn_charting` (2022+), `ngs_*`, `pfr_advstats` | varies | all ✅ 200 |

`games.parquet` carries what R1/R4/R5 need:
`game_id, season, game_type (REG/WC/DIV/CON/SB), week, gameday, away_team,
away_score, home_team, home_score, result, overtime, div_game, location`
plus QBs, coaches, referee, stadium and betting lines. 7,293 of 7,548 rows
have scores; the 255 nulls are 2026 fixtures not yet played. It is
byte-identical in content to Lee Sharpe's `nflverse/nfldata` `games.csv`
(also downloaded: same 7,548 rows, same range) — so the 1999 floor is
structural to nflverse, not an artifact of one asset.

**Recommendation.** nflverse primary for 1999–present: it satisfies R1–R5 and
R8–R11 in a handful of static Parquet files, it is CC-BY 4.0 (FTN data
CC-BY-SA 4.0), and SLAP Fantasy already runs it in production. ESPN stays as
the same-day delta — nflverse publishes on a batch cadence, ESPN is live, and
SLAP's daily pipeline already consumes it. **Pre-1999 is not solved by
nflverse and needs its own decision — see §5.1.**

**Two live defects found while verifying.**

1. **`nflverse-data`'s `player_stats` release is deprecated** — the release
   page reads `DEPRECATED 2025-08-01: USE stats_player OR stats_team INSTEAD`.
   Measured: `player_stats/stats_player_week_2024.parquet` → 200, but
   `…_2025.parquet` → **404**. The deprecated release simply stopped being
   written after 2024.
2. **SLAP Fantasy points at that dead asset.** `nfl_data.py:40` sets
   `_WEEKLY_URL = …/player_stats/stats_player_week_{season}.parquet`. For
   2025+ it 404s. **Severity: latent, not live** — `ensure_weekly()` and
   `load_weekly()` are defined but called from nowhere outside `nfl_data.py`
   (grepped across the repo), and `nfl_context.py` reconstructs fantasy points
   from PBP rather than from weekly aggregates. So nothing is broken today;
   the first caller to use it would break. Fix is one line —
   `stats_player/stats_player_week_{season}.parquet`. Not fixed here: this is
   an evaluation spike, it's a different repo, and it wants its own ticket.

`nfl_data_py` is deprecated in favour of **`nflreadpy`** (Polars-based).
Fantasy's decision to bypass the wrapper and fetch assets directly remains
sound and is now also the more portable choice.

### 2.2 MLB — **Retrosheet + Lahman for history, MLB Stats API for live**

The best-served sport in the set, and the only one with a clean commercial
licence.

| Source | Coverage | Licence | Status |
|---|---|---|---|
| **Retrosheet game logs** | **1871–present**, every game: date, score, teams, linescore, W/L pitcher, attendance, umpires | Free, commercial use **explicitly permitted** with a prominent attribution line | documented |
| **Lahman database** | **1871–present** season-level batting/pitching/fielding, standings, postseason, managers — 27 tables | CC BY-SA 3.0 (share-alike) | documented |
| **MLB Stats API** (`statsapi.mlb.com`) | Live scores, standings, box scores, rosters; deep history | **Free for "individual, non-commercial, and non-bulk use"; commercial *or bulk* needs written MLBAM authorization** | documented, unverified |
| **pybaseball** | Wrapper over Baseball-Reference / FanGraphs / Statcast / Retrosheet | mixed; see §5.3 on Sports-Reference | documented |

**Recommendation.** Retrosheet primary for R1–R3 (1871 satisfies R3
outright), Lahman for R4/R8, Chadwick Bureau register for R7. MLB Stats API
for the daily delta only — its terms carve out *bulk* use as well as
commercial, and a historical backfill is bulk by definition, so it must not
be the backfill source. ESPN remains the incumbent for box score images.
Retrosheet's licence is the most permissive of any source evaluated, and its
attribution string must be carried into anything published.

### 2.3 NHL — **official API primary; deep history needs a second source**

`api-web.nhle.com` and `api.nhle.com/stats/rest` replaced the retired
`statsapi.web.nhl.com`. No key, no registration, documented by the community
(`Zmalski/NHL-API-Reference`, `dword4/nhlapi`). Season-parameterised
(`20242025`), so results and standings can be walked back by season —
**how far back is documented but unverified**, and is the single check to run
first (§6).

`hockeyR-data` (`danmorse314/hockeyR-data`) publishes scraped PBP nightly but
only back to **2010-11**, because the NHL's JSON source has no detailed PBP
before that. It fails R3 badly and is PBP-only — which nothing in SLAP needs
for hockey (R11 is NFL-only). Not recommended.

**Recommendation.** NHL official API primary. Its licensing posture is
unstated, which is a risk of the same shape as ESPN's (§5.3). If the season
walk-back proves shallow, the fallback for champions and season records is a
small curated table — the Stanley Cup has ~107 winners, which is a
one-afternoon CSV, not an integration.

### 2.4 NBA — **viable, but the IP-blocking risk is the real story**

`stats.nba.com` via `nba_api` is the deepest free NBA source; coverage
nominally reaches 1946-47 but is **sparse and inconsistent for the pre-1983
era** (documented, unverified).

**The operational risk is severe and specific.** `stats.nba.com`:
- blocks cloud datacenter IP ranges — **AWS, GCP and Azure all reported
  blocked**; requests hang rather than erroring cleanly, and
- sits behind Akamai bot protection that rejects on **TLS fingerprint**, so a
  browser User-Agent is not enough; `curl-impersonate`-class tooling is
  needed.

**GitHub Actions runs on Azure.** This is the ESPN-403 problem SLAP already
paid for (CI blocked 8/14–9/4, fixed with `PROXY_URL` + `curl_cffi`), one
layer worse — ESPN answered 403, this silently hangs. The existing residential
proxy plus `curl_cffi` is likely the same remedy, which is a point in favour:
the workaround is already built and running in this repo.

`hoopR`/`hoopR-nba-data` publishes bulk ESPN-derived NBA data **2002–present**
as release assets — the nflverse access pattern, and the right *shape*, but it
fails R3 for a league founded in 1946.

**Recommendation.** `nba_api` primary, behind the existing proxy, run as a
one-time backfill rather than a daily dependency; `hoopR-nba-data` as the
2002+ bulk fallback and as the low-risk way to get moving. Budget real time
for the blocking. Champions/Finals results are again small enough to curate
if the deep query proves unreliable.

### 2.5 NCAA football — **CollegeFootballData (CFBD), with a quota to plan around**

CFBD is the purpose-built project the ticket hypothesises, and it exists:
games, box scores, rosters, stats, rankings, recruiting, betting, drives and
play-by-play, back to the sport's origins. v2 of the REST API is GA. Free key
at `collegefootballdata.com/key`; `cfbfastR` (R) and `CFBD/cbbd-r` wrap it.

**The constraint is the quota**, enforced per key with suspension for overrun.
Tiers, per the maintainer's own published tier docs:

| Tier | Calls / month |
|---|---|
| Free | 1,000 |
| Student / academic (`.edu` signup) | 3,000 |
| Patreon T1 — **$1/mo** | 5,000 |
| Patreon T2 — $5/mo | 30,000 |
| Patreon T3 — $10/mo | 75,000 + GraphQL |

**⚠ CFBD and CBBD share one monthly pool on the same key.** NCAAF and NCAAMB
are not two independent budgets — a backfill of one starves the other. Plan
them together.

A full historical backfill paginated by season will blow through 1,000 calls,
so **either take a paid tier for the backfill month or spread it across
months**. The cost is trivial — $1 buys 5x headroom — and materially cheaper
than first estimated. The recurring feed (SLA-7) fits inside the free tier
comfortably at a handful of calls a day.

**Tier differences are about call volume, not historical depth** — no tier
doc mentions gating years. Not yet confirmed against the API itself; it's
check 3 in §6.

**Recommendation.** CFBD primary. ESPN stays for the CFB poll and the
ranked-matchup box score filter already shipping. Decide the tier before
SLA-6-equivalent CFB work starts, not during it.

### 2.6 NCAA men's basketball — **CollegeBasketballData (CBBD)**

CBBD is CFBD's sibling, same team, same shape: games, play-by-play,
substitutions, team/player stats, lineups, ratings, rankings, betting,
recruiting, transfer portal, NBA draft. Free Bearer-token key. Wrapped by
`hoopR`'s `cbbd_*()` functions (full v1 surface) and `cbbreadr`.

**Historical depth is documented but unverified**, and unlike CFB I found no
statement anywhere of how far back games go — not on the site, not in the
wrappers, not in the maintainer's posts. That is the largest remaining
unknown in this evaluation and it is check 2 in §6. Alternatives:
`hoopR-mbb-data` (ESPN-derived, 2002+), Bart Torvik (2008+, advanced
metrics), KenPom (paid).

**Recommendation.** CBBD primary — **conditional on that check**. If games
reach back to the early 2000s, NCAAMB is a Tier A sport. If coverage starts
around 2013 (as several commercial NCAAMB feeds do), CBBD fails rubric R3 the
same way `hoopR-mbb-data` does, and NCAAMB either needs a deeper source or
gets consciously scoped to the modern era. **Note the shared CFBD/CBBD call
pool (§2.5)** — this is one key and one budget across both college sports,
not two.

### 2.7 Men's tennis — **Sackmann is the right data and the wrong licence**

The ticket guesses tennis has no clean analogue. It half does:
**Jeff Sackmann's `tennis_atp`** is a genuine nflverse-equivalent —
ATP match results back to **1968** (the Open era, i.e. R3 satisfied for the
era that matters), plus `atp_rankings_*` and `atp_players.csv`, updated
through the current season; ATP rankings publish weekly on Mondays. Siblings
cover Grand Slam point-by-point and the Match Charting Project. There is no
public ATP API; Sackmann is the field's de facto standard.

**The blocker is licensing: CC BY-NC-SA 4.0 — NonCommercial.** SLAP publishes
to Substack. If SLAP is now or ever becomes monetised (paid subscriptions,
sponsorship), building it on NC-licensed data is a real problem, and
ShareAlike raises a second question about a derived database. **This is a
decision for Abram, not an engineering call** (§6, Q1).

Tennis also fits the schema badly: no teams, no standings, no seasons in the
league sense. R4 and R6 don't apply; R1 becomes match-not-game.

**Recommendation.** Data-wise Sackmann, unreservedly. **Gate on the licence
question before building anything.** If NC is disqualifying, the fallback is
ESPN's tennis coverage for current results plus a curated majors-winners table
— which is most of what the newsletter actually cites about tennis anyway.

### 2.8 Men's golf — **the genuinely hard one; recommend deferring**

The ticket's suspicion is correct: **there is no free, maintained,
nflverse-equivalent for golf.** What exists:

- **DataGolf** — the serious option. Historical PGA Tour data 2004–2026,
  strokes-gained archive, documented API. **Paid** (~$30/mo tier).
  Starts at 2004, so R3 is not satisfied at any price.
- **SportsDataIO** — commercial, free trial only.
- **A scatter of GitHub scrapers** (`bradklassen/Professional_Golf_Database`,
  `codyheiser/pga-data`, `zachgoll/pga-tour-stats`, `holtonma/carl_spackler`)
  — mostly unmaintained, most stopping between 2016 and 2018, all scraping
  pgatour.com, whose stats site is now a React/GraphQL app those scrapers
  predate. Treat as reference, not as a dependency.
- **ESPN** — leaderboards for live events; already wired into SLAP.

Golf's shape fights the schema hardest: no teams, no standings, no fixtures —
a tournament is a field of 150 players over four rounds with a cut.

**Recommendation. Defer golf.** What SLAP's prompts actually cite about golf
is narrow — `pass1_story_selector.txt` names "final round Sunday of any major:
The Masters, PGA Championship, US Open, The Open Championship" as a Tier 1
lead trigger. That is **four events a year and a winners table**, not an
ingestion pipeline. A curated majors-champions CSV (all four majors, full
history, ~600 rows) satisfies essentially every golf claim the newsletter
makes, at a fraction of the cost of integrating DataGolf. Revisit only if
golf coverage deepens.

---

## 3. Step 3 summary — one table

| Sport | Primary | Fallback | History (R3) | R1 | R2 | R4 | R8 | R9 | Update | Top risk |
|---|---|---|---|---|---|---|---|---|---|---|
| **NFL** | nflverse | ESPN (live) | **1999** ⚠ (rosters 1920) | ✅ | ✅ | ✅ | ✅ | ✅ | batch + ESPN live | pre-1999 gap |
| **MLB** | Retrosheet + Lahman | MLB Stats API (live only) | **1871** ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | annual + ESPN live | Lahman share-alike |
| **NHL** | NHL official API | curated champions table | unverified ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ | live | undocumented API |
| **NBA** | `nba_api` (proxied) | `hoopR-nba-data` (2002+) | 1946, sparse pre-1983 ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ | live | **datacenter IP block** |
| **NCAAF** | CFBD | ESPN (poll, live) | deep ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | live | quota: 1k/mo free, shared with CBBD |
| **NCAAMB** | CBBD | `hoopR-mbb-data` (2002+) | unverified ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ | live | depth unconfirmed — the one open check that can move a tier |
| **Tennis** | Sackmann `tennis_atp` | ESPN + curated majors | **1968** ✅ | ✅ | ✅ | n/a | ✅ | n/a | weekly | **NC licence** |
| **Golf** | *defer* — curated majors CSV | DataGolf (paid, 2004+) | **none free** ❌ | ◐ | ✅ | n/a | ❌ | n/a | manual | no viable free source |

---

## 4. Framework for adding a sport later (WNBA, UFC, …)

The eight evaluations above all ran the same five questions. Reuse them
rather than re-deriving an approach:

1. **Does a purpose-built open project exist?** (nflverse / Retrosheet /
   Sackmann / CFBD class.) If yes it almost always wins — static files, no
   key, no bot wall, and a community that fixes upstream changes.
2. **What is its earliest season, measured?** Probe the oldest asset; don't
   read the README. R3 is where sources fail quietly.
3. **What is the licence, and does NonCommercial or ShareAlike appear in it?**
4. **Is it fetched from a static host or a live API?** Static wins for
   backfill (§5.2).
5. **What is the quota, and does a full backfill fit inside it?**

A sport that answers 1–5 cleanly is a week of work. A sport that fails 1 and 3
is golf, and the answer is to curate a small table instead.

---

## 5. Step 4 — Cross-cutting conclusions

### 5.1 ⚠ nflverse stops at 1999, and that changes SLA-6 and SLA-59

**This is the finding to act on.** Measured three independent ways: games,
play-by-play and season stats all start at **1999**. Only rosters (1920),
draft picks and a few reference tables go deeper.

SLA-6 is titled *"Historical backfill phase 1: full NFL history — results,
playoffs, champions"* and SLA-59 *"season-level NFL team & player stats (all
history)"*. **nflverse cannot deliver either as scoped.** It covers 1999–2026;
the NFL began in 1920. That is 79 missing seasons, including every
pre-merger championship, both Super Bowl dynasties of the 70s, and the
1958 title game.

This is not a small gap for SLAP specifically, because §1.3 R3 is driven by
drought-and-streak prose, and droughts are long. "Their first title since
1957" is precisely the sentence RULE 3 exists to stop the model inventing —
and 1999+ data cannot source it either.

**Recommended re-phasing (needs Abram's call):**

- **Split SLA-6 in two.** *6a, 1999–present*: essentially free — one 520 KB
  Parquet file, already downloaded and parsed in the course of writing this.
  Days, not weeks. *6b, 1920–1998*: a real project needing its own source
  decision, and the place where the schedule risk actually lives.
- **Same split for SLA-59**, same 1999 boundary, same reason.
- **SLA-60 (rosters) is unaffected and is the pleasant surprise** —
  `roster_1920.parquet` is real (369 players across Akron, Canton, Decatur
  and 11 others). The ticket's stated depth is correct as written.

**Candidate sources for 1920–1998, none free of friction:** Pro-Football-
Reference has it all but its terms forbid this use (§5.3); Wikipedia/Wikidata
season tables are permissively licensed but need parsing and validation; a
curated champions-only table is cheap and covers R2 but not R1. My
recommendation is to scope 6b as *champions and postseason results first*
(small, high-value, most of the prose benefit) and treat full pre-1999
regular-season results as a separate, later question.

### 5.2 It is a patchwork — but there is one architectural pattern

**Answer to the ticket's question: no single source is viable.** Eight sports
produced six different primaries. Don't design SLA-5's schema around one
provider's shape.

But the patchwork has a spine worth building on. **The sources that work are
the ones that publish static files; the ones that hurt are the ones you call
live.**

That was demonstrated accidentally and forcefully while writing this doc.
From a locked-down network where `statsapi.mlb.com`, `api-web.nhle.com`,
`api.collegefootballdata.com`, `stats.nba.com` and `site.api.espn.com` were
**all unreachable**, nflverse Parquet files downloaded fine, and real analysis
got done. That is the same failure SLAP has already lived through twice:
ESPN 403ing GitHub Actions for three weeks (8/14–9/4, fixed with a
residential proxy), and `stats.nba.com` silently hanging on Azure IPs.

**So SLA-5 should assume a two-lane design:**

- **Backfill lane** — static files from GitHub releases and archive hosts
  (nflverse, Retrosheet, Lahman, Sackmann, hoopR-data). Cacheable,
  reproducible, no bot wall, survives a hostile network.
- **Delta lane** — live APIs for today's games (ESPN today; CFBD, CBBD, NHL,
  MLB Stats API later). Proxy-aware from day one, because two of them already
  need it.

**Concrete schema implications for SLA-5:**

1. **Provenance is a column, not a comment.** Every row needs source and
   as-of, because facts arrive from different sources per sport and per era,
   and a 1994 NFL result will come from somewhere other than a 2024 one.
2. **Internal surrogate IDs for teams and players (R6/R7).** Sources disagree
   — `nfl_context.py:41` already carries a five-entry Sleeper↔nflverse
   abbreviation patch (`LAR→LA`, `OAK→LV`, `SD→LAC`, `STL→LA`, `WSH→WAS`), and
   that is *one* mapping between *two* sources for *one* sport. Franchise
   continuity across relocation is a first-class modelling problem, not a
   lookup table bolted on later — a drought query that doesn't know the Oilers
   became the Titans returns a wrong number confidently, which is exactly the
   RULE 3 failure mode the database is supposed to eliminate.
3. **Not every sport has teams, standings or seasons.** Tennis and golf are
   player-and-event shaped. Either model events generically from the start or
   consciously scope the database to team sports at launch (I'd suggest the
   latter — see §5.4).
4. **Pin upstream asset names and alert on 404.** The deprecated
   `player_stats` release (§2.1) is the cautionary tale: an upstream rename
   turned into a silent 404 that nobody noticed because the caller was
   dormant. A backfill that 404s half its seasons and reports success is the
   same class of failure as this repo's own hollow-`game_state.json` bug.

### 5.3 Licensing is a live risk, not boilerplate

Three findings that should reach whoever decides:

1. **Sports-Reference (PFR / Basketball-Reference / Hockey-Reference) is out.**
   Their stated policy: *"You should not create websites or tools based on
   data you scrape from Sports Reference or use their data to train generative
   artificial intelligence models without permission."* Feeding scraped
   Sports-Reference data to an LLM writer is the prohibited use, named
   explicitly. This is worth stating plainly because PFR is otherwise the
   obvious answer to §5.1's pre-1999 gap. Rate limits (20 req/min, 10 for
   FBref) are the lesser issue.
2. **MLB Stats API excludes *bulk* as well as commercial use** without written
   MLBAM authorization. A backfill is bulk. Hence Retrosheet primary.
3. **Sackmann tennis is NonCommercial** (CC BY-NC-SA 4.0), and
   **Lahman is ShareAlike** (CC BY-SA 3.0) — the latter raising a question
   about a derived database that mixes it with other sources.

**ESPN, the source SLAP runs on today, has no stated licence at all.** It is
an undocumented internal API used at the sufferance of a company that has
already IP-blocked us once. That is not an argument to stop using it — it is
an argument for §5.2's two-lane design, so that ESPN going away costs us
today's scores and not the archive.

### 5.4 Which sports are worth it at launch

| Tier | Sports | Why |
|---|---|---|
| **A — launch** | NFL, MLB, NHL, NCAAF | Purpose-built or official sources, permissive-or-workable licences, team-shaped. NFL and MLB are the deepest and are what the newsletter leads with most often |
| **B — fast follow** | NBA, NCAAMB | Sources exist; NBA needs the proxy work, NCAAMB needs its depth confirmed. Neither is a research problem |
| **C — defer** | Men's tennis, men's golf | Tennis is blocked on a licence question, not on data. Golf has no viable free source at any depth. Both fit the schema badly, and both are a handful of events a year in the newsletter |

Deferring C is not dropping coverage. Tier 1 golf and tennis moments still
lead the newsletter — they just keep sourcing from ESPN and tweets, as today,
plus a small curated champions table. **The cost of forcing tennis and golf
into the launch schema is that they distort the schema for the six sports
that actually fit it.**

### 5.5 Effect on SLA-7 (recurring feed)

Mostly unchanged, with three notes: the feed is **per-sport, not one job** —
nflverse is a batch republish, ESPN/NHL/CFBD/CBBD are live, Retrosheet is
roughly annual; the free-tier quotas (CFBD 1k/month) are comfortable for a
daily delta but must not be shared with a backfill; and it should **verify
what it fetched**, in the spirit of this repo's own `verify_run.py` and
`check_game_state.py`, because every failure mode found in this evaluation
was silent — a 404, a hang, or a hollow file.

---

## 6. Open questions for Abram — please answer before SLA-5 starts

1. **Is SLAP commercial, now or intended?** This decides tennis
   (CC BY-NC-SA) and constrains Lahman (ShareAlike) and MLB Stats API
   (non-bulk). It is the one answer I cannot derive from the codebase.
2. **How much does pre-1999 NFL actually matter?** §5.1. If champions and
   postseason results are enough, 6b is small. If full regular-season results
   back to 1920 are wanted, that is the largest single piece of work in the
   epic and PFR — the obvious source — is off the table (§5.3).
3. **Team sports only at launch?** I recommend yes (§5.4). It simplifies
   SLA-5's schema materially.
4. **Budget for a CFBD/CBBD paid tier during the backfill month?**
   **$1/mo** buys 5,000 calls and $5 buys 30,000 — cheaper than first
   estimated, and it removes the quota problem outright. Remember it is
   **one shared pool across both college sports** (§2.5).

### The three outstanding checks

Both routes out of the cloud sandbox are closed by egress policy — direct
`curl` and the fetch service alike return 403 at CONNECT for every live
sports API, and GitHub release assets are allowlisted to nflverse only, so
there is no indirect route either. These three therefore remain open. A
stdlib-only script that answers all three in about 12 API calls is attached
to the SLA-58 ticket; it needs a normal network and two free keys.

| # | Check | Status | If it comes back badly |
|---|---|---|---|
| 1 | NHL: how far back do standings and results actually go? | **open** | Fall back to a curated champions table (~107 rows). Does not change the primary recommendation |
| 2 | CBBD: earliest season with games? | **open — the one that can move a recommendation** | NCAAMB drops from Tier A to modern-era-only, or needs a deeper source |
| 3 | CFBD: is history tier-gated, or only call volume? | **largely closed** — published tiers differ on volume only, no year gating documented; unconfirmed against the API | Take the $1 tier and re-scope the backfill |

Check 2 is the one worth running before SLA-5 locks, because it is the only
one whose answer changes a sport's tier.

---

## 7. Hold

Per the ticket's hold instruction, this is an evaluation only. **No repo has
been stood up, no schema designed, no ingestion written.** The two defects
found in passing (§2.1) are reported, not fixed. Awaiting review before any
of SLA-5/6/7/59/60 proceeds.
