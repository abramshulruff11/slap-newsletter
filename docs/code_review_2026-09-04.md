# SLAP code quality and robustness review — 2026-09-04

Scope: every Python module on the production path, both workflows, all
production prompts, the five offline test suites (installed deps and ran them:
all pass), the last eight archived issues, and the CI logs for the daily runs
of 8/14, 8/24, 8/31, 9/2, 9/3 and 9/4 plus the 9/3 publish run.

Read Part 1 first. Those are not code-smell items; they are things that are
broken today while every CI run shows green.

## Status (updated 2026-09-04, evening)

Four of the six answers from the first read-through are now built and pushed to
`claude/newsletter-code-quality-j6ov6s`. All eight offline suites pass. Nothing
is merged to `main` yet.

| Item | State |
|---|---|
| ESPN blocked (scores + headlines) | **Fixed.** Direct call first, residential proxy on a 403. New CI health check fails loudly without blocking delivery. |
| Video tweets in written sections | **Fixed.** Tagged at fetch time, removed from every headliner section and beat, kept in Around the League. |
| Auto-publish missing 12:30 PM ET | **Fixed.** Polls every 30 minutes and publishes at the first slot past 12:30 ET; the daily run publishes immediately if it finishes late. |
| Editor VERIFY flags | **Removed.** 21.8 flags per issue down to 1.5, and every survivor is one Python computed and the editor acts on. |
| Email not sending | **Owner action.** Needs a Gmail app password in the `GMAIL_PASSWORD` secret. |
| Meme findings (Part 2.1-2.3) | Next, on Abram's instruction to finish the above first. |
| UAT runner collapse | Open. |

On pull requests: with no second reviewer a PR buys nothing here. The branch
plus offline tests plus a revertible history is the safety net. Merge straight
to `main`.

---

## Part 1 — Broken right now, silently

### 1.1 The email has not been delivered since at least August 14

Every daily run log ends with the same line, and the step exits 0:

```
✗ Email failed: (535, b'5.7.8 Username and Password not accepted ...')   # 2026-08-14
✗ Email failed: (534, b'5.7.9 Application-specific password required ...')  # 2026-08-24, 08-31, 09-02, 09-03, 09-04
```

`email_newsletter.py:send_email()` wraps `sendmail` in `try/except` and prints.
Nothing propagates. The workflow step "Email newsletter" completes in under a
second and is green. CLAUDE.md calls the email "the product".

Fix: regenerate the Gmail app password (`GMAIL_PASSWORD` secret) and make
`send_email()` re-raise (or `sys.exit(1)`) on any SMTP error. A failed
delivery must be a red run.

### 1.2 ESPN scoreboard has returned 403 on every run since at least August 14

```
✗ 403 even with browser UA: HTTP Error 403: Forbidden
  0 completed game(s) yesterday
  4 active sport(s) · 0 completed game(s) from yesterday
```

The standings and leaders endpoints still answer, so `game_state.json` is
stamped with today's date and looks healthy. It is hollow. Consequences, all
silent:

- Pass 1 and Pass 2 get no "GROUND TRUTH" block (`format_game_state_summary`
  returns "" when there are no completed games).
- Pass 3 (claim validator) validates against nothing. "✓ No claim flags
  raised" every day.
- Box score images contain standings and league leaders but no scores or box
  scores (see `archive/2026-09-04/box_score_sport_01_mlb.png`). The section is
  titled "Box Scores".
- `highlights.py` needs completed games to match clips. It has embedded zero
  clips in every run I checked.

This is the "game_state freshness — no guard" item already listed in
CLAUDE.md as open. It is the daily reality, not a hypothetical. `fetch_url`
gives up after a single browser-UA retry on 403; GitHub's datacenter IP is
what is blocked, which is the same problem that blocked Substack until it was
routed through `PROXY_URL`.

