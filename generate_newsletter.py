"""
SLAP Newsletter v6 — Middle-ground architecture.

Pass 1: Story Selector (editorial brain)
  - Reads raw_content.json
  - Outputs lightweight story plan: stories, depth targets, tweet assignments, research notes
  - NO block-by-block skeleton — just editorial decisions and raw material
  - Uses: pass1_story_selector.txt + editorial_annotations.txt + rolling_feedback.txt

Pass 2: Writer (full creative freedom)
  - Reads the story plan from Pass 1
  - Writes the complete newsletter with full structural control
  - Decides block count, GIF placement, rhythm, and depth per story
  - Uses: base_prompt.txt + pass2_writer.txt + voice_examples.txt + rolling_feedback.txt

Pass 3: Editor
  - Rewrites for flow, cohesion, energy arc
  - Uses: editor_prompt.txt + voice_examples.txt + editorial_annotations.txt

Prompt assembly:
  Pass 1: pass1_story_selector.txt + editorial_annotations.txt + rolling_feedback.txt
  Pass 2: base_prompt.txt + pass2_writer.txt + voice_examples.txt + rolling_feedback.txt
  Pass 3: editor_prompt.txt + voice_examples.txt + editorial_annotations.txt
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
SKELETON_PATH = SCRIPT_DIR / "newsletter_skeleton.json"
RECENT_OUTPUT_PATH = SCRIPT_DIR / "recent_output.json"
PROMPTS_DIR = SCRIPT_DIR / "prompts"

MODEL = "claude-sonnet-4-20250514"
SELECTOR_MODEL = "claude-sonnet-4-20250514"    # Pass 1: editorial judgment
WRITER_MODEL = "claude-sonnet-4-20250514"      # Pass 2: voice + structure
EDITOR_MODEL = "claude-sonnet-4-20250514"      # Pass 3: polish

MAX_SEARCHES = 8          # Hard cap on web search calls per run
MAX_HEADLINES = 40         # Pre-filter: send only top N headlines
MAX_TWEETS = 60            # Pre-filter: send only top N tweets
MAX_OUTPUT_TOKENS = 8192

RATE_LIMIT_PAUSE = 10      # Seconds to wait between passes (retry logic handles actual rate limits)


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
            blockquote_pattern = re.compile(
                r'<blockquote class="tweet">.*?' + re.escape(url) + r'.*?</blockquote>',
                re.DOTALL
            )
            replacement = f'<div class="tweet-embed">{oembed_html}</div>'
            html, count = blockquote_pattern.subn(replacement, html, count=1)
            if count > 0:
                embed_count += 1
                print(f"           -> Embedded successfully")
            else:
                print(f"           -> Could not find matching blockquote")
        else:
            print(f"           -> oEmbed failed, keeping fallback format")

        time.sleep(0.3)

    if embed_count > 0:
        widget_script = '<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>'
        html = html.replace("</body>", f"{widget_script}\n</body>")

    print(f"  [OEMBED] {embed_count}/{len(urls_found)} tweets embedded via oEmbed")
    return html


# ── GIF Auto-Embedding (Giphy API) ──────────────────────────────────────────

GIPHY_API_URL = "https://api.giphy.com/v1/gifs/search?api_key={}&q={}&limit=1&rating=pg-13"

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


def embed_gifs_in_html(html: str, api_key: str) -> str:
    gif_pattern = re.compile(
        r'<div class="gif-placeholder">\s*GIF:\s*(.+?)\s*</div>',
        re.DOTALL
    )

    matches = gif_pattern.findall(html)
    if not matches:
        return html

    embed_count = 0
    for search_term in matches:
        queries = clean_giphy_search(search_term)
        gif_url = None

        for q in queries:
            print(f"  [GIPHY] Searching: {q}")
            gif_url = fetch_giphy_url(q, api_key)
            if gif_url:
                break
            print(f"           -> No results, trying shorter...")
            time.sleep(0.2)

        if gif_url:
            placeholder_pattern = re.compile(
                r'<div class="gif-placeholder">\s*GIF:\s*'
                + re.escape(search_term)
                + r'\s*</div>',
                re.DOTALL
            )
            img_html = (
                f'<div style="margin: 16px 0; text-align: center;">'
                f'<img src="{gif_url}" alt="{queries[0]}" '
                f'style="max-width: 100%; border-radius: 8px;" '
                f'onerror="this.parentElement.innerHTML=\'<div class=\\\'gif-placeholder\\\'>GIF: {queries[0]}</div>\';" />'
                f'</div>'
            )
            html, count = placeholder_pattern.subn(img_html, html, count=1)
            if count > 0:
                embed_count += 1
                print(f"           -> Found: {gif_url[:80]}...")
            else:
                print(f"           -> Could not find matching placeholder")
        else:
            print(f"           -> All queries failed, keeping placeholder")

        time.sleep(0.3)

    print(f"  [GIPHY] {embed_count}/{len(matches)} GIFs embedded")
    return html


# ── HTML Templates ───────────────────────────────────────────────────────────

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


# ── Content Loading & Pre-Filtering ──────────────────────────────────────────

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

HIGH_VALUE_ACCOUNTS = {
    # Original
    "barstoolbigcat", "pftcommenter", "ballsacksports", "haterreport",
    "thenbacentel", "oldtakesexposed", "adamschefter",
    "shamscharania", "statmuse", "houseofhighlights",
    # New comedy/reaction
    "notsportscenter", "overtime", "sportscenter", "contextfreecbb",
    "clutchpoints", "nbamemes", "nflmemes", "joeymulinaro",
    "cjzero", "trashtalknfl",
    # New insiders with personality
    "jonrothstein", "lebatardshow", "barstoolreags", "kenjac",
    "billbarnwell", "fieldyates",
}


def score_headline(headline: dict) -> float:
    text = (headline.get("title", "") + " " + headline.get("source", "")).lower()
    sport_score = 10
    for keyword, priority in SPORT_PRIORITY.items():
        if keyword in text:
            sport_score = min(sport_score, priority)
            break
    return sport_score


def score_tweet(tweet: dict) -> float:
    username = tweet.get("username", tweet.get("account", "")).lower().replace("@", "")
    account_score = 0 if username in HIGH_VALUE_ACCOUNTS else 5
    text = tweet.get("text", "")
    length_bonus = -1 if len(text) > 100 else 0
    return account_score + length_bonus


def load_and_filter_raw_content() -> dict:
    with open(RAW_CONTENT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    headlines = raw.get("news_headlines", [])
    tweets = raw.get("tweets", [])

    original_h = len(headlines)
    original_t = len(tweets)

    headlines.sort(key=score_headline)
    tweets.sort(key=score_tweet)

    filtered = {
        "news_headlines": headlines[:MAX_HEADLINES],
        "tweets": tweets[:MAX_TWEETS],
    }

    for key in raw:
        if key not in ("news_headlines", "tweets"):
            filtered[key] = raw[key]

    print(f"  Headlines: {original_h} -> {len(filtered['news_headlines'])} (filtered)")
    print(f"  Tweets:    {original_t} -> {len(filtered['tweets'])} (filtered)")

    return filtered


# ── Prompt Loading ───────────────────────────────────────────────────────────

def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def strip_code_fences(text: str) -> str:
    """Remove code fences and any prose surrounding them."""
    # First try to extract content from between code fences
    fence_match = re.search(r'```(?:html|json)?\s*\n(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    # Fallback: strip leading/trailing fences
    text = re.sub(r'^```(?:html|json)?\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text


def extract_json(text: str) -> str:
    """Extract JSON from text that may contain prose before/after it."""
    text = strip_code_fences(text)
    # If it already starts with {, return as-is
    if text.lstrip().startswith('{'):
        return text.strip()
    # Find the first { and last } to extract the JSON object
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    return text.strip()


def repair_json(raw_text: str) -> dict | None:
    """
    Attempt to repair malformed JSON from LLM output.
    Common issues: unescaped quotes in strings, trailing commas,
    unescaped newlines, smart quotes, etc.
    Returns parsed dict on success, None on failure.
    """
    text = raw_text

    # 1. Replace smart quotes with straight quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")

    # 2. Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Remove trailing commas before } or ]
    text = re.sub(r',\s*([\]}])', r'\1', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 4. Try fixing unescaped quotes inside string values
    #    Strategy: use a line-by-line approach to escape interior quotes
    def fix_string_values(t: str) -> str:
        """Fix unescaped double quotes inside JSON string values."""
        result = []
        i = 0
        in_string = False
        escape_next = False

        while i < len(t):
            ch = t[i]

            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
                continue

            if ch == '\\':
                result.append(ch)
                escape_next = True
                i += 1
                continue

            if ch == '"':
                if not in_string:
                    in_string = True
                    result.append(ch)
                else:
                    # Look ahead to see if this quote ends the string value
                    # A closing quote is followed by: , ] } : or whitespace+one of those
                    rest = t[i+1:].lstrip()
                    if not rest or rest[0] in ',]}:':
                        # This is a real closing quote
                        in_string = False
                        result.append(ch)
                    else:
                        # This is an interior quote — escape it
                        result.append('\\"')
                        i += 1
                        continue
            else:
                # Escape literal newlines inside strings
                if in_string and ch == '\n':
                    result.append('\\n')
                    i += 1
                    continue
                result.append(ch)

            i += 1

        return ''.join(result)

    fixed = fix_string_values(text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 5. Nuclear option: try to extract just the key fields with regex
    #    (not implemented — return None to signal failure)
    return None


# ── API Call with Rate Limit Retry ───────────────────────────────────────────

def api_call_with_retry(client, max_retries=2, **kwargs):
    """Make an API call with automatic retry on rate limit errors."""
    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            if attempt < max_retries:
                print(f"  [RATE LIMIT] Hit rate limit, waiting {RATE_LIMIT_PAUSE}s before retry...")
                time.sleep(RATE_LIMIT_PAUSE)
            else:
                raise e


# ── Cost Tracking ────────────────────────────────────────────────────────────

class CostTracker:
    """Track API costs across all passes."""

    # Pricing per million tokens
    PRICING = {
        "claude-sonnet-4-20250514": {"input": 3, "output": 15, "cache_read": 0.30, "cache_write": 3.75},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4, "cache_read": 0.08, "cache_write": 1.0},
    }

    def __init__(self):
        self.passes = []

    def record(self, pass_name: str, model: str, response):
        pricing = self.PRICING.get(model, self.PRICING["claude-sonnet-4-20250514"])
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)

        cost = (
            input_tokens * pricing["input"] / 1_000_000
            + cache_read * pricing["cache_read"] / 1_000_000
            + cache_write * pricing["cache_write"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

        self.passes.append({
            "pass": pass_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "cost": cost,
        })

        print(f"  [COST] {pass_name}: ${cost:.4f} "
              f"(in: {input_tokens:,} | out: {output_tokens:,}"
              f"{f' | cache_read: {cache_read:,}' if cache_read else ''}"
              f"{f' | cache_write: {cache_write:,}' if cache_write else ''})")

    def total(self) -> float:
        return sum(p["cost"] for p in self.passes)

    def summary(self):
        print(f"\n{'='*50}")
        print(f"COST SUMMARY")
        print(f"{'='*50}")
        for p in self.passes:
            print(f"  {p['pass']:20s}  ${p['cost']:.4f}  ({p['model'].split('-')[1]})")
        print(f"  {'-'*35}")
        print(f"  {'TOTAL':20s}  ${self.total():.4f}")
        print(f"{'='*50}")


# ── Recent Output Memory ─────────────────────────────────────────────────────

def load_recent_output() -> dict | None:
    """Load yesterday's output summary for continuity checks."""
    if RECENT_OUTPUT_PATH.exists():
        try:
            with open(RECENT_OUTPUT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  [MEMORY] Loaded recent_output.json (lead: {data.get('lead_story', '?')[:50]})")
            return data
        except (json.JSONDecodeError, IOError):
            print("  [MEMORY] recent_output.json exists but couldn't be read — skipping")
            return None
    else:
        print("  [MEMORY] No recent_output.json found — first run or reset")
        return None


def save_recent_output(plan: dict, newsletter_html: str) -> None:
    """Save today's output summary for tomorrow's continuity checks."""
    stories = plan.get("stories", [])
    lead = next((s for s in stories if s.get("depth") == "deep"), {})

    # Extract GIF references from the final HTML
    gif_pattern = re.compile(r'GIF:\s*(.+?)\s*</div>', re.DOTALL)
    gif_refs = gif_pattern.findall(newsletter_html)

    # Collect all headline topics
    headlines = [s.get("headline", "") for s in stories]

    recent = {
        "date": plan.get("date", "unknown"),
        "lead_story": lead.get("headline", "unknown"),
        "all_headlines": headlines,
        "gif_references": gif_refs,
    }

    with open(RECENT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(recent, f, indent=2, ensure_ascii=False)
    print(f"  [MEMORY] Saved recent_output.json for next run")


# ── PASS 1: Story Selector ────────────────────────────────────────────────────

def pass1_story_selector(raw: dict, client: anthropic.Anthropic, tracker: CostTracker) -> dict:
    """
    Editorial brain: selects stories, assigns tweets, provides research notes.
    Returns lightweight story plan (no block skeleton).
    """
    print("\n" + "=" * 50)
    print("PASS 1: Story Selector")
    print("=" * 50)

    selector_prompt = load_prompt("pass1_story_selector.txt")
    if not selector_prompt:
        raise SystemExit("Error: prompts/pass1_story_selector.txt not found.")

    editorial_annotations = load_prompt("editorial_annotations.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")

    system_blocks = [
        {
            "type": "text",
            "text": selector_prompt,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    if editorial_annotations:
        system_blocks.append({
            "type": "text",
            "text": (
                "## EDITORIAL ANNOTATIONS\n\n"
                "Apply these rules to every story selection and tweet assignment.\n\n"
                + editorial_annotations
            ),
            "cache_control": {"type": "ephemeral"},
        })
        print("  [PROMPT] editorial_annotations.txt loaded")

    if rolling_feedback:
        system_blocks.append({
            "type": "text",
            "text": (
                "## ROLLING FEEDBACK\n\n"
                "Apply these editorial notes from recent issues.\n\n"
                + rolling_feedback
            ),
        })
        print("  [PROMPT] rolling_feedback.txt loaded")

    gif_reference = load_prompt("gif_reference.txt")
    if gif_reference:
        system_blocks.append({
            "type": "text",
            "text": (
                "## GIF REFERENCE\n\n"
                "Use this menu to select GIFs. Rotate selections.\n\n"
                + gif_reference
            ),
        })
        print("  [PROMPT] gif_reference.txt loaded")

    total_chars = sum(len(b["text"]) for b in system_blocks)
    print(f"  [PROMPT] Pass 1 system prompt: {total_chars:,} chars")

    # Load yesterday's output for continuity
    recent = load_recent_output()
    recent_context = ""
    if recent:
        recent_context = (
            "\n\n## YESTERDAY'S NEWSLETTER (avoid overlap)\n"
            f"Lead story: {recent.get('lead_story', 'unknown')}\n"
            f"All headlines: {json.dumps(recent.get('all_headlines', []))}\n"
            f"GIF references used: {json.dumps(recent.get('gif_references', []))}\n"
            "RULE: Do not repeat yesterday's lead. Do not reuse yesterday's GIFs.\n"
        )

    raw_json = json.dumps(raw, ensure_ascii=False)
    user_message = (
        "Here is today's raw content. Analyze it and output the story plan as JSON.\n\n"
        + raw_json
        + recent_context
    )
    print(f"  [INPUT] User message: {len(user_message):,} chars")

    messages = [{"role": "user", "content": user_message}]

    print("\n  Selecting stories and building plan...")
    print("  " + "-" * 48)

    # Pass 1 gets web search for research
    search_count = 0
    total_input = 0
    total_output = 0

    while True:
        response = api_call_with_retry(
            client,
            model=SELECTOR_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_blocks,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            for block in tool_uses:
                query = block.input.get("query", "unknown")
                search_count += 1
                print(f"  [SEARCH {search_count}/{MAX_SEARCHES}] {query}")

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_uses:
                if search_count > MAX_SEARCHES:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "SEARCH LIMIT REACHED. Output the JSON plan now with what you have.",
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search completed",
                    })

            messages.append({"role": "user", "content": tool_results})

            if search_count >= MAX_SEARCHES:
                print(f"  [LIMIT] Web search cap reached ({MAX_SEARCHES})")

            continue
        else:
            break

    # Extract JSON from response
    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    raw_text = extract_json(raw_text)

    tracker.record("Pass 1: Selector", SELECTOR_MODEL, response)

    try:
        plan = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"\n  [WARN] Initial JSON parse failed: {e}")
        print(f"  [REPAIR] Attempting JSON repair...")

        plan = repair_json(raw_text)

        if plan is None:
            print(f"  [ERROR] JSON repair also failed")
            print(f"  [DEBUG] Raw output (first 500 chars): {raw_text[:500]}")
            debug_path = SCRIPT_DIR / "pass1_debug.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(raw_text)
            print(f"  [DEBUG] Full output saved to {debug_path}")
            raise SystemExit("Pass 1 failed to produce valid JSON.")
        else:
            print(f"  [REPAIR] JSON repaired successfully")

    # Print plan summary
    stories = plan.get("stories", [])
    atl = plan.get("around_the_league", plan.get("timeline", []))
    quality = plan.get("quality_check", {})

    print(f"\n  [PLAN] Stories: {len(stories)} headlines")
    for s in stories:
        tweet_count = len(s.get("tweets", []))
        has_research = bool(s.get("research_notes"))
        print(f"         {'->':>3} [{s.get('depth', '?'):6s}] {s.get('headline', '?')[:55]} "
              f"({tweet_count} tweets{', +research' if has_research else ''})")
    print(f"  [PLAN] Around the League: {len(atl)} tweets")
    print(f"  [PLAN] Total tweets assigned: {quality.get('total_tweets_assigned', '?')}")

    # Save plan for inspection
    with open(SKELETON_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    print(f"\n  [SAVED] Plan -> {SKELETON_PATH.name}")

    return plan


# ── PASS 2: Writer ───────────────────────────────────────────────────────────

def pass2_writer(plan: dict, raw: dict, client: anthropic.Anthropic, tracker: CostTracker) -> str:
    """
    Takes the story plan and writes the complete newsletter with full creative freedom.
    Returns complete HTML newsletter.
    """
    print("\n" + "=" * 50)
    print("PASS 2: Writer")
    print("=" * 50)

    base_prompt = load_prompt("base_prompt.txt")
    writer_prompt = load_prompt("pass2_writer.txt")
    if not writer_prompt:
        raise SystemExit("Error: prompts/pass2_writer.txt not found.")

    voice_examples = load_prompt("voice_examples.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")

    system_blocks = []

    if base_prompt:
        system_blocks.append({
            "type": "text",
            "text": base_prompt,
            "cache_control": {"type": "ephemeral"},
        })
        print("  [PROMPT] base_prompt.txt loaded")

    system_blocks.append({
        "type": "text",
        "text": writer_prompt,
        "cache_control": {"type": "ephemeral"},
    })

    if voice_examples:
        system_blocks.append({
            "type": "text",
            "text": (
                "## VOICE EXAMPLES\n\n"
                "The following sections were written by the founder. "
                "This IS the voice. Match this energy exactly.\n\n"
                + voice_examples
            ),
            "cache_control": {"type": "ephemeral"},
        })
        print("  [PROMPT] voice_examples.txt loaded")

    if rolling_feedback:
        system_blocks.append({
            "type": "text",
            "text": (
                "## ROLLING FEEDBACK\n\n"
                "Apply these notes from recent issues.\n\n"
                + rolling_feedback
            ),
        })
        print("  [PROMPT] rolling_feedback.txt loaded")

    gif_reference = load_prompt("gif_reference.txt")
    meme_reference = load_prompt("meme_reference.txt")

    if gif_reference:
        system_blocks.append({
            "type": "text",
            "text": (
                "## GIF REFERENCE\n\n"
                "Use this menu to select GIFs. Rotate selections.\n\n"
                + gif_reference
            ),
        })
        print("  [PROMPT] gif_reference.txt loaded")

    if meme_reference:
        system_blocks.append({
            "type": "text",
            "text": (
                "## MEME REFERENCE\n\n"
                "Use this menu to select and specify memes.\n\n"
                + meme_reference
            ),
        })
        print("  [PROMPT] meme_reference.txt loaded")

    total_chars = sum(len(b["text"]) for b in system_blocks)
    print(f"  [PROMPT] Pass 2 system prompt: {total_chars:,} chars")

    # Send the plan directly — it already has tweet text included
    plan_json = json.dumps(plan, ensure_ascii=False)
    user_message = (
        "Here is the story plan for today's SLAP newsletter. "
        "Write the complete newsletter as HTML. Use the depth targets, "
        "assigned tweets, and research notes to guide your writing. "
        "You have full creative freedom on structure, block count, "
        "GIF/meme placement, and rhythm.\n\n"
        + plan_json
    )
    print(f"  [INPUT] User message: {len(user_message):,} chars")

    print("\n  Writing newsletter...")
    print("  " + "-" * 48)

    response = api_call_with_retry(
        client,
        model=WRITER_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_message}],
    )

    newsletter_html = ""
    for block in response.content:
        if hasattr(block, "text"):
            newsletter_html += block.text

    newsletter_html = strip_code_fences(newsletter_html)

    tracker.record("Pass 2: Writer", WRITER_MODEL, response)

    print(f"  [OUTPUT] Newsletter: {len(newsletter_html):,} chars")

    return newsletter_html


# ── PASS 3: Editor ───────────────────────────────────────────────────────────

def pass3_editor(draft_html: str, client: anthropic.Anthropic, tracker: CostTracker) -> str:
    """
    Rewrites the draft for flow, cohesion, and energy arc.
    """
    print("\n" + "=" * 50)
    print("PASS 3: Editor")
    print("=" * 50)

    editor_prompt = load_prompt("editor_prompt.txt")
    if not editor_prompt:
        print("  [SKIP] prompts/editor_prompt.txt not found — skipping editor pass")
        return draft_html

    voice_examples = load_prompt("voice_examples.txt")
    editorial_annotations = load_prompt("editorial_annotations.txt")

    system_blocks = [
        {
            "type": "text",
            "text": editor_prompt,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    if voice_examples:
        system_blocks.append({
            "type": "text",
            "text": (
                "## VOICE REFERENCE\n\n"
                "Match this energy.\n\n"
                + voice_examples
            ),
            "cache_control": {"type": "ephemeral"},
        })

    if editorial_annotations:
        system_blocks.append({
            "type": "text",
            "text": (
                "## EDITORIAL RULES REFERENCE\n\n"
                "Apply these rules during your edit pass.\n\n"
                + editorial_annotations
            ),
            "cache_control": {"type": "ephemeral"},
        })

    user_message = (
        "Here is today's SLAP newsletter draft. Edit it for flow, cohesion, and energy. "
        "Output the complete rewritten HTML.\n\n"
        + draft_html
    )

    print(f"  [EDITOR] Draft length: {len(draft_html):,} chars")

    response = api_call_with_retry(
        client,
        model=EDITOR_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_message}],
    )

    edited_html = ""
    for block in response.content:
        if hasattr(block, "text"):
            edited_html += block.text

    edited_html = strip_code_fences(edited_html)

    tracker.record("Pass 3: Editor", EDITOR_MODEL, response)

    return edited_html


# ── Output ───────────────────────────────────────────────────────────────────

SUBSTACK_OUTPUT_PATH = SCRIPT_DIR / "newsletter_substack.html"


def extract_tweet_urls(html: str) -> list[str]:
    pattern = re.compile(
        r'https?://(?:twitter\.com|x\.com)/\w+/status/\d+'
    )
    return pattern.findall(html)


def create_substack_version(html_content: str) -> str:
    blockquote_pattern = re.compile(
        r'<blockquote class="tweet">.*?'
        r'<a href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)"[^>]*>View tweet</a>'
        r'.*?</blockquote>',
        re.DOTALL
    )

    def replace_with_bare_url(match):
        url = match.group(1)
        return f'<p>{url}</p>'

    substack_html = blockquote_pattern.sub(replace_with_bare_url, html_content)
    return substack_html


def save_newsletter(content: str, skip_oembed: bool = False) -> None:
    giphy_key = os.getenv("GIPHY_API_KEY", "")

    preview_html = HTML_TEMPLATE.format(content=content)

    if not skip_oembed:
        print("\nPost-processing: tweet oEmbed for browser preview...")
        preview_html = embed_tweets_in_html(preview_html)

    if giphy_key:
        print("\nPost-processing: Giphy auto-embedding...")
        preview_html = embed_gifs_in_html(preview_html, giphy_key)
    else:
        gif_count = len(re.findall(r'class="gif-placeholder"', preview_html))
        if gif_count > 0:
            print(f"\n[GIPHY] Skipped — no GIPHY_API_KEY in .env ({gif_count} placeholders remain)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(preview_html)

    substack_content = create_substack_version(content)
    print("\n[SUBSTACK] Using bare tweet URLs — Substack auto-embeds these on publish")

    if giphy_key:
        substack_content = embed_gifs_in_html(substack_content, giphy_key)

    substack_html = SUBSTACK_TEMPLATE.format(content=substack_content)

    with open(SUBSTACK_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(substack_html)

    tweet_urls = extract_tweet_urls(content)
    print(f"\n[TWEETS] Found {len(tweet_urls)} tweet URLs in newsletter")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    skip_oembed = "--no-oembed" in sys.argv
    skip_editor = "--no-editor" in sys.argv
    skip_gifs = "--no-gifs" in sys.argv
    only_plan = "--plan-only" in sys.argv

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: Set ANTHROPIC_API_KEY in your .env file.")

    client = anthropic.Anthropic(api_key=api_key)
    tracker = CostTracker()

    print(f"Loading {RAW_CONTENT_PATH.name}...")
    raw = load_and_filter_raw_content()

    headline_count = len(raw.get("news_headlines", []))
    tweet_count = len(raw.get("tweets", []))

    if headline_count == 0 and tweet_count == 0:
        print("\nNo content to work with. Run fetch_content.py first.")
        return

    # ── Pass 1: Story selection + research ──
    plan = pass1_story_selector(raw, client, tracker)

    if only_plan:
        print(f"\n[DONE] Plan-only mode. Review {SKELETON_PATH.name} and rerun without --plan-only.")
        tracker.summary()
        return

    # ── Pause for rate limits ──
    print(f"\n  [PAUSE] Waiting {RATE_LIMIT_PAUSE}s for rate limit reset...")
    time.sleep(RATE_LIMIT_PAUSE)

    # ── Pass 2: Writer (full creative freedom) ──
    content = pass2_writer(plan, raw, client, tracker)

    # ── Pause for rate limits ──
    if not skip_editor:
        print(f"\n  [PAUSE] Waiting {RATE_LIMIT_PAUSE}s for rate limit reset...")
        time.sleep(RATE_LIMIT_PAUSE)

    # ── Pass 3: Editor ──
    if skip_editor:
        print("\n[EDITOR] Skipped (--no-editor flag)")
    else:
        content = pass3_editor(content, client, tracker)

    # ── Save outputs ──
    save_newsletter(content, skip_oembed=skip_oembed)

    # ── Save recent output for next run's continuity checks ──
    save_recent_output(plan, content)

    tracker.summary()

    print(f"\nOutputs saved:")
    print(f"  -> {SKELETON_PATH.name}         — Story plan (JSON)")
    print(f"  -> {OUTPUT_PATH.name}    — Browser preview")
    print(f"  -> {SUBSTACK_OUTPUT_PATH.name}  — Substack-ready")
    if skip_oembed:
        print("  (oEmbed skipped — run without --no-oembed for rich preview)")
    if skip_editor:
        print("  (Editor pass skipped — run without --no-editor for flow editing)")


if __name__ == "__main__":
    main()
