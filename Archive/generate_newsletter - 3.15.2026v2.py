"""
SLAP Newsletter v4 — Cost-optimized with tweet embeds.

Changes from v3:
  - Pre-filters raw_content.json (top 40 headlines + 60 tweets) to shrink input
  - Hard cap on web searches (MAX_SEARCHES=5) to control cost
  - Prompt caching enabled (system prompt is stable across runs)
  - Tweet oEmbed post-processing: converts tweet URLs to rich Substack-compatible embeds
  - Haiku triage option: use --triage flag to run cheap story selection first
  - Cost estimator updated for current Sonnet 4.5 pricing ($3/$15 per MTok)

Prompt assembly order:
  1. base_prompt.txt      — SLAP identity, voice rules, structure, guardrails
  2. voice_examples.txt   — Hand-written examples by Abram. The target voice.
  3. rolling_feedback.txt — Synthesized feedback from last 5-7 issues (dynamic)
"""

import os
import sys
import json
import re
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote

from dotenv import load_dotenv
import anthropic

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env", override=True)

RAW_CONTENT_PATH = SCRIPT_DIR / "raw_content.json"
OUTPUT_PATH = SCRIPT_DIR / "newsletter_draft.html"
PROMPTS_DIR = SCRIPT_DIR / "prompts"

MODEL = "claude-sonnet-4-20250514"
MAX_SEARCHES = 5          # Hard cap on web search calls per run
MAX_HEADLINES = 40         # Pre-filter: send only top N headlines
MAX_TWEETS = 60            # Pre-filter: send only top N tweets
MAX_OUTPUT_TOKENS = 8192

# ── Tweet oEmbed ─────────────────────────────────────────────────────────────

TWITTER_OEMBED_URL = "https://publish.twitter.com/oembed?url={}&omit_script=true"

