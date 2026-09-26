# SLAP Newsletter — Claude Context

## What This Is
SLAP is a daily AI-generated sports newsletter. Group-chat narrator voice, not sportswriter.
5-minute lunch read. 8-12 tweets with editorial commentary, ending with a box score section.
GitHub Actions runs the full pipeline daily at **2:17 AM EDT** (cron `17 6 * * *` UTC). Manual
`workflow_dispatch` is also available.

Why 2:17 and not a round hour: GitHub's scheduled workflows queue worst at the top of the hour.
Moving off it — and earlier — buys several hours of buffer before the morning review.

**Delivery (current reality, as of 9/20/2026): two paths run every day.**

1. **Email — exactly ONE a day, and it always fires (SLA-52).** `email_newsletter.py` runs
   **last** in the workflow under `if: always()` and sends a single message that is both the
   product and the status report: a pipeline status panel (succeeded / partially succeeded /
   failed, every stage with its outcome, the real error text of anything that broke, what the
   run-quality gate found, whether today's Substack draft exists), the daily cost breakdown, a
   copy-from-here marker, and then the newsletter with the per-sport box score images
   **embedded inline** under the "Box Scores" header. One select-all → copy → paste below the
   marker carries the whole issue, images included.

   It used to sit in the middle of the workflow, so a failure above it meant **no email at all**
   — the run Abram most needed to hear about was the one that stayed silent. A run that dies at
   Pass 2 now still produces an email naming the stage and quoting the traceback.

   The 12:30 PM ET publish job sends a second, lightweight email **only when the publish needs
   attention**. A normal publish, a draft Abram published himself, a draft he deleted: all
   silent. Silence at noon means it went out.
2. **Substack auto-post** — the morning run creates a **draft** via `substack_poc/publish.py`
   and commits a handoff file naming today's draft id. A separate workflow
   (`publish-substack.yml`) polls every 30 minutes and publishes that draft at the first slot
   at or after **12:30 PM ET**, unless it was
   already published, edited away, or deleted. Every "nothing to do" case is a graceful skip.

Auto-post **is live**. It was blocked by Cloudflare (403) through May; the fix was routing
through a residential proxy (`PROXY_URL`) with `curl_cffi`. Substack later removed its native
`/schedule` API endpoint, so scheduling is our own cron plus an immediate publish — hence the
two-job split. Beehiiv remains unused (post API is enterprise-only).

**Image delivery has a size guard (see Box Score System):** normal days embed images inline via
`cid:`; huge-slate days fall back to GitHub-hosted `<img>` URLs so the email never exceeds Gmail's
25 MB send limit.

---

## Backlog Execution (Linear)

- Linear (team: SLAP Sports) is the source of truth for what to work on next — not verbal/chat
  instructions given outside of Linear tickets.
- Before starting work, query Linear for issues in team SLAP Sports with **status = Todo**
  specifically (not Backlog — Backlog means not yet groomed/reviewed and must never be picked up
  automatically) that are also unblocked (no unresolved `blockedBy` dependencies), sorted by
  priority (Urgent > High > Medium > Low > None).
- Work exactly ONE issue per session unless explicitly told to chain multiple. Mark it
  "In Progress" before starting, and update its status when finished. Do not automatically pick
  up a second ticket at the end of a session.
- Before starting the actual work, do a brief sizing pass: skim the ticket description and the
  specific files/directories it touches — not a deep exploratory read of the whole repo. Based on
  that skim, give a rough size estimate (S/M/L) and flag explicitly if the ticket looks like it
  could plausibly run past a single 5-hour session window. If it's flagged L, stop and ask before
  proceeding rather than starting work that might get cut off mid-change.
- If a ticket's requirements are ambiguous, or contradicted by what's actually in the codebase,
  stop and ask rather than guessing — consistent with the "wrong is worse than nothing"
  principle below.
- Leave a brief comment on the Linear ticket summarizing what was done before marking it complete.

---

## Session Summary Format (for Abram)

Abram doesn't review code directly — he engages at the level of ideas, quality, and trade-offs.
When you finish a session (or a meaningful chunk of work), give him a summary in plain,
non-technical language instead of a technical changelog. No file names, function names, or
implementation details unless truly unavoidable to explain what changed.

Structure:

1. **What got done** — a short, plain-English recap of the work, written for tone and clarity
   rather than to a strict bullet count or length limit. Explain it the way you'd explain it to a
   smart non-engineer: what changed from his perspective, and what it means for the product or
   output.
2. **Judgment calls** (only if needed) — a clearly separated section flagging any trade-offs,
   decisions, or ambiguities where his input or approval matters. Skip this section entirely if
   there's nothing to flag — don't write "no major trade-offs this session" or similar filler.

This format applies only to the summary you give him directly in the session. Commit messages,
PR descriptions, and Linear ticket comments should stay as they are now — technical detail is
fine there since he's not the primary reader.

---

## Pipeline Architecture — 6 Passes

```
fetch_content.py      → raw_content.json   (ESPN/CBS RSS + Nitter RSS tweets)
fetch_sports_data.py  → game_state.json    (ESPN scores/standings/box scores — "ground truth")
        ↓
generate_newsletter.py (orchestrates all passes via Claude API; injects game_state as ground truth)
        ↓
Pass 1: Story Selector    → selects stories, emits beat skeletons, assigns tweets,
                            seeds GIF/meme concepts (tool_use: submit_story_plan)
        §2.3/§2.4       → plan_audit.py trims to the tweet budget and checks the
                            GIF/meme seed floor BEFORE the writer sees the plan
Pass 2: Writer            → writes HTML draft in SLAP voice, locked to Pass 1 beats
Pass 3: Claim Validator   → claim_validator.py, deterministic cross-check vs game_state.json
Pass 4: Voice Editor      → rewrites sportswriter-sounding <p> tags only
Pass 5: Pre-Edit          → deterministic Python auditor (tweet URLs, section mapping,
                            account caps, §2.2 redundancy — see plan_audit.py)
Pass 6: Editor            → mechanical checklist (flags + auto-fixes)
        ↓ highlights.py injects MLB/NHL/World Cup highlight embeds
        ↓ adds "<h2>Box Scores</h2>" after Around the League
        ↓ build_email_html.py builds the email body
newsletter_draft.html / newsletter_substack.html / newsletter_email.html
        ↓
box_score/build_box_score.py --per-sport  → per-sport HTML (MLB ~4 games/image, football ~3)
box_score/render_pngs.py                   → cropped PNGs (Chromium screenshot + Pillow trim)
        ↓
push → substack_poc/publish.py --draft → verify_run.py --record
        ↓
email_newsletter.py  (LAST, if: always() — the one daily status email)
        ↓
verify_run.py --gate  (the job's exit code)
        ↓
publish-substack.yml at 12:30 PM ET → publishes the draft
        → email_newsletter.py --publish-alert  (emails ONLY on trouble)
```

Pass numbering is sequential (1–6) and matches execution order. Passes 3 and 5 are
deterministic Python (no LLM); passes 1, 2, 4, and 6 are Claude API calls.

---

## File Structure

```
slap-newsletter/
├── CLAUDE.md                  ← this file
├── README.md                  ← front door / product framing
├── feedback_log.md            ← issue review intake + review ritual (read when asked to review)
├── fetch_content.py           ← ESPN/CBS RSS + Nitter RSS → raw_content.json
├── fetch_sports_data.py       ← ESPN scores/standings/box scores → game_state.json
├── claim_validator.py         ← deterministic fact check vs game_state.json (Pass 3)
├── champions_source.py        ← SLA-65: each league's defending champion from slap-sports-db,
│                                 written into game_state.json["champions"] by the fetch step
├── run_status.py              ← per-run state on disk, shared across processes
├── pipeline_status.py         ← per-STAGE outcomes on top of run_status.json;
│                                 PIPELINE_STAGES is the declared stage list
├── verify_run.py              ← run-quality gate; --record / --gate (see below)
├── check_game_state.py        ← ESPN fetch-health guard
├── heartbeat.py               ← SLA-56 dead man's switch pings (healthchecks.io);
│                                 stdlib only, always exits 0. docs/dead_mans_switch.md
├── ci/run_stage.sh            ← runs one workflow stage, tees its log, records
│                                 the exit code, re-raises it
├── generate_newsletter.py     ← orchestrates all passes (main script)
├── highlights.py              ← injects MLB/NHL/World Cup highlight video embeds
├── build_email_html.py        ← builds the email HTML body
├── generate_memes.py          ← Imgflip meme generation
├── nfl_standings.py           ← NFL standings: records, official tiebreakers,
│                                 responsive HTML table (mock + real feed)
├── runner_common.py            ← runner body shared by prod + UAT: 24 functions, models,
│                                 PRICING, PASS_COSTS. configure(prompts_dir=) per runner
├── plan_audit.py               ← deterministic audits, SHARED by prod + UAT (see below)
│   (runner_common.py also owns retry_api_call / is_transient — SLA-54)
├── library_studio.html         ← review/add/edit/delete BOTH libraries in a browser
├── library_studio.bat          ← double-click THIS to open the studio (not the .html)
├── library_studio_server.py    ← localhost host for the studio; PUT writes the two libraries
├── library_json.py             ← style-matching JSON writer; ONE copy, ported to JS in the studio
├── meme_library.py             ← meme library access layer, shared
├── meme_box_check.py           ← box-count guard: blocks memes that would render blank panels
├── gif_library_select.py       ← tiered GIF selection from the curated library, shared
├── gif_url_cache.py            ← GIF URL cache (gif_url_cache.json, gitignored)
├── email_newsletter.py        ← THE one daily email: status panel + cost + newsletter +
│                                 box scores inline (cid + size guard). Also
│                                 `--publish-alert`, the noon trouble-only note.
├── raw_content.json           ← daily input: headlines + tweets
├── game_state.json            ← daily ESPN ground truth (GITIGNORED build artifact)
├── story_plan.json            ← Pass 1 plan as Pass 2 received it, post §2.4/§2.3
│                                 (GITIGNORED; archived daily to archive/<date>/)
├── recent_output.json         ← rolling 30-day story_log + dedup state (GIFs, memes, stories)
├── cost_summary.json          ← per-pass cost breakdown, surfaced atop the email
├── newsletter_draft.html      ← browser-preview output
├── newsletter_substack.html   ← Substack paste/post output (bare tweet URLs)
├── newsletter_email.html      ← emailed body
├── gif_history.json           ← 7-day GIF dedup log
├── meme_history.json          ← meme dedup log
├── substack_post_state.json   ← handoff: today's Substack draft id (morning → noon job)
├── email_sent_state.json      ← SLA-55: proof today's daily email sent (committed, unlike
│                                 run_status.json — lets a --rerun-safe dispatch skip a duplicate)
├── .env                       ← API keys (gitignored — never commit)
├── requirements.txt           ← incl. playwright + Pillow (box score rendering)
├── box_score/                 ← box score subsystem (see "Box Score System" below)
│   ├── build_box_score.py     ← builds per-sport HTML from game_state.json
│   ├── render_pngs.py         ← Chromium screenshot + Pillow crop → box_score_sport_*.png
│   └── box_score_sport_NN_*.{html,png}  ← per-sport images, numeric-prefixed for order
├── substack_poc/              ← LIVE Substack integration (the "poc" name is historical)
│   ├── publish.py             ← creates the draft; publishes it (--publish-existing)
│   ├── convert.py             ← newsletter HTML → Substack block JSON
│   ├── tweets.py              ← tweet hydration (syndication API, t.co resolution)
│   ├── ci_auth_test.py        ← manual connectivity test (substack-ci-test.yml)
│   └── inspect_draft.py       ← dev-only draft inspector, not wired to anything
├── uat/                       ← UAT sandbox: own runner, frozen fixtures, own prompt copies
│   ├── run_uat.py             ← the UAT entry point
│   ├── generate_newsletter_uat.py
│   ├── promote.py             ← diff-and-confirm prompt promotion (USE THIS, never copy by hand)
│   ├── probe_meme_box_order.py ← renders marker captions to verify meme panel order
│   ├── probe_retry.py         ← the API retry drill, plain-English PASS/FAIL, 0 API calls
│   ├── tests/                 ← offline suites, 0 API calls — run before any prompt/code change
│   │   ├── test_runner_drift.py ← fails if a change reaches one runner and not the other
│   │   └── test_pipeline_status.py ← locks the status email + the stage list vs the workflow
│   ├── fixtures/              ← frozen inputs — the control. Deliberately NOT gitignored
│   └── prompts/               ← a FORK of prompts/. Promotion to prod is manual
├── prompts/                   ← all production prompt files (versioned in git)
│   ├── pass1_story_selector.txt
│   ├── pass2_writer.txt
│   ├── pass4_voice.txt
│   ├── editor_prompt.txt
│   ├── base_prompt.txt        ← project knowledge for claude.ai sessions (NOT sent to the API)
│   ├── rolling_feedback.txt   ← hard rules from real output failures (max 3/session)
│   ├── voice_examples.txt     ← target voice — read before writing anything
│   ├── gif_reference.txt
│   ├── meme_reference.txt
│   └── Archive/               ← timestamped backups of old prompt versions
├── archive/                   ← daily committed output snapshots (see Known Issues — case bug)
└── .github/workflows/
    ├── daily-newsletter.yml   ← the pipeline, 2:17 AM EDT
    ├── publish-substack.yml   ← publishes the draft, 12:30 PM ET
    └── substack-ci-test.yml   ← manual-only connectivity check
```

Shared sports database lives in `abramshulruff11/slap-sports-db` (SLA-5) — no longer staged here.

---

## Prompt File — What Each Does

| File | Pass | Job |
|------|------|-----|
| `pass1_story_selector.txt` | Pass 1 | Selects stories, emits beat skeletons, assigns tweets, seeds media |
| `pass2_writer.txt` | Pass 2 | Writes full HTML draft in SLAP voice, locked to Pass 1 beats |
| `pass4_voice.txt` | Pass 4 | Rewrites sportswriter `<p>` tags; leaves everything else alone |
| `editor_prompt.txt` | Pass 6 | Mechanical editor. Fixes the draft; never writes a flag for a human |
| `rolling_feedback.txt` | Pass 2 | Hard rules from output failures; overrides `pass2_writer.txt` |
| `voice_examples.txt` | Pass 2 + Pass 4 | The actual voice target — not a description, the target |
| `gif_reference.txt` | Pass 2 | GIF concept library + rotation rules |
| `meme_reference.txt` | Pass 2 | Meme-vs-GIF judgement + HTML syntax. Template list injected from the library |
| `base_prompt.txt` | *(none)* | Project knowledge for claude.ai. Never sent to the API |

`editorial_annotations.txt` is **retired** — its selection logic was folded into
`pass1_story_selector.txt`. Any reference to it elsewhere is stale.

---

## Key Rules (Read Before Touching Anything)

**Banned phrase sync:** `editor_prompt.txt` Check 1B and `pass2_writer.txt` BANNED PHRASES must
match exactly. When adding a new banned phrase, update BOTH files simultaneously. There is a
⚠ SYNC NOTE in editor_prompt.txt as a reminder.

**Pass 1 uses tool_use:** Story selector outputs via `submit_story_plan` tool, not raw JSON.
This was changed 5/12/2026 to fix GitHub Actions JSON escape failures from quote-heavy tweets.

**Pass 1 emits beats; Pass 2 is locked to them:** Pass 1 produces a beat skeleton per story
(`{angle, landing, media}`) and Pass 2 writes against it rather than free-forming. The lock
closes the "borrowed tweet" loophole where Pass 2 could pull a tweet assigned to another section.
If a beat's `media[]` is empty, Pass 2's only options are a GIF, a meme, or prose — it may not go
find a tweet elsewhere. That empty-beat rule is the mechanism the tweet budget leans on.

This landed in **production on 2026-09-01**, not 8/21. An earlier version of this file said the
8/21 UAT merge put beats in prod; that was wrong and it misled work for over a week.
`generate_newsletter.py` had zero references to `beats` until the 2026-09-01 merge.

**Rolling feedback owns hard rules:** `rolling_feedback.txt` overrides `pass2_writer.txt` when
in conflict. It captures real failure patterns from published issues. Max 3 rules added per
session. Rules are numbered (note: Rule 3 is missing — intentional gap from a removed rule).

**pre_edit() is deterministic Python:** Runs between Pass 4 (Voice) and Pass 6 (Editor) as
Pass 5. Splits HTML by h1/h2, maps sections to story plan by position, flags misassigned tweet
URLs, over-cap accounts, and tweets that restate their own section's prose. Not a Claude call.

**The editor never writes a flag for a human to read — but it still verifies (2026-09-04).**
Nobody reads HTML comments: they are invisible in Gmail and stripped before Substack, and across
the last 8 archived issues Pass 6 was writing **21.8 of them per issue** (15.4 `VERIFY STAT`) for
no reader. The fix is that every check must ACT. Checks 2 and 6 were deleted outright — both were
flag-only, and 6 was redundant with `drop_fabricated_tweets()` besides.

**Check 8 was deleted the same day and put straight back. That deletion was a mistake worth
recording:** "the flag is useless" was confused with "the check is useless", and factual
verification is the most valuable thing this pass does. Check 8 now **fixes or cuts** instead of
annotating — it strips an unverifiable conference placement or title claim, downgrades a specific
year or streak to relative framing (`rolling_feedback.txt` RULE 3, enforced rather than hoped
for), and cuts an unsourced number while keeping the sentence. Two things make a claim SOURCED and
therefore untouchable: a tweet in the same section carrying it, or the ground-truth block.

**That second source only became real on 2026-09-04.** `run_pass6()` had never been given
`game_state`, so the editor could only compare a number against the tweets next to it. It now
takes `game_state` and puts the ground-truth summary in the USER message (not the cached system
block, which would thrash the cache daily). Both runners pass it. A check that cites evidence it
was never given is worse than no check.

The check NUMBERS are left as gaps on purpose so references here and in `plan_audit.py` still
point at the right check. What remains either fixes the draft (1, 4, 5, 7, 8) or acts on a flag
Python already computed (3 = account caps and §2.2 from `plan_audit.py`, 9 = `FACT FLAG` /
`COHERENCE FLAG` from `claim_validator.py`). **Check 9 is new** and exists because Pass 3 had the
same disease: it had been injecting deterministic ground-truth contradictions that no prompt ever
told the editor to act on. The ~1.5 remaining flags per issue are working notes — they stay in the
archived `newsletter_draft.html` and are **stripped from `newsletter_substack.html`**, the file
that gets emailed and published. Locked by `uat/tests/test_editor_checks.py`.

**The meme library is the ONLY source of meme templates (2026-09-04).**
`prompts/meme_library.DRAFT.json` decides which templates exist, how many caption boxes each
has, where the subject goes, and what each box is for. Nothing else may state those facts.
`meme_reference.txt` and `pass2_writer.txt` each used to carry a hand-kept catalogue and
caption-count table; they disagreed with the library on **13 of 30 templates**, and since
`meme_box_check.py` DROPS a short caption set rather than shipping a blank panel, the writer was
losing memes for following its own prompt. Both tables are gone. The index is generated by
`meme_library.build_selector_index()` and substituted into `meme_reference.txt` at
`{{MEME_SELECTOR_INDEX}}` — same pattern as `{{GIF_LIBRARY_CATEGORIES}}`, and `promote.py`
already refuses to install a prompt whose placeholder the destination runner cannot substitute.

**The library files are now edited from a page, so everything derived from them is
generated (2026-09-15).** `library_studio.html` reviews, adds, edits and deletes entries in both
libraries and writes the JSON back directly. That only works if the JSON is the WHOLE truth, so
the two things that used to be hand-kept copies of it are gone:

- `CURATED_TEMPLATES` in `generate_memes.py` is **derived** from the library at import. It was a
  second mapping of slug → template_id that `test_meme_library.py` partly existed to police; a
  hand-kept dict would go stale the moment the page wrote a new template. It degrades to `{}`
  with a loud error rather than raising — a broken library must not kill the newsletter, which
  is the product — and `verify_run.py` already reports the meme count, so a run that silently
  lost every meme still colours red.
- `prompts/meme_selector_index.txt` is **deleted**. `load_selector_index()` now calls
  `build_selector_index()`, so Pass 1 gets an index built at the point of use and there is no
  file to go stale. It had already gone stale once, silently, when box counts were corrected.

Two guarantees moved into the browser with it, and both are tested by
`uat/tests/test_library_studio.mjs` (Node, in CI, 0 API calls) against the REAL library files:
the page's JS port of `library_json.py` must reproduce each file **byte-for-byte**, and its JS
port of the meme checks must still catch each bug it claims to. The page refuses to save when
either fails. Two things the port has to get right that Python never had to: both libraries are
**CRLF** and end **without a trailing newline** — `Path.write_text` was doing the first invisibly
and `dumps_matching_style` never added the second.

**`library_studio.bat` is how the page is opened — not the .html.** The File System Access API is
the only way a page can write back to a file it opened, and it requires a **secure context**;
`file://` is not one, in any browser. Opened directly the page can only hand back a downloaded
copy to move over the original yourself. The `.bat` starts `library_studio_server.py`, which
serves the repo on `http://localhost` (a secure context) and adds a PUT endpoint restricted to
an allowlist of exactly the two library files — so both load automatically and Save writes
straight to disk, atomically, keeping a `.bak`. The server re-checks the formatting before
writing rather than trusting the page.

**The library also has to agree with itself.** The 2026-08-27 and 2026-09-01 render corrections
updated `box_count`, `boxes[]`, `subject` and `selector_line` — and left `valence` and
`worked_example` describing the OLD panel mapping. `format_meme_specs()` prints all of them, and
`pass2_writer.txt` calls the VALENCE RULE hard, so for `distracted-boyfriend` the writer was told
"box 2 MUST name the subject" one line above "box 1: the SUBJECT". Nine worked examples also
demonstrated a caption shape SHORTER than their template, four of them with captions in the wrong
boxes. All fixed 2026-09-04; `uat/tests/test_meme_library.py` now fails on any disagreement
between `valence`, the examples, `subject.placement` and `boxes[]`. Same class of failure as the
runner half-ports: a correction applied to some fields and not the others.

**Meme rotation is decided at selection, not reported after rendering.** Pass 1 used to pick a
template without ever seeing the 7-day history — only Pass 2 got the "RECENTLY USED MEDIA" block,
by which point the slug was fixed. The only rotation signal was `[memes] ⚠ '<slug>' used in last
7 days — consider varying template`, printed *after* the meme was made; it fired on 8/31 and
twice on 9/4 and nothing acted on it. Pass 1 now receives the cooled slugs, and anything it still
picks is swapped by `meme_library.swap_cooled_templates()` for another template in the SAME
comedic engine, so the planned joke survives with a different picture. A repeat beats no meme:
with no free alternative the original is kept and the run says so. Locked by
`uat/tests/test_meme_rotation.py`.

**A pass that returns incomplete output must never look finished (2026-09-05).** Nothing checked
`stop_reason`. Pass 2 measured **7,029 output tokens on 2026-08-31 against a cap of 8,192** — 86% of
the ceiling — so one busy Saturday truncates the draft mid-sentence. Passes 4 and 6 each rewrite the
WHOLE draft, and the only guard was "did it come back as HTML?", which truncation cannot trip
because it removes the END, not the `<h1>`. `runner_common.was_truncated()` now catches both
`max_tokens` and `pause_turn`; Passes 4 and 6 fall back to their INPUT (an unedited draft beats a
half-edited one) and Pass 2, which has no earlier draft to fall back to, records it for the run
gate. `MAX_TOKENS_WRITER = 16384` — deliberately under the 21,333 non-streaming ceiling, re-bisected
against `anthropic==1.2.0`, because Pass 2's tool loop would be awkward to stream. Pass 2's loop is
also bounded at 8 turns now; it was `while True`.

**The run says what it produced, and can fail (2026-09-05).** `verify_run.py` runs last and reads
what actually shipped — tweets, GIFs, memes, un-rendered placeholders, whether the email sent,
whether any pass came back truncated — writes it to the GitHub step summary, and **fails the job**
when the issue is unfit to send. It warns, rather than failing, when the issue is merely thin: a
quiet sports day is not a bug, and a job that goes red every time it is light trains you to ignore
red. Calibrated against the archive: it **fails 2026-09-01**, the day seven GIFs shipped as invisible
empty divs, and passes the five issues around it with warnings only.

`run_status.json` (gitignored, reset at the top of each run) carries state between the separate
processes — `email_newsletter.py` now records whether delivery happened and **exits non-zero**,
which is why its workflow step carries `continue-on-error`: a failed send must colour the run
without skipping the Substack path that follows. UAT repoints `run_status.STATUS_PATH` at its own
output dir, so the sandbox never writes a production file.

**The offline suites run in CI (2026-09-05).** `.github/workflows/tests.yml` runs them all on
every push and pull request. They made zero API calls and took seconds, and until now nothing ran
them — `test_runner_drift.py` exists to catch a change reaching one runner and not the other, which
is exactly what caused the 2026-09-01 outage, and it could only do that if it ran before the code
landed.

**Rules the model is asked to follow must be checked in Python, not self-reported.** On
2026-08-27 Pass 1 reported its own account-cap violation *accurately* and shipped anyway, because
nothing acted on the number; the §2.2 "filter" was only ever printing a count the model wrote
about itself; and editor CHECK 3 missed `@TomPelissero` (4x) and `@ESPN` (3x) while flagging an
account that appeared once. All three are arithmetic now in `plan_audit.py`. When adding a rule,
decide where it is *enforced* — a prompt line with no check is not a rule.

**One email a day, always sent, and it is the only notification (SLA-52, 2026-09-20).**
`email_newsletter.py` is the last step of the workflow and runs under `if: always()`. Everything
that used to reach Abram only through the Actions tab — a blocked ESPN fetch, a failed Substack
draft, `verify_run.py`'s findings, a truncated pass — is in it.

- **Every stage reports itself.** `ci/run_stage.sh` wraps each step: it tees the stage's output
  to `ci_logs/`, records the exit code and (on failure only) the log tail into `run_status.json`
  via `pipeline_status.record_stage()`, and re-raises the original code. The wrapper is invisible
  to pass/fail — a critical stage still halts the job, it just leaves a record on the way out.
- **`PIPELINE_STAGES` must match the workflow, and `critical` must match `continue-on-error`.**
  A stage that never ran is the most important row in the report and the only one nothing can
  record; the declared list is what makes "never ran" printable. `uat/tests/test_pipeline_status.py`
  parses `daily-newsletter.yml` and fails on any disagreement in either direction, including
  order. **When you add a workflow step, add it to `PIPELINE_STAGES` in the same commit.**
  A step placed AFTER the email step must be declared `after_email=True` (the test checks both
  directions): the email is built before it can report, so it shows as "runs after this email"
  instead of "never ran". Before SLA-75 the marker commit read "never ran" in every email.
- **Warnings do NOT downgrade the verdict.** `verify_run.py` warns on a thin issue, and those
  fire on most days. A top line that reads PARTIAL every morning is a top line nobody reads,
  which is the failure this ticket exists to fix. Only real breakage moves the headline.
- **`verify_run.py` splits into `--record` and `--gate`.** `--record` runs BEFORE the email,
  writes its errors and warnings into `run_status.json`, and always exits 0 — a broken issue must
  not stop the email that explains it. `--gate` runs LAST, after the email has recorded whether
  it sent, and carries the job's exit code. Both compute the findings the same way; the plain
  invocation is unchanged.
- **`email_newsletter.py` and `pipeline_status.py` import nothing outside the standard library**,
  on purpose: a run that dies in `pip install` must still be able to say so. The test asserts it.
- **Noon is silent on success.** `publish.py --result-out` decides which outcomes need attention
  (no handoff / stale handoff / crash) and which are Abram himself (he published it, deleted it,
  scheduled it). `email_newsletter.py --publish-alert` sends only the first kind. That rule only
  holds if the innocent cases stay genuinely silent — don't widen it.

**The one alert that does not come from the pipeline: the dead man's switch (SLA-56,
2026-09-23).** Everything above runs inside the job, so none of it can report a job that never
started or was killed. `heartbeat.py` pings healthchecks.io — `start` before the installs,
`finish` right after the daily email under `if: always()` — and the SERVICE emails Abram when
pings are late. Setup, the cutoff's justification and how to pause it are in
`docs/dead_mans_switch.md`; read it before touching either step.

