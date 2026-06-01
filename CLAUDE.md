# SLAP Newsletter — Claude Context

## What This Is
SLAP is a daily AI-generated sports newsletter. Group-chat narrator voice, not sportswriter.
5-minute lunch read. 8-12 tweets with editorial commentary, ending with a box score section.
GitHub Actions runs the full pipeline daily at 4am EDT (cron `0 8 * * *` UTC). No manual trigger required.

**Delivery (current reality, as of 5/26/2026):** the pipeline emails the finished newsletter to
the owner's Gmail (`email_newsletter.py`) as an HTML body with the per-sport box score images
**embedded inline** (under the "Box Scores" header, in order) — so a single select-all → copy →
paste into Substack carries the whole issue, images included. The owner does that one paste; no
attachments to download. Auto-posting is NOT live: Substack auto-post is blocked by Cloudflare
(403), and the Beehiiv post API is enterprise-only (403). So delivery is email → single paste.

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
Pass 1: Story Selector    → selects stories, assigns tweets (tool_use: submit_story_plan)
Pass 2: Writer            → writes HTML draft in SLAP voice
Pass 3: Claim Validator   → claim_validator.py, deterministic cross-check vs game_state.json
Pass 4: Voice Editor      → rewrites sportswriter-sounding <p> tags only
Pass 5: Pre-Edit          → deterministic Python auditor (tweet URL integrity, section mapping)
Pass 6: Editor            → mechanical checklist (flags + auto-fixes)
        ↓ adds "<h2>Box Scores</h2>" after Around the League
newsletter_draft.html / newsletter_substack.html / newsletter_email.html
        ↓
box_score/build_box_score.py --per-sport  → per-sport HTML (MLB chunked ~4 games)
box_score/render_pngs.py                   → cropped PNGs (Chromium screenshot + Pillow trim)
        ↓
email_newsletter.py → emails HTML body with box scores INLINE (cid:, or hosted URLs if >15MB) →
                      owner does one copy/paste into Substack (push runs BEFORE email)
