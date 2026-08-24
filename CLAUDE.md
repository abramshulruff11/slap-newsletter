# SLAP Newsletter — Claude Context

## What This Is
SLAP is a daily AI-generated sports newsletter. Group-chat narrator voice, not sportswriter.
5-minute lunch read. 8-12 tweets with editorial commentary, ending with a box score section.
GitHub Actions runs the full pipeline daily at **2:17 AM EDT** (cron `17 6 * * *` UTC). Manual
`workflow_dispatch` is also available.

Why 2:17 and not a round hour: GitHub's scheduled workflows queue worst at the top of the hour.
Moving off it — and earlier — buys several hours of buffer before the morning review.

**Delivery (current reality, as of 8/23/2026): two paths run every day.**

1. **Email** — `email_newsletter.py` sends the finished newsletter to the owner's Gmail as an
   HTML body with the per-sport box score images **embedded inline** under the "Box Scores"
   header. A daily cost breakdown rides at the top. One select-all → copy → paste carries the
   whole issue, images included.
2. **Substack auto-post** — the morning run creates a **draft** via `substack_poc/publish.py`
   and commits a handoff file naming today's draft id. A separate workflow
   (`publish-substack.yml`) fires at **12:30 PM ET** and publishes that draft, unless it was
   already published, edited away, or deleted. Every "nothing to do" case is a graceful skip.

Auto-post **is live**. It was blocked by Cloudflare (403) through May; the fix was routing
through a residential proxy (`PROXY_URL`) with `curl_cffi`. Substack later removed its native
`/schedule` API endpoint, so scheduling is our own cron plus an immediate publish — hence the
two-job split. Beehiiv remains unused (post API is enterprise-only).

**Image delivery has a size guard (see Box Score System):** normal days embed images inline via
`cid:`; huge-slate days fall back to GitHub-hosted `<img>` URLs so the email never exceeds Gmail's
25 MB send limit.

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
Pass 2: Writer            → writes HTML draft in SLAP voice, locked to Pass 1 beats
Pass 3: Claim Validator   → claim_validator.py, deterministic cross-check vs game_state.json
Pass 4: Voice Editor      → rewrites sportswriter-sounding <p> tags only
Pass 5: Pre-Edit          → deterministic Python auditor (tweet URL integrity, section mapping)
Pass 6: Editor            → mechanical checklist (flags + auto-fixes)
        ↓ highlights.py injects MLB/NHL/World Cup highlight embeds
        ↓ adds "<h2>Box Scores</h2>" after Around the League
        ↓ build_email_html.py builds the email body
newsletter_draft.html / newsletter_substack.html / newsletter_email.html
        ↓
box_score/build_box_score.py --per-sport  → per-sport HTML (MLB chunked ~4 games)
box_score/render_pngs.py                   → cropped PNGs (Chromium screenshot + Pillow trim)
        ↓
push (continue-on-error) → email_newsletter.py → substack_poc/publish.py --draft
        ↓
publish-substack.yml at 12:30 PM ET → publishes the draft
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
├── generate_newsletter.py     ← orchestrates all passes (main script)
├── highlights.py              ← injects MLB/NHL/World Cup highlight video embeds
├── build_email_html.py        ← builds the email HTML body
├── generate_memes.py          ← Imgflip meme generation
├── email_newsletter.py        ← email delivery: HTML body + box scores inline (cid + size guard)
├── raw_content.json           ← daily input: headlines + tweets
├── game_state.json            ← daily ESPN ground truth (GITIGNORED build artifact)
├── recent_output.json         ← rolling 30-day story_log + dedup state (GIFs, memes, stories)
├── cost_summary.json          ← per-pass cost breakdown, surfaced atop the email
├── newsletter_draft.html      ← browser-preview output
├── newsletter_substack.html   ← Substack paste/post output (bare tweet URLs)
├── newsletter_email.html      ← emailed body
├── gif_history.json           ← 7-day GIF dedup log
├── meme_history.json          ← meme dedup log
├── substack_post_state.json   ← handoff: today's Substack draft id (morning → noon job)
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

---

## Prompt File — What Each Does