- **`finish` means "did Abram get today's email?", not "was the run green".** Email sent →
  success, whatever the verdict (the email already said so). Email not sent → `/fail`, and the
  service alerts at once, because nothing else can.
- **Cutoff 18:17 UTC** (cron + 12h grace, set in the service's UI, not in code): worst measured
  start 12:55 UTC + the 30-min job cap = 13:25, so 4h52m of headroom. Earlier buys little — on a
  never-ran day the noon publish job already alerts on the missing handoff.
- **Inert until the `SLAP_HEARTBEAT_URL` secret exists**, and never fails a step. Neither step is
  a pipeline stage (no `run_stage.sh`, not in `PIPELINE_STAGES`). Locked by
  `uat/tests/test_heartbeat.py`.
- **Pause it in the healthchecks.io UI, never by deleting the secret** — no pings means an alert.

**Transient API failures are retried underneath every pass; permanent ones are not (SLA-54,
2026-09-20).** `runner_common.retry_api_call()` wraps all seven Anthropic call sites — Pass 1, 2,
4, 6 in production, plus Pass 1B in UAT.

- **The SDK was already retrying.** `anthropic.Anthropic` defaults to `max_retries=2` and handles
  408/409/429/5xx with backoff, honouring `retry-after`. The outer loop adds what it does not:
  a line in the log (an SDK retry is silent, so a rate-limited run and a slow run look identical),
  waits longer than the SDK's ~8s cap, and keeps a transport failure out of the model's
  conversation. `max_retries` is now passed **explicitly** at both client construction sites —
  a number the pipeline leans on should not be an SDK default that can move in a patch release.
- **The real bug it fixed:** Pass 1's validation-retry loop caught API errors too, and appended a
  user turn asserting the failure was "usually caused by special characters (unescaped quotes,
  backslashes) inside string values". For a 429 that is false. It burned one of only three
  validation attempts on a problem the model did not cause, grew the prompt each time, and
  retried with **no backoff at all**. Pass 1's loop now retries the MODEL; `retry_api_call`
  retries the network.
