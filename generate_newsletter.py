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
import json
import re
import argparse
from pathlib import Path
from html import escape as escape_html

from dotenv import load_dotenv
import anthropic

# Deterministic audits shared with uat/generate_newsletter_uat.py. One copy on
# purpose: the prompt forks drifted for months because promotion was manual,
# and a second copy of this code would drift the same way somewhere a prompt
# diff would never surface it.
import plan_audit
import meme_library
import gif_library_select

# Runner body shared with the other runner. One copy, imported by both:
# on 2026-09-01 two changes reached one runner and not the other and both
# shipped, because promote.py diffs prompts only. See runner_common.py and
# uat/tests/test_runner_drift.py.
import runner_common
from runner_common import (
    MODEL, MODEL_DEFAULT, MODEL_WRITER, PASS_COSTS, PRICING,
    _normalize_tweet_url,
    blockquotes_to_substack_urls,
    clean_giphy_search,
    cost_summary,
    drop_fabricated_tweets,
    embed_gifs_in_html,
    extract_text,
    fetch_giphy_candidates,
    format_game_state_summary,
    format_recent_media_block,
    format_story_history,
    is_concept_recently_used,
    is_recently_used,
    load_gif_history,
    load_json,
    load_prompt,
    normalize_gif_concept,
    normalize_plan,
    normalize_topic_key,
    run_pass4,
    run_pass6,
    save_gif_history,
    save_story_log,
    strip_code_fences,
)

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env", override=True)

RAW_CONTENT_PATH     = SCRIPT_DIR / "raw_content.json"
RECENT_OUTPUT_PATH   = SCRIPT_DIR / "recent_output.json"
DRAFT_OUTPUT_PATH    = SCRIPT_DIR / "newsletter_draft.html"
SUBSTACK_OUTPUT_PATH = SCRIPT_DIR / "newsletter_substack.html"
EMAIL_OUTPUT_PATH    = SCRIPT_DIR / "newsletter_email.html"
GAME_STATE_PATH      = SCRIPT_DIR / "game_state.json"
STORY_PLAN_PATH      = SCRIPT_DIR / "story_plan.json"
PROMPTS_DIR          = SCRIPT_DIR / "prompts"

# Point the shared helpers at THIS runner's prompt tree.
runner_common.configure(prompts_dir=SCRIPT_DIR / "prompts")

# Around the League alone needs 8-10 real tweets; below this floor there isn't
# enough real tweet supply to build a normal issue (2026-08-23: a full Nitter
# outage produced 0). Below the floor, Pass 1/2 run in degraded (headline-only,
# no ATL) mode instead of the model inventing tweets to hit its schema minimums.
DEGRADED_TWEET_FLOOR = 8




COST_SUMMARY_PATH = SCRIPT_DIR / "cost_summary.json"

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









def strip_leading_narration(html: str) -> str:
    """Drop any "I'll research this first..." narration the model sometimes
    emits before the actual draft when it used web_search mid-response
    (observed 2026-08-23 testing degraded mode). The newsletter always opens
    on the lead story's <h1>; anything before the first one is stray text."""
    m = re.search(r'<h1[\s>]', html, re.IGNORECASE)
    return html[m.start():] if m else html






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

































# ---------------------------------------------------------------------------
# Pass 1 — Story Selector
# ---------------------------------------------------------------------------

