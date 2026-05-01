"""
SLAP Newsletter — Three-Pass Pipeline
Pass 1: Story Selector  — picks stories, assigns tweets, enforces account diversity
Pass 2: Writer          — generates newsletter HTML (with web search)
Pass 3: Editor          — catches mechanical failures before shipping

Outputs:
  newsletter_draft.html    — styled preview for browser
  newsletter_substack.html — bare tweet URLs ready for Substack embedding
"""

import os
import json
import re
from pathlib import Path

from dotenv import load_dotenv
import anthropic

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env", override=True)

RAW_CONTENT_PATH    = SCRIPT_DIR / "raw_content.json"
RECENT_OUTPUT_PATH  = SCRIPT_DIR / "recent_output.json"
DRAFT_OUTPUT_PATH   = SCRIPT_DIR / "newsletter_draft.html"
SUBSTACK_OUTPUT_PATH = SCRIPT_DIR / "newsletter_substack.html"
PROMPTS_DIR         = SCRIPT_DIR / "prompts"

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


def cost_summary(label: str, in_tokens: int, out_tokens: int) -> None:
    est = (in_tokens * 3 + out_tokens * 15) / 1_000_000
    print(f"  [{label}] {in_tokens:,} in / {out_tokens:,} out — ~${est:.4f}")


# ---------------------------------------------------------------------------
# Tweet URL conversion for Substack
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GIF Auto-Embedding (Giphy API)
# ---------------------------------------------------------------------------

GIPHY_API_URL = "https://api.giphy.com/v1/gifs/search?api_key={}&q={}&limit=1&rating=pg-13"

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
    """Return True if this GIF URL was used in the past N days."""
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


def save_gif_history(repo_root: Path, new_entries: list, history: list):
    """Append new GIF uses to gif_history.json, keep last 60 entries."""
    history_path = repo_root / "gif_history.json"
    combined = new_entries + history
    combined = combined[:60]
    history_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")


def fetch_giphy_url(search_term: str, api_key: str) -> str | None:
    """Search Giphy for a term and return the top result's direct GIF URL."""
    try:
        encoded_term = quote(search_term)
        api_url = GIPHY_API_URL.format(api_key, encoded_term)
        req = Request(api_url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            if results:
                images = results[0].get("images", {})
                for size_key in ("downsized_medium", "downsized", "original"):
                    if size_key in images and images[size_key].get("url"):
                        return images[size_key]["url"]
        return None
    except (URLError, json.JSONDecodeError, KeyError, TimeoutError):
        return None


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
                if not is_recently_used(candidate_url, history):
                    gif_url = candidate_url
                    chosen_query = q
                    break
                else:
                    print(f"           -> Skipping recently used: {candidate_url[:60]}...")

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


# ---------------------------------------------------------------------------
# Pass 1 — Story Selector
# ---------------------------------------------------------------------------

def run_pass1(raw: dict, client: anthropic.Anthropic) -> str:
    print("\n── PASS 1: Story Selector ──────────────────────────")

    selector_prompt = load_prompt("pass1_story_selector.txt")
    if not selector_prompt:
        raise SystemExit("Error: prompts/pass1_story_selector.txt not found.")

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=selector_prompt,
        messages=[{
            "role": "user",
            "content": (
                "Here is today's raw content. Output the story plan as JSON.\n\n"
                + json.dumps(raw, ensure_ascii=False)
            )
        }]
    )

    cost_summary("PASS 1", response.usage.input_tokens, response.usage.output_tokens)

    story_plan_raw = extract_text(response)
    story_plan_raw = strip_code_fences(story_plan_raw)

    # Validate it's parseable JSON — warn but don't crash
    try:
        plan = json.loads(story_plan_raw)
        dist = plan.get("account_distribution", {})
        over_cap = {k: v for k, v in dist.items() if v > 2}
        if over_cap:
            print(f"  ⚠ Account cap violations in plan: {over_cap}")
        else:
            print(f"  ✓ Account distribution within caps")
    except json.JSONDecodeError:
        print("  ⚠ Pass 1 output is not valid JSON — writer will receive raw text")

    return story_plan_raw


# ---------------------------------------------------------------------------
# Pass 2 — Writer
# ---------------------------------------------------------------------------

def run_pass2(story_plan: str, client: anthropic.Anthropic) -> str:
    print("\n── PASS 2: Writer ──────────────────────────────────")

    # pass2_writer.txt is the writer-specific prompt (voice, structure, HTML rules).
    # base_prompt.txt and editorial_annotations.txt are not needed here —
    # their key rules are already embedded in pass2_writer.txt.
    writer_prompt    = load_prompt("pass2_writer.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")
    voice_examples   = load_prompt("voice_examples.txt")
    gif_reference    = load_prompt("gif_reference.txt")
    meme_reference   = load_prompt("meme_reference.txt")

    system_parts = [writer_prompt]
    if rolling_feedback:
        system_parts.append("## ROLLING FEEDBACK (hard rules — apply every time)\n\n" + rolling_feedback)
    if voice_examples:
        system_parts.append(voice_examples)
    if gif_reference:
        system_parts.append("## GIF REFERENCE\n\n" + gif_reference)
    if meme_reference:
        system_parts.append("## MEME REFERENCE\n\n" + meme_reference)

    system_prompt = "\n\n" + ("\n\n" + "="*80 + "\n\n").join(p for p in system_parts if p)

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
    total_in = total_out = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=system_prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )
        total_in  += response.usage.input_tokens
        total_out += response.usage.output_tokens

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

    cost_summary("PASS 2", total_in, total_out)
    return strip_code_fences(extract_text(response))