- **What is NOT retried matters more.** 400, 401, 404, 422, a bad model string, and the SDK's own
  client-side streaming `ValueError` all raise immediately with a line saying waiting will not
  help. Retrying them turns a five-second red run into a several-minute one that looks like an
  outage — which is how 2026-09-01 got misdiagnosed.
- **Bounded:** 4 attempts, 4s → 8s → 16s, worst case 12 HTTP attempts per call including the
  SDK's own. That is quick for FAST failures (a 429/529 answers in seconds). It is NOT a bound on
  a HUNG call — this file used to say it was, and it was wrong (SLA-74): with the SDK's default
  600s per request, 12 hung attempts is two hours. Two limits now cover that case:
  `API_REQUEST_TIMEOUT = 420` s per request (sized from a measured ~58 tok/s for Pass 2, so a
  full 16,384-token draft still fits with ~1.5x headroom; for streaming Pass 1 it bounds silence
  between chunks, not the whole generation), and a hard **17-minute `timeout` on the "Generate
  newsletter" stage** in `daily-newsletter.yml`, inside `run_stage.sh` so the stage is still
  recorded with an explanation. The stage limit is the real guarantee: it stops the generator in
  time for the `always()` status email to go out inside the job's 30-minute cap.
- **Cost is not double-counted, and that needs no special handling.** `cost_summary()` is only
  ever called with the usage of a response that arrived; a failed attempt returns none. The one
  honest gap: a STREAMING call that dies mid-stream generated billed tokens the SDK gives us no
  usage object for, so a day that retried Pass 1 slightly under-reports. An invented number
  would be worse.
- **Retries are visible in the morning email**, via `run_status` `api_retries` → the API RETRIES
  section of the SLA-52 panel. A recovered retry deliberately does **not** change the verdict —
  the run survived it — but a day that recovered from three rate limits should not look identical
  to a day that sailed through.
- **The drill switch.** `SLAP_SIMULATE_API_FAILURES` makes every pass fail on cue
  (`2` / `529:1` / `timeout:3` / `fatal` / `exhaust`). It is exposed as a **`workflow_dispatch`
  input only** — scheduled runs carry no inputs, so it is a `getenv` and a return, and
  `test_api_retry.py` fails if anything else ever sets it. `python -X utf8 uat/probe_retry.py`
  runs the same drill offline and prints a plain-English PASS/FAIL per behaviour; `tests.yml`
  runs it on every push so the verdict is on the run page.

**A Substack image that never uploads is a DELIVERY failure, not a warning (SLA-68,
2026-09-21).** `upload_box_scores()` returns `(items, report)` where report is
`{expected, uploaded, failed[], seconds}`, records it to `run_status` under `substack_images`,
and every reporter reads it from there.

- **It used to return only its successes**, so a partial upload was literally
  indistinguishable from a complete one — there was no number to compare against.
  `verify_run.py` counted the PNGs on *disk*, saw them all, and was satisfied. On 2026-09-21
  three of thirteen images never reached Substack and the run reported SUCCESS.
- **A shortfall makes the run PARTIAL**, and that is a deliberate exception to "warnings do not
  downgrade the verdict". A thin issue (memes under their floor) fires most days and must not
  move the headline. This is different: content that was successfully produced failed to reach
  the published issue, and it is rare — 0 failures on 09-20, 3 on 09-21. Rare plus real is what
  the top line is for. `verify_run.py` still only *warns*, because the issue is shippable.
- **The emailed copy is unaffected** and the panel says so — `email_newsletter.py` embeds the
  PNGs from disk via `cid:`, independently of Substack. Only the published post loses them.
- **Two time bounds, with different jobs.** `MAX_RETRY_SECONDS = 180` caps time spent on second
  and third tries. `MAX_UPLOAD_SECONDS = 600` is a hard stop for the catastrophe where even
  first attempts are all timing out. **Every image always gets its first attempt**, regardless
  of budget — an image never tried is an image guaranteed missing, and on a big slate most of
  them would have worked. (The first cut of this gated first attempts too; the new test caught
  it by modelling 13 slow-but-healthy uploads, which silently lost the last three.)
  Worst case is now ~10.5 min against a 30-minute job cap and a ~13.5-minute base run; before,
  it was `attempts x timeout x images` with nothing stopping it, and 2026-09-21 finished at
  **24m48s** with only three failures.
- **This does NOT reuse `runner_common.retry_api_call`.** That helper imports `anthropic`,
  classifies HTTP statuses against Anthropic's semantics, and records into `api_retries` — using
  it here would put Substack retries in the Pass-retry report. `substack_poc/` is also runnable
  standalone against an archived issue, so its `run_status` import is wrapped and optional.