Fix: (a) a content-presence guard in the workflow that fails when an in-season
league reports zero completed games on a day that had games; (b) route the
ESPN calls through `PROXY_URL` with `curl_cffi`, exactly as `publish.py` does.

### 1.3 ESPN RSS has returned zero headlines since July 15

All six ESPN feeds answer HTTP 202 with an empty body (a bot challenge, not a
feed). Archived `raw_content.json` shows ESPN headline counts were
intermittent through June and have been 0 every day since 2026-07-15. All
headlines now come from the single CBS feed (31-36 per day). The warning line
has printed daily for seven weeks. Same fix as 1.2, or drop the ESPN feeds
and add other sources.

### 1.4 The "12:30 PM ET" auto-publish actually fires at about 3:20 PM ET

GitHub queues scheduled workflows late. Measured start times:

| Workflow | Cron (UTC) | Actual start (UTC) |
|---|---|---|
| daily-newsletter | 06:17 | 11:20 – 11:24 (9/2, 9/3, 9/4) |
| publish-substack | 16:30 | 19:20 – 19:27 (9/1, 9/2, 9/3) |

The 9/3 publish run found the draft already published by hand. So the
auto-publish is not doing the job it exists for. The publish step is
idempotent (every "nothing to do" case skips), so the cheap fix is several
cron slots (e.g. every 30 minutes from 16:00 to 20:00 UTC) plus a check in the
daily workflow that publishes immediately when the draft is created after
12:30 ET. The durable fix is triggering the publish from the draft commit
(`workflow_run` or `repository_dispatch`) rather than from a wall clock.

### 1.5 Nothing in the workflow can fail for a bad newsletter

The only gates are "raw_content has headlines or tweets" and "the two output
files exist". A run that has 0/51 handles reachable (8/23 and 8/24 shipped
headline-only issues), 0 completed games, an email that never sent, and 54
editor flags is green. Recommend one `verify_run.py` step after generation
that fails the job (or at minimum writes to `$GITHUB_STEP_SUMMARY`) on:
tweets below the degraded floor, zero completed games for an in-season
league, zero GIFs, zero memes, email not sent, any `[MEME FAILED` or
un-rendered placeholder in the output.

---

## Part 2 — Findings against the product goals

### 2.1 Memes: the writer is given two contradictory caption-count tables

`prompts/pass2_writer.txt` lines 379-384 and `prompts/meme_reference.txt` are
both in Pass 2's system prompt. They disagree on 13 of 30 templates:

| Template | pass2_writer.txt says | Library / meme_reference say |
|---|---|---|
| distracted-boyfriend, left-exit-12-off-ramp, two-buttons, corporates-want-you-to-find-the-difference, epic-handshake, surprised-pikachu, trade-offer, eric-andre-shooting, is-this-a-pigeon | 2 | 3 |
| buff-doge-vs-cheems | 2 | 4 |
| expanding-brain, clown-applying-makeup | 3 | 4 |
| vince-mcmahon-reaction | 3 | 5 |

`meme_box_check.py` now drops any meme whose caption count is short, so the
wrong table no longer ships blank panels. It ships no meme instead. Memes are
running 1-2 per issue against a floor of 3 (9/2: 1, 9/3: 1, 9/4: 2), and this
table is one direct cause. `meme_reference.txt` also carries six stale Imgflip
IDs (clown-applying-makeup, hide-the-pain-harold, bernie, spider-man,
eric-andre, ight-imma-head-out) that disagree with `CURATED_TEMPLATES`; the
code keys on slug so they are harmless, but they are wrong.

Fix: delete the table from `pass2_writer.txt` and the per-template ID list
from `meme_reference.txt`; render both from `meme_library.DRAFT.json` at load
time, the way `{{GIF_LIBRARY_CATEGORIES}}` already works for GIFs. One source
of truth for box counts, which the repo already went to the trouble of
verifying against Imgflip.

### 2.2 Memes: Pass 1 picks the template blind to the 7-day history

