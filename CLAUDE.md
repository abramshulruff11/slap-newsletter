# SLAP Newsletter — Claude Context

## What This Is
SLAP is a daily AI-generated sports newsletter. Group-chat narrator voice, not sportswriter.
5-minute lunch read. 8-12 tweets with editorial commentary. Sent via Beehiiv (migrating from Substack).
GitHub Actions runs the full pipeline daily at 4am EDT. No manual trigger required.

---

## Pipeline Architecture — 5 Passes

```
fetch_content.py → raw_content.json
        ↓
generate_newsletter.py (orchestrates all passes via Claude API)
        ↓
Pass 1: Story Selector    → selects stories, assigns tweets (tool_use: submit_story_plan)
Pass 2: Writer            → writes HTML draft in SLAP voice
Pass 2.5: Voice Editor    → rewrites sportswriter-sounding <p> tags only
pre_edit()                → deterministic Python auditor (tweet URL integrity, section mapping)
Pass 3: Editor            → mechanical checklist (8 checks, flags + auto-fixes)
        ↓
newsletter_draft.html → post to Beehiiv API (scheduled)
```

---

## File Structure

```
slap-newsletter/
├── CLAUDE.md                  ← this file
├── feedback_log.md            ← issue review intake + review ritual (read when asked to review)
├── fetch_content.py           ← pulls ESPN/CBS RSS + Nitter RSS → raw_content.json
├── generate_newsletter.py     ← orchestrates all 5 passes (51KB — main script)
├── generate_memes.py          ← Imgflip meme generation
├── email_newsletter.py        ← legacy email delivery (mostly superseded)
├── raw_content.json           ← daily input: headlines + tweets (123KB typical)
├── recent_output.json         ← previous issue output (for dedup: GIFs, memes, stories)
├── newsletter_draft.html      ← final output
├── gif_history.json           ← 7-day GIF dedup log
├── meme_history.json          ← meme dedup log
├── .env                       ← API keys (gitignored — never commit)
├── requirements.txt
├── prompts/                   ← all prompt files (versioned in git)
│   ├── pass1_story_selector.txt
│   ├── pass2_writer.txt
│   ├── pass2_5_voice.txt
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
| `pass2_5_voice.txt` | Pass 2.5 | Rewrites sportswriter `<p>` tags; leaves everything else alone |
| `editor_prompt.txt` | Pass 3 | 8-check mechanical editor: auto-fixes + flags |
| `base_prompt.txt` | All | Loaded into claude.ai Project for ad-hoc sessions |
| `rolling_feedback.txt` | Pass 2 | Hard rules from output failures; overrides base_prompt |
| `voice_examples.txt` | Pass 2 | The actual voice target — not a description, the target |
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

**pre_edit() is deterministic Python:** Runs between Pass 2.5 and Pass 3. Splits HTML by h1/h2,
maps sections to story plan by position, flags misassigned tweet URLs as EDITOR FLAG comments.
Not a Claude call — pure Python auditing.

**Calendar beats hierarchy:** Tier 1 sports calendar events (NBA Playoffs, Super Bowl, Masters,
etc.) override the NFL-first hierarchy in Pass 1. Check the calendar before selecting the lead.

**Evergreen content cannot lead:** A stat post about a finished season cannot be the lead story
if any active Tier 1 event is available (see Rule 5 in rolling_feedback.txt).

**Feedback intake lives in `feedback_log.md`:** When the user asks to review SLAP issues, propose
rule updates, or audit recent newsletters, read `feedback_log.md` first. The file contains the
review ritual (instructions for Claude) and the active log of unresolved observations. Do not
edit `rolling_feedback.txt` directly during review — propose changes for the user to integrate.

---

## Model & Cost

- All passes: `claude-sonnet-4-5` (or current Sonnet family — pin to family string, not version)
- Prompt caching enabled on all passes
- Estimated cost: ~$2-5/month
- Pass 1 `max_tokens`: 8,192 (raised from 4,096 — silent truncation caused ATL regression)

---

## Known Issues / TODO

- **Imgflip 'expanding-brain' 4-panel meme** — not generating correctly; shows raw placeholder text
- **Cross-section callback rule** — discussed but not yet implemented in pass2_writer.txt
  (callbacks only valid when same person/team/event appears in BOTH sections literally)
- **Beehiiv migration** — Substack auto-post blocked by Cloudflare 403; migrating to Beehiiv API
- **Championship/speculation facts** — Pass 2 still writes "defending champions" from training data
  without verifying; fix is adding to Pass 2 mandatory verification list (deferred)

---

## How to Run Locally

```bash
# Pull latest content
python fetch_content.py

# Generate newsletter (all passes)
python generate_newsletter.py

# Flags:
# --no-editor    skip Pass 3 editor
# --no-gifs      skip GIF embedding
```

Requires `.env` with: `ANTHROPIC_API_KEY`, `GIPHY_API_KEY`, `IMGFLIP_USERNAME`,
`IMGFLIP_PASSWORD`, `SUBSTACK_EMAIL`, `SUBSTACK_PASSWORD`, `SUBSTACK_URL`

---

## GitHub Actions

Workflow: `.github/workflows/` — runs daily at 4am EDT (cron: `0 8 * * *` UTC).
Secrets are stored in GitHub repository settings (Settings → Secrets → Actions).
If the pipeline fails, check: model string deprecation, Nitter RSS availability, API rate limits.