**"Defending champion" is checked, not flagged (SLA-65, 2026-09-26).** The fetch step reads each
league's current champion (NFL, MLB, NHL, NBA, college football, men's college basketball) from
slap-sports-db (`champions_source.py`, `v_league_title`) into `game_state.json["champions"]`.
It reaches the writer and editor as a DEFENDING CHAMPIONS part of the GROUND TRUTH block, and
Pass 3 Check 3 resolves every "defending champion" in our own prose (never inside an embedded
tweet): the named team IS the champion → nothing added; a DIFFERENT team → `FACT FLAG [HIGH]`
naming the real one, which editor Check 9 corrects; no team it can match, or a league whose
champion isn't known today → `FACT FLAG [LOW]`, name it or cut the title phrase. An unnamed
"the defending champs" is confirmed when the sentence or its section names a current champion.
- **Stale means unknown.** A league whose newest title is older than the calendar says
  (`champions_source.DECIDED_BY`) is `stale`, never offered as the champion, so a missed yearly
  update in slap-sports-db (Linear SLA-94/95/96) can't turn last year's champion into this year's.
- **Inert until the `SPORTS_DB_URL` secret exists, and never fatal.** No secret, no driver or no
  database → the block says `unavailable` and every claim is the LOW case, which is what Check 3
  always did. `psycopg` is imported only when the URL is set; its password is scrubbed from errors.
- Replayed on every archived issue that used the phrase (Apr–Sep 2026): 13 confirmed, 0 HIGH,
  3 LOW (two World Cup, not a launch league; one NFL item naming no team). Locked by
  `uat/tests/test_defending_champion.py`.

**Calendar beats hierarchy:** Tier 1 sports calendar events (NBA Playoffs, Super Bowl, Masters,
etc.) override the NFL-first hierarchy in Pass 1. Check the calendar before selecting the lead.

**Evergreen content cannot lead:** A stat post about a finished season cannot be the lead story
if any active Tier 1 event is available (see Rule 5 in rolling_feedback.txt).

**Feedback intake lives in `feedback_log.md`:** When the user asks to review SLAP issues, propose
rule updates, or audit recent newsletters, read `feedback_log.md` first. The file contains the
review ritual (instructions for Claude) and the active log of unresolved observations. Do not
edit `rolling_feedback.txt` directly during review — propose changes for the user to integrate.

**GIF/meme library work starts at `docs/library_expansion_handoff.md`:** read it before touching
`prompts/gif_library.DRAFT.json`, `prompts/meme_library.DRAFT.json` or anything named
`*_expand_probe.py` / `review_*.py` / `apply_*_decisions.py`. It carries the tooling map (all of
it already exists — a session nearly rebuilt `review_gifs.py` from scratch by not checking), which
steps need network to Giphy/Imgflip and so cannot run from a cloud session, and the failure modes
worth not re-learning: the two library files use different JSON formatting and must be written
through `library_json.dumps_matching_style()`; meme `status` gates nothing until
`active_templates()` is respected; and a decisions export replays every verdict ever stored in that
browser, so its row count is not the size of the review.

**UAT before prod:** `uat/` has its own prompt copies. Changes are tested there, then promoted
with `python -X utf8 uat/promote.py` — never by hand-copying, which is how the two trees drifted
for months. It classifies each pair (identical / eol-only / uat-ahead / prod-ahead / diverged),
refuses any copy that would delete content from the destination, and refuses a prompt whose
`{{PLACEHOLDER}}` the destination runner cannot substitute. No flags = read-only status.

**Shared logic lives at the repo root, imported by both runners — never copied.** `plan_audit.py`,
`meme_library.py`, `meme_box_check.py`, `gif_library_select.py`, `gif_url_cache.py` and
`runner_common.py` each have exactly one copy. Duplicating any of them into
`generate_newsletter.py` recreates the drift problem somewhere `promote.py` cannot see it,
because it only diffs prompts.

**`runner_common.py` holds the runner body itself.** 24 functions were byte-identical copies in
both runners until 2026-09-01, when two separate half-ports shipped on the same day (Pass 1's
`max_tokens` raise without its streaming call; the GIF library prompts without their consumer).
It also owns `MODEL_DEFAULT`, `MODEL_WRITER`, `PRICING` and the `PASS_COSTS` accumulator.

Per-runner config is **injected, never assumed**: each runner calls
`runner_common.configure(prompts_dir=...)` at import, because `PROMPTS_DIR` genuinely differs —
prod reads `prompts/`, UAT reads `uat/prompts/`, and that fork is the whole point of the sandbox.
`configure()` raises on a conflicting reconfigure and `load_prompt()` raises if it was never
called, so a UAT run can never silently read production's prompts.

**Any function still defined in BOTH runners must be identical.** `uat/tests/test_runner_drift.py`
enforces it against a declared ledger (`KNOWN_DIVERGENT`) that currently holds four entries:
`run_pass1`, `run_pass2`, `pre_edit` and `main`. New drift fails the test; a pair that converges
must be deleted from the ledger, so it can never over-state the debt.

**Tests are offline and free.** `uat/tests/` makes zero API calls: `test_account_audit.py` locks
the deterministic audits, `test_runner_drift.py` locks prod-vs-UAT runner divergence,
`test_history_dedup.py` the GIF/meme history writers,
`test_meme_wiring_dryrun.py` the UAT meme wiring, and
`test_prod_wiring_dryrun.py` exercises the real production path with the Anthropic client
stubbed, and `test_proxy_fallback.py` locks the ESPN 403 / RSS bot-wall proxy fallback.
`test_library_studio.mjs` is the one Node suite — it locks `library_studio.html`'s JS ports of
`library_json.py` and the meme checks against the real library files. Run all three before changing a prompt or a pass.

---

## Box Score System (`box_score/`)

A "The Box Score" newspaper-style section appended after Around the League. Built from
`game_state.json` (ESPN ground truth), delivered as **images** because complex stat tables don't
paste cleanly into Substack as HTML.

**How it works:**
- `build_box_score.py --per-sport` writes one standalone HTML per sport that has data. Sports with
  a full daily slate are split: a summary image (standings/poll + leaders + results + today's
  games) plus box scores chunked per `CHUNK_SIZES` (`build_chunk_blocks`) — **MLB 4 games per
  image, NFL and CFB 3** (a football box score is three tables per side against baseball's two).
  Other sports = one image each.
- `render_pngs.py` screenshots each HTML with **Chromium via Playwright** (full-page, locked 400px
  width, 2× scale for crisp text), then **Pillow** trims top/bottom whitespace. Prefers system
  Chrome locally; uses Playwright's bundled Chromium in CI. Output is **PNG** (lossless — crisper
  than JPG for text).
- `email_newsletter.py` embeds the `box_score_sport_*.png` files **inline in the email body** under
  the "Box Scores" header — not as attachments — so one copy/paste carries them.
- `substack_poc/publish.py` uploads the same images into the Substack draft.

**Football box scores (added 2026-09-15) — MLB was the only sport that had any.** Three gaps
stacked: `box_sports` in `fetch_sports_data.py` excluded `nfl`/`ncaafb` so no summary was ever
requested; `parse_box_score()` had no football branch; and `_render_sport_inline()`'s
regular-season path emitted standings plus a one-line-per-game scores strip and never called a
per-game renderer at all — MLB escaped it only because `_render_mlb_sections()` is a separate
function. Measured across the 8 issues shipped 09-08 → 09-15: MLB rendered 10–15 box score tables
a day, NFL and CFB rendered **0 every single day**. The best football day (09-14, the 13-game
Sunday slate) shipped `Buccaneers 27, Bengals 33` and nothing else; 09-13 put **80 CFB games** in
one unchunked image. Football now renders passing/rushing/receiving per side, a quarter linescore
and a scoring summary (the agate equivalent), through `_render_football_sections()`.

**CFB ships box scores for RANKED matchups only.** 80 full box scores is a different product from
MLB's 15 — it would run to twenty images and blow the email size guard every Saturday. "Ranked"
means **at least one** team in the top 25, not both (`CFB_REQUIRE_BOTH_RANKED = False`): an
unranked team beating a top-10 team is the story of the week, and requiring both would drop
exactly that game. `MAX_FOOTBALL_BOX_FETCHES = 16` caps the summary requests per sport per run (CFB sorts by
best rank first, so the cap keeps the marquee games). **Rank matching is exact on the full team
name or the abbreviation, never a substring of the poll nickname** — FBS nicknames are duplicated
across dozens of schools, so "Bulldogs" made Louisiana Tech vs Fresno State read as ranked because
Georgia is #4. If the poll fetch fails, CFB shows **no** table rather than falling back to the
one-arbitrary-conference standings this change removed.
The results strip still carries the **full** slate — only the box scores are filtered — and the
first CFB box score image is labelled `Box Scores — Ranked Matchups` *even in bare mode*, breaking
the MLB rule that bare chunks carry no label, because otherwise the reader cannot tell the other
60 games were filtered rather than lost.

**CFB standings were replaced by the AP poll.** `_drill_for_entries()` returns whichever group it
finds first, so the shipped page carried one arbitrary conference (the AAC) out of ~130 teams —
and `_parse_entries` matched none of college football's stat field names, so every row read
`W=0, L=?, Pct=?`. `fetch_cfb_rankings()` now supplies `sports.ncaafb.rankings` (CFP once it
exists in December, else AP), which is both the right furniture and the rank source the
ranked-matchup filter falls back to. `_mi_simple_standings()` also scrubs the `?` sentinel, so a
league whose field names ESPN changes degrades to blanks instead of shipping punctuation.

`PLAYOFF_WINDOWS` gained `"nfl": (1, 2)` — it had no NFL entry, so the Jan/Feb playoffs never
triggered the playoff branch or bracket rendering. Locked by `uat/tests/test_football_box.py`.

**Ordering:** files use a zero-padded numeric prefix (`box_score_sport_01_nba.png`, `02_nhl`, …)
so the email and shell glob attach them in a fixed order: **playoffs first** (per `SPORT_ORDER`),
**then regular season**, golf/tennis at the end. The playoff/regular split is computed per-sport
from `game_state.json` (`_ordered_sport_keys`), so it self-updates as seasons change — no calendar
edits needed.

**Delivery — inline with a size guard:** `email_newsletter.py` injects `<img>` tags under the
"Box Scores" header so the images travel with one copy/paste.
- **Normal days** (≤ `MAX_INLINE_RAW_BYTES`, 15 MB raw): images embed **inline via `cid:`**
  (`multipart/related` + inline image parts) — self-contained, no external dependency.
- **Huge slates** (NFL Sundays, CFB Saturdays, March Madness — would exceed Gmail's 25 MB send
  limit after ~37% base64 inflation): falls back to **GitHub-hosted `<img>` URLs**
  (`raw.githubusercontent.com/<repo>/<branch>/box_score/…` via `_github_raw_base()`).
- **cid (not base64 data URIs) is deliberate:** it keeps the HTML body small, avoiding Gmail's
  ~102 KB "[Message clipped]" truncation that would otherwise break the copy/paste.
- **Push runs BEFORE email** in the workflow (with `continue-on-error`) so the hosted-URL fallback
  resolves when Gmail fetches it; the push can hiccup without ever blocking the email.

**"Bare" mode:** per-sport images omit all masthead chrome because the newsletter already carries
a "Box Scores" header. The **section band appears on the first image of a chunked sport only**;
the rest are continuations of one photo split for size.

**CI gotcha (fixed 5/26):** per-sport filenames shift with the daily slate size (WNBA might be
`06` one day, `08` the next). The commit step stages `box_score/` with `git add -A` so removed
files are staged as deletions — a bare glob only matches existing files and leaves stale deletions
unstaged, which breaks `git pull --rebase`.

---

## Model & Cost

- **Pass 2 (Writer): `claude-opus-4-7`** — the prose quality lift is the product. A/B trial began
  2026-06-01 and stuck.
- **Passes 1, 4, 6: `claude-sonnet-4-5`** — sufficient for selection and transformation.
- Constants are `MODEL_WRITER` and `MODEL_DEFAULT` in **`runner_common.py`** (moved there
  2026-09-01 with the shared runner body; both runners re-export them).
- Prompt caching enabled on all passes. `PRICING` in `runner_common.py` is the single source
  of truth for the cost breakdown that lands atop the daily email (`cost_summary.json`).