Only Pass 2 receives the "RECENTLY USED MEDIA — DO NOT REPEAT" block. Pass 1
picks `meme_template` without it, then Pass 2 is told that spec is binding.
Result in the logs:

```
[memes] ⚠ 'drake' used in last 7 days — consider varying template        # 9/4
[memes] ⚠ 'two-buttons' used in last 7 days — consider varying template  # 9/4
[memes] ⚠ 'trade-offer' used in last 7 days — consider varying template  # 8/31
```

The warning is advisory. Fix: pass recent slugs into Pass 1's user message,
and have `plan_audit` blank a `meme_template` that is inside its cooldown
(same mechanism the GIF library uses by ID), so the rotation rule is
enforced in Python rather than asked for.

### 2.3 Memes seeded by Pass 2 alone get no spec

On 9/3 the plan had zero meme seeds and the writer placed `is-this-a-pigeon`
on its own. That is allowed ("replace it with something better"), but
`format_meme_specs` only injects specs for Pass 1's slugs, so an unseeded
meme is written from the stale table in 2.1 with no valence or subject rule.
Either inject the full compact spec for every template Pass 2 is allowed to
use, or forbid Pass 2 from adding templates Pass 1 did not seed.

### 2.4 GIFs: the do-not-repeat block fights the library's own rotation

`format_recent_media_block` lists library entries as
`[library:cockiness_smugness] 'Told you so' point-made (Bounce)` under "do
not repeat". The writer reads a category name it is being told to avoid,
while `gif_reference.txt` says library categories manage their own rotation.
Filter `library_id` entries out of that block; it should list Tier 3 search
concepts only.

### 2.5 GIFs: several library categories are too thin to rotate

Verified entries per category: domination 1, betrayal_self_sabotage 3,
denial_copium 4, debate_takes 4, fatigue_over_it 5. With a 7-day cooldown,
any of these used twice in a week forces `pick_entry` into "cooldown
relaxed", i.e. a repeat. The stats already count it (`relaxed_cooldown`);
nothing acts on it. Either seed those categories to ~8 verified entries or
have the writer's category menu omit categories with fewer than N eligible
entries today.

### 2.6 Video tweets are not filtered in production

`pass1_story_selector.txt` tells Pass 1: "VIDEO TWEETS ARE ALREADY GONE ...
filtered out of your candidate list before you see it." That is only true in
UAT. `has_video` tagging exists in `uat/enrich_fixture.py`; `fetch_content.py`
never sets it and `generate_newsletter.run_pass1` never filters on it. The
same prompt also still contains "PREFER GIF TWEETS OVER VIDEO TWEETS", which
contradicts the sentence above it. The video-tweet reduction you believe is
in place is a prompt claim, not a pipeline step.

Fix: port the tagging into `fetch_content.py` (Nitter RSS descriptions carry
`<video>` / `pic.twitter.com` markers; the heuristic already exists) and the
filter into `run_pass1`, then delete the "prefer GIF over video" paragraph.

### 2.7 Tweets: Pass 1 over-selects every day and Python trims positionally

Every run prints an account-cap violation from Pass 1 (`@ESPN: 9` on 9/4,
`@ShamsCharania: 5, @AdamSchefter: 3, @TalkinBaseball_: 4` on 9/3) and then
`enforce_tweet_budget` cuts 3-10 tweets (28→19, 28→18, 24→21). Because the
trim runs after selection, the model never learns the ceiling and the
deterministic rule decides which tweets survive by list position. Two ATL
tweets are thrown away daily for a structural reason: the tool schema demands
exactly 10 ATL tweets (`minItems: 10, maxItems: 10`), the prompt says 8-10,
and `ATL_MAX` is 8. Three numbers for one rule.

Fix: put `TWEET_CEILING`, `ATL_MAX` and the account caps into the schema and
prompt text (generate the numbers from `plan_audit` constants), and on a cap
violation send a `tool_result` error back to Pass 1 for one retry instead of
printing. The retry plumbing already exists in `run_pass1`.

