"""
SLAP Newsletter v3 — Generate newsletter with web search enabled.
Uses Claude Sonnet with the web_search tool for deeper context.
Outputs newsletter_draft.html.

Prompt assembly order:
  1. base_prompt.txt      — SLAP identity, voice rules, structure, guardrails
  2. voice_examples.txt   — Hand-written examples by Abram. The target voice.
  3. rolling_feedback.txt — Synthesized feedback from last 5-7 issues (dynamic)
"""

import os
import json
import re
from pathlib import Path

from dotenv import load_dotenv
import anthropic

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env", override=True)

RAW_CONTENT_PATH = SCRIPT_DIR / "raw_content.json"
OUTPUT_PATH = SCRIPT_DIR / "newsletter_draft.html"
PROMPTS_DIR = SCRIPT_DIR / "prompts"
MODEL = "claude-sonnet-4-20250514"

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


def load_raw_content() -> dict:
    with open(RAW_CONTENT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def build_system_prompt() -> str:
    """
    Assembles the full system prompt from three files in priority order:
      1. base_prompt.txt      — stable identity, voice rules, structure
      2. voice_examples.txt   — Abram's hand-written examples (few-shot anchor)
      3. rolling_feedback.txt — dynamic daily feedback layer
    """
    base_prompt = load_prompt("base_prompt.txt")
    voice_examples = load_prompt("voice_examples.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")

    if not base_prompt:
        raise SystemExit("Error: prompts/base_prompt.txt not found.")

    sections = [base_prompt]

    if voice_examples:
        sections.append(
            "## VOICE EXAMPLES\n\n"
            "The following sections were written by the founder. "
            "This is not a description of the voice -- this IS the voice. "
            "Read these carefully before writing anything. "
            "Match this energy exactly.\n\n"
            + voice_examples
        )
        print("  [PROMPT] voice_examples.txt loaded")
    else:
        print("  [PROMPT] WARNING: voice_examples.txt not found -- output quality will suffer")

    if rolling_feedback:
        sections.append(
            "## ROLLING FEEDBACK (last 5-7 issues)\n\n"
            "These are editorial notes from recent issues. Apply them.\n\n"
            + rolling_feedback
        )
        print("  [PROMPT] rolling_feedback.txt loaded")
    else:
        print("  [PROMPT] rolling_feedback.txt empty -- first run or not yet created")

    return "\n\n" + ("=" * 80) + "\n\n".join(sections)


def strip_code_fences(text: str) -> str:
    text = re.sub(r'^```html\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text


def generate_newsletter(raw: dict) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: Set ANTHROPIC_API_KEY in your .env file.")

    client = anthropic.Anthropic(api_key=api_key)

    print("Assembling system prompt...")
    system_prompt = build_system_prompt()
    print(f"  [PROMPT] Total system prompt: {len(system_prompt):,} chars\n")

    # Build user message with raw content
    user_message = (
        "Here is today's raw content. Write today's SLAP newsletter.\n\n"
        + json.dumps(raw, ensure_ascii=False)
    )

    messages = [{"role": "user", "content": user_message}]

    print("Generating newsletter (agent may search the web for context)...")
    print("-" * 50)

    total_input_tokens = 0
    total_output_tokens = 0
    search_count = 0

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=system_prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        if response.stop_reason == "tool_use":
            # Log each search query
            for block in response.content:
                if block.type == "tool_use":
                    query = block.input.get("query", "unknown")
                    search_count += 1
                    print(f"  [SEARCH {search_count}] {query}")

            # Add assistant turn to conversation history
            messages.append({"role": "assistant", "content": response.content})

            # Build tool results and add as user turn
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search completed",
                    })

            messages.append({"role": "user", "content": tool_results})
            continue

        else:
            # Stop reason is "end_turn" -- agent is done
            break

    # Extract HTML from final response
    newsletter_html = ""
    for block in response.content:
        if hasattr(block, "text"):
            newsletter_html += block.text

    newsletter_html = strip_code_fences(newsletter_html)

    print("-" * 50)
    print(f"[DONE] Newsletter generated ({search_count} web searches)")
    print(f"[STATS] Tokens -- Input: {total_input_tokens:,} | Output: {total_output_tokens:,}")
    est_cost = (total_input_tokens * 3 + total_output_tokens * 15) / 1_000_000
    print(f"[COST]  Estimated: ${est_cost:.4f}")

    return newsletter_html


def save_newsletter(content: str) -> None:
    html = HTML_TEMPLATE.format(content=content)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


def main() -> None:
    print(f"Loading {RAW_CONTENT_PATH.name}...")
    raw = load_raw_content()

    headline_count = len(raw.get("news_headlines", []))
    tweet_count = len(raw.get("tweets", []))
    print(f"  Found {headline_count} headlines, {tweet_count} tweets\n")

    if headline_count == 0 and tweet_count == 0:
        print("No content to work with. Run fetch_content.py first.")
        return

    content = generate_newsletter(raw)
    save_newsletter(content)
    print(f"\nNewsletter saved to {OUTPUT_PATH.name}")
    print("Open it in a browser to preview!")


if __name__ == "__main__":
    main()
