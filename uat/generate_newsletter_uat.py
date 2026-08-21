"""
SLAP Newsletter — Six-Pass Pipeline
Pass 1: Story Selector    — picks stories, assigns tweets, enforces account diversity
Pass 2: Writer            — generates newsletter HTML (with web search)
Pass 3: Claim Validator   — deterministic cross-check vs game_state.json (claim_validator.py)
Pass 4: Voice Editor      — prose-only pass to enforce SLAP voice
Pass 5: Pre-Edit          — deterministic tweet misassignment audit (no LLM)
Pass 6: Editor            — judgment-based checks (dueling sentences, punching down, etc.)

Outputs:
  newsletter_draft.html    — styled preview for browser
  newsletter_substack.html — bare tweet URLs ready for Substack embedding
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path

from dotenv import load_dotenv
import anthropic

# Windows consoles default to cp1252 and cannot encode the box-drawing and
# check/warning glyphs this pipeline prints.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# UAT PATHS — every read and write is scoped to uat/
# ---------------------------------------------------------------------------
# This is a COPY of generate_newsletter.py, not an import of it. The production
# module has hardcoded paths and module-level side effects, so it is duplicated
# and repointed rather than reused. Nothing here may read or write a production
# file. The only exception is the production .env, read for API keys.

UAT_DIR     = Path(__file__).resolve().parent
REPO_ROOT   = UAT_DIR.parent

SCRIPT_DIR  = UAT_DIR            # kept: referenced throughout as repo_root for history
PROMPT_DIR  = UAT_DIR / "prompts"
FIXTURE_DIR = UAT_DIR / "fixtures"
OUTPUT_DIR  = UAT_DIR / "output"
MEDIA_DIR   = UAT_DIR / "media"

for _d in (OUTPUT_DIR, MEDIA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Shared helper modules (claim_validator, generate_memes, highlights,
# build_email_html) live at the repo root. They are read-only from here — the
# ones that write history take a repo_root argument, which is passed OUTPUT_DIR.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# API keys only. Production .env is read, never written.
load_dotenv(REPO_ROOT / ".env", override=True)

# Inputs: frozen fixtures, so every run sees the same news day.
RAW_CONTENT_PATH     = FIXTURE_DIR / "raw_content_enriched.json"
GAME_STATE_PATH      = FIXTURE_DIR / "game_state.json"

# Outputs and history: uat/output/ only. The gif/meme/recent history copies are
# seeded from production so dedup logic behaves realistically without ever
# writing back to the real files.
RECENT_OUTPUT_PATH   = OUTPUT_DIR / "recent_output.json"
DRAFT_OUTPUT_PATH    = OUTPUT_DIR / "newsletter_draft.html"
SUBSTACK_OUTPUT_PATH = OUTPUT_DIR / "newsletter_substack.html"
EMAIL_OUTPUT_PATH    = OUTPUT_DIR / "newsletter_email.html"
HIGHLIGHT_PLAN_PATH  = OUTPUT_DIR / "highlight_plan.json"
HIGHLIGHT_HISTORY_PATH = OUTPUT_DIR / "highlight_history.json"
PROMPTS_DIR          = PROMPT_DIR   # load_prompt() reads this

# Hard block: this sandbox must never be able to send an email. The production
# send lives in email_newsletter.py, which is not imported here and must not be.
# main() below builds newsletter_email.html for layout review only.
import builtins as _builtins
_real_import = _builtins.__import__


def _blocked_import(name, *a, **kw):
    if name.split(".")[0] == "email_newsletter":
        raise ImportError(
            "UAT sandbox: importing email_newsletter is blocked — this copy "
            "must never be able to send mail."
        )
    return _real_import(name, *a, **kw)


_builtins.__import__ = _blocked_import

# Model selection. Pass 2 (Writer) uses Opus 4.7 for the prose quality lift —
# A/B trial week starting 2026-06-01. All other LLM passes use Sonnet 4.5,
# which is sufficient for selection/transformation tasks.
# NOTE on Opus 4.7: docs warn it follows instructions more literally than
# Sonnet — prompts tuned for Sonnet may need adjustment if output regresses.
# Also ships with a new tokenizer that can use 1.0–1.35x more tokens for the
# same input vs. 4.6; budget for that when reading the cost summary.
MODEL_DEFAULT = "claude-sonnet-4-5"   # Pass 4, 6
MODEL_WRITER  = "claude-opus-4-7"     # Pass 2
# UAT beats test: Pass 1 becomes the editorial brain (story selection + beat
# skeletons), so it gets its own model constant instead of sharing
# MODEL_DEFAULT with Pass 4/6. Pass 1B (highlight selection) stays on
# MODEL_DEFAULT deliberately — it's a small selection task, not judgment-heavy.
MODEL_PASS1   = "claude-opus-4-7"     # Pass 1

# Backwards-compat alias — some downstream code still references MODEL.
MODEL = MODEL_DEFAULT

# Per-million-token prices used by cost_summary. Cache write ≈ 1.25x base
# input; cache read ≈ 0.1x base input. Keep in sync with the Anthropic rate
# card. If pricing changes, update here — it's the single source of truth
# for the daily cost breakdown that lands at the top of the email.
PRICING = {
    "claude-sonnet-4-5": {"in": 3.0, "out": 15.0, "cw":  3.75, "cr": 0.30},
    "claude-opus-4-7":   {"in": 5.0, "out": 25.0, "cw":  6.25, "cr": 0.50},
}

# Accumulator for per-pass cost so email_newsletter.py can surface a price
# breakdown above the daily issue. Reset on each run.
PASS_COSTS: list[dict] = []
COST_SUMMARY_PATH = OUTPUT_DIR / "cost_summary.json"

# ---------------------------------------------------------------------------
# HTML wrappers
# ---------------------------------------------------------------------------

DRAFT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLAP Newsletter — Draft Preview</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 20px; line-height: 1.7; color: #1a1a1a; background: #fff; }}
        h1 {{ font-family: Arial, sans-serif; font-size: 28px; line-height: 1.2; margin-top: 40px; }}
        h2 {{ font-family: Arial, sans-serif; font-size: 22px; margin-top: 36px; border-bottom: 2px solid #e94560; padding-bottom: 6px; }}
        p {{ margin-bottom: 16px; font-size: 17px; }}
        blockquote.tweet {{ background: #f8f9fa; border-left: 4px solid #1da1f2; padding: 16px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; font-family: Arial, sans-serif; font-size: 15px; line-height: 1.5; }}
        blockquote.tweet strong {{ color: #1da1f2; }}
        blockquote.tweet a {{ color: #1da1f2; text-decoration: none; font-size: 13px; }}
        .gif-placeholder {{ background: #fff3cd; border: 2px dashed #ffc107; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        .meme-placeholder {{ background: #fce4ec; border: 2px dashed #e91e63; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        hr {{ border: none; border-top: 3px solid #e94560; margin: 40px 0; }}
        a {{ color: #e94560; }}
    </style>
</head>
<body>
{content}
</body>
</html>"""