| File | Pass | Job |
|------|------|-----|
| `pass1_story_selector.txt` | Pass 1 | Selects stories, emits beat skeletons, assigns tweets, seeds media |
| `pass2_writer.txt` | Pass 2 | Writes full HTML draft in SLAP voice, locked to Pass 1 beats |
| `pass4_voice.txt` | Pass 4 | Rewrites sportswriter `<p>` tags; leaves everything else alone |
| `editor_prompt.txt` | Pass 6 | 8-check mechanical editor: auto-fixes + flags |
| `rolling_feedback.txt` | Pass 2 | Hard rules from output failures; overrides `pass2_writer.txt` |
| `voice_examples.txt` | Pass 2 + Pass 4 | The actual voice target — not a description, the target |
| `gif_reference.txt` | Pass 2 | GIF concept library + rotation rules |
| `meme_reference.txt` | Pass 2 | Imgflip template slugs + use-case rules |
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

**Pass 1 emits beats; Pass 2 is locked to them:** since the 8/21 UAT merge, Pass 1 produces a
beat skeleton per story and Pass 2 writes against it rather than free-forming. The lock closes
the "borrowed tweet" loophole where Pass 2 could pull a tweet assigned to another section.

**Rolling feedback owns hard rules:** `rolling_feedback.txt` overrides `pass2_writer.txt` when
in conflict. It captures real failure patterns from published issues. Max 3 rules added per
session. Rules are numbered (note: Rule 3 is missing — intentional gap from a removed rule).

**pre_edit() is deterministic Python:** Runs between Pass 4 (Voice) and Pass 6 (Editor) as
Pass 5. Splits HTML by h1/h2, maps sections to story plan by position, flags misassigned tweet
URLs as EDITOR FLAG comments. Not a Claude call — pure Python auditing.

**Calendar beats hierarchy:** Tier 1 sports calendar events (NBA Playoffs, Super Bowl, Masters,
etc.) override the NFL-first hierarchy in Pass 1. Check the calendar before selecting the lead.

**Evergreen content cannot lead:** A stat post about a finished season cannot be the lead story
if any active Tier 1 event is available (see Rule 5 in rolling_feedback.txt).

**Feedback intake lives in `feedback_log.md`:** When the user asks to review SLAP issues, propose
rule updates, or audit recent newsletters, read `feedback_log.md` first. The file contains the
review ritual (instructions for Claude) and the active log of unresolved observations. Do not
edit `rolling_feedback.txt` directly during review — propose changes for the user to integrate.

**UAT before prod:** `uat/` has its own prompt copies. Changes are tested there and promoted by
hand. When editing prompts, be explicit about whether you're touching the UAT fork or production.

---

## Box Score System (`box_score/`)

A "The Box Score" newspaper-style section appended after Around the League. Built from
`game_state.json` (ESPN ground truth), delivered as **images** because complex stat tables don't
paste cleanly into Substack as HTML.

**How it works:**
- `build_box_score.py --per-sport` writes one standalone HTML per sport that has data. MLB has a
  full daily slate, so it's split: a summary image (standings + leaders + results + today's games)
  plus box scores chunked **~4 games per image** (`build_mlb_chunk_blocks`). Other sports = one
  image each.
- `render_pngs.py` screenshots each HTML with **Chromium via Playwright** (full-page, locked 400px
  width, 2× scale for crisp text), then **Pillow** trims top/bottom whitespace. Prefers system
  Chrome locally; uses Playwright's bundled Chromium in CI. Output is **PNG** (lossless — crisper
  than JPG for text).
- `email_newsletter.py` embeds the `box_score_sport_*.png` files **inline in the email body** under
  the "Box Scores" header — not as attachments — so one copy/paste carries them.
- `substack_poc/publish.py` uploads the same images into the Substack draft.

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
- Constants are `MODEL_WRITER` and `MODEL_DEFAULT` in `generate_newsletter.py`.
- Prompt caching enabled on all passes. `PRICING` in `generate_newsletter.py` is the single source
  of truth for the cost breakdown that lands atop the daily email (`cost_summary.json`).
- Estimated cost: ~$2-5/month.
- Note on Opus 4.7: it follows instructions more literally than Sonnet, and its tokenizer can use
  1.0–1.35× more tokens for the same input. Budget for both when reading the cost summary.