def run_pass1(raw: dict, recent_output: list, client: anthropic.Anthropic, game_state: dict | None = None,
              degraded: bool = False,
              recent_meme_slugs: set | None = None) -> str:
    print("\n── PASS 1: Story Selector ──────────────────────────")

    selector_prompt = load_prompt("pass1_story_selector.txt")
    # Compact one-line-per-template index (~1.8K tokens). Pass 1 picks the slug;
    # Pass 2 receives only the chosen templates' full entries, which is why the
    # whole ~20K library is never injected here.
    _meme_index = meme_library.load_selector_index()
    if _meme_index:
        selector_prompt = selector_prompt + "\n\n" + "=" * 80 + "\n\n" + _meme_index
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

    # Tweets carry two extra fields from fetch_content.py. media_kind is for
    # operators, not the model, so it is dropped here; has_video is kept ONLY
    # when true, so the flag reads as a marker on the handful of tweets it
    # applies to instead of ~270 lines of "has_video": false. Pass 1's prompt
    # tells it what the marker means; plan_audit enforces it afterwards.
    def _slim_tweet(d):
        d = _slim_item(d)
        if not isinstance(d, dict):
            return d
        d = {k: v for k, v in d.items() if k != "media_kind"}
        if not d.get("has_video"):
            d.pop("has_video", None)
        return d

    raw_slim = dict(raw)
    raw_slim["news_headlines"] = [_slim_item(h) for h in raw.get("news_headlines", [])]
    raw_slim["tweets"]         = [_slim_tweet(t) for t in raw.get("tweets", [])]

    degraded_block = ""
    if degraded:
        degraded_block = (
            "## ⚠ DEGRADED MODE — TWEET SUPPLY UNAVAILABLE TODAY\n"
            "Twitter/X content could not be fetched today (Nitter is down or nearly "
            "so) — today's raw content has headlines but effectively no tweets. This "
            "overrides the AROUND THE LEAGUE and tweet-related instructions below:\n"
            "- Build the lead and every supporting story from headlines only. Do NOT "
            "invent, paraphrase, or guess at a tweet to fill a story — leave every "
            "story's \"tweets\" array empty.\n"
            "- Submit around_the_league with an EMPTY tweets array. It is not "
            "mandatory today — do not manufacture tweets to reach any count.\n"
            "- Seed a gif_concept (and meme_concept where it fits) for every story, "
            "including ones that wouldn't normally get one — with no tweet commentary "
            "to carry the voice, GIFs/memes are doing more of that work today.\n\n"
        )

    game_state_block = format_game_state_summary(game_state or {})
    # Rotation is decided HERE, where the template is chosen — not left to a
    # warning printed after the meme has already been rendered.
    cooldown_block = meme_library.format_cooldown_block(recent_meme_slugs or set())
    user_content = (
        degraded_block
        + (game_state_block + "\n\n" if game_state_block else "")
        + cooldown_block
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
    # A beat is one complete emotional/informational movement (setup + landing)
    # within a story. Pass 2 writes against these rather than free-forming,
    # which closes the "borrowed tweet" loophole where the writer could pull a
    # tweet assigned to another section. Optional: a light story with no real
    # sub-angles can submit zero beats and the writer falls back to
    # research_notes. "media" holds candidate tweet(s) for that beat, empty
    # when the beat is carried by prose, a GIF or a meme instead.
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
            # Tier is a STRUCTURED decision, not left to the writer's judgment.
            # UAT runs showed the writer collapses every specific concept into a
            # generic library category when the tier is only described in prose
            # — including a seed that literally named a Home Alone scene.
            "gif_tier": {
                "type": "integer",
                "enum": [1, 3],
                "description": (
                    "1 = generic emotional beat, any face works — the writer must "
                    "use a curated library category. 3 = a SPECIFIC named person, "
                    "clip or moment IS the joke and no generic reaction substitutes "
                    "— the writer must use live search. Set 3 only when you named a "
                    "specific person/scene in gif_concept. Omit when gif_concept is "
                    "empty."
                ),
            },
            "meme_concept":   {"type": "string"},
            # Pass 1 picks the TEMPLATE, not just a prose concept.
            # meme_template must be a slug from the MEME SELECTOR INDEX;
            # meme_subject names who the meme is about. Both empty when no meme
            # fits — an empty pair is a valid, common answer.
            "meme_template":  {"type": "string"},
            "meme_subject":   {"type": "string"},
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
                        "tweets": (
                            {
                                "type": "array",
                                "items": _tweet,
                                "maxItems": 10,
                                "description": (
                                    "Degraded mode: real tweet supply is unavailable "
                                    "today. Submit an EMPTY array — do not invent "
                                    "tweets to fill it."
                                ),
                            } if degraded else {
                                "type": "array",
                                "items": _tweet,
                                "minItems": 10,
                                "maxItems": 10,
                                "description": "Exactly 10 tweets. No more, no fewer.",
                            }
                        )
                    },
                    "required": ["tweets"],
                },
                "account_distribution": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
            },
            "required": ["date", "story_log", "lead_story", "supporting_stories",
                         "around_the_league", "account_distribution"],
        },
        "cache_control": {"type": "ephemeral"},
    }

    MAX_ATTEMPTS = 3
    total_in = total_out = total_cache_read = total_cache_write = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        api_error = None
        response  = None

        try:
            # Streaming is REQUIRED here, not a preference. The SDK refuses a
            # non-streaming request whose max_tokens implies a >10-minute
            # generation: it raises ValueError when
            # 3600 * max_tokens / 128_000 > 600, i.e. above 21,333 tokens
            # (anthropic/_base_client.py::_calculate_nonstreaming_timeout).
            # Pass 1 needs 32768 -- beats plus the meme/gif fields roughly
            # double the plan's size, and leaving it at 16384 truncates
            # silently, which is what cost Around the League a regression the
            # last time this limit was too low. So the limit stays and the
            # call streams. 2026-09-01's run died on exactly this: three
            # instant attempts, zero tokens billed, no newsletter.
            # get_final_message() returns the same Message object
            # messages.create() would have -- tool_use blocks and usage
            # included -- so everything below is unchanged.
            with client.messages.stream(
                model=MODEL_DEFAULT,
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
                response = stream.get_final_message()
        except Exception as e:
            api_error = f"{type(e).__name__}: {e}"

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
            validation_error = f"API call failed: {api_error}"

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

            # Safety: if we extracted no usable IDs from raw content, the filter
            # can't validate anything. Two different situations look identical here
            # and need opposite handling:
            #  - raw.tweets is NON-empty but ID extraction failed (a bug in this
            #    file) \u2014 dropping every tweet would be worse than keeping them
            #    (forces Pass 2 to fabricate placeholder URLs), so no-op and warn.
            #  - raw.tweets is EMPTY (Nitter down, degraded mode) \u2014 there is
            #    nothing real to verify against, so anything the model wrote is
            #    by definition fabricated. Drop it instead of shipping fake tweets.
            if not _raw_url_set:
                if raw.get("tweets"):
                    print("  \u26a0 No tweet URLs found in raw content \u2014 skipping cross-reference filter (keeping all plan tweets)")
                else:
                    print("  \u26a0 Zero tweets in raw content \u2014 dropping all plan tweets (tweet supply is down; nothing to verify against)")
                    _lead = plan.get("lead_story", {})
                    _lead["tweets"] = []
                    plan["lead_story"] = _lead
                    plan["supporting_stories"] = [
                        {**_s, "tweets": []} for _s in plan.get("supporting_stories", [])
                    ]
                    plan["around_the_league"] = {"tweets": []}
            else:
                _fab_total = 0
                _lead = plan.get("lead_story", {})
                _lead["tweets"], _n = _filter_and_attach(_lead.get("tweets", []))
                plan["lead_story"] = _lead
                _fab_total += _n

                _new_sup = []
                for _s in plan.get("supporting_stories", []):
                    _s["tweets"], _n = _filter_and_attach(_s.get("tweets", []))
                    _new_sup.append(_s)
                    _fab_total += _n
                plan["supporting_stories"] = _new_sup

                _atl = plan.get("around_the_league", {})
                _atl["tweets"], _n = _filter_and_attach(_atl.get("tweets", []))
                plan["around_the_league"] = _atl
                _fab_total += _n

                if _fab_total:
                    print(f"  \u26a0 Dropped {_fab_total} fabricated tweet(s) \u2014 URLs not in today's raw content")
                else:
                    print(f"  \u2713 All plan tweets verified against today's raw content")

            # §2.1 — video tweets are Around the League only. Enforced here
            # rather than trusted to the prompt: on 2026-08-27 Pass 1 reported
            # its own account-cap violation accurately and shipped anyway.
            # A headliner beat left with no media is the expected outcome —
            # that is the case a GIF or meme fills.
            _vid_rep = plan_audit.enforce_video_policy(
                plan, plan_audit.video_status_ids(raw))
            if _vid_rep["dropped"]:
                _by_section: dict = {}
                for _sec, _acct in _vid_rep["dropped"]:
                    _by_section.setdefault(_sec, []).append(_acct)
                print(f"  §2.1 video filter: {len(_vid_rep['dropped'])} video tweet(s) "
                      f"removed from headliners "
                      f"({_vid_rep['atl_kept']} kept in Around the League)")
                for _sec, _accts in _by_section.items():
                    _before, _after = _vid_rep["sections"].get(_sec, ("?", "?"))
                    print(f"       {_sec}: {_before} → {_after} tweet(s) — "
                          f"{', '.join(_accts)}")
            else:
                print(f"  ✓ §2.1 no video tweets in headliners "
                      f"({_vid_rep['atl_kept']} in Around the League)")

            # Backstop for the cooldown block above: swap anything Pass 1
            # still picked from the last 7 days for another template driven by
            # the SAME comedic engine, so the planned joke survives with a
            # different picture. Pass 2 receives the replacement's full spec.
            for _headline, _old, _new in meme_library.swap_cooled_templates(
                    plan, recent_meme_slugs or set()):
                if _new:
                    print(f"  ♻ meme rotation: {_old} → {_new}  ({_headline})")
                else:
                    print(f"  ⚠ meme rotation: {_old} used in the last 7 days and "
                          f"its engine has no free alternative — kept ({_headline})")

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

            cost_summary("PASS 1", MODEL_DEFAULT, total_in, total_out, total_cache_read, total_cache_write)
            if attempt > 1:
                print(f"  ✓ Pass 1 succeeded on attempt {attempt}/{MAX_ATTEMPTS}")

            # account_distribution is written by the MODEL and spans every
            # tweet including Around the League — but ATL is uncapped under the
            # unified account-cap policy, so trusting it flags accounts that
            # are within policy. On 2026-08-27 the UAT copy of this check named
            # @AdamSchefter and @TalkinBaseball_, both over only because of
            # ATL, while the real violations went unmentioned. Count the
            # headliners ourselves.
            head_dist: dict = {}
            for _s in [plan.get("lead_story", {})] + plan.get("supporting_stories", []):
                for _t in (_s.get("tweets") or []):
                    if isinstance(_t, dict) and _t.get("account"):
                        _a = "@" + str(_t["account"]).lstrip("@")
                        head_dist[_a] = head_dist.get(_a, 0) + 1
            over_cap = {k: v for k, v in head_dist.items()
                        if v > plan_audit.effective_cap(k)}
            if over_cap:
                print(f"  ⚠ Headliner account cap violations: {over_cap}")
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
            cost_summary("PASS 1", MODEL_DEFAULT, total_in, total_out, total_cache_read, total_cache_write)
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

def run_pass2(story_plan: str, client: anthropic.Anthropic, game_state: dict | None = None,
              degraded: bool = False) -> str:
    print("\n── PASS 2: Writer ──────────────────────────────────")

    # pass2_writer.txt is the writer-specific prompt (voice, structure, HTML rules).
    # Voice examples load first — imitation before instruction.
    writer_prompt    = load_prompt("pass2_writer.txt")
    rolling_feedback = load_prompt("rolling_feedback.txt")
    voice_examples   = load_prompt("voice_examples.txt")
    gif_reference    = load_prompt("gif_reference.txt")
    meme_reference   = load_prompt("meme_reference.txt")

    # The GIF library's category menu is injected at load time rather than
    # pasted into the prompt file, so the two can never drift as categories are
    # added or a category's last verified entry is retired. Without this the
    # prompt would ship a literal "{{GIF_LIBRARY_CATEGORIES}}" to the model.
    if "{{GIF_LIBRARY_CATEGORIES}}" in gif_reference:
        import gif_library_select as _GL
        gif_reference = gif_reference.replace(
            "{{GIF_LIBRARY_CATEGORIES}}", _GL.category_prompt_block())

    # The meme template index is injected from the library at load time, for the
    # same reason the GIF categories are: a hand-kept copy in the prompt file
    # drifts. It did — meme_reference.txt and pass2_writer.txt disagreed with
    # the library on 13 of 30 box counts, and a meme built from the wrong count
    # is dropped by meme_box_check rather than shipped, so the writer lost the
    # meme for following its own instructions.
    if "{{MEME_SELECTOR_INDEX}}" in meme_reference:
        _index = meme_library.load_selector_index()
        if _index:
            meme_reference = meme_reference.replace("{{MEME_SELECTOR_INDEX}}", _index)
        else:
            # Never let raw template syntax reach the model; say so loudly,
            # because the writer now has no list of templates to choose from.
            meme_reference = meme_reference.replace("{{MEME_SELECTOR_INDEX}}", "")
            print("  ⚠ meme selector index empty — Pass 2 has NO template list this run")

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

    degraded_block = ""
    if degraded:
        degraded_block = (
            "## ⚠ DEGRADED MODE — NO AROUND THE LEAGUE TODAY\n"
            "Real tweet supply was unavailable today (Nitter down). This overrides "
            "the AROUND THE LEAGUE instructions below:\n"
            "- Do NOT write an \"Around the League\" heading or section at all — "
            "the story plan's around_the_league.tweets is empty on purpose, not "
            "an error to fill. End the issue after the last supporting story.\n"
            "- Every story here is headline-only (no tweets in the plan). Write "
            "them with full confidence from the research notes — a missing tweet "
            "is not a gap to apologize for or work around in the prose.\n"
            "- Lean harder on GIFs/memes than usual: with no tweet commentary "
            "carrying reactions, use the seeded gif_concept/meme_concept on every "
            "story, not just where one happens to fit.\n\n"
        )

    # Pass 2 only needs the story plan — all tweets are pre-assigned by Pass 1.
    # Sending full raw_content.json here was redundant and added ~40K tokens per run.
    game_state_block = format_game_state_summary(game_state or {})
    recent_media_block = format_recent_media_block(SCRIPT_DIR)
    # Full spec for ONLY the 1-2 templates Pass 1 chose (~550 tokens each).
    # Deliberately in the user message, never the cached system block: the
    # selection changes per run and would thrash the prompt cache.
    try:
        _plan_obj = json.loads(story_plan)
    except (json.JSONDecodeError, TypeError):
        _plan_obj = {}
    _meme_slugs = meme_library.collect_meme_slugs(_plan_obj)
    _meme_specs = meme_library.format_meme_specs(_meme_slugs)
    if _meme_slugs:
        print(f"  [memelib] Pass 2 spec injected for: {', '.join(_meme_slugs)}")

    user_message = (
        degraded_block
        + (game_state_block + "\n\n" if game_state_block else "")
        + recent_media_block
        + (_meme_specs + "\n" if _meme_specs else "")
        + "## TODAY'S STORY PLAN\n\n"
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
    return strip_leading_narration(strip_code_fences(extract_text(response)))


# ---------------------------------------------------------------------------
# Pass 4 — Voice Editor
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Pre-Edit — Programmatic Tweet Misassignment Auditor
# ---------------------------------------------------------------------------



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

    # Account caps and §2.2 redundancy, counted rather than asked for.
    # editor_prompt.txt CHECK 3 used to ask the model to do this and it got the
    # answer backwards; see plan_audit.py.
    result = plan_audit.audit_account_diversity(result)
    result = plan_audit.audit_redundant_tweets(result)

    return result


# ---------------------------------------------------------------------------
# Pass 6 — Editor
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SLAP Newsletter Generator")
    parser.add_argument("--no-editor", action="store_true", help="Skip Pass 6 editor")
    parser.add_argument("--no-gifs", action="store_true", help="Skip GIF embedding")
    parser.add_argument("--max-search-gifs", type=int, default=3,
                        help="Tier 3 (live Giphy search) GIFs allowed per issue")
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

    degraded = tweet_count < DEGRADED_TWEET_FLOOR
    if degraded:
        print(f"  ⚠ DEGRADED MODE: only {tweet_count} tweet(s) (< {DEGRADED_TWEET_FLOOR}) — "
              f"today's issue will be headline-only, no Around the League")

    client = anthropic.Anthropic(api_key=api_key)

    # Passes: selector → writer → voice editor → tweet audit → LLM editor
    game_state    = load_json(GAME_STATE_PATH)
    if game_state:
        print(f"  game_state.json loaded — {len(game_state.get('sports', {}))} sport(s)")
    else:
        print("  ⚠ game_state.json not found — run fetch_sports_data.py first")

    # Templates used in the last 7 days, so Pass 1 can avoid them rather than
    # being told off afterwards by the meme pipeline.
    _meme_hist = load_json(SCRIPT_DIR / "meme_history.json") or []
    _cooled = meme_library.recently_used_slugs(_meme_hist)
    if _cooled:
        print(f"  meme cooldown: {len(_cooled)} template(s) used in the last 7 days")

    story_plan    = run_pass1(raw, recent_output, client, game_state, degraded=degraded,
                              recent_meme_slugs=_cooled)
    recent_output = save_story_log(story_plan, recent_output, RECENT_OUTPUT_PATH)

    # §2.4 — verify the media seed floor the Pass 1 prompt asks for. GIF
    # shortfalls are filled from the story's own beat landings; meme shortfalls
    # are reported, never fabricated, because a meme needs a named subject.
    try:
        _p = json.loads(story_plan)
        _s = plan_audit.audit_media_seeds(_p)
        print(f"  §2.4 media seeds: {_s['gif']} gif / {_s['meme']} meme across "
              f"{_s['stories']} stories (floor {plan_audit.MIN_GIF_SEEDS}/"
              f"{plan_audit.MIN_MEME_SEEDS})")
        if _s["gif_short"]:
            for _h, _c in plan_audit.backfill_gif_seeds(_p):
                print(f"       + gif seed from beat landing — {_h}: {_c}…")
            _s = plan_audit.audit_media_seeds(_p)
            story_plan = json.dumps(_p, ensure_ascii=False)
        if _s["meme_short"]:
            print(f"       ⚠ {_s['meme_short']} meme seed(s) below floor — "
                  f"subject gate unmet; not fabricated")
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"  ⚠ §2.4 seed audit skipped — {e}")

    # §2.3 — trim to the tweet budget BEFORE Pass 2 writes, so the excess never
    # reaches the draft. Pass 1's prompt states no ceiling; on 2026-08-27 it
    # took 35 against a 20-24 target. Degrades safely on a plan with no beats.
    try:
        _plan = json.loads(story_plan)
        _rep = plan_audit.enforce_tweet_budget(_plan)
        if _rep["dropped"]:
            _by: dict = {}
            for _reason, _acct in _rep["dropped"]:
                _by[_reason] = _by.get(_reason, 0) + 1
            print(f"  §2.3 tweet budget: {_rep['before']} → {_rep['after']} "
                  f"(ceiling {plan_audit.TWEET_CEILING})")
            for _reason, _n in sorted(_by.items(), key=lambda kv: -kv[1]):
                print(f"       -{_n:<2d} {_reason}")
            story_plan = json.dumps(_plan, ensure_ascii=False)
        else:
            print(f"  ✓ §2.3 tweet budget OK — {_rep['after']} tweet(s)")
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"  ⚠ §2.3 tweet budget skipped — {e}")

    # Persist the plan Pass 2 is about to receive — AFTER §2.4 backfill and §2.3
    # trimming, so what lands on disk is what the writer actually saw.
    #
    # Production discarded this until 2026-09-02. story_log in recent_output.json
    # keeps only topic/title/section, so when the 09-01 issue shipped 1 meme
    # against a floor of 3 there was no way to tell whether Pass 1 under-seeded
    # or Pass 2 under-emitted — the evidence had never been written down. The
    # media seed counts, gif_tier and beats[] all live here and nowhere else.
    try:
        STORY_PLAN_PATH.write_text(story_plan, encoding="utf-8")
        print(f"  ✓ story_plan.json saved — {len(story_plan):,} chars")
    except OSError as e:
        print(f"  ⚠ story_plan.json not saved — {e}")

    draft_html    = run_pass2(story_plan, client, game_state, degraded=degraded)

    # Pass 3 — Claim Validator (deterministic, cross-refs game_state.json)
    try:
        from claim_validator import validate_claims
        validated_html, _val_flags = validate_claims(draft_html, GAME_STATE_PATH)
    except ImportError:
        print("\n── PASS 3: Claim Validator ─────────────────────────")
        print("  ⚠ claim_validator.py not found — skipping")
        validated_html = draft_html

    voiced_html   = run_pass4(validated_html, client)

    # Gate: if Pass 4 returned a meta-response instead of HTML (e.g. it wrote
    # about its approach to obituaries rather than returning the draft), fall back
    # to Pass 2 output. A real newsletter always has at least one h1/h2 tag.
    if not re.search(r'<h[12][\s>]', voiced_html, re.IGNORECASE):
        print("  ⚠ Pass 4 returned non-HTML — falling back to validated draft")
        voiced_html = validated_html

    audited_html  = pre_edit(voiced_html, story_plan)   # deterministic tweet check
    if args.no_editor:
        print("\n\u2500\u2500 PASS 6: Editor \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        print("  \u26a0 Skipped via --no-editor flag")
        final_html = audited_html
    else:
        final_html = run_pass6(audited_html, recent_output, client, game_state)

    # Embed media once into the shared body (below), then write both files
    # from it. This keeps GIF/meme history from being logged twice per run
    # and guarantees the draft and Substack versions show identical media.
    body = final_html

    # Safety net: drop any tweet whose URL lacks a real numeric status ID.
    # The writer occasionally invents a reaction-tweet slot and emits a
    # placeholder URL (e.g. .../status/placeholder) when no real tweet exists
    # for a beat. Those never embed in Substack, so strip them before output.
    body, _fake_tweets = drop_fabricated_tweets(body)
    if _fake_tweets:
        print(f"  \u26a0 Dropped {_fake_tweets} fabricated tweet(s) with non-numeric status IDs")

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
        # Curated library first. It consumes only the placeholders carrying
        # data-library-category; anything the writer left as a plain
        # "GIF: <search term>" falls through to the live-search path below,
        # unchanged. Order matters: library placeholders must be resolved
        # before embed_gifs_in_html() so the two never contend.
        _hist = load_gif_history(SCRIPT_DIR)
        body, _lib_entries, _lib_stats = gif_library_select.render_library_gifs(
            body, giphy_key, _hist)
        if _lib_entries:
            save_gif_history(SCRIPT_DIR, _lib_entries, _hist)

        # Tier 3 budget is enforced BEFORE the search runs, so an over-budget
        # GIF costs nothing rather than being fetched and then discarded.
        body, _search_stats = gif_library_select.enforce_search_budget(
            body, args.max_search_gifs)

        body, _used_gifs = embed_gifs_in_html(body, giphy_key, repo_root=SCRIPT_DIR)
        gif_library_select.report(_lib_stats, _search_stats["kept"])
        try:
            _planned, _seeds = gif_library_select.count_planned_tier3_from_plan(
                json.loads(story_plan))
        except (json.JSONDecodeError, TypeError):
            _planned, _seeds = 0, []
        gif_library_select.report_tiers(_lib_stats, _search_stats,
                                        args.max_search_gifs, _planned, _seeds)
        print(f"  ✓ GIFs embedded in both output files")

    # Nothing un-rendered may reach the output. On 2026-09-01 the writer emitted
    # 7 data-library-category placeholders and prod had no consumer for them, so
    # they shipped as invisible empty divs and the only log line was "No GIF
    # placeholders found" — a silent media loss that read like an editorial
    # choice. This is the backstop for that whole class: any placeholder no
    # consumer claimed gets stripped and counted out loud.
    body, _orphans = gif_library_select.strip_orphan_gif_placeholders(body)
    if _orphans:
        print(f"  ⚠ {len(_orphans)} GIF placeholder(s) reached the output "
              f"un-rendered and were stripped: {', '.join(_orphans)}")

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

            # Box-count guard. Imgflip returns HTTP 200 for a short boxes[]
            # list and renders the leftover panels BLANK, so process_newsletter
            # would otherwise count a meme with an empty punchline as a
            # success. Audit before spending the API call, and drop any
            # placeholder that would render short — a missing meme is better
            # than one with a blank payoff panel.
            import meme_box_check as _MB
            _findings, _ = _MB.check_html(body, template_map)
            _MB.report(_findings, strict=True)
            body, _short = _MB.strip_short_memes(body, _findings)
            if _short:
                print(f"  ⚠ {_short} short meme placeholder(s) removed")

            body, _ = process_newsletter(body, template_map, imgflip_user, imgflip_pass, repo_root=SCRIPT_DIR)
            print(f"  ✓ Memes embedded in both output files")
    except Exception as e:
        print(f"  ✗ Meme pipeline failed: {e}")
        print("    Newsletter saved without memes — safe to publish as-is.")

    # ── Highlights Pipeline ────────────────────────────────
    # Splice official MLB/NHL YouTube game-highlight embeds into the story
    # sections they belong to (Substack -> inline player, email -> linked
    # thumbnail). Runs before the Box Scores header so embeds land within stories.
    print("\n── HIGHLIGHTS PIPELINE ─────────────────────────────")
    try:
        from highlights import inject_highlights
        body, _n_hl = inject_highlights(body, game_state)
        if _n_hl:
            print(f"  ✓ {_n_hl} highlight embed(s) added in both output files")
        else:
            print("  No highlight embeds added.")
    except Exception as e:
        print(f"  ✗ Highlights pipeline failed: {e}")
        print("    Newsletter saved without highlights — safe to publish as-is.")

    # Box Scores section header. Around the League is the last written section,
    # so appending here places "Box Scores" at the very bottom. The cleaned
    # per-sport box score images are uploaded under this header in Substack.
    body = body.rstrip() + '\n<h2>Box Scores</h2>\n'

    # Write both output files from the single embedded body.
    DRAFT_OUTPUT_PATH.write_text(
        DRAFT_TEMPLATE.format(content=body), encoding="utf-8"
    )
    # The published file carries no HTML comments. Every flag in the draft is
    # an instruction to an earlier pass (Pass 5's account caps, Pass 3's
    # ground-truth corrections) and has already been acted on by Pass 6 — it is
    # working notes, not content. Invisible in Gmail either way, but they push
    # the body toward Gmail's ~102 KB "[Message clipped]" limit, which is the
    # exact truncation the cid: image design exists to avoid. The DRAFT keeps
    # them: that copy is archived daily and is where a bad issue gets diagnosed.
    substack_html = re.sub(r'<!--.*?-->', '', blockquotes_to_substack_urls(body),
                           flags=re.DOTALL)
    SUBSTACK_OUTPUT_PATH.write_text(
        SUBSTACK_TEMPLATE.format(content=substack_html), encoding="utf-8"
    )

    flag_count = len(re.findall(r'<!-- EDITOR FLAG:', final_html))
    if flag_count:
        print(f"\n  {flag_count} flag(s) recorded in newsletter_draft.html "
              f"(acted on by Pass 6; stripped from the published file).")

    # Build the email-safe HTML. Consumed by the box-score builder
    # (box_score/build_box_score.py --append); not auto-sent anywhere.
    print("\n── EMAIL BUILD ─────────────────────────────────────")
    try:
        from build_email_html import build_email_html
        draft_for_email = DRAFT_OUTPUT_PATH.read_text(encoding="utf-8")
        email_html = build_email_html(draft_for_email)
        EMAIL_OUTPUT_PATH.write_text(email_html, encoding="utf-8")
        print(f"  ✓ newsletter_email.html built ({len(email_html):,} bytes)")
    except Exception as e:
        print(f"  ✗ Email build failed: {e}")
        print("    newsletter_email.html not updated — box score will reuse the previous build.")

    # Write cost_summary.json so email_newsletter.py can prepend a daily price
    # breakdown above the issue body. Keep the schema minimal — the email
    # script renders directly from this list.
    try:
        from datetime import date
        total = sum(p["cost"] for p in PASS_COSTS)
        COST_SUMMARY_PATH.write_text(
            json.dumps({
                "date":    date.today().isoformat(),
                "total":   round(total, 6),
                "passes":  PASS_COSTS,
            }, indent=2),
            encoding="utf-8",
        )
        print(f"\n  ✓ cost_summary.json written — total ~${total:.4f}")
    except Exception as e:
        print(f"  ✗ cost_summary.json write failed: {e}")


if __name__ == "__main__":
    main()
