# The sport fact-bundle pattern (SLA-118)

Football was the pilot (SLA-114 → 115, 116, 119, 120, 117, 130, October 2026). The writer gets
**one connected bundle of verified facts per game**, and Pass 3 **checks every claim of the
kinds the bundle states**. This doc is what the next sport (MLB, NBA, NHL) has to provide so the
pattern is copied, not re-invented. It is written from what the pilot built and measured, not
from the proposal. Read it before opening the first ticket for a new sport.

| Piece | Football's version | Ticket |
|---|---|---|
| Lookup views in slap-sports-db | `v_team_game`, `v_head_to_head`, `v_poll_asof` | SLA-115 |
| One assembly file | `football_bundle.py` → `game_state["football"]` | SLA-116 |
| Shown to every pass, deduplicated | `runner_common.format_game_state_summary`, `attach_story` | SLA-119 |
| Database lag handled | ESPN's post-game record as the cross-check | SLA-120 |
| Checker | `claim_validator` Check 3C (rank, record, standings) + 3B kinds (upset, playoff win) | SLA-117, SLA-130 |
| Replay harness | `uat/replay_football_claims.py` | SLA-117 |

## 1. The database: views, and what they taught

**The three views are not football views.** `v_team_game` (one row per team per final game,
with the record going in), `v_head_to_head` (per franchise pair) and `v_poll_asof` (poll ranks
with the dates each was current) are built on the shared `game` table, for every league. A new
sport mostly needs its **games stored and kept current**, not new views:

- **History back far enough to say something.** NFL games start in 1999 here, MLB 1876, NHL
  1917-18, NBA 1946-47, college football 1869. Every count must state its depth ("since at
  least 1999"), never imply it.
- **The current season, live.** NFL, MLB, NHL and NBA are fed every six hours (`feed.yml`);
  college football once a day (the CFBD ration). Know your sport's lag at 06:17 UTC: see §2's
  stale rule.
- **Postseason games**, if the sport's claims need them. Football's bowls are not stored
  (deferred to next season), so every college count says "regular season". MLB/NBA/NHL hold
  champions and runners-up but not every playoff game (SLA-77): no "last playoff series win".

What the football views taught, in order of how much time each cost:

1. **Name the league and year in every `v_team_game` read** (or a list of franchises). A filter
   on `game_date` alone doesn't push below the window: ~7 s on MLB, versus ~40 ms.
2. **Read `game` as home/away halves (UNION ALL), never `team IN (home, away)`.** The join
   form scanned every game and timed out on MLB (SLA-108).
3. **Fence LATERALs with `OFFSET 0`** where the planner reorders badly: `v_poll_asof` went from
   23 s to 0.25 s, and a test fails if the fence is removed.
4. **When SQL is slow, fetch once and compute in Python.** "Last win over a top-10 team" as SQL
   took 56 s for 20 teams; one fetch of the AP ranges plus Python is instant.
5. **One query per slate, not per game.** An 80-game Saturday crosses the pooler 80 times
   otherwise. Series lookups are one LATERAL query per slate (2.3 s for 80 pairs).

## 2. The assembly file

One self-contained file per sport family, beside `champions_source.py` and `history_source.py`,
writing one key of `game_state.json` at fetch time. Its interface is what the rest of the
pipeline relies on; copy it:

| Function | Contract |
|---|---|
| `fetch_bundles(game_state, url=None)` | Never raises. No URL or no database → `{"status": "unavailable", "reason": ...}` with the password scrubbed; the run goes on. |
| `render(game_state, story=None, budget=...)` | `(lines, {game_id: "full" / "core" / "result"})`, under a hard character budget. |
| `summary_lines(...)` | The block as text: `render()[0]`. |
| `shown(game_state)` | Game ids the block shows at any size, and the (sport, team) pairs shown in full, so `format_game_state_summary` drops their duplicate results line and HISTORICAL CONTEXT line. |
| `attach_story(game_state, story_plan)` | Called by **both runners** after Pass 1: stores the plan's story text in memory so later passes put story games first. Never raises, returns one log line. |
| `claim_facts(game_state)` | Per team in yesterday's games, the facts Pass 3 checks, as **data**, not sentences. |

Each bundle carries its facts twice: as **lines** for the writer and as **structured fields** on
each team (ranks, records, `standing`, `facts` with `kind` / `last` / `floor`) for the checker.
Never make the checker parse the writer's sentences back. SLA-109 had to for history, and every
sentence template then needed a round-trip test.

The rules every bundle follows:

- **Game selection and priority.** Story games first, matched with SLA-111's conservative
  matcher plus every pro team's name (so "Pittsburgh" in a Steelers story isn't Pitt), then the
  sport's marquee games (football: upsets, ranked matchups, every NFL game), then one result line
  for the rest. Each game gets the richest size that fits: full bundle, then without history,
  then result only. What doesn't fit is **counted** in the last line, never silently dropped.