- Estimated cost: ~$2-5/month.
- Note on Opus 4.7: it follows instructions more literally than Sonnet, and its tokenizer can use
  1.0–1.35× more tokens for the same input. Budget for both when reading the cost summary.
- **Every call is retried on transient failures** (`runner_common.retry_api_call`, SLA-54): 4
  attempts at 4/8/16s on top of the SDK's own 2. A retried call adds no cost row — `cost_summary()`
  only ever sees a response that arrived.
- Pass 1 `max_tokens`: **32,768** (raised 4,096 → 8,192 → 16,384 → 32,768; silent truncation
  caused an ATL regression and truncated story plans on full slates). Raised to 32,768 with the
  beats port on 2026-09-01 — beats plus the meme/gif fields roughly double the plan, and this
  failure mode is silent, so the limit moves in the same commit as anything that enlarges the
  plan. Other passes: 8,192.
- **Pass 1 MUST stream, and that is a consequence of the 32,768 above.** The Anthropic SDK
  refuses a *non-streaming* request whose `max_tokens` implies a >10-minute generation — a
  client-side `ValueError` ("Streaming is required for operations that may take longer than 10
  minutes") raised by `_calculate_nonstreaming_timeout` when `3600 * max_tokens / 128_000 > 600`.
  That puts the non-streaming ceiling at **21,333 tokens**. 16,384 was under it; 32,768 is not.
  So Pass 1 calls `client.messages.stream(...)` + `.get_final_message()`, which returns the same
  `Message` object (tool_use blocks and `usage` included) that `messages.create()` did. Anything
  that raises another pass above 21,333 must convert that pass to streaming in the same commit.
  **Since SLA-74 the SDK guard no longer fires at all:** the SDK only runs it when the client
  timeout is the default, and `API_REQUEST_TIMEOUT` makes it non-default. An oversized
  non-streaming call would now be sent and then cut off at 420s instead of refused. The rule
  moved into `test_api_retry.py`, which reads every non-streaming `messages.create()` in both
  runners and `runner_common.py` and fails if its `max_tokens` exceeds 21,333 or cannot finish
  inside `API_REQUEST_TIMEOUT`.

---

## Known Issues / TODO

- **⚠ SPORTS-DATA LICENSING IS CONDITIONAL ON SLAP BEING FREE — RAISE THIS THE DAY IT ISN'T
  (2026-09-23):** SLAP is a free Substack, ~30 subscribers, no ads and no sponsorship. On that
  basis every source recommended in `docs/sports-source-evaluation.md` is in bounds. Two of them
  are conditioned on it and **must be re-checked the moment paid subscriptions, sponsorship or
  advertising are switched on**:
  - **Lahman** (MLB season stats, CC BY-SA 3.0). ShareAlike bites on *distributing a derived
    database*, not on writing prose from it — facts are not copyrightable — so publishing the
    newsletter is fine either way. Publishing or sharing the database itself would not be.
  - **MLB Stats API** — free for "individual, non-commercial, **and non-bulk**" use; anything
    else needs written MLBAM authorization. It is already scoped to the live-delta lane only,
    because a historical backfill is *bulk* regardless of money. Going paid removes the
    non-commercial leg as well.
  - **Retrosheet**, the actual MLB backfill source, expressly permits commercial use with
    attribution, so the core of the design is unaffected either way.
  - Tennis's NonCommercial source (Sackmann) is already out of scope — team sports only.
  - **FiveThirtyEight `nfl_games.csv`** (pre-1999 NFL playoff results, SLA-84/85): MIT / CC BY,
    free including commercial, with attribution. But its historical scores have **undisclosed
    provenance and may trace to Pro-Football-Reference**, whose terms bar generative-AI use.
    Abram accepted that knowingly on 2026-09-25 (scores are facts; we never touch PFR). Worth a
    second look alongside Lahman if SLAP is monetised.

  **Trigger for Claude: if Abram mentions turning on paid Substack subscriptions, sponsorship,
  ads, or otherwise monetising SLAP, surface this note before the work proceeds.** The answer is
  not "stop" — it is "re-check Lahman and MLB Stats API, and confirm Retrosheet still carries the
  attribution line."

- **Scheduled runs land hours late (WORKED AROUND 2026-09-04; the delay itself is GitHub's):**
  measured over three consecutive days, `daily-newsletter.yml` fired +5h05m after its 06:17 UTC
  cron (11:20–11:24 UTC) and `publish-substack.yml` +2h50m after its 16:30 UTC cron (19:20–19:27
  UTC), so the "12:30 PM ET" publish was landing near 3:20 PM ET — and on 9/3 it found the issue
  already published by hand. A punctual cron is not available to us, so the publish no longer
  depends on one: `publish-substack.yml` now fires **every 30 minutes from 11:30 to 20:00 UTC**
  and a **time gate inside the job** publishes only at or after 12:30 PM ET (read in
  `America/New_York`, so DST is handled). The issue goes out on the first firing past 12:30 ET,
  punctual or five hours late. The other half: if the daily run itself finishes past 12:30 ET
  (2026-08-28 committed its handoff at 19:01 UTC = 3 PM ET, after the publish job had run and
  skipped), its last step publishes immediately rather than waiting for tomorrow.
  **Do NOT "fix" the delay by moving the daily cron earlier.** The 11:20 UTC landing is what
  aligns `yesterday` with last night's games; firing at, say, 02:00 UTC would compute
  `yesterday` while those games were still being played.

- **`anthropic` pinned (RESOLVED 2026-09-02); all of `requirements.txt` pinned (RESOLVED
  2026-09-23, SLA-57):** every entry is now pinned to the version GitHub Actions run
  `35602375183` (2026-09-21, green end to end) actually installed — read out of that run's
  `pip install` log, not a local `pip freeze` (the dev machine had `anthropic==0.84.0` installed
  against CI's pinned `1.2.0`; pinning from the wrong environment would pin the pipeline to
  versions it never ran on). **To bump any entry:** change its version, re-run the pipeline via
  `workflow_dispatch`, confirm green, then leave it pinned forward — one entry at a time, same as
  the original `anthropic` rule this generalizes. **Workflows that install a short list by name
  instead of `-r requirements.txt`** (the noon publish job, the Substack and Nitter CI checks)
  carry the same `==` versions — the noon publish job was left unpinned by SLA-57 and fixed in
  SLA-74. `uat/tests/test_workflow_pins.py` fails if any workflow's `pip install` disagrees with
  `requirements.txt`, so a bump changes both in one commit. Every run now also uploads a `pip-freeze`
  build artifact (90-day retention) recording exactly what was installed, so the next bump never
  again has to mine an Actions log before it expires.
- **Chromium is not separately pinned, and that's a deliberate decision, not an oversight
  (SLA-57).** `playwright install chromium` downloads the browser build tied to the installed
  **Playwright package's** own revision manifest, not "whatever is newest" — so pinning
  `playwright==1.63.0` in `requirements.txt` already pins which Chromium build CI downloads on
  every run. A Chromium-level change can only reach us through a `playwright` package bump, which
  the pin already guards and which gets the same workflow_dispatch-and-confirm-green treatment as
  every other pin (box score renders are part of what "confirm green" means to check). No
  separate `playwright install chromium@<rev>` or browser cache was added — it would be
  redundant machinery for a case the package pin already covers.

- **Runner duplication (MOSTLY RESOLVED 2026-09-01):** 24 byte-identical functions moved to
  `runner_common.py`, and `uat/tests/test_runner_drift.py` now fails on any undeclared
  divergence. Duplicated LOC across identical functions went 668 → 0. **Four functions remain
  duplicated and diverged** — `run_pass1`, `run_pass2`, `pre_edit`, `main` — and are declared in
  that test's `KNOWN_DIVERGENT` ledger with reasons. `run_pass1` and `run_pass2` are diverged in
  *both* directions (prod has degraded mode; UAT has the §2.1 video filter and Pass 1B), so
  neither can be promoted by copying — they need a real merge. Until then, **any change to those
  four must be applied to both copies in the same commit.**

- **`Archive/` vs `archive/` case collision (open, real):** git's index holds 11 files under
  `Archive/` (old code versions) and 910 under `archive/` (daily CI output). On Windows
  (`core.ignorecase=true`) these are the **same physical folder**. Because `.gitignore` lists
  `Archive/`, git **also silently ignores `archive/` locally** — new daily archive dirs never
  appear in local `git status`. CI is Linux (case-sensitive), so it commits them fine. Fix is to
  rename one side; until then, don't trust local `git status` for `archive/`.
- **ESPN blocked from CI (FIXED 2026-09-04, first live run pending):** every scoreboard call
  returned 403 to the GitHub runner's IP from at least 8/14 to 9/4 (standings still loaded, so
  the file looked fresh with zero games), and the ESPN RSS feeds answered 202 (a bot wall)
  since 7/15. `fetch_sports_data.py`, `fetch_content.py` and `highlights.py` now retry a
  blocked request through `PROXY_URL` (`proxy_session.py`, the Substack trick). `PROXY_URL`
  is job-level in `daily-newsletter.yml`. `game_state.json` carries a `fetch_health` block and
  `check_game_state.py` (continue-on-error step) fails loudly on a blocked fetch. Offline test:
  `uat/tests/test_proxy_fallback.py`. Full review: `docs/code_review_2026-09-04.md`.
- **`game_state.json` freshness — guard added (see above):** `fetch_sports_data.py` always stamps the file
  with today's date, even if every ESPN call silently fails (each is wrapped in try/except
  returning empty). A today-stamped but *hollow* file degrades silently — the newsletter loses its
  ground-truth block (`format_game_state_summary` returns "" on empty) and box scores thin out,
  with no alarm. A date check is useless; the real fix is a **content-presence guard** (fail CI if
  the payload has no games/standings) plus logging `as_of_date` + per-sport counts in CI.
- **UAT prompt drift (RESOLVED 2026-09-01):** `uat/promote.py` now diffs and copies on confirm,
  and all nine pairs are identical or deliberately one-sided (`editor_prompt` is UAT-ahead by the
  highlight-placeholder rules prod has no Pass 1B for; `pass1b_highlight_selector.txt` is UAT-only).
  Run `uat/promote.py` after any prompt change to keep it that way.
- **No `.gitattributes` (RESOLVED 2026-09-01):** `* text=auto` added. `promote.py` also reports
  line-ending-only differences as `eol-only` rather than as drift.
- **`requirements.txt` drift (RESOLVED 2026-09-01):** `python-substack==0.1.22` and `curl_cffi`
  are now listed.
- **Meme panel counts and order (RESOLVED 2026-09-01):** every template's `box_count` was checked
  against Imgflip's live `get_memes` API (9 corrected), then panel ORDER was verified by rendering
  each flagged template with marker captions (`uat/probe_meme_box_order.py`), correcting 7 more.
  `vince-mcmahon-reaction` turned out to be 5 panels, not 4 — it had been shipping a blank payoff
  frame and reporting success. `meme_box_check.py` now blocks that class of failure in production.
  Re-run the probe after adding any template.