### 2.8 Editor flags go nowhere

Pass 6 inserts 13-54 flags per issue, mostly `VERIFY STAT`. They are HTML
comments: invisible in Gmail, dropped by `convert.py` before Substack, and
the email has not been arriving anyway. Nobody can act on them. Pass 6 spends
roughly half its output tokens writing them. Decide: either surface them
(a review block at the top of the email, like the cost table) or cut Check 8
to the categories that changed a published issue. Separately, Check 1B
(banned phrases) is a string match and should be a regex in Python, not a
Sonnet call; leave Pass 6 the judgment checks only.

### 2.9 Voice: fragment stacking is still prompt-only enforcement

`rolling_feedback.txt` Rule 4 says this keeps recurring, and 9/4 has it again
("Playing a tournament. Positive test. Out-of-competition ruling." /
"Pick a lane."). CLAUDE.md's own rule is that a rule with no Python check is
not a rule. A `voice_audit.py` can flag deterministically: runs of four or
more consecutive sentences under eight words inside one `<p>`, the dueling
sentence construction (`This wasn't X. This was Y.`), a closing fragment that
is only a year or "do the math", and the banned-phrase list. Flags feed Pass 4
the same way Pass 5 feeds Pass 6.

### 2.10 Measured media mix, for reference

| Issue | Tweets | GIFs | Memes | Media share | Meme seeds in plan |
|---|---|---|---|---|---|
| 9/2 | 21 | 10 | 1 | 34% | 1 |
| 9/3 | 15 | 9 | 1 | 40% | 0 |
| 9/4 | 19 | 6 | 2 | 30% | 2 |

GIF fit on 9/4 was reasonable (facepalm after the blocked kick, "told you
so" for the ex-coordinator beating his old team). The library approach is
working; the meme side is the weak half, for the reasons in 2.1-2.3.

---

## Part 3 — Robustness gaps in the code

### 3.1 Output truncation is undetected

Passes 2, 4 and 6 run with `max_tokens=8192` and never check
`stop_reason == "max_tokens"`. Recent output sizes: Pass 2 5.2K-7.0K tokens,
Pass 4 3.4K, Pass 6 3.8K. A long day pushes Pass 2 over the cap, and a
truncated Pass 4 or 6 output silently replaces the full draft (the only
fallback checks for an `<h1>`). Fix: check `stop_reason`; on truncation keep
the previous pass's HTML and log loudly; raise the caps (Pass 2 to 16K with
streaming, since the SDK requires streaming above 21,333 anyway).

### 3.2 Pass 2's search loop is written for client tools

`web_search` is a server tool. The `stop_reason == "tool_use"` branch never
runs for it, `[SEARCH]` logging never fires (blocks are `server_tool_use`),
and `pause_turn` is not handled, so a long search turn returns partial text.
`web_search_20250305` is also the legacy tool type; Opus 4.7 supports
`web_search_20260209`. Minor today, but the loop is dead code that looks
alive.

### 3.3 Prompt caching writes daily and reads never

Pass 1, 4 and 6 show `cache read: 0 | cache write: 13,814 / 8,321 / 3,646`
every run. One call per day cannot hit a five-minute cache. Cache writes cost
1.25× input, so this is a small daily surcharge for nothing. Only Pass 2's
in-loop search turns benefit. Drop `cache_control` on 1/4/6, or use a 1h TTL
only where a second call happens.

### 3.4 Substack hydration publishes empty cards

`hydrate_tweets` prints `EMPTY` for a tweet the syndication API did not
return and keeps the url-only node, which renders as an "Invalid Date" card.
All 19 hydrated on 9/4, but the failure mode is "publish a broken embed".
Drop the node (or retry once via the proxy) instead.

### 3.5 Dependencies are mostly unpinned

`anthropic` and `python-substack` are pinned; `feedparser`,
`beautifulsoup4`, `requests`, `playwright`, `Pillow`, `curl_cffi` are not.
The 9/1 outage was exactly this class. Pin everything (or commit a
`pip-compile` lockfile). The Substack step also re-installs two packages
that `requirements.txt` already installs.