- Pass 1 `max_tokens`: 16,384 (raised 4,096 → 8,192 → 16,384; silent truncation caused ATL
  regression and truncated story plans on full slates). Other passes: 8,192.

---

## Known Issues / TODO

- **`Archive/` vs `archive/` case collision (open, real):** git's index holds 11 files under
  `Archive/` (old code versions) and 910 under `archive/` (daily CI output). On Windows
  (`core.ignorecase=true`) these are the **same physical folder**. Because `.gitignore` lists
  `Archive/`, git **also silently ignores `archive/` locally** — new daily archive dirs never
  appear in local `git status`. CI is Linux (case-sensitive), so it commits them fine. Fix is to
  rename one side; until then, don't trust local `git status` for `archive/`.
- **`game_state.json` freshness — no guard (open):** `fetch_sports_data.py` always stamps the file
  with today's date, even if every ESPN call silently fails (each is wrapped in try/except
  returning empty). A today-stamped but *hollow* file degrades silently — the newsletter loses its
  ground-truth block (`format_game_state_summary` returns "" on empty) and box scores thin out,
  with no alarm. A date check is useless; the real fix is a **content-presence guard** (fail CI if
  the payload has no games/standings) plus logging `as_of_date` + per-sport counts in CI.
- **UAT prompt drift (open):** `uat/prompts/` is a fork of `prompts/` and promotion is manual.
  As of 8/23 four files are ahead in UAT (`pass2_writer` +126/-40, `pass1_story_selector` +95/-13,
  `editor_prompt` +16, `rolling_feedback` +10); the other four are identical. Things slip — prod's
  `pass2_writer.txt` has the 8/23 meta-narration bans while prod's `rolling_feedback.txt` is still
  missing the entry explaining why. A `uat/promote.py` that diffs and copies on confirm would fix
  this class of bug.
- **No `.gitattributes` (open):** CRLF/LF differences make files look changed when they're
  content-identical (`voice_examples.txt` diffs as different but is byte-equal ignoring line
  endings). This masks real drift. `* text=auto` would settle it.
- **`requirements.txt` drift (open):** `python-substack==0.1.22` and `curl_cffi` are installed
  inline by both workflows but are not in `requirements.txt`, so a local `publish.py` run fails
  until you install them by hand.
- **Imgflip 'expanding-brain' 4-panel meme** — historically flaky; verify after the 5/23 `boxes[]`
  multi-panel pipeline change.
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
| `publish-substack.yml` | `30 16 * * *` UTC (12:30 PM EDT) + dispatch | Publishes today's draft |
| `substack-ci-test.yml` | manual only | Substack connectivity check; creates and deletes a throwaway draft |

Live secrets (Settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`,
`YOUTUBE_API_KEY`, `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_PASSWORD`,
`SUBSTACK_COOKIES_STRING`, `SUBSTACK_PUBLICATION_URL`, `PROXY_URL`.

Daily pipeline steps: checkout → setup Python → `pip install -r requirements.txt` →
`playwright install --with-deps chromium` → fetch content → fetch sports data → validate →
generate newsletter → render box score PNGs → verify outputs → archive → **commit & push**
(continue-on-error) → **email** → **create Substack draft** → commit handoff.

Ordering notes: push is before email so the size-guard's hosted-URL fallback resolves when Gmail
fetches it. The Substack draft step runs **last**, deliberately without `continue-on-error` — the
email (the product) has already gone out, so a red run there surfaces the failure without ever
blocking delivery.

DST caveat: GitHub cron is UTC and ignores DST. In winter (EST) the two jobs fire at 1:17 AM and
11:30 AM ET respectively.

If the pipeline fails, check, in rough likelihood order: **Nitter RSS availability** (there is now
an outage probe that degrades to a headline-only newsletter), the **commit/push** step (daily box
score filename churn — must use `git add -A box_score/`), the **Playwright Chromium install**,
the **Substack proxy** (`PROXY_URL` — datacenter IPs are Cloudflare-blocked), model string
deprecation, and API rate limits.

---

## Change Log (what / why / when)

Most recent first. Daily auto-commits ("SLAP newsletter output for …" / "Substack draft handoff
for …") omitted.

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