```

Pass numbering is sequential (1–6) and matches execution order. Passes 3 and 5 are
deterministic Python (no LLM); passes 1, 2, 4, and 6 are Claude API calls.

---

## File Structure

```
slap-newsletter/
├── CLAUDE.md                  ← this file
├── feedback_log.md            ← issue review intake + review ritual (read when asked to review)
├── fetch_content.py           ← pulls ESPN/CBS RSS + Nitter RSS → raw_content.json
├── fetch_sports_data.py       ← pulls ESPN scores/standings/box scores → game_state.json
├── claim_validator.py         ← deterministic fact check vs game_state.json (Pass 3)
├── generate_newsletter.py     ← orchestrates all passes (main script)
├── generate_memes.py          ← Imgflip meme generation
├── email_newsletter.py        ← ACTIVE delivery: emails HTML body w/ box scores inline (cid + size guard)
├── raw_content.json           ← daily input: headlines + tweets (123KB typical)
├── game_state.json            ← daily ESPN ground truth (GITIGNORED build artifact — not committed)
├── recent_output.json         ← rolling 30-day story_log + dedup state (GIFs, memes, stories)
├── newsletter_draft.html      ← browser-preview output
├── newsletter_substack.html   ← Substack-paste output (bare tweet URLs)
├── newsletter_email.html      ← emailed body
├── gif_history.json           ← 7-day GIF dedup log
├── meme_history.json          ← meme dedup log
├── .env                       ← API keys (gitignored — never commit)
├── requirements.txt           ← incl. playwright + Pillow (box score rendering)
├── box_score/                 ← box score subsystem (see "Box Score System" below)
│   ├── build_box_score.py     ← builds per-sport HTML from game_state.json
│   ├── render_pngs.py         ← Chromium screenshot + Pillow crop → box_score_sport_*.png
│   └── box_score_sport_NN_*.{html,png}  ← per-sport images, numeric-prefixed for order
├── prompts/                   ← all prompt files (versioned in git)
│   ├── pass1_story_selector.txt
│   ├── pass2_writer.txt
│   ├── pass4_voice.txt
│   ├── editor_prompt.txt
│   ├── base_prompt.txt        ← loaded into project knowledge for Claude.ai sessions
│   ├── rolling_feedback.txt   ← hard rules from real output failures (max 3/session)
│   ├── voice_examples.txt     ← target voice — read before writing anything
│   ├── editorial_annotations.txt ← HOW TO THINK about story/tweet selection
│   ├── gif_reference.txt
│   ├── meme_reference.txt
│   └── Archive/               ← timestamped backups of old prompt versions
└── .github/workflows/         ← GitHub Actions cron (4am EDT daily)
```

---

## Prompt File — What Each Does

| File | Pass | Job |
|------|------|-----|
| `pass1_story_selector.txt` | Pass 1 | Selects 4-6 stories, assigns tweets, outputs via tool_use |
| `pass2_writer.txt` | Pass 2 | Writes full HTML draft in SLAP voice |
| `pass4_voice.txt` | Pass 4 | Rewrites sportswriter `<p>` tags; leaves everything else alone |
| `editor_prompt.txt` | Pass 6 | 8-check mechanical editor: auto-fixes + flags |
| `base_prompt.txt` | All | Loaded into claude.ai Project for ad-hoc sessions |
| `rolling_feedback.txt` | Pass 2 | Hard rules from output failures; overrides base_prompt |
| `voice_examples.txt` | Pass 2 + Pass 4 | The actual voice target — not a description, the target |
| `editorial_annotations.txt` | Pass 1+2 | Selection and curation logic |

---

## Key Rules (Read Before Touching Anything)

**Banned phrase sync:** `editor_prompt.txt` Check 1B and `pass2_writer.txt` BANNED PHRASES must
match exactly. When adding a new banned phrase, update BOTH files simultaneously. There is a
⚠ SYNC NOTE in editor_prompt.txt as a reminder.

**Pass 1 uses tool_use:** Story selector outputs via `submit_story_plan` tool, not raw JSON.
This was changed 5/12/2026 to fix GitHub Actions JSON escape failures from quote-heavy tweets.

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
  the "Box Scores" header (see Delivery below) — not as attachments — so one copy/paste carries them.

**Ordering:** files use a zero-padded numeric prefix (`box_score_sport_01_nba.png`, `02_nhl`, …)
so the email and shell glob attach them in a fixed order: **playoffs first** (per `SPORT_ORDER`,
NBA before NHL), **then regular season** (MLB chunks, WNBA last), golf/tennis at the end. The
playoff/regular split is computed per-sport from `game_state.json` (`_ordered_sport_keys`), so it
self-updates as seasons change — no calendar edits needed.

**Delivery — inline with a size guard:** `email_newsletter.py` injects `<img>` tags under the
"Box Scores" header so the images travel with one copy/paste.
- **Normal days** (≤ `MAX_INLINE_RAW_BYTES`, 15 MB raw): images embed **inline via `cid:`**
  (`multipart/related` + inline image parts) — self-contained, no external dependency.
- **Huge slates** (NFL Sundays, CFB Saturdays, March Madness — would exceed Gmail's 25 MB send
  limit after ~37% base64 inflation): falls back to **GitHub-hosted `<img>` URLs**
  (`raw.githubusercontent.com/<repo>/<branch>/box_score/…` via `_github_raw_base()`), so the email
  stays tiny regardless of slate size.
- **cid (not base64 data URIs) is deliberate:** it keeps the HTML body small, avoiding Gmail's
  ~102 KB "[Message clipped]" truncation that would otherwise break the copy/paste.
- **Push runs BEFORE email** in the workflow (with `continue-on-error`) so the hosted-URL fallback
  resolves when Gmail fetches it; the push can hiccup without ever blocking the email.

**"Bare" mode:** per-sport images omit all masthead chrome (no "The Box Score" title, date, sport
subtitle, "Data via ESPN", footer, or border lines) because the newsletter already carries a
"Box Scores" header. The **"MLB" section band appears on the first MLB image only**; the remaining
MLB images are continuations of one photo split for size.

**CI gotcha (fixed 5/26):** per-sport filenames shift with the daily slate size (WNBA might be
`06` one day, `08` the next). The commit step stages `box_score/` with `git add -A` so removed
files are staged as deletions — a bare glob only matches existing files and leaves stale deletions
unstaged, which breaks `git pull --rebase`.

---

## Change Log (what / why / when)

Most recent first. Daily auto-commits ("SLAP newsletter output for …") omitted.

**2026-05-26 — Box scores delivered inline (cid) + email size guard**
- Switched box scores from email **attachments** to **inline** images under the "Box Scores"
  header, so a single copy/paste into Substack carries them (no download/open/paste-each). (beb1c9b)
- Added a **size guard**: inline `cid:` normally; if total PNG bytes exceed 15 MB raw (~20.5 MB
  encoded), fall back to **GitHub-hosted `<img>` URLs** so the email never hits Gmail's 25 MB send
  limit. Reordered the workflow so **push runs before email** (hosted URLs live when fetched), with
  `continue-on-error` so a push hiccup never blocks delivery. (d5bdfe2)
- Why cid not base64 data URIs: keeps HTML body small → avoids Gmail's ~102 KB clip that would
  break the copy/paste.

**2026-05-26 — Box score images: per-sport, ordered, clean, CI-rendered**
- Split the one giant box score image (≈15,000px tall, too big to paste) into per-sport images,
  MLB chunked ~4 games each. (296ee15)
- Added a `<h2>Box Scores</h2>` section after Around the League; stripped masthead chrome from the
  images. (338f6da)
- Ordered images playoffs-first via numeric filename prefix so attach order is deterministic. (aae81ea)
- Switched CI rendering from `wkhtmltoimage` (JPG) to **Playwright Chromium + Pillow crop (PNG)** so
  CI output matches the locally-approved images; "MLB" header now on the first MLB image only;
  added `playwright`+`Pillow` to requirements. (a3b08dc)
- Fixed the commit step to `git add -A box_score/` so daily filename churn doesn't break the push. (239b7ee)

**2026-05-26 — Tweet + continuing-story fixes**
- **Tweet URL key mismatch:** the cross-reference filter read `t.get("url")` but `fetch_content`
  stores it under `link`, so the valid-URL set was always empty and EVERY real tweet was dropped as
  "fabricated" — leaving stories tweetless and ATL filled with invented `/status/123456789x`
  placeholders. Now matches on the numeric status ID with a `link`/`url` fallback, plus a guard that
  no-ops if the URL set is empty. (4e23c10)
- **`normalize_topic_key()`:** strips `-game3`/`-g3`/date suffixes so a playoff series collapses to
  one stable key — the model kept embedding the game number, which defeated continuing-story
  detection and made the series re-explain its backstory every issue. (4e23c10)

**2026-05-22 → 05-25 — Box score subsystem brought online**
- `fetch_sports_data.py` + `claim_validator.py` added and wired in as Pass 3. (5/14, edccac6)
- Box score build, leaders, MLB AL/NL sections, NHL/NBA box scores. (5/22, 9d9d5e5)
- Multi-panel meme `boxes[]` pipeline + prompt sync. (5/23, 610788b)
- ATL made non-fatal and mandatory on somber-lead days. (5/24, bcdd1b9)
- ATL fabrication fix + PNG box score attachment. (5/25, 672dc46)

**2026-05-16 → 05-20 — Structure + dedup**
- Removed the Closer section: structure is now Lead → Supporting → ATL only. (5/16, 6169974)
- `normalize_plan()`, removed closer from tool schema, ATL retry threshold 8→5. (5/17)
- Removed MailerLite; added `feedback_log.md` review intake. (5/19)
- GIF/meme volume, dedup, and double-logging fixes. (5/20, 1e6d517)

---

## Model & Cost

- All passes: `claude-sonnet-4-5` (or current Sonnet family — pin to family string, not version)
- Prompt caching enabled on all passes
- Estimated cost: ~$2-5/month
- Pass 1 `max_tokens`: 16,384 (raised 4,096 → 8,192 → 16,384; silent truncation caused ATL
  regression and truncated story plans on full slates). Other passes: 8,192.

---

## Known Issues / TODO

- **`game_state.json` freshness — no guard (LOOSE END, open):** `fetch_sports_data.py` always
  stamps the file with today's date, even if every ESPN call silently fails (each is wrapped in
  try/except returning empty). So a today-stamped but *hollow* file is possible, and it degrades
  silently — the newsletter loses its ground-truth block (`format_game_state_summary` returns ""
  on empty) and box scores thin out or vanish, with no alarm. A date check is useless; the real
  fix is a **content-presence guard** (fail CI if the payload has no games/standings) plus logging
  `as_of_date` + per-sport counts in CI. The 5/26 run was healthy (full slate), so no confirmed bug
  — just no safety net.
- **Delivery is manual paste, not auto-post** — Substack auto-post is blocked by Cloudflare (403);
  Beehiiv post API is enterprise-only (403). Current flow: Gmail email + manual paste. Re-evaluate
  if either platform opens up.
- **Imgflip 'expanding-brain' 4-panel meme** — historically flaky; verify after the 5/23 `boxes[]`
  multi-panel pipeline change.
- **Cross-section callback rule** — discussed but not yet implemented in pass2_writer.txt
  (callbacks only valid when same person/team/event appears in BOTH sections literally)
- **Championship/speculation facts** — Pass 2 still writes "defending champions" from training data
  without verifying; fix is adding to Pass 2 mandatory verification list (deferred)

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
# --no-editor    skip Pass 3 editor
# --no-gifs      skip GIF embedding
```