### 3.6 No concurrency group on the daily workflow

`publish-substack.yml` has one; `daily-newsletter.yml` does not. A manual
dispatch while the delayed scheduled run is in flight races on `main`, and
both send email and both create a Substack draft.

### 3.7 Tests exist but CI never runs them

Five offline suites, zero API calls, seconds to run, all passing today. No
workflow executes them. Add a `tests.yml` on push and pull request; the
drift test in particular only protects you if it runs before a merge.

### 3.8 `newsletter_email.html` is built, archived, committed and never sent

`email_newsletter.py` sends `newsletter_substack.html`. `build_email_html.py`
says its output is consumed by `build_box_score.py --append`, which no longer
exists. Either send the email-safe build (it is the better email) or delete
the module and the daily artifact.

### 3.9 Editor comments ship inside the emailed HTML

`newsletter_substack.html` keeps every `<!-- EDITOR FLAG -->`. Gmail hides
them, but they push the body toward Gmail's ~102 KB clipping limit that the
cid design exists to avoid. Strip comments when writing the Substack file;
`convert.py` ignores them anyway.

### 3.10 `fetch_sports_data.py` gives up on 403 after one retry

`fetch_url` returns `None` on the first 403 with browser headers. Combined
with 1.2, every scoreboard call fails fast and quietly. Route through the
proxy; if a call still fails, fail the step.

---

## Part 4 — Code that is not clean

### 4.1 Two runners, 2,567 lines, four functions declared divergent

`generate_newsletter.py` (1,305 lines) and `uat/generate_newsletter_uat.py`
(1,262 lines). `runner_common.py` removed the identical parts, and
`test_runner_drift.py` pins the rest, but `run_pass1`, `run_pass2`,
`pre_edit` and `main` are still copies that must be edited twice. The test
is a tripwire, not a fix. The clean shape is one runner with a config
object: `RunnerConfig(prompts_dir, paths, degraded_mode, highlight_plan)`,
where UAT is a config, not a fork. Prod's degraded mode and UAT's video
filter and Pass 1B become optional stages of the same function.

### 4.2 `story_plan` travels as a JSON string and is re-parsed five times

`run_pass1` returns `json.dumps(plan)`; the §2.4 audit, §2.3 trim,
`run_pass2`, `pre_edit` and the tier report each `json.loads` it, and two of
them `json.dumps` it back. Normalize once, pass a dict, serialize only when
writing `story_plan.json` and the Pass 2 message.

### 4.3 Leftovers in `generate_newsletter.py` from the lift

About 60 consecutive blank lines, section headers with nothing under them
("HTML wrappers", "Tweet URL conversion for Substack", "GIF Auto-Embedding"),
unused imports (`urlopen`, `Request`, `URLError`, `quote`, `time`,
`escape_html`), a `MODEL` backwards-compat alias nothing uses, and a
`from runner_common import (...)` list of 26 names of which the runner itself
calls about ten. `ruff --select F401,F841` would have caught most of it.

### 4.4 Duplicated helper to avoid an import

`plan_audit._normalize_tweet_url` is a byte copy of
`runner_common._normalize_tweet_url` "so this module has no import back into
either caller." Put it in a tiny `tweet_urls.py` both import.

### 4.5 Dead code and stale docstrings

- `run_pass6` reads `recent_output[0]["gifs_used"]` / `["memes_used"]`;
  `save_story_log` never writes those keys. The "PREVIOUS ISSUE MEDIA" block
  has never fired.
- `gif_reference.txt` tells the model to "check gif_history.json" and
  "recent_output.json" before choosing. The model cannot read files.
- `pass1_story_selector.txt` asks for `filter_stats.update_tweets_rejected`;
  the tool schema has no such field.
