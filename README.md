# SLAP Newsletter

SLAP is a daily sports newsletter designed to replace doomscrolling. Instead of falling down a
Twitter rabbit hole, readers get a curated, five-minute lunch read that delivers the best of
sports Twitter and internet culture — commentary, GIFs, memes, and stats — without the infinite
scroll trap.

SLAP is built for the sports fan who wants to stay in the loop without losing an hour of their
day. Every edition is written by an AI agent with a consistent editorial voice: the friend who
watched every game, knows every stat, and can't wait to share what they saw — without making you
feel like you missed something. It's the group-chat narrator, not a sportswriter.

**Format:** 8-12 tweets with editorial commentary, GIFs/memes as reactions (not illustrations),
ending with a "Box Scores" section. Five-minute read. Runs daily via GitHub Actions at 2:17am EDT.

**Voice:** Inclusive, joyful, evangelizing — the opposite of sports gatekeeping.

**Structure:** Lead Story (biggest news, can run up to 80% of the issue on huge days) →
Supporting Stories (2-4, scaled to how newsy the day is) → Around the League (mandatory,
tweet-only, runs regardless of lead tone) → Box Scores.

**Core sports:** NFL (year-round anchor), NBA, NHL, MLB, College Football, College Basketball,
World Cup. **Secondary:** Golf, Tennis, Boxing/MMA. **Lighter coverage:** WNBA, Women's College
Basketball, College Baseball, non-World Cup soccer, motorsports. Olympics tiered individually
by sport. Earned narrative (e.g. a Slam upset ending a dominance streak) beats calendar-tier —
a story doesn't need to be a "big event" round to lead.

## Account List

Sourced via Nitter RSS. The live list is `TWITTER_HANDLES` in `fetch_content.py` (currently 51
accounts, spanning insiders, beat writers, meme/reaction accounts, and comedy accounts — grown
well past the original founding 18). Dead or renamed handles are pruned and logged inline as
comments when discovered. Handle health (reachable / rate-limited / dead) is tracked and printed
on every run.

## Tech Stack

- **Sports news:** ESPN + CBS Sports RSS feeds (free)
- **Tweets:** Nitter RSS feeds (free), with a per-handle retry ladder, an outage probe, and a
  hard time budget so a full Nitter outage fails fast instead of hanging CI
- **Ground truth / box scores:** ESPN JSON API (free, no key) → `game_state.json`
- **Agent:** Anthropic Claude API — Opus 4.7 writes the draft (Pass 2), Sonnet 4.5 handles
  selection and editing (Passes 1, 4, 6). Prompt caching enabled — ~$2-5/month
- **GIFs:** Giphy API · **Memes:** Imgflip · **Highlights:** YouTube + official league clips
- **Box score rendering:** Playwright (Chromium) + Pillow, per-sport PNGs
- **Delivery:** two paths, both automatic. (1) Gmail email with box scores embedded inline and a
  daily cost breakdown up top. (2) Substack — the morning run creates a draft, and a second
  workflow publishes it at 12:30 PM ET unless it was already published or pulled. Substack's
  Cloudflare block was solved by routing through a residential proxy; Beehiiv is unused (post API
  is enterprise-only).

## Data & Generation Pipeline

```
fetch_content.py      → raw_content.json   (ESPN/CBS RSS headlines + Nitter RSS tweets)
fetch_sports_data.py  → game_state.json    (ESPN scores/standings/box scores — ground truth)
        ↓
generate_newsletter.py  — orchestrates 6 passes via the Claude API
        ↓
Pass 1  Story Selector   → picks stories, emits beat skeletons, assigns tweets
                            (tool_use: submit_story_plan)
Pass 2  Writer            → writes the full HTML draft in SLAP voice, locked to Pass 1 beats
Pass 3  Claim Validator   → deterministic Python cross-check vs. game_state.json
Pass 4  Voice Editor      → rewrites sportswriter-sounding prose; leaves everything else alone
Pass 5  Pre-Edit          → deterministic Python audit (tweet URL integrity, section mapping)
Pass 6  Editor            → mechanical checklist: auto-fixes + flags
        ↓
newsletter_draft.html / newsletter_substack.html / newsletter_email.html
        ↓
box_score/build_box_score.py --per-sport  → per-sport HTML (MLB chunked ~4 games/image)
box_score/render_pngs.py                   → cropped PNGs (Chromium screenshot + Pillow trim)
        ↓
email_newsletter.py     → emails the finished issue, box scores inline
substack_poc/publish.py → creates the Substack draft; a 12:30 PM ET job publishes it
```

Passes 3 and 5 are deterministic Python (no LLM); passes 1, 2, 4, and 6 call Claude.

Prompt system: each pass owns its own file (`pass1_story_selector.txt`, `pass2_writer.txt`,
`pass4_voice.txt`, `editor_prompt.txt`). `rolling_feedback.txt` holds hard rules synthesized from
real published-issue failures and overrides `pass2_writer.txt` when the two conflict.
`base_prompt.txt` is project knowledge for claude.ai sessions — it is never sent to the API.

Changes are tested in the `uat/` sandbox — its own runner, frozen fixtures, and its own copy of
the prompts — before being promoted to production by hand.

**Guardrails:** no heavy politics, no sexual content, no personal tweets, no retweets, no
team-obsessive content.

## Feedback Loop

Issue runs → owner reviews the emailed draft → observations get logged in `feedback_log.md` →
periodic review session verifies patterns against archived issues and proposes rule updates →
approved rules get integrated into `rolling_feedback.txt` (or the relevant prompt file) by hand.
Not auto-synthesized — deliberately human-gated.

## Repo Layout

See `CLAUDE.md` for the full file-by-file breakdown, current known issues, and the change log.
That file is the source of truth for pipeline internals; this README is the front door.

## Status

Actively running daily via GitHub Actions. Both delivery paths are automatic: the email lands
each morning, and the Substack draft self-publishes at 12:30 PM ET unless it's pulled first.
See `CLAUDE.md` → "Known Issues / TODO" for what's still open (game_state freshness guard,
UAT prompt drift, the `Archive/` case collision, cross-section callback rule).