SUBSTACK_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLAP Newsletter — Substack Ready</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 20px; line-height: 1.7; color: #1a1a1a; background: #fff; }}
        h1 {{ font-family: Arial, sans-serif; font-size: 28px; line-height: 1.2; margin-top: 40px; }}
        h2 {{ font-family: Arial, sans-serif; font-size: 22px; margin-top: 36px; border-bottom: 2px solid #e94560; padding-bottom: 6px; }}
        p {{ margin-bottom: 16px; font-size: 17px; }}
        .tweet-url {{ background: #e8f4fd; border: 1px solid #1da1f2; border-radius: 8px; padding: 12px 16px; margin: 20px 0; font-family: monospace; font-size: 13px; word-break: break-all; }}
        .gif-placeholder {{ background: #fff3cd; border: 2px dashed #ffc107; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        .meme-placeholder {{ background: #fce4ec; border: 2px dashed #e91e63; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        hr {{ border: none; border-top: 3px solid #e94560; margin: 40px 0; }}
        a {{ color: #e94560; }}
    </style>
</head>
<body>
{content}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def format_game_state_summary(game_state: dict) -> str:
    """
    Format game_state.json into a concise ground truth block for LLM prompts.
    Covers yesterday's completed games with series state for playoffs.
    Pass 1 and Pass 2 both receive this so they never rely on training data
    for scores, series state, or game numbers.
    """
    if not game_state or not game_state.get("sports"):
        return ""

    lines = [
        "## GROUND TRUTH: YESTERDAY'S GAME RESULTS",
        f"Source: ESPN API as of {game_state.get('as_of_date', 'unknown')}",
        "Treat as AUTHORITATIVE. Any claim about scores, series state, or game numbers",
        "must match this data exactly or be omitted. Do not invent or assume.",
        "",
    ]

    found_any = False
    for sport_key, sport_data in game_state.get("sports", {}).items():
        label = sport_data.get("label", sport_key.upper())
        completed = [
            g for g in sport_data.get("yesterday_games", [])
            if g.get("completed")
        ]
        if not completed:
            continue
        found_any = True
        lines.append(f"{label}:")
        for game in completed:
            home  = game.get("home_team", "?")
            away  = game.get("away_team", "?")
            hs    = game.get("home_score", 0)
            as_   = game.get("away_score", 0)
            ot    = " (OT)" if game.get("overtime") else ""
            winner = game.get("winner", "")
            lines.append(f"  {away} {as_}, {home} {hs}{ot}" + (f" — {winner} wins" if winner else ""))
            series = game.get("series")
            if series:
                if series.get("series_over"):
                    lines.append(f"    SERIES OVER. {winner} advances. Final: {series.get('home_wins',0)}-{series.get('away_wins',0)}.")
                else:
                    summary  = series.get("summary", "")
                    next_g   = series.get("next_game_number", "?")
                    elim_h   = series.get("elimination_game_for_home", False)
                    elim_a   = series.get("elimination_game_for_away", False)
                    elim_who = []
                    if elim_h: elim_who.append(home)
                    if elim_a: elim_who.append(away)
                    elim_str = f" {', '.join(elim_who)} eliminated." if elim_who else ""
                    lines.append(f"    PLAYOFFS — {summary}. Next: Game {next_g}.{elim_str}")
        lines.append("")

    if not found_any:
        return ""
    return "\n".join(lines)


def strip_code_fences(text: str) -> str:
    text = re.sub(r'^```(?:html|json)?\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text.strip()


def extract_text(response) -> str:
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def cost_summary(label: str, model: str, in_tokens: int, out_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> float:
    p = PRICING.get(model, PRICING[MODEL_DEFAULT])
    est = (cache_write * p["cw"] + cache_read * p["cr"]
           + in_tokens * p["in"] + out_tokens * p["out"]) / 1_000_000
    cache_note = ""
    if cache_read or cache_write:
        cache_note = f" (cache read: {cache_read:,} | cache write: {cache_write:,})"
    short_model = model.replace("claude-", "")
    print(f"  [{label}] {short_model} — {in_tokens:,} in / {out_tokens:,} out — ~${est:.4f}{cache_note}")
    PASS_COSTS.append({
        "label":       label,
        "model":       model,
        "in_tokens":   in_tokens,
        "out_tokens":  out_tokens,
        "cache_read":  cache_read,
        "cache_write": cache_write,
        "cost":        round(est, 6),
    })
    return est


# ---------------------------------------------------------------------------
# Tweet URL conversion for Substack
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GIF Auto-Embedding (Giphy API)
# ---------------------------------------------------------------------------

from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote
import time


def load_gif_history(repo_root: Path) -> list:
    """Load gif_history.json to check for recently used GIFs."""
    history_path = repo_root / "gif_history.json"
    if history_path.exists():
        try:
            return json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def is_recently_used(gif_url: str, history: list, days: int = 7) -> bool:
    """Return True if this exact GIF URL was used in the past N days."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    for entry in history:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if entry_date >= cutoff and entry.get("url") == gif_url:
                return True
        except Exception:
            continue
    return False


def normalize_gif_concept(search_term: str) -> str:
    """
    Extract the core meme reference from a search term for concept-level dedup.
    Strips context notes after " — " or " - ", then takes the first 4 words.
    This catches the same GIF being used with slightly different search terms.

    Examples:
      "Monkey Puppet Side-Eye — extremely awkward situation" → "monkey puppet side-eye"
      "Leonardo DiCaprio raising glass Django — pure respect" → "leonardo dicaprio raising glass"
    """
    concept = search_term.split(" — ")[0].split(" - ")[0]
    concept = concept.strip('"\' ').lower()
    words = concept.split()[:4]
    return " ".join(words)


def is_concept_recently_used(search_term: str, history: list, days: int = 7) -> bool:
    """Return True if a GIF with the same core concept was used in the past N days."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    concept = normalize_gif_concept(search_term)
    if not concept:
        return False
    for entry in history:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if entry_date >= cutoff:
                entry_concept = normalize_gif_concept(entry.get("search_term", ""))
                if concept == entry_concept:
                    return True
        except Exception:
            continue
    return False


def save_gif_history(repo_root: Path, new_entries: list, history: list):
    """Append new GIF uses to gif_history.json, keep last 60 entries."""
    history_path = repo_root / "gif_history.json"
    combined = new_entries + history
    combined = combined[:60]
    history_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")


def format_recent_media_block(repo_root: Path, days: int = 7) -> str:
    """
    Build a 'do not repeat' block of GIF concepts and meme templates used in the
    last N days, for injection into Pass 2's user message.

    Without this, Pass 2 cannot honor the rotation rules in gif_reference.txt /
    meme_reference.txt — it never sees what was recently used. History files log
    each item multiple times per day (embed runs over multiple output files), so
    concepts/slugs are de-duplicated here. Returns "" if there is no history.
    """
    from datetime import date, timedelta

    def _parse_date(s):
        try:
            return date.fromisoformat(s)
        except Exception:
            return None

    cutoff = date.today() - timedelta(days=days)

    # GIF concepts (most recent first, de-duplicated by normalized concept)
    gif_concepts, seen_concepts = [], set()
    for e in load_gif_history(repo_root):
        d = _parse_date(e.get("date", ""))
        if not d or d < cutoff:
            continue
        raw = e.get("search_term", "")
        concept = normalize_gif_concept(raw)
        if concept and concept not in seen_concepts:
            seen_concepts.add(concept)
            gif_concepts.append(raw.split(" — ")[0].strip())

    # Meme templates (de-duplicated by slug)
    meme_slugs, seen_slugs = [], set()
    meme_path = repo_root / "meme_history.json"
    if meme_path.exists():
        try:
            meme_hist = json.loads(meme_path.read_text(encoding="utf-8"))
        except Exception:
            meme_hist = []
        for e in meme_hist:
            d = _parse_date(e.get("date", ""))
            if not d or d < cutoff:
                continue
            slug = e.get("slug", "")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                meme_slugs.append(slug)

    if not gif_concepts and not meme_slugs:
        return ""

    lines = [
        "## RECENTLY USED MEDIA — DO NOT REPEAT (last 7 days)",
        "These GIFs and meme templates ran in recent issues. Pick different ones "
        "from the same emotional category in the GIF / MEME REFERENCE. The only "
        "exception is a deliberate escalation callback within a single story.",
    ]
    if gif_concepts:
        lines.append("GIF concepts used recently: " + "; ".join(gif_concepts))
    if meme_slugs:
        lines.append("Meme templates used recently: " + ", ".join(meme_slugs))
    return "\n".join(lines) + "\n\n"



def clean_giphy_search(term: str) -> list[str]:
    clean = term.strip().strip('"').strip("'").strip("[]")
    for filler in ["search Giphy for ", "search for ", "from ", "meme", "gif", "reaction"]:
        clean = clean.replace(filler, "").strip()
    queries = [clean]
    words = clean.split()
    if len(words) > 4:
        queries.append(" ".join(words[:4]))
    if len(words) > 6:
        queries.append(" ".join(words[:3]))
    return queries


def fetch_giphy_candidates(search_term: str, api_key: str, limit: int = 5) -> list[str]:
    """Search Giphy and return up to `limit` candidate URLs (best quality per result)."""
    try:
        encoded_term = quote(search_term)
        api_url = f"https://api.giphy.com/v1/gifs/search?api_key={api_key}&q={encoded_term}&limit={limit}&rating=pg-13"
        req = Request(api_url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            urls = []
            for result in results:
                images = result.get("images", {})
                for size_key in ("downsized_medium", "downsized", "original"):
                    if size_key in images and images[size_key].get("url"):
                        urls.append(images[size_key]["url"])
                        break
            return urls
    except Exception:
        return []


def embed_gifs_in_html(html: str, api_key: str, repo_root: Path = None) -> tuple[str, list]:
    gif_pattern = re.compile(
        r'<div class="gif-placeholder">\s*GIF:\s*(.+?)\s*</div>',
        re.DOTALL
    )
    matches = gif_pattern.findall(html)
    if not matches:
        print("  [GIPHY] No GIF placeholders found")
        return html, []

    history = load_gif_history(repo_root) if repo_root else []
    embed_count = 0
    used_gifs = []

    for search_term in matches:
        queries = clean_giphy_search(search_term)
        gif_url = None
        chosen_query = None

        for q in queries:
            print(f"  [GIPHY] Searching: {q}")
            candidates = fetch_giphy_candidates(q, api_key, limit=5)

            # Pick first candidate not used in the last 7 days
            for candidate_url in candidates:
                if not is_recently_used(candidate_url, history) and not is_concept_recently_used(q, history):
                    gif_url = candidate_url
                    chosen_query = q
                    break
                else:
                    reason = "URL" if is_recently_used(candidate_url, history) else "concept"
                    print(f"           -> Skipping recently used ({reason}): {candidate_url[:60]}...")

            if gif_url:
                break

            # All candidates were recently used — fall back to first result anyway
            if candidates:
                print(f"           -> All candidates recently used, using freshest anyway")
                gif_url = candidates[0]
                chosen_query = q
                break

            print(f"           -> No results, trying shorter...")
            time.sleep(0.2)

        if gif_url and chosen_query:
            placeholder_pattern = re.compile(
                r'<div class="gif-placeholder">\s*GIF:\s*'
                + re.escape(search_term)
                + r'\s*</div>',
                re.DOTALL
            )
            img_html = (
                f'<div style="margin: 16px 0; text-align: center;">'
                f'<img src="{gif_url}" alt="{chosen_query}" '
                f'style="max-width: 100%; border-radius: 8px;" />'
                f'</div>'
            )
            html, count = placeholder_pattern.subn(img_html, html, count=1)
            if count > 0:
                embed_count += 1
                print(f"           -> Embedded: {gif_url[:80]}...")
                entry = {
                    "date": __import__('datetime').date.today().isoformat(),
                    "url": gif_url,
                    "search_term": chosen_query,
                }
                used_gifs.append(entry)
                history.insert(0, entry)  # update in-memory history for same-run dedup
        else:
            print(f"           -> All queries failed, keeping placeholder")
        time.sleep(0.3)

    print(f"  [GIPHY] {embed_count}/{len(matches)} GIFs embedded")

    # Save updated history
    if repo_root and used_gifs:
        save_gif_history(repo_root, used_gifs, history)

    return html, used_gifs


def blockquotes_to_substack_urls(html: str) -> str:
    """
    Replace styled tweet blockquotes with bare tweet URLs on their own line.
    Substack auto-embeds a bare twitter.com or x.com URL when pasted.
    """
    def replace(match):
        block = match.group(0)
        # Prefer twitter.com, fall back to x.com, strip Nitter fragments
        url_match = re.search(
            r'href="(https?://(?:twitter\.com|x\.com|nitter\.net)/[^"]+)"',
            block
        )
        if url_match:
            url = url_match.group(1)
            # Normalise Nitter → Twitter
            url = url.replace("nitter.net", "twitter.com")
            # Strip Nitter fragment (#m)
            url = re.sub(r'#m$', '', url)
            return f'\n<p class="tweet-url">{url}</p>\n'
        return block

    return re.sub(
        r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
        replace,
        html,
        flags=re.DOTALL
    )


def drop_fabricated_tweets(html: str) -> tuple[str, int]:
    """
    Remove any tweet blockquote whose URL lacks a real numeric status ID.

    The model sometimes invents a reaction-tweet slot for a headliner beat and
    emits a made-up URL like ".../status/placeholder" when no real tweet exists
    in the feed for that beat. A fabricated URL never embeds in Substack, so it
    is strictly worse than no tweet. This deterministic guard strips them before
    the draft/substack/email files are written, regardless of whether Pass 1 or
    Pass 2 introduced them.
    """
    pattern = re.compile(
        r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
        re.DOTALL | re.IGNORECASE,
    )
    dropped = 0

    def _check(m: re.Match) -> str:
        nonlocal dropped
        block = m.group(0)
        href = re.search(r'href="([^"]+)"', block)
        if href and re.search(r'/status/\d+', href.group(1)):
            return block
        dropped += 1
        return ""

    return pattern.sub(_check, html), dropped


def normalize_topic_key(key: str) -> str:
    """Collapse per-game / per-day topic keys to a stable series-level key.

    The model routinely appends a game number ('-game3', '-g3') or a date to an
    otherwise-stable key, which defeats continuing-story matching and makes a
    multi-game playoff series look brand new every single day. Stripping those
    segments means 'knights-avalanche-game2-wcf-2026' and
    'knights-avalanche-game3-wcf-2026' both collapse to
    'knights-avalanche-wcf-2026', so the series is recognized as one ongoing
    story. The 4-digit season year (e.g. '-2026') is preserved.
    """
    if not isinstance(key, str):
        return ""
    k = key.lower().strip()
    k = re.sub(r'-?\bgame[-_]?\d+\b', '', k)   # -game3, game_3
    k = re.sub(r'-?\bg\d+\b', '', k)           # -g3
    k = re.sub(r'-?\bgm\d+\b', '', k)          # -gm3
    k = re.sub(r'-?\bday[-_]?\d+\b', '', k)    # -day2
    k = re.sub(r'-\d{4}-\d{2}-\d{2}\b', '', k) # -2026-05-25
    k = re.sub(r'-\d{1,2}-\d{1,2}\b', '', k)   # -05-25
    k = re.sub(r'-{2,}', '-', k).strip('-')    # collapse/trim stray hyphens
    return k


def format_story_history(recent_output: list) -> str:
    """
    Format the last 14 days of story_log entries into a readable block
    for Pass 1 to use in continuing story detection.
    Handles both new format (story_log) and old format (all_headlines).
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=14)
    lines = []

    for entry in recent_output:
        try:
            entry_date = date.fromisoformat(entry.get("date", ""))
        except Exception:
            continue
        if entry_date < cutoff:
            continue

        lines.append(f"\n--- {entry['date']} ---")

        if "story_log" in entry:
            for story in entry["story_log"]:
                resolved = " [RESOLVED]" if story.get("resolved") else ""
                _tk = normalize_topic_key(story.get('topic_key', '')) or story.get('topic_key', '?')
                lines.append(
                    f"  [{story.get('section', '?').upper()}] "
                    f"{_tk} — "
                    f"{story.get('title', '?')}{resolved}"
                )
                lines.append(f"    Development: {story.get('development', '?')}")
        elif "all_headlines" in entry:
            # Legacy format — provide headline list without topic keys
            lines.append(f"  Lead: {entry.get('lead_story', '?')}")
            for h in entry.get("all_headlines", [])[1:]:
                lines.append(f"  Supporting: {h}")

    if not lines:
        return "No recent story history available."
    return "\n".join(lines)


def normalize_plan(plan: dict) -> dict:
    """
    Sanitize every field in the Pass 1 plan dict to its expected type.

    The model occasionally returns wrong types for nested fields — a string
    where a list is expected, a string where a dict is expected, a list of
    strings where a list of tweet-dicts is expected. This has caused crashes
    in three different places (missing-text loop, pre_edit, and would have
    hit others).

    Running this once, immediately after Pass 1 returns, means every
    downstream consumer (pre_edit, save_story_log, run_pass2, etc.) receives
    a guaranteed-clean structure and needs no individual type guards.
    """
    def safe_dict(val) -> dict:
        return val if isinstance(val, dict) else {}

    def safe_list(val) -> list:
        return val if isinstance(val, list) else []

    def safe_tweet_list(val) -> list:
        """Return a list containing only well-formed tweet dicts with a REAL
        numeric tweet URL. Drops fabricated tweets (e.g. .../status/placeholder)
        that the model invents when it wants a reaction tweet not in the feed."""
        return [
            t for t in safe_list(val)
            if isinstance(t, dict)
            and t.get("url")
            and re.search(r'/status/\d+', str(t.get("url", "")))
        ]

    def safe_str(val) -> str:
        return val if isinstance(val, str) else ""

    def normalize_beat_list(val) -> list:
        """Same guarantee as safe_tweet_list, one level down: each beat's
        media[] gets the fabricated-URL filter too, and a beat missing its
        required text fields is dropped rather than crashing a downstream
        consumer that assumes angle/landing are present strings."""
        out = []
        for b in safe_list(val):
            if not isinstance(b, dict):
                continue
            angle   = safe_str(b.get("angle"))
            landing = safe_str(b.get("landing"))
            if not angle or not landing:
                continue
            out.append({
                "angle": angle,
                "landing": landing,
                "media": safe_tweet_list(b.get("media")),
            })
        return out

    def normalize_story(val) -> dict:
        s = safe_dict(val)
        s["tweets"] = safe_tweet_list(s.get("tweets"))
        s["gif_concept"]  = safe_str(s.get("gif_concept"))
        s["meme_concept"] = safe_str(s.get("meme_concept"))
        s["beats"]        = normalize_beat_list(s.get("beats"))
        return s

    plan["lead_story"]         = normalize_story(plan.get("lead_story"))
    plan["supporting_stories"] = [
        normalize_story(s)
        for s in safe_list(plan.get("supporting_stories"))
        if isinstance(s, dict)
    ]
    atl = safe_dict(plan.get("around_the_league"))
    atl["tweets"]              = safe_tweet_list(atl.get("tweets"))
    plan["around_the_league"]  = atl
    plan["story_log"]          = [
        s for s in safe_list(plan.get("story_log"))
        if isinstance(s, dict)
    ]

    return plan


def save_story_log(story_plan_raw: str, recent_output: list, path: Path) -> list:
    """
    Extract today's story_log from Pass 1's JSON output, prepend it to
    recent_output, trim to 30 entries, and save to disk.
    Returns the updated recent_output list.
    """
    try:
        plan = json.loads(story_plan_raw)
        story_log = plan.get("story_log", [])
        if not story_log:
            print("  ⚠ No story_log in Pass 1 output — recent_output not updated")
            return recent_output
        # Normalize topic keys before persisting so a multi-game series stores
        # one stable key (strips -game3/-g3/date suffixes the model appends).
        for _s in story_log:
            if isinstance(_s, dict) and _s.get("topic_key"):
                _norm = normalize_topic_key(_s["topic_key"])
                if _norm:
                    _s["topic_key"] = _norm
    except json.JSONDecodeError:
        print("  ⚠ Pass 1 output not valid JSON — recent_output not updated")
        return recent_output

    from datetime import date
    new_entry = {
        "date": date.today().isoformat(),
        "story_log": story_log,
    }

    # Ensure recent_output is a list (handles old dict format)
    if not isinstance(recent_output, list):
        recent_output = [recent_output] if recent_output else []

    updated = [new_entry] + recent_output
    updated = updated[:30]  # keep last 30 days

    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ story_log saved — {len(story_log)} stories logged")
    return updated


# ---------------------------------------------------------------------------
# Pass 1 — Story Selector
# ---------------------------------------------------------------------------

def run_pass1(raw: dict, recent_output: list, client: anthropic.Anthropic, game_state: dict | None = None) -> str:
    print("\n── PASS 1: Story Selector ──────────────────────────")

    selector_prompt = load_prompt("pass1_story_selector.txt")
    if not selector_prompt:
        raise SystemExit("Error: prompts/pass1_story_selector.txt not found.")

    story_history = format_story_history(
        recent_output if isinstance(recent_output, list) else []
    )

    # Slim the raw payload before dumping into the (uncached) user message.
    # Truncate pubDate to date-only: per-second timestamps add ~1.9K tokens
    # across ~300 items and carry no selection value — day-level recency is
    # enough, and game_state + story history hold the authoritative "what
    # happened when" signal. Other fields (text/link/account) are untouched;
    # the full `raw` is still used below to attach verbatim tweet text.
    def _slim_item(d):
        if not isinstance(d, dict):
            return d
        pd = d.get("pubDate")
        if isinstance(pd, str) and len(pd) > 10:
            d = {**d, "pubDate": pd[:10]}
        return d

    raw_slim = dict(raw)
    raw_slim["news_headlines"] = [_slim_item(h) for h in raw.get("news_headlines", [])]

    # UAT §2.1 — video tweets render as dead grey boxes in email, so they never
    # reach the selector. They are tagged, not deleted: Pass 1B mines the same
    # set for highlight clips. The UAT-only fields are stripped here so Pass 1's
    # payload stays shaped exactly like production's.
    _kept = [t for t in raw.get("tweets", []) if not t.get("has_video")]
    _drop_uat_fields = ("has_video", "media_kind", "detect_source", "status_id")
    raw_slim["tweets"] = [
        _slim_item({k: v for k, v in t.items() if k not in _drop_uat_fields})
        for t in _kept
    ]
    _excluded = len(raw.get("tweets", [])) - len(_kept)
    print(f"  §2.1 video filter: {_excluded} video tweet(s) excluded, "
          f"{len(_kept)} candidates remain")

    game_state_block = format_game_state_summary(game_state or {})
    user_content = (
        (game_state_block + "\n\n" if game_state_block else "")
        + "## TODAY'S RAW CONTENT\n\n"
        + json.dumps(raw_slim, ensure_ascii=False)
        + "\n\n## RECENT STORY HISTORY — last 14 days\n"
        "Read this before selecting stories. Use it to identify continuing "
        "stories and apply the Continuing Story Detection rules.\n"
        + story_history
    )

    messages = [{"role": "user", "content": user_content}]

    # Tool definition for structured output.
    # Using tool_use instead of free-form JSON eliminates JSON escape errors
    # (unescaped quotes in tweet text, etc.) because the SDK parses tool inputs
    # and we re-serialize with json.dumps() which handles escaping correctly.
    # Pass 1 selects tweets by url + account only. The verbatim "text" is attached
    # in Python from raw_content (by status ID) after selection — see the text
    # injection block below. Dropping "text" from the schema removes ~15-20 tweets'
    # worth of duplicated text from Pass 1's output every run and makes the text
    # authoritative (the model can no longer paraphrase or truncate it). The old
    # "reason" field was never read by any downstream code, so it's gone too.
    _tweet = {
        "type": "object",
        "properties": {
            "account": {"type": "string"},
            "url":     {"type": "string"},
        },
        "required": ["account", "url"],
    }
    # UAT beats test: a beat is one complete emotional/informational movement
    # (setup + landing) within a story. Optional — a light story with no real
    # sub-angles can submit zero beats and the writer falls back to
    # research_notes alone. "media" holds candidate tweet(s) for that beat;
    # empty if the beat is carried by prose/GIF/meme instead.
    _beat = {
        "type": "object",
        "properties": {
            "angle":   {"type": "string"},
            "landing": {"type": "string"},
            "media":   {"type": "array", "items": _tweet},
        },
        "required": ["angle", "landing"],
    }
    _story = {
        "type": "object",
        "properties": {
            "topic":          {"type": "string"},
            "headline":       {"type": "string"},
            "tweets":         {"type": "array", "items": _tweet},
            "research_notes": {"type": "string"},
            "gif_concept":    {"type": "string"},
            "meme_concept":   {"type": "string"},
            "beats":          {"type": "array", "items": _beat},
        },
        "required": ["topic", "headline", "tweets"],
    }
    _log_item = {
        "type": "object",
        "properties": {
            "topic_key":   {"type": "string"},
            "title":       {"type": "string"},
            "section":     {"type": "string"},
            "development": {"type": "string"},
            "is_new":      {"type": "boolean"},
            "resolved":    {"type": "boolean"},
        },
        "required": ["topic_key", "title", "section", "development", "is_new", "resolved"],
    }
    tool_definition = {
        "name": "submit_story_plan",
        "description": (
            "Submit the completed story plan for today's SLAP newsletter. "
            "You MUST call this tool — do not respond with plain text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date":       {"type": "string"},
                "story_log":  {"type": "array", "items": _log_item},
                "lead_story": _story,
                "supporting_stories": {"type": "array", "items": _story},
                "around_the_league": {
                    "type": "object",
                    "properties": {
                        "tweets": {
                            "type": "array",
                            "items": _tweet,
                            "minItems": 10,
                            "maxItems": 10,
                            "description": "Exactly 10 tweets. No more, no fewer.",
                        }
                    },
                    "required": ["tweets"],
                },
                "account_distribution": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                # UAT: the rejection count cannot be reported unless it is asked
                # for explicitly — the model will not volunteer it.
                "filter_stats": {
                    "type": "object",
                    "properties": {
                        "update_tweets_rejected": {
                            "type": "integer",
                            "description": (
                                "How many candidate tweets you rejected under the "
                                "PURE UPDATE FILTER because they only restated news "
                                "the commentary already states."
                            ),
                        },
                    },
                    "required": ["update_tweets_rejected"],
                },
            },
            "required": ["date", "story_log", "lead_story", "supporting_stories",
                         "around_the_league", "account_distribution", "filter_stats"],
        },
        "cache_control": {"type": "ephemeral"},
    }

    MAX_ATTEMPTS = 3
    total_in = total_out = total_cache_read = total_cache_write = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        api_error = None
        response  = None

        try:
            # Opus + 32768 max_tokens crosses the SDK's own long-request
            # guard ("Streaming is required for operations that may take
            # longer than 10 minutes") — a client-side check, not an API
            # call, so it fails instantly and free. .get_final_message()
            # returns the same Message shape client.messages.create() did,
            # so every downstream .content / .usage access below is unchanged.
            with client.messages.stream(
                model=MODEL_PASS1,
                max_tokens=32768,
                system=[
                    {
                        "type": "text",
                        "text": selector_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[tool_definition],
                tool_choice={"type": "tool", "name": "submit_story_plan"},
                messages=messages,
            ) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_message()
        except Exception as e:
            api_error = str(e)

        if response is not None:
            total_in          += response.usage.input_tokens
            total_out         += response.usage.output_tokens
            total_cache_read  += getattr(response.usage, "cache_read_input_tokens", 0)
            total_cache_write += getattr(response.usage, "cache_creation_input_tokens", 0)

        # --- Determine outcome ---
        validation_error = None
        plan         = None
        tool_use_id  = None

        if api_error:
            validation_error = f"API error (likely malformed JSON in tool input): {api_error}"

        elif response is None:
            validation_error = "No response received from API"

        else:
            tool_block = next(
                (b for b in response.content
                 if b.type == "tool_use" and b.name == "submit_story_plan"),
                None,
            )
            if tool_block is None:
                validation_error = "Model did not invoke submit_story_plan tool"
            else:
                tool_use_id = tool_block.id
                plan = tool_block.input  # already a parsed Python dict from the SDK

                # Opus (observed 2026-08-21, beats test) sometimes wraps the
                # entire payload in an extra "story_plan" key that isn't part
                # of the schema — a literal reading of the tool's own name
                # (submit_STORY_PLAN) rather than the field list. The inner
                # dict is otherwise complete and correctly shaped, so unwrap
                # it defensively instead of retrying (which just reproduces
                # the same wrap) or trusting prompt wording to hold across
                # every regeneration.
                if (isinstance(plan, dict) and set(plan.keys()) == {"story_plan"}
                        and isinstance(plan.get("story_plan"), dict)):
                    plan = plan["story_plan"]

                if not isinstance(plan, dict):
                    validation_error = "Tool input is not a dict"
                elif "lead_story" not in plan:
                    validation_error = "Missing required field: lead_story"
                elif "story_log" not in plan:
                    validation_error = "Missing required field: story_log"
                else:
                    # Structurally valid. Around the League completeness is no
                    # longer checked or retried here: a full plan regeneration
                    # (25K in / up to 16K out) just to top up a few ATL tweets is
                    # not worth the cost, and a thin ATL ships anyway by design.
                    # The final ATL count is logged after the fabricated-URL
                    # filter below (which is what actually determines it).
                    pass

        if validation_error is None:
            # Normalize every field to its expected type in one place.
            # This prevents type-mismatch crashes in all downstream consumers
            # (pre_edit, save_story_log, missing-text loop, etc.).
            plan = normalize_plan(plan)

            # Cross-reference all plan tweet URLs against today's raw_content.
            # Pass 1's prompt already prohibits fabricated URLs, but the model
            # ignores it under pressure (e.g. when pushed to fill ATL on a day
            # where Nitter missed some overnight game tweets). Enforce here at
            # code level so fabricated tweets never reach the newsletter.
            # Match on the numeric status ID — the only invariant across
            # nitter.net / twitter.com / x.com, #m suffixes, case, and query
            # strings. fetch_content.py stores the URL under "link"; older
            # data used "url" — read both so the filter never silently
            # empties every story (which forces Pass 2 to fabricate URLs).
            def _status_id(url: str) -> str:
                m = re.search(r'/status/(\d+)', url or "")
                return m.group(1) if m else ""

            # Map status ID -> source tweet so we can both (a) verify the URL
            # exists in raw content and (b) re-attach the VERBATIM text/account.
            # Pass 1 now outputs only {url, account}; the canonical tweet text
            # lives here in raw_content, so we overwrite both fields from source.
            # This cuts Pass 1 output tokens and kills the whole class of
            # "missing/paraphrased tweet text" errors at the root.
            _raw_by_sid = {}
            for _t in raw.get("tweets", []):
                _sid = _status_id(_t.get("link") or _t.get("url") or "")
                if _sid and _sid not in _raw_by_sid:
                    _raw_by_sid[_sid] = _t
            _raw_url_set = set(_raw_by_sid)

            def _filter_and_attach(tweets):
                kept = []
                for t in tweets:
                    sid = _status_id(t.get("url", ""))
                    src = _raw_by_sid.get(sid) if sid else None
                    if not src:
                        continue
                    t["text"] = src.get("text", "") or t.get("text", "")
                    src_acct = src.get("account") or src.get("handle")
                    if src_acct:
                        t["account"] = src_acct
                    kept.append(t)
                return kept, len(tweets) - len(kept)

            def _filter_beats(beats):
                # Beats-test wiring: each beat's candidate media goes through
                # the same cross-reference-and-attach as top-level tweets, so
                # Pass 2 never receives a beat pointing at a fabricated URL —
                # it only ever sees beats whose media is already verified with
                # real attached text, or no media at all (prose/GIF/meme beat).
                dropped = 0
                for beat in beats:
                    beat["media"], n = _filter_and_attach(beat.get("media", []))
                    dropped += n
                return beats, dropped

            # Safety: if we extracted no usable IDs from raw content, the filter
            # can't validate anything \u2014 and dropping every tweet is far worse
            # than keeping them (it forces Pass 2 to fabricate placeholder URLs).
            # No-op the filter in that case and warn loudly.
            if not _raw_url_set:
                print("  \u26a0 No tweet URLs found in raw content \u2014 skipping cross-reference filter (keeping all plan tweets)")
            else:
                _fab_total = 0
                _lead = plan.get("lead_story", {})
                _lead["tweets"], _n = _filter_and_attach(_lead.get("tweets", []))
                _fab_total += _n
                _lead["beats"], _n = _filter_beats(_lead.get("beats", []))
                _fab_total += _n
                plan["lead_story"] = _lead

                _new_sup = []
                for _s in plan.get("supporting_stories", []):
                    _s["tweets"], _n = _filter_and_attach(_s.get("tweets", []))
                    _fab_total += _n
                    _s["beats"], _n = _filter_beats(_s.get("beats", []))
                    _fab_total += _n
                    _new_sup.append(_s)
                plan["supporting_stories"] = _new_sup

                _atl = plan.get("around_the_league", {})
                _atl["tweets"], _n = _filter_and_attach(_atl.get("tweets", []))
                plan["around_the_league"] = _atl
                _fab_total += _n

                if _fab_total:
                    print(f"  \u26a0 Dropped {_fab_total} fabricated tweet(s) \u2014 URLs not in today's raw content")
                else:
                    print(f"  \u2713 All plan tweets verified against today's raw content")

            story_plan_raw = json.dumps(plan, ensure_ascii=False)

            # Fix malformed tweet URLs where model writes status= instead of status/
            if 'status=' in story_plan_raw:
                story_plan_raw = re.sub(
                    r'(twitter\.com/\w+)/status=',
                    r'\1/status/',
                    story_plan_raw,
                )
                story_plan_raw = re.sub(
                    r'(x\.com/\w+)/status=',
                    r'\1/status/',
                    story_plan_raw,
                )
                print("  ⚠ Fixed malformed tweet URLs (status= → status/)")

            cost_summary("PASS 1", MODEL_PASS1, total_in, total_out, total_cache_read, total_cache_write)
            if attempt > 1:
                print(f"  ✓ Pass 1 succeeded on attempt {attempt}/{MAX_ATTEMPTS}")

            dist = plan.get("account_distribution", {})
            over_cap = {k: v for k, v in dist.items() if v > 2}
            if over_cap:
                print(f"  ⚠ Account cap violations in plan: {over_cap}")
            else:
                print(f"  ✓ Account distribution within caps")

            atl = plan.get("around_the_league", {})
            atl_tweets = (
                atl.get("tweets", []) if isinstance(atl, dict)
                else (atl if isinstance(atl, list) else [])
            )
            missing_text = 0
            for section in ([plan.get("lead_story", {})]
                            + plan.get("supporting_stories", [])):
                for t in section.get("tweets", []):
                    if not t.get("text", "").strip():
                        missing_text += 1
            for t in atl_tweets:
                if not t.get("text", "").strip():
                    missing_text += 1
            if missing_text:
                print(f"  ⚠ {missing_text} tweet(s) missing text — writer will skip them")
            else:
                print(f"  ✓ All tweets have text")
            print(f"  ✓ Around the League: {len(atl_tweets)} tweets")

            return story_plan_raw

        # --- Handle failure: build corrective messages then retry or abort ---
        if attempt < MAX_ATTEMPTS:
            print(f"  ⚠ Pass 1 attempt {attempt}/{MAX_ATTEMPTS} invalid: {validation_error}")
            print( "    Retrying with corrective instruction...")

            if response is not None and tool_use_id:
                # Proper tool-use retry: return a tool_result with the error so
                # the model understands exactly what went wrong and can fix it.
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type":        "tool_result",
                        "tool_use_id": tool_use_id,
                        "content":     (
                            f"❌ Validation failed: {validation_error}\n\n"
                            "Fix the issue and call submit_story_plan again. "
                            "If Around the League is short, scan the raw content for "
                            "any unused tweets from accounts not yet at their 2-tweet cap "
                            "and add them — aim for the full 8-10. "
                            "All required top-level fields must be present."
                        ),
                        "is_error": True,
                    }],
                })

            elif response is not None:
                # Model responded with text instead of calling the tool.
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": (
                        f"⚠ Error: {validation_error}\n\n"
                        "You must call the submit_story_plan tool with your full story plan. "
                        "Do not respond with plain text."
                    ),
                })

            else:
                # API-level error (e.g. malformed JSON in tool input).
                # Don't append an assistant turn — add a corrective user message.
                messages.append({
                    "role": "user",
                    "content": (
                        f"⚠ The previous API call failed: {api_error}\n\n"
                        "This is usually caused by special characters (unescaped quotes, "
                        "backslashes) inside string values. "
                        "Call submit_story_plan again with your complete story plan."
                    ),
                })

        else:
            cost_summary("PASS 1", MODEL_PASS1, total_in, total_out, total_cache_read, total_cache_write)
            print(f"  ✗ Pass 1 FAILED after {MAX_ATTEMPTS} attempts: {validation_error}")
            raise RuntimeError(
                f"Pass 1 produced invalid output after {MAX_ATTEMPTS} attempts. "
                f"Last error: {validation_error}. "
                f"Aborting pipeline to prevent garbage downstream output. "
                f"Check pass1_story_selector.txt and raw content for issues."
            )

    # Unreachable, but appease linters/type checkers.
    raise RuntimeError("Pass 1 retry loop exited unexpectedly")


# ---------------------------------------------------------------------------
# Pass 2 — Writer
# ---------------------------------------------------------------------------

def _format_highlight_plan_block(highlight_plan: list | None) -> str:
    """The writer's view of the highlight plan: ids it may place, and what is
    in each clip. Only clips that survived conversion reach this point."""
    if not highlight_plan:
        return (
            "\n\n## HIGHLIGHT PLAN\n\n"
            "No highlight clips today. Do NOT emit any highlight-placeholder div."
        )
    lines = [
        "\n\n## HIGHLIGHT PLAN",
        "",
        "These clips have already been generated and are ready to place. Use the "
        "setup / clip / reaction pattern. Use each id AT MOST ONCE, and never "
        "invent an id that is not listed here.",
        "",
    ]
    for h in highlight_plan:
        lines.append(f"- **{h['id']}** (story: {h.get('story_id','?')})")
        lines.append(f"    what happens: {h.get('description','')}")
        if h.get("why"):
            lines.append(f"    why it matters: {h['why']}")
    return "\n".join(lines)


def run_pass1b(story_plan: str, video_tweets: list, client: anthropic.Anthropic) -> list:
    """UAT §3.1 — pick 3-5 video tweets worth turning into highlight GIFs.

    A separate pass rather than extra work bolted onto Pass 1: Pass 1 already
    runs a 16K max_tokens tool call over the full raw payload, and highlight
    selection needs a different, much smaller input. Uses MODEL_DEFAULT — this
    is a selection task, not a writing task.

    Returns a list of highlight dicts (possibly empty). Never raises.
    """
    print("\n── PASS 1B: Highlight Selector ─────────────────────")

    prompt = load_prompt("pass1b_highlight_selector.txt")
    if not prompt:
        print("  ⚠ prompts/pass1b_highlight_selector.txt not found — skipping")
        return []
    if not video_tweets:
        print("  No video tweets tagged — no highlights to select.")
        return []

    # Only the sections that actually get WRITTEN are valid homes for a clip.
    # The plan's story_log is much larger (it logs Around-the-League and
    # continuing stories too) — feeding it here let the model attach clips to
    # stories the newsletter never runs, and the writer then had nowhere to put
    # them. Observed 2026-08-19: 19 logged topic_keys vs 7 written sections.
    try:
        plan = json.loads(story_plan)
    except json.JSONDecodeError:
        print("  ⚠ story plan unparseable — skipping highlight selection")
        return []

    sections = []
    lead = plan.get("lead_story") or {}
    if lead:
        sections.append({"story_id": lead.get("topic", "lead"),
                         "headline": lead.get("headline", ""),
                         "slot": "lead"})
    for s in plan.get("supporting_stories", []):
        sections.append({"story_id": s.get("topic", ""),
                         "headline": s.get("headline", ""),
                         "slot": "supporting"})
    if not sections:
        print("  ⚠ no written sections in plan — skipping highlight selection")
        return []
    valid_story_ids = {s["story_id"] for s in sections}

    candidates = [
        {"account": t.get("account", ""),
         "text":    t.get("text", ""),
         "url":     t.get("link", "")}
        for t in video_tweets
    ]
    print(f"  {len(sections)} written section(s), {len(candidates)} video tweet candidate(s)")

    tool_definition = {
        "name": "submit_highlight_plan",
        "description": (
            "Submit the highlight plan. You MUST call this tool — do not respond "
            "with plain text. An empty highlights array is a valid answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "highlights": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":               {"type": "string"},
                            "story_id":         {"type": "string"},
                            "source_tweet_url": {"type": "string"},
                            "description":      {"type": "string"},
                            "why":              {"type": "string"},
                        },
                        "required": ["id", "story_id", "source_tweet_url",
                                     "description", "why"],
                    },
                },
            },
            "required": ["highlights"],
        },
    }

    user_message = (
        "## SECTIONS BEING WRITTEN TODAY\n\n"
        "These are the ONLY valid values for story_id. A clip that does not "
        "belong to one of these has no home in the newsletter and must not be "
        "selected.\n\n"
        + json.dumps(sections, ensure_ascii=False, indent=2)
        + "\n\n## VIDEO TWEET CANDIDATES\n\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
    )

    try:
        response = client.messages.create(
            model=MODEL_DEFAULT,
            max_tokens=4096,
            system=[{"type": "text", "text": prompt,
                     "cache_control": {"type": "ephemeral"}}],
            tools=[tool_definition],
            tool_choice={"type": "tool", "name": "submit_highlight_plan"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        print(f"  ✗ Pass 1B failed: {e}")
        print("    Continuing without highlights.")
        return []

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 1B", MODEL_DEFAULT, response.usage.input_tokens,
                 response.usage.output_tokens, cache_read, cache_write)

    block = next((b for b in response.content
                  if b.type == "tool_use" and b.name == "submit_highlight_plan"), None)
    if block is None:
        print("  ⚠ Model did not call submit_highlight_plan — no highlights.")
        return []

    highlights = block.input.get("highlights", []) or []

    # The URL must come from the candidate list verbatim. A hallucinated or
    # edited URL would send yt-dlp at the wrong tweet, which is exactly the
    # "wrong is worse than nothing" failure the prompt warns about.
    valid_urls = {c["url"] for c in candidates}
    kept = []
    for h in highlights:
        url = h.get("source_tweet_url", "")
        if url not in valid_urls:
            print(f"  ⚠ dropped {h.get('id','?')} — URL not in candidate list: {url}")
            continue
        if not (h.get("description") or "").strip():
            print(f"  ⚠ dropped {h.get('id','?')} — empty description")
            continue
        # A clip pinned to a story that is never written cannot be placed, and
        # the writer silently discards it. Drop it here so the failure is loud
        # and so we never pay to encode a GIF nothing can use.
        if h.get("story_id") not in valid_story_ids:
            print(f"  ⚠ dropped {h.get('id','?')} — story_id "
                  f"'{h.get('story_id')}' is not a written section")
            continue
        kept.append(h)

    if len(kept) > 5:
        print(f"  ⚠ trimming {len(kept)} highlights to the 5 cap")
        kept = kept[:5]

    print(f"  ✓ {len(kept)} highlight(s) selected")
    for h in kept:
        print(f"      [{h['id']}] {h['story_id']}: {h['description'][:70]}")
    return kept


def run_pass2(story_plan: str, client: anthropic.Anthropic, game_state: dict | None = None,
              highlight_plan: list | None = None) -> str:
    print("\n── PASS 2: Writer ──────────────────────────────────")

    # pass2_writer.txt is the writer-specific prompt (voice, structure, HTML rules).
    # Voice examples load first — imitation before instruction.
    writer_prompt    = load_prompt("pass2_writer.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")
    voice_examples   = load_prompt("voice_examples.txt")
    gif_reference    = load_prompt("gif_reference.txt")
    meme_reference   = load_prompt("meme_reference.txt")

    # Voice examples load FIRST so the model reads the target before the rules.
    # This matches how Pass 4 (Voice Editor) works and weights imitation over instruction.
    static_parts = []
    if voice_examples:
        static_parts.append(voice_examples)
    static_parts.append(writer_prompt)
    if gif_reference:
        static_parts.append("## GIF REFERENCE\n\n" + gif_reference)
    if meme_reference:
        static_parts.append("## MEME REFERENCE\n\n" + meme_reference)

    static_text = "\n\n" + ("\n\n" + "="*80 + "\n\n").join(p for p in static_parts if p)

    system_blocks = [
        {
            "type": "text",
            "text": static_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if rolling_feedback:
        system_blocks.append({
            "type": "text",
            "text": "## ROLLING FEEDBACK (hard rules — apply every time)\n\n" + rolling_feedback,
            "cache_control": {"type": "ephemeral"},
        })

    # Pass 2 only needs the story plan — all tweets are pre-assigned by Pass 1.
    # Sending full raw_content.json here was redundant and added ~40K tokens per run.
    game_state_block = format_game_state_summary(game_state or {})
    recent_media_block = format_recent_media_block(OUTPUT_DIR)
    user_message = (
        (game_state_block + "\n\n" if game_state_block else "")
        + recent_media_block
        + "## TODAY'S STORY PLAN\n\n"
        "The story selector has already decided which stories to cover and which "
        "tweets to use. Follow this plan. Do not add stories or tweets not listed "
        "here. You may search the web for additional context and stats on the "
        "stories listed.\n\n"
        + story_plan
        + _format_highlight_plan_block(highlight_plan)
    )

    messages = [{"role": "user", "content": user_message}]
    total_in = total_out = total_cache_read = total_cache_write = 0

    while True:
        response = client.messages.create(
            model=MODEL_WRITER,
            max_tokens=8192,
            system=system_blocks,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )
        total_in         += response.usage.input_tokens
        total_out        += response.usage.output_tokens
        total_cache_read  += getattr(response.usage, "cache_read_input_tokens", 0)
        total_cache_write += getattr(response.usage, "cache_creation_input_tokens", 0)

        if response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [SEARCH] {block.input.get('query', '?')}")
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": "Search completed"}
                for b in response.content if b.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    cost_summary("PASS 2", MODEL_WRITER, total_in, total_out, total_cache_read, total_cache_write)
    return strip_code_fences(extract_text(response))


# ---------------------------------------------------------------------------
# Pass 4 — Voice Editor
# ---------------------------------------------------------------------------

def run_pass4(draft_html: str, client: anthropic.Anthropic) -> str:
    print("\n── PASS 4: Voice Editor ────────────────────────────")

    voice_examples = load_prompt("voice_examples.txt")
    voice_prompt   = load_prompt("pass4_voice.txt")

    if not voice_prompt:
        print("  ⚠ prompts/pass4_voice.txt not found — skipping voice pass")
        return draft_html

    # Voice examples lead the system prompt so they are the first thing
    # the model reads — imitation before instruction.
    system_blocks = []
    if voice_examples:
        system_blocks.append({
            "type": "text",
            "text": voice_examples,
            "cache_control": {"type": "ephemeral"},
        })
    system_blocks.append({
        "type": "text",
        "text": voice_prompt,
        "cache_control": {"type": "ephemeral"},
    })

    response = client.messages.create(
        model=MODEL_DEFAULT,
        max_tokens=8192,
        system=system_blocks,
        messages=[{
            "role": "user",
            "content": (
                "Rewrite any <p> tag prose that fails the Sportswriter Test. "
                "Leave everything else unchanged. Return the full HTML.\n\n"
                + draft_html
            )
        }]
    )

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 4", MODEL_DEFAULT, response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

    return strip_code_fences(extract_text(response))


# ---------------------------------------------------------------------------
# Pre-Edit — Programmatic Tweet Misassignment Auditor
# ---------------------------------------------------------------------------

def _normalize_tweet_url(url: str) -> str:
    """Normalize a tweet URL for reliable comparison across sources."""
    url = url.strip()
    url = url.replace("nitter.net", "twitter.com")
    url = re.sub(r'#m$', '', url)
    url = re.sub(r'(/status)=(\d)', r'\1/\2', url)  # status= → status/
    return url.lower()


def pre_edit(draft_html: str, story_plan_raw: str) -> str:
    """
    Deterministic pre-editor: flags tweets placed in the wrong story section.
    Runs after Pass 4, before Pass 6, so the LLM editor sees flags in place.

    Logic:
      - Builds a URL → plan-section map from the story plan JSON.
      - Splits the draft HTML into sections by h1/h2 headings.
      - Maps each HTML section to its plan section by position (lead first,
        then supporting stories in order, ATL identified by heading text).
      - For each tweet blockquote, checks if its URL belongs to this section.
      - Injects an HTML comment flag immediately after any mismatch.
    """
    print("\n── PASS 5: Pre-Edit (Tweet Audit) ───────────────────")

    try:
        plan = json.loads(story_plan_raw)
    except (json.JSONDecodeError, TypeError):
        print("  ⚠ Could not parse story plan — skipping tweet audit")
        return draft_html

    # --- Build ordered plan sections: [(label, set_of_normalized_urls), ...] ---
    plan_sections: list[tuple[str, set]] = []

    def _safe_tweet_urls(tweets) -> set:
        """Extract normalized URLs from a tweets list, skipping any non-dict entries."""
        if not isinstance(tweets, list):
            return set()
        return {
            _normalize_tweet_url(t["url"])
            for t in tweets
            if isinstance(t, dict) and t.get("url")
        }

    lead = plan.get("lead_story", {})
    plan_sections.append(("lead_story", _safe_tweet_urls(lead.get("tweets", []))))

    for i, story in enumerate(plan.get("supporting_stories", [])):
        plan_sections.append((f"supporting_{i}", _safe_tweet_urls(story.get("tweets", []))))

    atl = plan.get("around_the_league", {})
    atl_tweets = atl.get("tweets", []) if isinstance(atl, dict) else []
    atl_urls = _safe_tweet_urls(atl_tweets)

    # Full set of every known URL — used to detect tweets not in plan at all
    all_plan_urls = atl_urls.copy()
    for _, urls in plan_sections:
        all_plan_urls |= urls

    if not all_plan_urls:
        print("  ⚠ No tweet URLs found in plan — skipping tweet audit")
        return draft_html

    # --- Split HTML into sections at every h1/h2 boundary ---
    # re.split with a lookahead preserves the h1/h2 tag at the start of each part.
    raw_parts = re.split(r'(?=<h[12][\s>])', draft_html, flags=re.IGNORECASE)

    # Tag each part: (html_text, is_atl_section, has_heading)
    tagged: list[tuple[str, bool, bool]] = []
    for part in raw_parts:
        if not part.strip():
            tagged.append((part, False, False))
            continue
        heading = re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, re.IGNORECASE | re.DOTALL)
        if not heading:
            tagged.append((part, False, False))
            continue
        heading_text = re.sub(r'<[^>]+>', '', heading.group(1)).lower()
        tagged.append((part, "around the league" in heading_text, True))

    # --- Audit: match HTML sections to plan sections by position ---
    tweet_re = re.compile(
        r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
        re.DOTALL | re.IGNORECASE
    )
    story_idx = 0   # walk plan_sections in order for non-ATL sections
    flag_count = 0
    result_parts: list[str] = []

    for part, is_atl, has_heading in tagged:
        if not has_heading:
            # Preamble or structureless content — pass through unchanged
            result_parts.append(part)
            continue

        if is_atl:
            allowed = atl_urls
            section_label = "around_the_league"
        else:
            if story_idx < len(plan_sections):
                section_label, allowed = plan_sections[story_idx]
                story_idx += 1
            else:
                # More HTML sections than plan sections — can't audit, pass through
                result_parts.append(part)
                continue

        # For each tweet blockquote in this section, check URL assignment
        def audit_tweet(m: re.Match) -> str:
            nonlocal flag_count
            block = m.group(0)
            href = re.search(r'href="([^"]+)"', block)
            if not href:
                return block

            raw_url = href.group(1)
            norm = _normalize_tweet_url(raw_url)

            if norm not in all_plan_urls:
                # Tweet URL wasn't in the plan at all
                flag_count += 1
                return (
                    block
                    + f'\n<!-- EDITOR FLAG: TWEET NOT IN PLAN — "{raw_url}" was not '
                    f'assigned to any story. Verify it belongs in [{section_label}]. -->'
                )

            if norm not in allowed:
                # Tweet is in the plan but assigned to a different section
                actual = next(
                    (lbl for lbl, urls in plan_sections if norm in urls),
                    "around_the_league" if norm in atl_urls else "unknown",
                )
                flag_count += 1
                return (
                    block
                    + f'\n<!-- EDITOR FLAG: TWEET MISASSIGNMENT — "{raw_url}" belongs '
                    f'to [{actual}] but appears in [{section_label}]. '
                    f'Remove or replace. -->'
                )

            return block

        result_parts.append(tweet_re.sub(audit_tweet, part))

    result = "".join(result_parts)

    if flag_count:
        print(f"  ⚠ {flag_count} tweet misassignment flag(s) inserted")
    else:
        print("  ✓ All tweets correctly assigned to their sections")

    return result


# ---------------------------------------------------------------------------
# Pass 6 — Editor
# ---------------------------------------------------------------------------

def run_pass6(draft_html: str, recent_output: dict, client: anthropic.Anthropic) -> str:
    print("\n── PASS 6: Editor ──────────────────────────────────")

    editor_prompt = load_prompt("editor_prompt.txt")
    if not editor_prompt:
        print("  ⚠ prompts/editor_prompt.txt not found — skipping editor pass")
        return draft_html

    # Build system as cached blocks.
    # The static editor prompt is cached. The dynamic media note (changes daily)
    # is appended as a separate uncached block so the static cache still hits.
    system_blocks = [
        {
            "type": "text",
            "text": editor_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    latest = recent_output[0] if isinstance(recent_output, list) and recent_output else (recent_output or {})
    recent_gifs  = latest.get("gifs_used", [])
    recent_memes = latest.get("memes_used", [])
    if recent_gifs or recent_memes:
        media_note = (
            "## PREVIOUS ISSUE MEDIA — DO NOT REUSE\n"
            f"GIFs used: {json.dumps(recent_gifs)}\n"
            f"Memes used: {json.dumps(recent_memes)}"
        )
        system_blocks.append({"type": "text", "text": media_note})

    response = client.messages.create(
        model=MODEL_DEFAULT,
        max_tokens=8192,
        system=system_blocks,
        messages=[{
            "role": "user",
            "content": f"Edit this newsletter draft and return the corrected HTML:\n\n{draft_html}"
        }]
    )

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 6", MODEL_DEFAULT, response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

    edited = strip_code_fences(extract_text(response))

    # Count editor flags for the operator log
    flags = re.findall(r'<!-- EDITOR FLAG:', edited)
    if flags:
        print(f"  ⚠ {len(flags)} editor flag(s) inserted — review before publishing")
    else:
        print(f"  ✓ No flags raised")

    return edited


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Production's main() was REMOVED, not disabled. It orchestrated the full
# production chain (including Pass 4 and the email HTML build) against
# repo-root paths. Orchestration for UAT lives in run_uat.py, which selects
# passes, runs Pass 1B, converts clips, and prints the Media Mix Report.
#
# There is deliberately no runnable main() here: two entry points means one of
# them eventually runs by accident.


def main() -> None:
    raise SystemExit(
        "generate_newsletter_uat.py is a library for the UAT sandbox, not an "
        "entry point.  Run:  python uat/run_uat.py"
    )


if __name__ == "__main__":
    main()