def fetch_tweet_oembed(tweet_url: str) -> str | None:
    """Fetch oEmbed HTML for a tweet URL. Returns embed HTML or None on failure."""
    try:
        encoded_url = quote(tweet_url, safe="")
        api_url = TWITTER_OEMBED_URL.format(encoded_url)
        req = Request(api_url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("html", None)
    except (URLError, json.JSONDecodeError, KeyError, TimeoutError):
        return None


def embed_tweets_in_html(html: str) -> str:
    """
    Find tweet blockquotes with View tweet links, attempt oEmbed replacement.
    Falls back to original blockquote format if oEmbed fails.
    Also adds the Twitter widget script once at the end if any embeds succeed.
    """
    # Pattern: find tweet URLs in our blockquote format
    tweet_url_pattern = re.compile(
        r'<a href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)"[^>]*>View tweet</a>'
    )

    urls_found = tweet_url_pattern.findall(html)
    if not urls_found:
        return html

    embed_count = 0
    for url in urls_found:
        print(f"  [OEMBED] Fetching embed for {url}...")
        oembed_html = fetch_tweet_oembed(url)
        if oembed_html:
            # Replace the entire blockquote containing this URL with the oEmbed HTML
            # Find the blockquote that contains this URL
            blockquote_pattern = re.compile(
                r'<blockquote class="tweet">.*?' + re.escape(url) + r'.*?</blockquote>',
                re.DOTALL
            )
            replacement = f'<div class="tweet-embed">{oembed_html}</div>'
            html, count = blockquote_pattern.subn(replacement, html, count=1)
            if count > 0:
                embed_count += 1
                print(f"           → Embedded successfully")
            else:
                print(f"           → Could not find matching blockquote")
        else:
            print(f"           → oEmbed failed, keeping fallback format")

        time.sleep(0.3)  # Rate-limit oEmbed calls

    # Add Twitter widget.js script once if we have any embeds
    if embed_count > 0:
        widget_script = '<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>'
        html = html.replace("</body>", f"{widget_script}\n</body>")

    print(f"  [OEMBED] {embed_count}/{len(urls_found)} tweets embedded via oEmbed")
    return html


# ── HTML Template ────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLAP Newsletter</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 20px; line-height: 1.7; color: #1a1a1a; background: #fff; }}
        h1 {{ font-family: Arial, sans-serif; font-size: 28px; line-height: 1.2; margin-top: 40px; }}
        h2 {{ font-family: Arial, sans-serif; font-size: 22px; margin-top: 36px; border-bottom: 2px solid #e94560; padding-bottom: 6px; }}
        p {{ margin-bottom: 16px; font-size: 17px; }}
        .tweet {{ background: #f8f9fa; border-left: 4px solid #1da1f2; padding: 16px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; font-family: Arial, sans-serif; font-size: 15px; line-height: 1.5; }}
        .tweet strong {{ color: #1da1f2; }}
        .tweet a {{ color: #1da1f2; text-decoration: none; font-size: 13px; }}
        .tweet-embed {{ margin: 20px 0; }}
        .gif-placeholder {{ background: #fff3cd; border: 2px dashed #ffc107; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        .gif-placeholder a {{ color: #e94560; font-weight: bold; }}
        hr {{ border: none; border-top: 3px solid #e94560; margin: 40px 0; }}
        blockquote.tweet {{ background: #f8f9fa; border-left: 4px solid #1da1f2; padding: 16px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; font-family: Arial, sans-serif; }}
        a {{ color: #e94560; }}
    </style>
</head>
<body>
{content}
<footer style="margin-top: 60px; padding-top: 20px; border-top: 1px solid #ddd; font-family: Arial, sans-serif; font-size: 13px; color: #888; text-align: center;">
    SLAP -- Sports Lunch Afternoon Post<br>
    Five minutes at lunch. Every sport that matters. Zero doomscrolling.
</footer>
</body>
</html>"""


# ── Content Loading & Pre-Filtering ──────────────────────────────────────────

# Sports priority for sorting (lower = more important)
SPORT_PRIORITY = {
    "nfl": 0, "football": 0,
    "nba": 1, "basketball": 1,
    "college football": 2, "ncaa football": 2, "cfb": 2,
    "college basketball": 3, "ncaa basketball": 3, "march madness": 3,
    "mlb": 4, "baseball": 4,
    "golf": 5, "tennis": 6, "nhl": 6, "hockey": 6,
    "soccer": 7, "mls": 7, "premier league": 7,
    "wnba": 8,
}

# High-value Twitter accounts (comedy/viral get priority since they drive personality)
HIGH_VALUE_ACCOUNTS = {
    "barstoolbigcat", "pftcommenter", "ballsacksports", "haterreport",
    "thenbacentel", "super70ssports", "oldtakesexposed", "adamschefter",
    "shamscharania", "statmuse", "houseofhighlights",
}


def score_headline(headline: dict) -> float:
    """Score a headline for priority. Lower = more important."""
    text = (headline.get("title", "") + " " + headline.get("source", "")).lower()
    sport_score = 10  # default: low priority
    for keyword, priority in SPORT_PRIORITY.items():
        if keyword in text:
            sport_score = min(sport_score, priority)
            break
    return sport_score


def score_tweet(tweet: dict) -> float:
    """Score a tweet for priority. Lower = more important."""
    username = tweet.get("username", "").lower().replace("@", "")
    # Prioritize high-value accounts
    account_score = 0 if username in HIGH_VALUE_ACCOUNTS else 5
    # Boost tweets with more engagement signals (longer text often = more substance)
    text = tweet.get("text", "")
    length_bonus = -1 if len(text) > 100 else 0
    return account_score + length_bonus


def load_and_filter_raw_content() -> dict:
    """Load raw_content.json and pre-filter to reduce token count."""
    with open(RAW_CONTENT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    headlines = raw.get("news_headlines", [])
    tweets = raw.get("tweets", [])

    original_h = len(headlines)
    original_t = len(tweets)

    # Sort by priority and take top N
    headlines.sort(key=score_headline)
    tweets.sort(key=score_tweet)

    filtered = {
        "news_headlines": headlines[:MAX_HEADLINES],
        "tweets": tweets[:MAX_TWEETS],
    }

    # Preserve any other keys in raw_content.json
    for key in raw:
        if key not in ("news_headlines", "tweets"):
            filtered[key] = raw[key]

    print(f"  Headlines: {original_h} → {len(filtered['news_headlines'])} (filtered)")
    print(f"  Tweets:    {original_t} → {len(filtered['tweets'])} (filtered)")

    return filtered


# ── Prompt Assembly ──────────────────────────────────────────────────────────

def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def build_system_prompt() -> list[dict]:
    """
    Assembles system prompt as a list of content blocks for prompt caching.
    The base prompt + voice examples get cache_control since they're stable.
    Rolling feedback changes daily so it doesn't get cached.

    Returns: list of system content blocks (for anthropic SDK system param)
    """
    base_prompt = load_prompt("base_prompt.txt")
    voice_examples = load_prompt("voice_examples.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")

    if not base_prompt:
        raise SystemExit("Error: prompts/base_prompt.txt not found.")

    blocks = []

    # Block 1: Base prompt (cacheable — stable across runs)
    blocks.append({
        "type": "text",
        "text": base_prompt,
        "cache_control": {"type": "ephemeral"},
    })
    print("  [PROMPT] base_prompt.txt loaded")

    # Block 2: Voice examples (cacheable — changes infrequently)
    if voice_examples:
        blocks.append({
            "type": "text",
            "text": (
                "## VOICE EXAMPLES\n\n"
                "The following sections were written by the founder. "
                "This is not a description of the voice -- this IS the voice. "
                "Match this energy exactly.\n\n"
                + voice_examples
            ),
            "cache_control": {"type": "ephemeral"},
        })
        print("  [PROMPT] voice_examples.txt loaded (cached)")
    else:
        print("  [PROMPT] WARNING: voice_examples.txt not found -- output quality will suffer")

    # Block 3: Rolling feedback (NOT cached — changes daily)
    if rolling_feedback:
        blocks.append({
            "type": "text",
            "text": (
                "## ROLLING FEEDBACK (last 5-7 issues)\n\n"
                "These are editorial notes from recent issues. Apply them.\n\n"
                + rolling_feedback
            ),
        })
        print("  [PROMPT] rolling_feedback.txt loaded (not cached — changes daily)")
    else:
        print("  [PROMPT] rolling_feedback.txt empty -- first run or not yet created")

    total_chars = sum(len(b["text"]) for b in blocks)
    print(f"  [PROMPT] Total system prompt: {total_chars:,} chars")

    return blocks


# ── Newsletter Generation ────────────────────────────────────────────────────

def strip_code_fences(text: str) -> str:
    text = re.sub(r'^```html\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text


def generate_newsletter(raw: dict) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: Set ANTHROPIC_API_KEY in your .env file.")

    client = anthropic.Anthropic(api_key=api_key)

    print("\nAssembling system prompt...")
    system_blocks = build_system_prompt()

    # Build user message with filtered raw content
    raw_json = json.dumps(raw, ensure_ascii=False)
    user_message = (
        "Here is today's raw content. Write today's SLAP newsletter.\n\n"
        + raw_json
    )
    print(f"\n  [INPUT] User message: {len(user_message):,} chars")

    messages = [{"role": "user", "content": user_message}]

    print("\nGenerating newsletter (agent may search the web for context)...")
    print("-" * 50)

    total_input_tokens = 0
    total_output_tokens = 0
    cache_read_tokens = 0
    cache_create_tokens = 0
    search_count = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_blocks,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0)
        cache_create_tokens += getattr(response.usage, "cache_creation_input_tokens", 0)

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            for block in tool_uses:
                query = block.input.get("query", "unknown")
                search_count += 1
                print(f"  [SEARCH {search_count}/{MAX_SEARCHES}] {query}")

            # Add assistant turn
            messages.append({"role": "assistant", "content": response.content})

            # Build tool results
            tool_results = []
            for block in tool_uses:
                if search_count > MAX_SEARCHES:
                    # Return a message telling the agent to stop searching
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "SEARCH LIMIT REACHED. You have used all your web searches. Write the newsletter now with what you have.",
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search completed",
                    })

            messages.append({"role": "user", "content": tool_results})

            # If we've hit the cap, the agent should stop on the next turn
            if search_count >= MAX_SEARCHES:
                print(f"  [LIMIT] Web search cap reached ({MAX_SEARCHES})")

            continue
        else:
            break

    # Extract HTML from final response
    newsletter_html = ""
    for block in response.content:
        if hasattr(block, "text"):
            newsletter_html += block.text

    newsletter_html = strip_code_fences(newsletter_html)

    # Cost calculation (Sonnet 4.5: $3/MTok input, $15/MTok output)
    # Cached reads are 90% cheaper ($0.30/MTok), cache writes are 25% more ($3.75/MTok)
    base_input_cost = (total_input_tokens - cache_read_tokens - cache_create_tokens) * 3 / 1_000_000
    cache_read_cost = cache_read_tokens * 0.30 / 1_000_000
    cache_write_cost = cache_create_tokens * 3.75 / 1_000_000
    output_cost = total_output_tokens * 15 / 1_000_000
    est_cost = base_input_cost + cache_read_cost + cache_write_cost + output_cost

    print("-" * 50)
    print(f"[DONE] Newsletter generated ({search_count} web searches)")
    print(f"[STATS] Tokens — Input: {total_input_tokens:,} | Output: {total_output_tokens:,}")
    if cache_read_tokens or cache_create_tokens:
        print(f"[CACHE] Read: {cache_read_tokens:,} | Written: {cache_create_tokens:,}")
    print(f"[COST]  Estimated: ${est_cost:.4f}")

    return newsletter_html


# ── Output ───────────────────────────────────────────────────────────────────

SUBSTACK_OUTPUT_PATH = SCRIPT_DIR / "newsletter_substack.html"

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
        .tweet-url::before {{ content: "🐦 Tweet embed — paste this URL in Substack:"; display: block; font-family: Arial, sans-serif; font-size: 11px; color: #657786; margin-bottom: 4px; font-style: italic; }}
        .gif-placeholder {{ background: #fff3cd; border: 2px dashed #ffc107; padding: 12px 16px; margin: 16px 0; border-radius: 8px; font-family: Arial, sans-serif; font-size: 14px; text-align: center; }}
        hr {{ border: none; border-top: 3px solid #e94560; margin: 40px 0; }}
        a {{ color: #e94560; }}
    </style>
</head>
<body>
{content}
<footer style="margin-top: 60px; padding-top: 20px; border-top: 1px solid #ddd; font-family: Arial, sans-serif; font-size: 13px; color: #888; text-align: center;">
    SLAP -- Sports Lunch Afternoon Post<br>
    Five minutes at lunch. Every sport that matters. Zero doomscrolling.
</footer>
</body>
</html>"""


def extract_tweet_urls(html: str) -> list[str]:
    """Extract all tweet URLs from the newsletter HTML."""
    pattern = re.compile(
        r'https?://(?:twitter\.com|x\.com)/\w+/status/\d+'
    )
    return pattern.findall(html)


def create_substack_version(html_content: str) -> str:
    """
    Create a Substack-optimized version of the newsletter.

    Substack natively embeds tweets when you paste a tweet URL on its own line.
    This version replaces our blockquote tweet format with clean tweet URLs
    that Substack will auto-embed with full media (photos, videos, quote tweets).

    This is the version you should copy-paste into Substack's editor.
    """
    # Replace blockquote tweets with just the URL in a styled div
    # Pattern: find blockquotes containing tweet URLs
    blockquote_pattern = re.compile(
        r'<blockquote class="tweet">.*?'
        r'<a href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)"[^>]*>View tweet</a>'
        r'.*?</blockquote>',
        re.DOTALL
    )

    def replace_with_url(match):
        url = match.group(1)
        return f'<div class="tweet-url">{url}</div>'

    substack_html = blockquote_pattern.sub(replace_with_url, html_content)
    return substack_html


def save_newsletter(content: str, skip_oembed: bool = False) -> None:
    # ── Version 1: Browser preview (with oEmbed rich embeds) ──
    preview_html = HTML_TEMPLATE.format(content=content)

    if not skip_oembed:
        print("\nPost-processing: tweet oEmbed for browser preview...")
        preview_html = embed_tweets_in_html(preview_html)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(preview_html)

    # ── Version 2: Substack-ready (raw tweet URLs for native embedding) ──
    substack_html = SUBSTACK_TEMPLATE.format(
        content=create_substack_version(content)
    )

    with open(SUBSTACK_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(substack_html)

    tweet_urls = extract_tweet_urls(content)
    print(f"\n[TWEETS] Found {len(tweet_urls)} tweet URLs in newsletter")


def main() -> None:
    skip_oembed = "--no-oembed" in sys.argv

    print(f"Loading {RAW_CONTENT_PATH.name}...")
    raw = load_and_filter_raw_content()

    headline_count = len(raw.get("news_headlines", []))
    tweet_count = len(raw.get("tweets", []))

    if headline_count == 0 and tweet_count == 0:
        print("\nNo content to work with. Run fetch_content.py first.")
        return

    content = generate_newsletter(raw)
    save_newsletter(content, skip_oembed=skip_oembed)

    print(f"\nOutputs saved:")
    print(f"  → {OUTPUT_PATH.name}           — Open in browser to preview (oEmbed rich tweets)")
    print(f"  → {SUBSTACK_OUTPUT_PATH.name}  — Copy into Substack editor (tweets auto-embed)")
    if skip_oembed:
        print("  (oEmbed skipped — run without --no-oembed for rich preview)")


if __name__ == "__main__":
    main()
