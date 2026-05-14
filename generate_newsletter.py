"""
SLAP Newsletter — Three-Pass Pipeline
Pass 1:   Story Selector  — picks stories, assigns tweets, enforces account diversity
Pass 2:   Writer          — generates newsletter HTML (with web search)
Pass 2.5: Voice Editor    — prose-only pass to enforce SLAP voice
Pre-Edit: Python auditor  — deterministic tweet misassignment check (no LLM)
Pass 3:   Editor          — judgment-based checks (dueling sentences, punching down, etc.)

Outputs:
  newsletter_draft.html    — styled preview for browser
  newsletter_substack.html — bare tweet URLs ready for Substack embedding
"""

import os
import json
import re
import argparse
from pathlib import Path

from dotenv import load_dotenv
import anthropic

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env", override=True)

RAW_CONTENT_PATH     = SCRIPT_DIR / "raw_content.json"
RECENT_OUTPUT_PATH   = SCRIPT_DIR / "recent_output.json"
DRAFT_OUTPUT_PATH    = SCRIPT_DIR / "newsletter_draft.html"
SUBSTACK_OUTPUT_PATH = SCRIPT_DIR / "newsletter_substack.html"
EMAIL_OUTPUT_PATH    = SCRIPT_DIR / "newsletter_email.html"
PROMPTS_DIR          = SCRIPT_DIR / "prompts"

# claude-sonnet-4-5 is the current stable Sonnet alias on the Anthropic API.
# Use this string — do NOT pin a dated version like claude-sonnet-4-20250514.
# Anthropic keeps this alias pointing at the current stable release.
MODEL = "claude-sonnet-4-5"

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


def strip_code_fences(text: str) -> str:
    text = re.sub(r'^```(?:html|json)?\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text.strip()


def extract_text(response) -> str:
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def cost_summary(label: str, in_tokens: int, out_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> None:
    est = (cache_write * 3.75 + cache_read * 0.30 + in_tokens * 3 + out_tokens * 15) / 1_000_000
    cache_note = ""
    if cache_read or cache_write:
        cache_note = f" (cache read: {cache_read:,} | cache write: {cache_write:,})"
    print(f"  [{label}] {in_tokens:,} in / {out_tokens:,} out — ~${est:.4f}{cache_note}")


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
                lines.append(
                    f"  [{story.get('section', '?').upper()}] "
                    f"{story.get('topic_key', '?')} — "
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

def run_pass1(raw: dict, recent_output: list, client: anthropic.Anthropic) -> str:
    print("\n── PASS 1: Story Selector ──────────────────────────")

    selector_prompt = load_prompt("pass1_story_selector.txt")
    if not selector_prompt:
        raise SystemExit("Error: prompts/pass1_story_selector.txt not found.")

    story_history = format_story_history(
        recent_output if isinstance(recent_output, list) else []
    )

    user_content = (
        "## TODAY'S RAW CONTENT\n\n"
        + json.dumps(raw, ensure_ascii=False)
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
    _tweet = {
        "type": "object",
        "properties": {
            "account": {"type": "string"},
            "url":     {"type": "string"},
            "text":    {"type": "string"},
            "reason":  {"type": "string"},
        },
        "required": ["account", "url", "text"],
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
                "closer": _story,
                "account_distribution": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
            },
            "required": ["date", "story_log", "lead_story", "supporting_stories",
                         "around_the_league", "closer", "account_distribution"],
        },
        "cache_control": {"type": "ephemeral"},
    }

    MAX_ATTEMPTS = 3
    total_in = total_out = total_cache_read = total_cache_write = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        api_error = None
        response  = None

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=16384,
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
            )
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

                if not isinstance(plan, dict):
                    validation_error = "Tool input is not a dict"
                elif "lead_story" not in plan:
                    validation_error = "Missing required field: lead_story"
                elif "story_log" not in plan:
                    validation_error = "Missing required field: story_log"
                else:
                    atl = plan.get("around_the_league", {})
                    atl_tweets = (
                        atl.get("tweets", []) if isinstance(atl, dict)
                        else (atl if isinstance(atl, list) else [])
                    )
                    if len(atl_tweets) < 8:
                        validation_error = (
                            f"Around the League has {len(atl_tweets)} tweets — "
                            f"must be exactly 10. Add {10 - len(atl_tweets)} more."
                        )

        if validation_error is None:
            # Success — re-serialize via json.dumps so downstream always gets
            # correctly escaped JSON regardless of what was in tweet text.
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

            cost_summary("PASS 1", total_in, total_out, total_cache_read, total_cache_write)
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
                            + plan.get("supporting_stories", [])
                            + [plan.get("closer", {})]):
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
                            "If Around the League is short, add more tweet objects until there are exactly 10. "
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
            cost_summary("PASS 1", total_in, total_out, total_cache_read, total_cache_write)
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