- **A hard budget.** Football's is 10,000 characters (Abram's decision): the block goes into
  every pass's prompt. Measure the heaviest day of the season before choosing a number.
- **Stale = unknown, but check before giving up (SLA-120).** A game not yet in the database
  gets nothing this season's rows would supply. That blanked every Saturday college game on
  Sunday mornings (54 of 54 on 2026-10-04). ESPN's scoreboard carries each team's record **after**
  a final: when the stored record before the game equals ESPN's record minus the result, every
  earlier game is stored, and everything dated before the game is trusted. Same check, per team,
  in `history_source.catch_up()`, which otherwise showed "4 straight wins" beside a loss.
- **As of the issue's date, in ET.** Nothing on or after the game's date counts as history; a
  Thursday-night NFL game is the ET date, not UTC's.
- **Depth stated, unknowns dropped.** "Since at least 1999 (game data starts 1999)". A poll week
  that was skipped (2001, 2020) makes an upset fact unknowable, so it is dropped, never "first since".

Wiring a new block in: add it to `format_game_state_summary` (with `shown()` dedup), call its
`attach_story` in **both** `generate_newsletter.py` and `uat/run_uat.py` beside
`extend_for_stories`, and re-pin `main` in `test_runner_drift.py`'s `KNOWN_DIVERGENT`.

## 3. The checker

Two routes, by claim shape:

- **Check 3C** (`check_football_claims`): claims about the game just played. Rank, record going
  in and after, standings, conference record.
- **Check 3B** (`check_history_claims`): anything "first since YEAR" / "N-year". Add new kinds
  to `_claim_kinds` and feed structured facts in through `_with_football_facts()`. Then the
  decision table, `_verdict` and the final re-check apply unchanged.

The caution rules are the same for every kind, and they are the point:

| Outcome | When |
|---|---|
| confirmed, nothing added | any source agrees (ESPN, the database, any poll) |
| `FACT FLAG [HIGH]`, quoting the data | the claim **attaches to one team** and that team's fact plainly disagrees |
| `FACT FLAG [LOW]`: cut the number | anything else about yesterday's teams: two teams named, none named, data missing, a forecast ("now No. 5") |
| nothing | a team that didn't play yesterday (a preview); a player; a score |

"Attaches to one team" is decided by **grammar**, not by counting names: a rank attaches to the
team written right after it, a parenthetical record to the team right before it. A verb-cued
claim ("improved to 4-1") attaches only when the sentence names a single team. A team the section
names but the sentence doesn't can confirm a claim but never convict one. SLA-112's final re-check
reports what the editor left in, labelled by kind. A contradicted one makes the run PARTIAL. A
tweet in the same section carrying the same number sources it.

**Claim kinds a pro sport will need**, and where football's code will not carry over:

| Kind | Football | MLB / NBA / NHL notes |
|---|---|---|
| Rank | AP / CFP, college only | None. Pro "No. 1 seed" / "No. 2 pick" must never match (they don't: a rank needs a team after it). |
| Record | `_RECORD_RE` skips any "W-L" summing over 20 as a score | **This filter is football's.** MLB's 90-70 must not be dropped as a score; use a sport-aware bound and the cue words. NHL records are W-L-OTL; check `has_ot_losses` seasons. |
| Standings | NFL division place, games back, `shares_top` | MLB "games back" / "magic number" / wild card; NHL points, not games back. "Leads" must count a tie at the top (the Raiders, 2026-09-27). |
| Upsets | top-5 / top-10 / ranked buckets; the bucket must agree | Rarely meaningful in pro sports. |
| Playoff | last playoff win (NFL only: the only league with stored playoff games) | Needs playoff games stored first (SLA-77). |
| History kinds | streaks, starts, droughts, head-to-head (SLA-108/109) | Already exist for every league. |

Two traps the pilot hit that every sport will:

- **Names.** College teams are matched by the database's **school** name ("Florida" is the Gators,
  never the start of "Florida Atlantic"). A nickname another league shares (Giants, Cardinals,
  Panthers, Jets, Kings, Rangers...) is never alone the team: "the Giants are two games back" in
  September is baseball. `other_league_nicknames()` reads them from the bundle's name pool.