- **Meme output runs below the floor (DIAGNOSED + FIX SHIPPED 2026-09-23, SLA-76; confirm over
  the next week):** `MIN_MEME_SEEDS = 3`, but 20 of 22 archived issues (09-02 → 09-23) seeded
  fewer, median 1. **Every seeded meme rendered** on all 22 days, so the loss was entirely in
  Pass 1's plan, not Pass 2 or Imgflip. Cause: `pass1_story_selector.txt` stated the floor and,
  thirty lines later, a "70% GIFs, 30% memes" balance with "most stories should ... leave
  meme_concept empty". On a five-story day that is 1-2 memes, which is exactly what shipped. GIFs
  had no competing instruction and hit their floor every day. The ratio is gone from both prompt
  copies; the subject gate and "reported, never fabricated" are unchanged. Pass 1 is also told
  3 is the CEILING (the writer's "Max 2-3 memes"): Pass 2 has rendered every seed one-for-one,
  so its own cap would not stop an over-seeded plan. `uat/tests/
  test_media_seed_prompt.py` fails if a percentage split or a "leave meme_concept empty" default
  comes back, or if the stated floors drift from `plan_audit.py`. To re-measure, compare
  `audit_media_seeds()` on `archive/<date>/story_plan.json` with the `i.imgflip.com` count in
  that day's `newsletter_substack.html`.

- **Cross-section callback rule** — discussed but not yet implemented in pass2_writer.txt
  (callbacks only valid when same person/team/event appears in BOTH sections literally).
- **Content guardrails not in the pipeline** — the "no heavy politics / no gambling advice /
  injury humor / no moralizing" block lives only in `prompts/base_prompt.txt`, which is never sent
  to the API. Tragedy handling (Pass 1) and punching-down removal (editor Check 7) *are* enforced.
  Decide whether to port the rest into `pass2_writer.txt`.

---

## How to Run Locally

```bash
# Pull latest content + sports data
python fetch_content.py
python fetch_sports_data.py

# Generate newsletter (all passes)
python generate_newsletter.py

# Build box score images (per-sport, ordered, cropped PNGs)
python box_score/build_box_score.py --per-sport
python box_score/render_pngs.py        # needs Chrome/Chromium + Pillow

# Flags (generate_newsletter.py):
# --no-editor    skip the editor pass
# --no-gifs      skip GIF embedding

# UAT run against frozen fixtures (no live fetch)
python uat/run_uat.py
```

On Windows, prefix Python with `-X utf8` to avoid Unicode console errors (e.g.
`python -X utf8 generate_newsletter.py`). `render_pngs.py` uses your installed Chrome locally; in
CI, `python -m playwright install chromium` provides the browser.

Requires `.env` with: `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`, `YOUTUBE_API_KEY`, `IMGFLIP_USERNAME`,
`IMGFLIP_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_PASSWORD`. Substack publishing additionally needs
`SUBSTACK_COOKIES_STRING`, `SUBSTACK_PUBLICATION_URL`, and `PROXY_URL`.

---

## GitHub Actions

| Workflow | Trigger | Job |
|---|---|---|
| `daily-newsletter.yml` | `17 6 * * *` UTC (2:17 AM EDT) + dispatch | Full pipeline → email → Substack draft |
| `publish-substack.yml` | every 30 min, 11:30–20:00 UTC + dispatch | Publishes today's draft at the first slot past 12:30 PM ET (time-gated in-job) |
| `substack-ci-test.yml` | manual only | Substack connectivity check; creates and deletes a throwaway draft |
| `tests.yml` | push + PR + dispatch | The offline suites (21 Python + 1 Node) + the retry drill, 0 API calls |

Live secrets (Settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`,
`YOUTUBE_API_KEY`, `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_PASSWORD`,
`SUBSTACK_COOKIES_STRING`, `SUBSTACK_PUBLICATION_URL`, `PROXY_URL`, and two optional ones:
`SLAP_HEARTBEAT_URL` (the dead man's switch is off without it) and `SPORTS_DB_URL` (the
defending-champion check falls back to cutting the phrase without it; SLA-65).

Daily pipeline steps: checkout → setup Python → **start pipeline status** → heartbeat start → install deps →
install Chromium → fetch content → fetch sports data → check sports data health → validate →
generate newsletter → render box score PNGs → verify outputs → archive → **commit & push** →
**create Substack draft** → commit handoff → publish now if already past 12:30 PM ET →
**assess run quality** (`verify_run.py --record`) → **send the daily status email**
(`if: always()`) → heartbeat finish (`if: always()`) → commit email-sent marker → **run-quality gate** (`verify_run.py --gate`, `if: always()`, fails the job).
Every one of those from "install deps" down runs through `ci/run_stage.sh`.

Ordering notes: push is before the email so the size-guard's hosted-URL fallback resolves when
Gmail fetches it. The **email is last**, because it reports on everything above it — including
the Substack draft, which it could not name when the email ran in the middle. Every step between
the fetches and the email is `continue-on-error`, so nothing short-circuits the report; the gate
at the bottom is what turns a bad run red.

DST caveat: GitHub cron is UTC and ignores DST, so `daily-newsletter.yml` fires an hour earlier
in ET each winter (1:17 AM EST). The **publish job no longer has this problem** — its gate reads
`America/New_York`, so 12:30 PM ET is 12:30 PM ET year round.

**Re-running a failed run does NOT pick up a fix.** GitHub's "Re-run jobs" replays the run at its
*original* commit, so a run that failed before a fix was pushed fails again identically. To run
fixed code, use **Run workflow** (`workflow_dispatch`) on `main`. A dispatch with **"This is a
rerun of a failed/partial attempt today" checked (SLA-55)** is rerun-safe: it skips re-sending the
one daily email if `email_sent_state.json` already shows today's went out, and updates today's
existing Substack draft in place (`--rerun-safe`, a `PUT` via `substack_poc/publish.py`) instead of
minting a second one that orphans the first. Leave it unchecked for a deliberate full fresh run
(e.g. testing) — that still behaves exactly as before.

If the pipeline fails, check, in rough likelihood order: **Nitter RSS availability** (there is now
an outage probe that degrades to a headline-only newsletter), the **commit/push** step (daily box
score filename churn — must use `git add -A box_score/`), the **Playwright Chromium install**,
the **Substack proxy** (`PROXY_URL` — datacenter IPs are Cloudflare-blocked), model string
deprecation, and API rate limits.

---

## Change Log (what / why / when)

Most recent first. Daily auto-commits ("SLAP newsletter output for …" / "Substack draft handoff
for …") omitted.

**2026-09-26 — "Defending champion" is resolved against the sports database (SLA-65)**
- The first consumer of slap-sports-db. RULE 3.4 told the writer to verify "defending champion"
  against game_state.json, which only ever held yesterday's games, so Pass 3 could do nothing but
  ask a human to check. `champions_source.py` now puts each league's current champion into
  game_state.json; Check 3 confirms, corrects (HIGH) or asks for the phrase to be cut (LOW).
- Replaying the archive found three things the unit tests hadn't: sentence-opening words read as
  team names ("Against the defending champions."), tweets being checked as if they were our prose,
  and quoted text that could close the flag's HTML comment early. All three fixed and tested.
- RULE 3.4 (both copies, via `promote.py`) and editor Check 8 (source rule + Category B exception,
  both copies) point at the DEFENDING CHAMPIONS list. `psycopg[binary]==3.2.3` added (the
  version slap-sports-db's CI runs); `test_workflow_pins.py` now accepts pip extras as an exact pin.

**2026-09-23 — Dead man's switch: an alert that does not depend on the pipeline (SLA-56)**
- Every reliability check ran inside the job, so a scheduled run that never started — or was
  killed by `timeout-minutes` (09-21 finished at 24m48s of 30) — produced no email at all.
- `heartbeat.py` + two workflow steps ping healthchecks.io (option 1 in the ticket; the only one
  that survives an Actions outage). `start` precedes the installs so a pip death still counts as
  started; `finish` follows the email and sends `/fail` only when the email did NOT go out, so a
  failed-but-reported run does not produce a second alert.
- Cutoff 18:17 UTC, justified against the measured delay table in `docs/dead_mans_switch.md`.
  Configured in the service (cron `17 6 * * *` UTC, 12h grace); the code holds no cutoff.
- Stdlib only, always exits 0, inert until `SLAP_HEARTBEAT_URL` is set. Verified against a local
  HTTP server as well as the stubbed suite. **Armed 2026-09-23** (check created, secret added);
  the "zero false alarms over a week" criterion is being watched from there.
- `uat/tests/test_heartbeat.py` — signal decision table, URL suffixes, never-fails, wiring order,
  stdlib-only imports. 0 network requests.

**2026-09-23 — Pass 1 stops capping memes below its own floor (SLA-76)**
- Measured the open Known Issue against 22 days of archived plans: seeds ≥3 on only 2 days,
  median 1, and zero days where a seeded meme failed to render. Pass 1 under-seeds.
- The Pass 1 prompt asked for "at least 3" memes and for a 70/30 GIF/meme split in the same
  section; the split won. Replaced with one consistent instruction (a GIF and a meme are not
  competing for one slot; the subject gate is the only reason to fall short) in both copies.
  3 is stated as the ceiling too, matching the writer's cap, so the fix cannot overshoot.
- `uat/tests/test_media_seed_prompt.py` locks it. Not yet confirmed on live runs.

**2026-09-23 — Reliability review follow-ups (SLA-74)**
- A review of the week's reliability work (SLA-52/54/55/57/68) found three gaps the tests did
  not cover. Each fix has a test that fails against the old code.
- **The rerun checkbox could swallow the newsletter.** `email_newsletter.py` wrote
  `email_sent_state.json` after ANY successful send, including a FAILED-verdict failure report
  with no newsletter in it. A rerun with the box checked — the exact case the box exists for —
  then built the issue and skipped emailing it. Only a non-failed email (success or partial,
  i.e. one that carried the issue) writes the marker now.
- **A hung Claude call was unbounded.** See the SLA-54 rule above: `API_REQUEST_TIMEOUT` at both
  client sites, plus a 17-minute stage limit on "Generate newsletter". Before this, the only
  bound was the job's own 30-minute cap, which may kill the run before the status email.
- **The noon publish job installed unpinned packages** (`curl_cffi`, `python-dotenv`,
  `requests`) after SLA-57. Pinned there and in the Substack/Nitter CI checks, and locked by the
  new `uat/tests/test_workflow_pins.py`.
- Also: the email subject's date came from the runner's UTC clock while the panel used ET, so a
  run after 8 PM ET had a subject dated tomorrow. Both are ET now.
- Follow-up (SLA-75): "Commit email-sent marker" read "never ran" in every email, because it
  runs after the send. Stages can now be declared `after_email=True`; the email shows them as
  "runs after this email". The test fixture that built a "clean" status had every stage
  reporting, including this one, which can never happen on a real run; it now matches reality.

**2026-09-22 — Manual pipeline reruns are safe: no duplicate email, no orphaned Substack draft
(SLA-55)**
- Per the Known Issues note this ticket closes: a `workflow_dispatch` re-run after a failure did
  a full run — a second daily email, and a second Substack draft that overwrote
  `substack_post_state.json` and orphaned the first, needing manual cleanup. That risk discouraged
  using the one tool available for fixing a bad morning run.
- **Why `run_status.json` couldn't answer this.** It's gitignored and reset at the top of every
  run on purpose (see `run_status.py`) — but that also means it holds nothing from an *earlier*
  run today, because a manual re-dispatch gets a brand-new runner with a fresh checkout. Only what
  was committed to git survives between separate runs, which is exactly the shape of the existing
  `substack_post_state.json` handoff.
- A new `rerun_of_failed_run` `workflow_dispatch` input (default off — a deliberate full fresh run
  is unaffected) threads a `--rerun-safe` flag into two places:
  - `email_newsletter.py --rerun-safe` skips the send if `email_sent_state.json` (new, committed —
    unlike `run_status.json`) already shows today's email went out, from an earlier run today. It
    still records `email_sent=True` in `run_status.json` either way, so `verify_run.py --gate`
    reads the day correctly regardless of which run actually sent it. A new "Commit email-sent
    marker" step (continue-on-error, `if: always()`, declared in `PIPELINE_STAGES` like every
    other stage) commits the marker right after the send, mirroring the Substack handoff commit.
  - `substack_poc/publish.py --draft --rerun-safe` checks `--state-out`'s existing handoff before
    creating anything (`_rerun_safe_draft_plan()`, three outcomes): a still-open draft from
    *today* gets updated in place via `put_draft` instead of `post_draft`-ing a second one; no
    valid same-day handoff (missing, unreadable, stale, or the draft it names 404s) falls through
    to the ordinary fresh-draft path, unchanged; and — the one that isn't a copy of the original
    bug's shape — a draft that's **already published, or already carries its own Substack
    schedule**, creates NOTHING at all rather than falling through to a fresh draft. The first cut
    of this fix did fall through in that case, which is worse than the original bug: the new
    draft is unpublished, so the handoff would point at it and either "Publish late" later in the
    same run or the noon job would auto-publish it — a second LIVE post, not just an orphaned
    draft. Caught in review before this shipped, not after.
- `uat/tests/test_rerun_safe.py` — offline, no network/Substack/SMTP: locks the draft-reuse
  decision table, the marker's cross-run skip behavior (and that it's ignored without the flag),
  and that the workflow's new input/flags/stage stay wired together.

**2026-09-21 — A Substack image that never uploads is no longer silent (SLA-68)**
- The 2026-09-21 run shipped a Substack post missing **3 of 13 box score images** and reported
  **PIPELINE SUCCESS**. `upload_box_scores()` returned only its successes, so nothing downstream
  had a number to compare against, and `verify_run.py` counted the PNGs on disk rather than the
  ones that arrived. Found while grooming SLA-56/57, not during a fix.
- It now returns `(items, report)` and records `substack_images` to `run_status`. A shortfall
  makes the verdict **PARTIAL**, shows a SUBSTACK IMAGES section in the daily email naming the
  files, and warns in `verify_run.py`. The panel also says the emailed copy is unaffected,
  because it is — the email embeds from disk.
- **The time bound was the more urgent half.** 4 attempts x a 30s curl timeout plus backoff is
  ~132s per hard failure, so three failures cost ~11 minutes and the run finished at **24m48s**
  against `timeout-minutes: 30`. Two more would have killed the job — and a killed job never
  reaches the step that emails Abram, which is precisely what SLA-52 exists to prevent.
  `MAX_RETRY_SECONDS = 180` caps retries; `MAX_UPLOAD_SECONDS = 600` hard-stops the phase;
  attempts 4 → 3.
- **The first implementation had the bug its own test then caught.** The code comment promised
  "every image still gets its FIRST attempt" while the code skipped first attempts too — so 13
  slow-but-healthy uploads silently lost the last three. Split into two bounds with distinct
  jobs. A comment that disagrees with the code beside it is the same class of failure as the
  half-ported runners.
- Root cause of the failures themselves looks like residential-proxy flakiness (`curl (28)`
  timeouts through `PROXY_URL`), not a code bug: the 09-20 run uploaded 8 of 8. Not addressed
  here — this change makes it visible and bounded rather than fixing the proxy.
- `uat/tests/test_substack_uploads.py` — stubs the Substack API *and* the clock, so the
  time-box checks run instantly with no network and no real waiting.

**2026-09-20 — Transient API failures no longer end the day's run (SLA-54)**
- `runner_common.retry_api_call()` wraps all seven Anthropic call sites: 4 attempts, 4/8/16s
  backoff, transient only. `is_transient()` is built on the SDK's PUBLIC exception classes and
  `.status_code`, never its internals — the pin is 1.2.0 but a developer's local install may not be.
- **The SDK was already retrying twice.** The ticket's premise was half right, and saying so
  matters: what was actually missing was visibility (an SDK retry is silent), patience (its delay
  caps at ~8s), and keeping a transport failure out of the model's conversation.
- **That last one was a real bug.** Pass 1's validation-retry loop caught API errors too and told
  the model the failure was "usually caused by special characters (unescaped quotes, backslashes)
  inside string values" — false for a 429, and it burned one of only three validation attempts,
  grew the prompt, and retried instantly with no backoff. Fixed in BOTH runners in this commit;
  the misleading sentence is gone from prod (UAT never carried it).
- Non-transient failures — 400/401/404/422, a bad model string, the SDK's client-side streaming
  `ValueError` — raise immediately with a line saying waiting will not help. Retrying those is how
  a fast red run becomes a slow one that looks like an outage.
- `max_retries=SDK_MAX_RETRIES` is now explicit at both client construction sites.
- Retries reach the morning email (`api_retries` → the SLA-52 panel) but deliberately do not move
  the verdict: the run survived them.
- **Drill switch, per Abram's request:** `SLAP_SIMULATE_API_FAILURES` fakes 429s/529s/timeouts, a
  fatal 400, or a permanent outage. Exposed as a `workflow_dispatch` input only, and
  `test_api_retry.py` fails if anything else sets it. `uat/probe_retry.py` runs it offline with a
  plain-English PASS/FAIL per behaviour and now runs in `tests.yml`.
- `uat/tests/test_api_retry.py` — 70+ offline checks, 0 API calls, 0 seconds slept (the clock is
  stubbed). It counts the wrapped call sites **in the source** of all three files, because two of
  them live in `run_pass1`/`run_pass2`, which are declared divergent — exactly where a half-port
  hides. All three `KNOWN_DIVERGENT` entries re-pinned; `run_pass1` and `run_pass2` changed in
  both runners in this commit, as the standing rule requires.

**2026-09-20 — One daily status email, always sent (SLA-52)**
- Abram got several notifications a day and silence on the days that mattered. `verify_run.py`
  failing the job, `check_game_state.py` finding a blocked ESPN fetch, a Substack draft that was
  never created — all of it reached the GitHub Actions UI and stopped there. Worse, the email
  step sat in the MIDDLE of the workflow, so a run that died at Pass 2 sent nothing at all.
- There is now exactly one email, sent last, under `if: always()`: status panel (SUCCESS /
  PARTIAL / FAILED, every stage, the real error text of whatever broke), run-quality findings,
  the cost breakdown, the Substack draft confirmation, then the newsletter with box scores inline.
  **Verified by rendering all three variants against the real 2026-09-19 issue**, not just asserted.
- `pipeline_status.py` + `ci/run_stage.sh`: every stage tees its log, records its exit code, and
  re-raises. `PIPELINE_STAGES` is the declared list that lets the report print "never ran" for a
  stage nothing could record, and `uat/tests/test_pipeline_status.py` parses the workflow and
  fails if the two disagree in either direction — including `critical` vs `continue-on-error`.
- **The status email imports nothing outside the standard library.** A run that dies in
  `pip install` must still be able to say so, and the installs are declared stages for that reason.
- `verify_run.py` gained `--record` (before the email, always exits 0) and `--gate` (after it,
  carries the exit code). Splitting them is what lets a broken issue still send the email that
  explains it. The plain invocation is untouched, so `test_run_quality.py` still locks it.
- **Warnings deliberately do not downgrade the verdict.** Sub-floor memes and thin slates fire on
  most days; PARTIAL every morning is a headline nobody reads.
- Noon stays silent on success. `publish.py --result-out` classifies each outcome and
  `email_newsletter.py --publish-alert` emails only the ones that need Abram — a missing or stale
  handoff, or a crash. A draft he published, deleted or scheduled himself sends nothing.
- `generate_newsletter.main()` now calls `pipeline_status.ensure_started()` instead of
  `run_status.reset()`: two fetch stages record into that file before the generator starts, and a
  reset would have wiped them. Re-pinned in `test_runner_drift.py`'s `KNOWN_DIVERGENT`.

**2026-09-18 — NFL box score shows real division standings (SLA-45)**
- The NFL summary image showed ESPN's standings as one flat league-wide list cut to 16 rows —
  no divisions, half the league missing. `_nfl_division_standings()` in `build_box_score.py`
  now renders `nfl_standings.py` from `sports.nfl.season_games`; an empty or malformed log
  falls back to the old flat list rather than no table. At the 400px render width the
  component's own breakpoints drop PCT, CONF and DIV.
- **Verified against live data, run locally** (ESPN is 403 from cloud sessions): W/L/T, PF, PA,
  DIFF, streak, DIV and CONF match ESPN for all 32 teams. For **ordering, NFL.com is the
  reference, not ESPN** — ESPN's standings API ordered four divisions differently from NFL.com,
  and we matched NFL.com in six of eight. The two misses are 3+ team ties, which are already
  labelled provisional (the multi-team procedure is SLA-51).

**2026-09-18 — NFL standings: records, official tiebreakers, responsive table (SLA-43)**
- `nfl_standings.py` turns the season game log `fetch_nfl_season_games()` writes to
  `game_state.json` (`sports.nfl.season_games`, added for SLA-42) into AFC-then-NFC division
  standings and a self-contained HTML component. Nothing in it fetches and nothing in it is
  Substack- or Beehiiv-specific, so the live-render test and the real-data wiring reuse the same
  code. `python -X utf8 nfl_standings.py` writes `nfl_standings_mock.html` from a seeded mock
  season; `--game-state <path>` reads the real log instead.
- **The 32-team division map is imported, not copied.** `fetch_sports_data.NFL_DIVISIONS` stays
  the only copy — a second hand-kept table is the exact drift this repo has paid for before.
- **Tiebreakers are the official two-team procedure, all twelve steps, and a step that cannot be
  evaluated falls through rather than guessing.** Head-to-head between teams that never met,
  common games under the four-game minimum, division record across divisions, net touchdowns
  (the feed carries scores, not TDs) all return "does not apply" and the procedure moves on.
  Deriving touchdowns from a final score would be the approximation the ticket rules out. Step 12
  is a coin toss, so it is **reported as unresolved** and the pair is ordered alphabetically —
  a randomized order would be non-deterministic and would hide the tie.
- **Three-or-more-team ties are flagged, not faked.** The NFL's multi-team procedure eliminates
  one club and restarts from step 1; it is not the two-team procedure applied pairwise. That is
  its own piece of work, so such a group is ordered provisionally and every row says so.
- **The responsive scheme deliberately does not copy MLB's, and here is why.** `PAGE_CSS`'s
  `@media(max-width:680px)` stacks two-column layout regions, never a table column;
  `_mi_std_table()` handles narrow screens by pre-trimming to six short columns at build time.
  NFL's column list is nine wide with two five-character records (DIV, CONF), so a single
  build-time trim would have to drop DIV and CONF at every width. Columns are cut by breakpoint
  instead — 680px reused from `PAGE_CSS`, then 560/460/380 — always off the BOTTOM of the
  ticket's priority list (PCT, then CONF, then DIV, then DIFF). Callers that know their
  destination strips `<style>` can still pre-trim via `columns=`.
- **"No horizontal scroll, ever" was measured, not asserted.** Chromium at 320/360/375/390/414/
  460/560/680/768/1024px: `scrollWidth` never exceeds the viewport and no element overflows —
  including the worst case where the `<style>` block is stripped and all nine columns survive.
  `table-layout:fixed` plus `overflow-wrap:anywhere` is what holds that: without the wrap, a
  header like "W-L-T" sets a min-content floor the fixed layout cannot go under.
- `uat/tests/test_nfl_standings.py` — 80+ offline checks, 0 API calls. Every tiebreaker step has
  its own fixture in which every earlier step is deliberately level, so the step under test is
  the one that decides; otherwise a step that quietly stopped applying would still look fine.

**2026-09-15 — Meme library expanded to 44; both libraries editable from a page**
- 14 templates and 5 engines added, chosen by comparing *demand against supply* rather than by
  popularity. `meme_history.json` held 54 memes over 28 days grouped by engine:
  `emotional_whiplash` had been used 5 times against ONE template, `lopsided_exchange` 4 against
  one. With ~13 meme slots a week against a 7-day cooldown, and `swap_cooled_templates()` only
  able to swap WITHIN an engine, depth 1 means the repeat is kept rather than avoided. Panel
  ORDER for all 14 was settled by rendering marker captions and looking at the image; two were
  not what a guess would have said (`flex-tape` index 2 is in the TOP panel;
  `mother-ignoring-kid-drowning` index 2 is the mother, not the drowning child).
  `emotional_whiplash` and `forced_choice_dilemma` are still depth 1 on purpose — nothing in
  Imgflip's top-100 is an honest sibling, and forcing one is the failure mode `_meta.engines`
  warns about.
- `library_studio.html` + `library_studio.bat` + `library_studio_server.py`: review, add, edit
  and delete entries in both libraries from a browser, with real Imgflip and Giphy previews, and
  save straight to disk. See the rules above for why the `.bat` rather than the `.html`, and for
  what became generated as a result (`CURATED_TEMPLATES`, and `meme_selector_index.txt`, which
  is deleted).
- `uat/tests/test_library_studio.mjs` locks the browser's copies of `library_json.py` and the
  meme checks, and `tests.yml` runs it.

**2026-09-15 — NFL and college football get box scores; CFB is ranked-only**
- Football had never produced a box score. Not a regression — the code path did not exist, in
  three places at once (fetch, parse, render), and the renderer gap covers NBA and NHL in the
  regular season too. See the Box Score System section for the measured before/after.
- `fetch_sports_data.py`: `nfl`/`ncaafb` added to `box_sports`; `_parse_football_box()` +
  `_parse_football_scoring()`; `parse_game()` now captures `home_rank`/`away_rank` from
  `curatedRank` (99 is ESPN's unranked sentinel); `fetch_cfb_rankings()` and `_ranked_game_ids()`;
  football entries in `_LEADERS_CONFIG` and `_STAT_LABELS`.
- `box_score/build_box_score.py`: `_mi_football_game()`, `_mi_football_side()`,
  `_mi_football_scoring()`, `_mi_rankings()`, `_render_football_sections()`;
  `build_mlb_chunk_blocks()` generalized to `build_chunk_blocks(game_state, sport_key, …)` with
  the old name kept as a wrapper; `_mi_stat_table()`'s row label was hardcoded to `Batter`/
  `Pitcher` and now comes from `_STAT_TABLE_LABELS`; `_mi_cat()` accepts `team_set=None` for
  leagues with no AL/NL split.
- `uat/tests/test_football_box.py` — 65 offline checks, 0 API calls. Verified end to end by
  rendering a real 13-game NFL Sunday and a 64-game CFB Saturday through the actual CLI and
  Playwright: 6 NFL images and 3 CFB images, each in the same size class as an MLB chunk.

**2026-09-01 — Pass 1 streams; the first post-merge run had failed outright**
- The 2026-09-01 scheduled run (168) died in Pass 1 with "Streaming is required for operations
  that may take longer than 10 minutes" — no newsletter, no email, no Substack draft. Three
  attempts burned in under a second at zero cost, because that error is a **client-side SDK
  guard**, not an API call. Pass 1 now streams and keeps `max_tokens=32768`; see the Model & Cost
  note on the 21,333 non-streaming ceiling. (2b642a7)
- **The bug was a half-ported change.** UAT had already hit this and already streamed its Pass 1
  call, with a comment naming the exact error. The 8/27→9/01 merge carried the 16,384 → 32,768
  raise into production but not the streaming call that made it safe. `promote.py` only diffs
  *prompts*, so a runner-code divergence was structurally invisible to it — the same class of
  problem as the "shared logic lives at the repo root" rule, except Pass 1's body is still
  duplicated between `generate_newsletter.py` and `uat/generate_newsletter_uat.py`.
- Pass 1's handler caught every exception and reported it as "API error (likely malformed JSON in
  tool input)", so a `ValueError` about streaming was labelled a JSON-escaping bug and pointed at
  a fix from May. It now reports the exception type and message. Same wording fixed in UAT.
- Re-run 169 on the fix was green end to end: Pass 1 went from a 2-second rejection to 6m32s of
  real generation, 22 tweets, 3 box score images, email sent, draft 213696047 created.

**2026-09-02 — GIF library wired into prod; runner body shared; history double-write fixed**
- The 09-01 issue shipped 22 tweets, 1 meme and **0 GIFs**. Pass 2 had emitted 7
  `data-library-category` placeholders; production had no consumer for them, so they shipped as
  invisible empty divs under a "No GIF placeholders found" log line. Prod now runs the same
  library-first sequence UAT had, and `strip_orphan_gif_placeholders()` removes and *names* any
  placeholder no consumer claimed. Replaying the shipped draft renders 7/7. (6c264a6)
- `runner_common.py`: 24 byte-identical functions lifted out of both runners along with the model
  constants, `PRICING` and `PASS_COSTS`. Duplicated LOC 668 → 0. `configure(prompts_dir=)` keeps
  the UAT prompt fork intact and raises rather than letting a second import repoint it.
  `uat/tests/test_runner_drift.py` fails on any undeclared divergence. Four remain, declared:
  `run_pass1`, `run_pass2`, `pre_edit`, `main`. (34c9880)
- Promoted `normalize_plan` from UAT (prod normalized neither `beats[]` nor `gif_tier`) and ported
  `escape_html` into prod's `embed_gifs_in_html` — drift was not only UAT-ahead-and-harmless.
- **Every history row was written twice.** Both writers appended new entries to the in-memory
  `history` for same-run dedup, then saved `new_entries + history`. `gif_history.json` and
  `meme_history.json` each held 60 rows and 30 distinct entries; the GIF file covered only **6
  days against a 7-day rotation lookback**, so that rule was silently under-enforced. Fixed by
  saving against the pre-run snapshot, which keeps same-run dedup working — deleting the insert
  would have broken it the other way. Both files de-duplicated in place.
- `story_plan.json` is now written by production and archived daily. It was discarded before, so
  when 09-01 shipped 1 meme against a floor of 3 there was no way to tell whether Pass 1
  under-seeded or Pass 2 under-emitted.
- `anthropic` pinned to `==1.2.0`.

**2026-08-27 → 09-01 — Meme + GIF libraries and deterministic audits reach production**
- Merged six weeks of UAT-only work into the shipping pipeline: beats, the 30-template meme
  library, the tiered GIF library, and the audits. Production had none of it — the note in this
  file claiming beats merged on 8/21 was wrong. (623d659)
- Replaced self-graded rules with arithmetic in `plan_audit.py`, shared by both runners. Account
  caps, §2.2 redundancy and the tweet count had all been things the model was asked to follow and
  then asked to report on; on 8/27 it reported a cap violation accurately and shipped anyway.
  (08c902c, 4ddbd8c, aee3950)
- Tweet budget: 35 → 24 on real data, pruning `beats[].media` in lockstep. Two bugs the
  before/after caught: a per-issue insider cap emptied a whole story, and dropping an account's
  first tweet made its second look over-cap. (4ddbd8c)
- Media seed floor of 3 GIFs + 3 memes; media target 50% → 40%. GIF shortfalls backfill from the
  beat's own `landing`; meme shortfalls are reported, never fabricated. (7958fbf)
- Verified every meme box count against Imgflip, then every panel ORDER by render. 16 templates
  corrected in total, including `vince-mcmahon-reaction` (5 panels, not 4 — was shipping a blank
  payoff frame) and `distracted-boyfriend` (subject is box 1; boxes 0 and 2 were reversed).
  `meme_box_check.py` ported to prod so a short caption set can no longer report success.
  (5aa1fdf, 965b6b7)
- `uat/promote.py` replaces hand-copying prompts, refusing destructive copies and prompts whose
  placeholders the destination cannot substitute. (c473580)
- Pass 1 `max_tokens` 16,384 → 32,768 alongside beats. (0e093f2)

**2026-08-21 → 08-23 — Fetch hardening + AI-speak bans**
- Nitter outage probe: detects a full outage and degrades to a headline-only newsletter instead of
  hanging. (8b5587f)
- Bounded RSS fetches with a socket timeout, capped total tweet-fetch time, and unbuffered CI logs
  so a hang shows *where* it hung instead of nothing at all — the 8/21–8/23 hangs were completely
  silent. (0269925, 4149b11, 80dc636)
- Pruned dead handles, dropped retweets, added per-handle health reporting. `HouseOfHighlights` →
  `HoHighlights`, `KevinOConnorNBA` → `KevinOConnor`, `CoveringCBB` removed. (3b19ffb, 940997d)
- Banned meta-narration ("here's the karmic layer," announcing "punchline") and vague hedging
  ("went there") at the *pattern* level, not just literal matches, in both writer and editor.
  (ee7744b, 43a89f5)

**2026-08-19 → 08-21 — UAT beats system, merged to prod**
- Pass 1 now emits beat skeletons per story; Pass 2 writes against them and drops the sentence
  cap. Closes the borrowed-tweet loophole. Pass 1 got its own model constant. (092118e, ff841cd,
  4aca5d1, 46e5e13, 1195fb9)
- UAT sandbox gained a media-mix + highlight GIF pipeline and partial-run output. (eee567a,
  8d48026, 183676f)

**2026-06-04 → 06-25 — Substack auto-post goes live**
- Routed Substack through a residential proxy with `curl_cffi` to get past the Cloudflare 403 on
  Actions' datacenter IP. (5ff0d83, b6da208)
- Auto-create a draft in the daily pipeline; upload box score images into it. (0a01d54, 768b04f)
- Substack removed its `/schedule` endpoint → replaced with a **two-job publish**: morning draft +
  12:30 PM ET publish job reading a committed handoff file. Defaults to emailing subscribers.
  (1bdfade, 92b0a84, a29076e)
- Hardened tweet hydration: retry syndication, resolve all t.co links, emit Substack's
  `img_url`/`link_url` shape so photo/video/quote embeds render. (12fa2cd, 4b89274)
- Retry Substack auth on transient proxy failures. (b667ab3)

**2026-06-08 → 06-16 — Highlights + calendar**
- Highlight video embeds for MLB/NHL, then v2: clips synced to our writing, cool plays and a Top
  Plays cluster. World Cup coverage with official @FIFA embeds + group-stage priority. (8191bad,
  c2b3af2, 1c00026)
- Story selector: added Stanley Cup Final and tennis majors to the Tier 1 calendar; stopped
  re-running prior series games. (50b943b, ed5c101)

**2026-06-01 → 06-04 — Six-pass refactor + policy tidy**
- Six-pass pipeline refactor, Opus 4.7 for Pass 2, cost tracking, rolling rules 9-11.
- Unified account-cap policy (headliner cap, ATL uncapped), aligned media-repeat window to 7 days,
  retired duplicate rolling_feedback rules and routed Pass-1 rules to Pass 1. (23e48db, db26d37,
  bf425f7)

**2026-05-26 — Box scores delivered inline (cid) + email size guard**
- Switched box scores from attachments to **inline** images so one copy/paste carries them.
  (beb1c9b)
- Added the size guard and reordered the workflow so push runs before email. (d5bdfe2)

**2026-05-26 — Box score images: per-sport, ordered, clean, CI-rendered**
- Split one ~15,000px image into per-sport images, MLB chunked ~4 games each. (296ee15)
- Added the `<h2>Box Scores</h2>` section; stripped masthead chrome. (338f6da)
- Ordered playoffs-first via numeric filename prefix. (aae81ea)
- Switched CI rendering from `wkhtmltoimage` (JPG) to **Playwright Chromium + Pillow crop (PNG)**.
  (a3b08dc)
- `git add -A box_score/` so daily filename churn doesn't break the push. (239b7ee)

**2026-05-26 — Tweet + continuing-story fixes**
- **Tweet URL key mismatch:** the cross-reference filter read `t.get("url")` but `fetch_content`
  stores it under `link`, so EVERY real tweet was dropped as "fabricated." Now matches on the
  numeric status ID with a `link`/`url` fallback. (4e23c10)
- **`normalize_topic_key()`:** strips `-game3`/`-g3`/date suffixes so a playoff series collapses to
  one stable key, restoring continuing-story detection. (4e23c10)

**2026-05-16 → 05-25 — Structure + box score subsystem**
- Removed the Closer section: structure is Lead → Supporting → ATL only. (6169974)
- ATL made non-fatal and mandatory on somber-lead days; ATL fabrication fix. (bcdd1b9, 672dc46)
- `fetch_sports_data.py` + `claim_validator.py` wired in as Pass 3. (edccac6)
- Box score build, leaders, MLB AL/NL sections, NHL/NBA box scores. (9d9d5e5)
- Multi-panel meme `boxes[]` pipeline + prompt sync. (610788b)