On Windows, prefix Python with `-X utf8` to avoid Unicode console errors (e.g.
`python -X utf8 generate_newsletter.py`). `render_pngs.py` uses your installed Chrome locally; in
CI, `python -m playwright install chromium` provides the browser.

Requires `.env` with: `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`, `IMGFLIP_USERNAME`,
`IMGFLIP_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_PASSWORD` (email delivery).
`SUBSTACK_*` / `BEEHIIV_*` vars are legacy — auto-post is not currently used (see Known Issues).

---

## GitHub Actions

Workflow: `.github/workflows/daily-newsletter.yml` — runs daily at 4am EDT (cron: `0 8 * * *` UTC).
Also supports manual `workflow_dispatch` (Actions tab → Run workflow), with `skip_editor` /
`skip_gifs` inputs.

Live secrets (Settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`,
`IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_PASSWORD`. (MailerLite and the
SUBSTACK_* secrets were removed/retired — no longer referenced by the workflow.)

Pipeline steps: checkout → setup Python → `pip install -r requirements.txt` →
`playwright install --with-deps chromium` → fetch content → fetch sports data → validate →
generate newsletter → render box score PNGs → archive → **commit & push** (continue-on-error) →
email. (Push is before email so the size-guard's hosted-URL fallback resolves when Gmail fetches it.)

If the pipeline fails, check, in rough likelihood order: the **commit/push** step (daily box score
filename churn — must use `git add -A box_score/`), the **Playwright Chromium install**, model
string deprecation, Nitter RSS availability, and API rate limits.