- **Sentence splitting.** Split with `claim_validator._sentences()`, never a bare `[.!?]` regex:
  the old splitter cut "No. 4 Ole Miss" in two and hid every rank claim.

Prompts change in step: RULE 3 item 5/6 and editor Check 8 name the new block as a source; Check 9
says how to fix each new flag. Edit UAT first, `uat/promote.py` for `rolling_feedback.txt`. Edit
`editor_prompt.txt` by hand in both copies, because it is deliberately UAT-ahead.

## 4. The replay, before anything ships

A ticket that adds a claim kind is not done until it has been replayed over every archived
issue of the sport's season, with **every HIGH read by hand**. A HIGH that's actually right is a
bug.

`uat/replay_football_claims.py` is the template. It rebuilds each issue's previous-day games from
ESPN's scoreboard, whose ranks and records are as of that game, not today (verified: Florida 4-0
after 09-26, Bengals 1-0 after week 1). It rebuilds the bundles from the live database, cuts the
season log at each day for standings, and runs the checks over that issue's archived final draft.
Scoreboards are cached under `uat/output/replay_cache/`. It needs network and the database, makes
no Claude calls, and is a tool, not a test. Count confirmed / HIGH / LOW per kind. Also print the
confirmed ones: a claim that should have been seen and wasn't is how the splitter bug surfaced.

Expect the archive to be thin. Read the real sentences the parser finds, and put the variety in
the offline tests: real fixtures from a real day, plus a wrong-number variant of every claim
shape. Then watch the first live weeks.

## 5. What the football pilot measured

| Measure | Result |
|---|---|
| Heaviest Saturday block | 9,936 chars (2026-09-12, 80 games: all 20 ranked kept, 13 full bundles) |
| Heaviest Sunday block | 9,764 chars (2026-09-20, 14 NFL games: 9 full, 5 result lines) |
| Weekdays | 1-4K chars; ~2 s per build |
| Ground truth per pass, with the block (2026-10-03) | 16.8K → 22.3K chars (~1.4K tokens) |
| Saturday games trusted on Sunday morning | 0 of 54 before SLA-120, 51 of 54 after (the 3 have FCS opponents) |
| Football claims in a month of issues (09-05 → 10-04) | 5: 3 rank, 1 record, 1 standings; all confirmed, **0 HIGH, 0 LOW** |
| History replay (SLA-109, 146 issues) | 4 confirmed, 0 HIGH, 102 LOW |

**Bugs the replays found that the unit tests had not,** the best argument for §4:

- School names matched pro-city stories ("Pittsburgh" → Pitt).
- 44 unranked result lines crowded story bundles out of the budget.
- Upsets sorted behind routine ranked games.
- A stale playoff game said "last playoff win before this game" while missing a win from the
  same postseason.
- Every Saturday game was stale on Sunday, and history lines were a game behind and false.
- "No. 4 Ole Miss" was split at "No.", hiding every rank claim.
- "Down 20 to No. 1 Ohio State" was read as a poll forecast.
- "Tied with Kansas City atop the AFC West" wasn't read as a share of the lead.
- An upset claim was classified as a poll claim: a latent false HIGH.

## 6. Open items the next sport inherits

- **Upset and playoff-win claims are unproven on real prose.** No archived issue made one
  about the previous day's games. Playoff wins first come up in January.
- **A college conference lead is never confirmed** (LOW): we hold each team's conference record,
  not the table.
- **Replay-only artifact:** history_source's poll fact reads the latest stored poll, so a
  replay can show the following week's rank. Live runs aren't affected.
- **Bowls and the CFP** are not stored; deferred to next season (Abram, 2026-10-03).
- Only **yesterday's** games are checked; a Monday recap of a Saturday game is not.