- `pass2_writer.txt` references a HIGHLIGHT PLAN and `highlight-placeholder`
  ids that only UAT's Pass 1B produces; prod never supplies them.
- `count_planned_tier3_from_plan` docstring: "Production never writes
  story_plan.json". It has since 9/2.
- `gif_url_cache.py` docstring: "the cache lives in uat/output/". It lives at
  the repo root.
- `meme_library.DRAFT.json` `_meta.status`: "Still not wired into the
  pipeline." It is production data, as is `gif_library.DRAFT.json`. Drop the
  DRAFT suffix and move both out of `prompts/`.
- `publish.py` keeps a `--schedule` mode documented as BROKEN.

### 4.6 Repository hygiene

- `Archive/` (11 dated copies of old scripts; git history has them) collides
  with `archive/` on case-insensitive filesystems, which CLAUDE.md documents
  as hiding daily archives from local `git status`. Delete `Archive/`.
- `Substack/` holds 12 MB of PDFs; `SLAP_PRD_v1.docx` at root;
  `pull_latest.sh` hardcodes a Windows path; `nitter_ci_test.py` and
  `gif_library_expand_probe.py` sit at root next to the pipeline.
- `prompts/` mixes prompts with libraries (`*.DRAFT.json`), generated review
  pages (`*_review.html`), task specs (`CLAUDE_TASK_*.md`) and a draft
  selector script. A `prompts/` directory should contain prompts.
- `uat/` carries seven one-off probe scripts alongside the runner and tests.

### 4.7 Prompts are long and partly obsolete

`pass1_story_selector.txt` is 41 KB and `pass2_writer.txt` 38 KB, each with
sections that describe a "test" that is now permanent ("BEATS — LOCKED FOR
THIS TEST", "WHY THE LOCK: this is a controlled test"). `gif_reference.txt`
spends 150 lines on a named-GIF menu that only applies to Tier 3, which the
writer used zero or one time per issue this week. Shorter prompts are cheaper
and, on Opus 4.7, follow more literally; the migration notes for that model
say over-prescriptive prompts reduce output quality.

---

## Part 5 — Clarifying questions

1. **Email.** Are you aware the email has not arrived since mid-August? If
   you have been reviewing from the Substack draft instead, is email still
   the product, or is the draft?
2. **Publishing.** Are you publishing by hand each morning? The 9/3 auto-run
   found it already done. If so, do you still want a timed auto-publish, or
   "publish N hours after the draft is created"?
3. **Box scores.** While the ESPN scoreboard is blocked, do you want the
   section to run on standings and leaders only (as it does now), be dropped
   for the day, or should I route ESPN through the proxy?
4. **Video tweets.** Should prod drop them outright (UAT behaviour) or allow
   them with a preference against? Tagging costs one syndication call per
   tweet, about 270 per run, unless we rely on the RSS heuristic.
5. **Editor flags.** Who is meant to act on the VERIFY flags? If the answer
   is nobody, Check 8 should go. If it is you, they need a visible home.
6. **UAT runner.** Are you open to collapsing it into configuration over the
   production runner? It is the largest single cleanliness item and the
   source of every half-port incident in the change log.
7. **Models.** Passes 1, 4, 6 run `claude-sonnet-4-5` and Pass 2
   `claude-opus-4-7`; newer generations exist. Not a defect, but say if you
   want a recommendation.

## Suggested order

1. Gmail app password + fail the step on SMTP error (under an hour).
2. Game-state content guard + ESPN via proxy (a couple of hours).
3. Generate the meme caption table from the library; give Pass 1 the meme
   history; enforce cooldown in `plan_audit` (an hour or two).
4. Video-tweet tagging in `fetch_content.py` and the Pass 1 filter.
5. `verify_run.py` gate and step summary; concurrency group; tests in CI.
6. Publish timing (multi-slot cron or draft-triggered).
7. `voice_audit.py` and the banned-phrase regex; trim Pass 6.
8. Runner collapse, then the hygiene list in Part 4.