# ---------------------------------------------------------------------------
# Pass 3 — Editor
# ---------------------------------------------------------------------------

def run_pass3(draft_html: str, recent_output: dict, client: anthropic.Anthropic) -> str:
    print("\n── PASS 3: Editor ──────────────────────────────────")

    editor_prompt = load_prompt("editor_prompt.txt")
    if not editor_prompt:
        print("  ⚠ prompts/editor_prompt.txt not found — skipping editor pass")
        return draft_html

    # Inject previous issue media into the prompt
    # recent_output is a list (14-day rolling history) — use the most recent entry
    latest = recent_output[0] if isinstance(recent_output, list) and recent_output else (recent_output or {})
    recent_gifs  = latest.get("gifs_used", [])
    recent_memes = latest.get("memes_used", [])
    if recent_gifs or recent_memes:
        media_note = (
            "\n\n## PREVIOUS ISSUE MEDIA — DO NOT REUSE\n"
            f"GIFs used: {json.dumps(recent_gifs)}\n"
            f"Memes used: {json.dumps(recent_memes)}"
        )
        editor_prompt = editor_prompt + media_note

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=editor_prompt,
        messages=[{
            "role": "user",
            "content": f"Edit this newsletter draft and return the corrected HTML:\n\n{draft_html}"
        }]
    )

    cost_summary("PASS 3", response.usage.input_tokens, response.usage.output_tokens)

    edited = strip_code_fences(extract_text(response))

    # Count editor flags for the operator log
    flags = re.findall(r'<!-- EDITOR FLAG:', edited)
    if flags:
        print(f"  ⚠ {len(flags)} editor flag(s) inserted — review before publishing")
    else:
        print(f"  ✓ No flags raised")

    return edited


# ---------------------------------------------------------------------------
# Substack auto-draft
# ---------------------------------------------------------------------------

def post_to_substack(html: str) -> None:
    """
    Creates an unpublished draft in Substack via python-substack.
    Requires SUBSTACK_EMAIL, SUBSTACK_PASSWORD, SUBSTACK_URL env vars.
    Skips silently if credentials are not set (local runs).
    """
    email    = os.getenv("SUBSTACK_EMAIL")
    password = os.getenv("SUBSTACK_PASSWORD")
    pub_url  = os.getenv("SUBSTACK_URL")

    if not all([email, password, pub_url]):
        print("  ⚠ Substack credentials not set — skipping auto-draft")
        return

    try:
        from substack import Api
        from substack.post import Post
    except ImportError:
        print("  ⚠ python-substack not installed — skipping auto-draft")
        return

    print("\n── SUBSTACK AUTO-DRAFT ─────────────────────────────")

    try:
        api = Api(
            email=email,
            password=password,
            publication_url=pub_url,
        )
        user_id = api.get_user_id()

        # Extract title from first <h1> in the HTML
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else "SLAP Newsletter"

        from datetime import date
        subtitle = f"Sports Lunch Afternoon Post — {date.today().strftime('%B %-d, %Y')}"

        post = Post(
            title=title,
            subtitle=subtitle,
            user_id=user_id,
        )

        # Extract just the body content — strip the full HTML document wrapper.
        # Substack's API wants the inner content, not a full HTML document.
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
        body_content = body_match.group(1).strip() if body_match else html

        # Remove HTML comments (editor flags) before sending to Substack
        body_content = re.sub(r'<!--.*?-->', '', body_content, flags=re.DOTALL).strip()

        post.add({"type": "html", "content": body_content})

        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id")
        print(f"  ✓ Draft created in Substack (id: {draft_id})")
        print(f"  → Review and publish at: {pub_url}/publish/post/{draft_id}")

    except Exception as e:
        print(f"  ✗ Substack auto-draft failed: {e}")
        print("    Newsletter files saved locally — paste manually if needed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
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

    # Three passes
    story_plan  = run_pass1(raw, client)
    draft_html  = run_pass2(story_plan, client)
    final_html  = run_pass3(draft_html, recent_output, client)

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
    if not giphy_key:
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
            for output_path in [DRAFT_OUTPUT_PATH, SUBSTACK_OUTPUT_PATH]:
                html = output_path.read_text(encoding="utf-8")
                updated = process_newsletter(html, template_map, imgflip_user, imgflip_pass)
                output_path.write_text(updated, encoding="utf-8")
            print(f"  ✓ Memes embedded in both output files")
    except Exception as e:
        print(f"  ✗ Meme pipeline failed: {e}")
        print("    Newsletter saved without memes — safe to publish as-is.")

    flag_count = len(re.findall(r'<!-- EDITOR FLAG:', final_html))
    if flag_count:
        print(f"\n⚠  {flag_count} editor flag(s) need review before publishing.")
        print(f"   Search 'EDITOR FLAG' in newsletter_draft.html to find them.")

    # Auto-post draft to Substack (skips if credentials not set)
    # Read from disk — not the in-memory variable — so GIFs and memes are included
    post_to_substack(SUBSTACK_OUTPUT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