def run_pass2(story_plan: str, client: anthropic.Anthropic) -> str:
    print("\n── PASS 2: Writer ──────────────────────────────────")

    # pass2_writer.txt is the writer-specific prompt (voice, structure, HTML rules).
    # Voice examples load first — imitation before instruction.
    writer_prompt    = load_prompt("pass2_writer.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")
    voice_examples   = load_prompt("voice_examples.txt")
    gif_reference    = load_prompt("gif_reference.txt")
    meme_reference   = load_prompt("meme_reference.txt")

    # Voice examples load FIRST so the model reads the target before the rules.
    # This matches how Pass 2.5 works and weights imitation over instruction.
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
    user_message = (
        "## TODAY'S STORY PLAN\n\n"
        "The story selector has already decided which stories to cover and which "
        "tweets to use. Follow this plan. Do not add stories or tweets not listed "
        "here. You may search the web for additional context and stats on the "
        "stories listed.\n\n"
        + story_plan
    )

    messages = [{"role": "user", "content": user_message}]
    total_in = total_out = total_cache_read = total_cache_write = 0

    while True:
        response = client.messages.create(
            model=MODEL,
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

    cost_summary("PASS 2", total_in, total_out, total_cache_read, total_cache_write)
    return strip_code_fences(extract_text(response))


# ---------------------------------------------------------------------------
# Pass 2.5 — Voice Editor
# ---------------------------------------------------------------------------

def run_pass2_5(draft_html: str, client: anthropic.Anthropic) -> str:
    print("\n── PASS 2.5: Voice Editor ──────────────────────────")

    voice_examples = load_prompt("voice_examples.txt")
    voice_prompt   = load_prompt("pass2_5_voice.txt")

    if not voice_prompt:
        print("  ⚠ prompts/pass2_5_voice.txt not found — skipping voice pass")
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
        model=MODEL,
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
    cost_summary("PASS 2.5", response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

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
    Runs after Pass 2.5, before Pass 3, so the LLM editor sees flags in place.

    Logic:
      - Builds a URL → plan-section map from the story plan JSON.
      - Splits the draft HTML into sections by h1/h2 headings.
      - Maps each HTML section to its plan section by position (lead first,
        then supporting stories in order, ATL identified by heading text).
      - For each tweet blockquote, checks if its URL belongs to this section.
      - Injects an HTML comment flag immediately after any mismatch.
    """
    print("\n── PRE-EDIT: Tweet Audit ───────────────────────────")

    try:
        plan = json.loads(story_plan_raw)
    except (json.JSONDecodeError, TypeError):
        print("  ⚠ Could not parse story plan — skipping tweet audit")
        return draft_html

    # --- Build ordered plan sections: [(label, set_of_normalized_urls), ...] ---
    plan_sections: list[tuple[str, set]] = []

    lead = plan.get("lead_story", {})
    plan_sections.append(("lead_story", {
        _normalize_tweet_url(t["url"]) for t in lead.get("tweets", []) if t.get("url")
    }))

    for i, story in enumerate(plan.get("supporting_stories", [])):
        plan_sections.append((f"supporting_{i}", {
            _normalize_tweet_url(t["url"]) for t in story.get("tweets", []) if t.get("url")
        }))

    atl = plan.get("around_the_league", {})
    atl_tweets = atl.get("tweets", []) if isinstance(atl, dict) else []
    atl_urls = {_normalize_tweet_url(t["url"]) for t in atl_tweets if t.get("url")}

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
# Pass 3 — Editor
# ---------------------------------------------------------------------------

def run_pass3(draft_html: str, recent_output: dict, client: anthropic.Anthropic) -> str:
    print("\n── PASS 3: Editor ──────────────────────────────────")

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
        model=MODEL,
        max_tokens=8192,
        system=system_blocks,
        messages=[{
            "role": "user",
            "content": f"Edit this newsletter draft and return the corrected HTML:\n\n{draft_html}"
        }]
    )

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 3", response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

    edited = strip_code_fences(extract_text(response))

    # Count editor flags for the operator log
    flags = re.findall(r'<!-- EDITOR FLAG:', edited)
    if flags:
        print(f"  ⚠ {len(flags)} editor flag(s) inserted — review before publishing")
    else:
        print(f"  ✓ No flags raised")

    return edited


# ---------------------------------------------------------------------------
# MailerLite auto-post
# ---------------------------------------------------------------------------

def post_to_mailerlite(html: str) -> None:
    """
    Creates an unpublished draft in Substack via cookie-based auth.
    Uses SUBSTACK_SID (session cookie) + SUBSTACK_URL env vars.
    Bypasses Cloudflare which blocks email/password login from GitHub Actions.
    Skips silently if credentials are not set (local runs).
    """
    import requests
    from datetime import datetime, date, timedelta
    from zoneinfo import ZoneInfo

    api_key    = os.getenv("MAILERLITE_API_KEY")
    from_email = os.getenv("MAILERLITE_FROM_EMAIL")
    group_id   = os.getenv("MAILERLITE_GROUP_ID")

    if not all([api_key, from_email, group_id]):
        print("  ⚠ MAILERLITE_API_KEY / MAILERLITE_FROM_EMAIL / MAILERLITE_GROUP_ID not set — skipping")
        return

    print("\n── MAILERLITE AUTO-POST ────────────────────────────")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    try:
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else "SLAP Newsletter"

        today   = date.today()
        subject = f"SLAP — {today.strftime('%B')} {today.day}, {today.strftime('%Y')}: {title}"

        body_match   = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1).strip() if body_match else html
        body_content = re.sub(r'<!--.*?-->', '', body_content, flags=re.DOTALL).strip()

        eastern    = ZoneInfo("America/New_York")
        now_et     = datetime.now(eastern)
        publish_et = now_et.replace(hour=12, minute=0, second=0, microsecond=0)
        if now_et.hour >= 12:
            publish_et = publish_et + timedelta(days=1)

        create_resp = requests.post(
            "https://connect.mailerlite.com/api/campaigns",
            headers=headers,
            json={
                "name":   f"SLAP — {today.strftime('%B')} {today.day}, {today.strftime('%Y')}",
                "type":   "regular",
                "emails": [{
                    "subject":   subject,
                    "from_name": "SLAP Newsletter",
                    "from":      from_email,
                    "content":   body_content,
                }],
                "groups": [group_id],
            },
            timeout=30,
        )

        if create_resp.status_code not in (200, 201):
            print(f"  ✗ Campaign creation failed ({create_resp.status_code}): {create_resp.text[:400]}")
            print("    Newsletter saved locally — publish manually if needed.")
            return

        campaign_id = create_resp.json()["data"]["id"]
        print(f"  ✓ Campaign created (id: {campaign_id})")

        sched_resp = requests.post(
            f"https://connect.mailerlite.com/api/campaigns/{campaign_id}/schedule",
            headers=headers,
            json={
                "delivery": "scheduled",
                "schedule": {
                    "date":    publish_et.strftime("%Y-%m-%d"),
                    "hours":   publish_et.strftime("%H"),
                    "minutes": publish_et.strftime("%M"),
                },
            },
            timeout=30,
        )

        if sched_resp.status_code in (200, 201):
            print(f"  ✓ Scheduled for {today.strftime('%B')} {publish_et.day} at 12:00 PM ET")
            print(f"  → Review: https://dashboard.mailerlite.com/campaigns")
        else:
            print(f"  ✗ Scheduling failed ({sched_resp.status_code}): {sched_resp.text[:400]}")
            print(f"    Campaign created — schedule manually: https://dashboard.mailerlite.com/campaigns")

    except Exception as e:
        print(f"  ✗ MailerLite auto-post failed: {e}")
        print("    Newsletter saved locally — publish manually if needed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SLAP Newsletter Generator")
    parser.add_argument("--no-editor", action="store_true", help="Skip Pass 3 editor")
    parser.add_argument("--no-gifs", action="store_true", help="Skip GIF embedding")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY not set in .env")

    print(f"Loading content...")
    raw            = load_json(RAW_CONTENT_PATH)
    recent_output  = load_json(RECENT_OUTPUT_PATH)

    headline_count = len(raw.get("news_headlines", []))
    tweet_count    = len(raw.get("tweets", []))
    print(f"  {headline_count} headlines · {tweet_count} tweets")

    if headline_count == 0 and tweet_count == 0:
        raise SystemExit("No content. Run fetch_content.py first.")

    client = anthropic.Anthropic(api_key=api_key)

    # Passes: selector → writer → voice editor → tweet audit → LLM editor
    story_plan    = run_pass1(raw, recent_output, client)
    recent_output = save_story_log(story_plan, recent_output, RECENT_OUTPUT_PATH)
    draft_html    = run_pass2(story_plan, client)
    voiced_html   = run_pass2_5(draft_html, client)

    # Gate: if Pass 2.5 returned a meta-response instead of HTML (e.g. it wrote
    # about its approach to obituaries rather than returning the draft), fall back
    # to Pass 2 output. A real newsletter always has at least one h1/h2 tag.
    if not re.search(r'<h[12][\s>]', voiced_html, re.IGNORECASE):
        print("  ⚠ Pass 2.5 returned non-HTML — falling back to Pass 2 output")
        voiced_html = draft_html

    audited_html  = pre_edit(voiced_html, story_plan)   # deterministic tweet check
    if args.no_editor:
        print("\n── PASS 3: Editor ──────────────────────────────────")
        print("  ⚠ Skipped via --no-editor flag")
        final_html = audited_html
    else:
        final_html = run_pass3(audited_html, recent_output, client)

    # Save draft (styled blockquotes for browser preview)
    DRAFT_OUTPUT_PATH.write_text(
        DRAFT_TEMPLATE.format(content=final_html), encoding="utf-8"
    )

    # Save Substack version (bare URLs for embedding)
    substack_html = blockquotes_to_substack_urls(final_html)
    SUBSTACK_OUTPUT_PATH.write_text(
        SUBSTACK_TEMPLATE.format(content=substack_html), encoding="utf-8"
    )

    print("\n" + "="*50)
    print(f"✓ newsletter_draft.html    — open in browser to review")
    print(f"✓ newsletter_substack.html — paste into Substack")

    # ── GIF Embedding ──────────────────────────────────────
    print("\n── GIF PIPELINE ────────────────────────────────────")
    giphy_key = os.getenv("GIPHY_API_KEY", "")
    if args.no_gifs:
        print("  ⚠ Skipped via --no-gifs flag")
    elif not giphy_key:
        print("  ⚠ GIPHY_API_KEY not set — skipping GIF embedding")
    else:
        for output_path in [DRAFT_OUTPUT_PATH, SUBSTACK_OUTPUT_PATH]:
            html = output_path.read_text(encoding="utf-8")
            updated, used_gifs = embed_gifs_in_html(html, giphy_key, repo_root=Path(__file__).parent)
            output_path.write_text(updated, encoding="utf-8")
        print(f"  ✓ GIFs embedded in both output files")

    # ── Meme Pipeline ──────────────────────────────────────
    print("\n── MEME PIPELINE ───────────────────────────────────")
    try:
        from generate_memes import build_template_map, process_newsletter
        imgflip_user = os.getenv("IMGFLIP_USERNAME")
        imgflip_pass = os.getenv("IMGFLIP_PASSWORD")
        if not imgflip_user or not imgflip_pass:
            print("  ⚠ IMGFLIP_USERNAME / IMGFLIP_PASSWORD not set — skipping meme generation")
        else:
            template_map = build_template_map()
            repo_root = Path(__file__).parent
            for output_path in [DRAFT_OUTPUT_PATH, SUBSTACK_OUTPUT_PATH]:
                html = output_path.read_text(encoding="utf-8")
                updated, _ = process_newsletter(html, template_map, imgflip_user, imgflip_pass, repo_root=repo_root)
                output_path.write_text(updated, encoding="utf-8")
            print(f"  ✓ Memes embedded in both output files")
    except Exception as e:
        print(f"  ✗ Meme pipeline failed: {e}")
        print("    Newsletter saved without memes — safe to publish as-is.")

    flag_count = len(re.findall(r'<!-- EDITOR FLAG:', final_html))
    if flag_count:
        print(f"\n⚠  {flag_count} editor flag(s) need review before publishing.")
        print(f"   Search 'EDITOR FLAG' in newsletter_draft.html to find them.")

    # Build email version and post to MailerLite
    print("\n── EMAIL BUILD ─────────────────────────────────────")
    try:
        from build_email_html import build_email_html
        draft_for_email = DRAFT_OUTPUT_PATH.read_text(encoding="utf-8")
        email_html = build_email_html(draft_for_email)
        EMAIL_OUTPUT_PATH.write_text(email_html, encoding="utf-8")
        print(f"  ✓ newsletter_email.html built ({len(email_html):,} bytes)")
        post_to_mailerlite(email_html)
    except Exception as e:
        print(f"  ✗ Email build failed: {e}")
        print("    Falling back to substack HTML for MailerLite.")
        post_to_mailerlite(SUBSTACK_OUTPUT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
