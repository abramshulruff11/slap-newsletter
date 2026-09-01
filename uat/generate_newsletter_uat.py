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
from html import escape as escape_html
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
sys.path.insert(0, str(REPO_ROOT))
import meme_library  # shared with prod; reads prompts/meme_library.DRAFT.json

# Runner body shared with production. One copy, imported by both: on
# 2026-09-01 two changes reached one runner and not the other and both
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

# Point the shared helpers at the UAT prompt FORK, not production's. This
# injection is the whole reason configure() exists: a shared load_prompt()
# that defaulted to prod's tree would silently defeat the sandbox.
runner_common.configure(prompts_dir=PROMPT_DIR)

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

# UAT beats test: Pass 1 becomes the editorial brain (story selection + beat
# skeletons), so it gets its own model constant instead of sharing
# MODEL_DEFAULT with Pass 4/6. Pass 1B (highlight selection) stays on
# MODEL_DEFAULT deliberately — it's a small selection task, not judgment-heavy.
MODEL_PASS1   = "claude-opus-4-7"     # Pass 1



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

def run_pass1(raw: dict, recent_output: list, client: anthropic.Anthropic, game_state: dict | None = None) -> str:
    print("\n── PASS 1: Story Selector ──────────────────────────")

    selector_prompt = load_prompt("pass1_story_selector.txt")
    # Compact one-line-per-template index (~2K tokens). Pass 1 selects the slug;
    # Pass 2 receives only the chosen templates' full entries.
    _meme_index = meme_library.load_selector_index()
    if _meme_index:
        selector_prompt = selector_prompt + "\n\n" + "="*80 + "\n\n" + _meme_index
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
            # Tier is a STRUCTURED decision, not left to the writer's judgment.
            # Three UAT runs showed the writer collapses every specific concept
            # into a generic library category ("close enough") when the tier is
            # only described in prose — including a seed that literally named a
            # Home Alone scene. Pass 1 already knows which shape it wrote, so it
            # records the tier here and Pass 2 is forbidden from downgrading it.
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
            # UAT meme-library test: Pass 1 now picks the TEMPLATE, not just a
            # prose concept. meme_template must be a slug from the MEME SELECTOR
            # INDEX; meme_subject names who the meme is about. Both empty when
            # no meme fits — an empty pair is a valid, common answer.
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

            # account_distribution is written by the MODEL and covers every
            # tweet including Around the League — but ATL is uncapped, so
            # trusting it flags accounts that are actually within policy.
            # On 2026-08-27 it named @AdamSchefter and @TalkinBaseball_, both
            # of which were over only because of ATL. Count the headliners
            # ourselves instead.
            head_dist: dict = {}
            for _s in [plan.get("lead_story", {})] + plan.get("supporting_stories", []):
                for _t in (_s.get("tweets") or []):
                    if isinstance(_t, dict) and _t.get("account"):
                        _a = "@" + str(_t["account"]).lstrip("@")
                        head_dist[_a] = head_dist.get(_a, 0) + 1
            over_cap = {k: v for k, v in head_dist.items() if v > 2}
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

    # The library's category menu is injected at load time rather than pasted
    # into the prompt file, so the two can never drift apart as categories are
    # added or a category's last verified entry is retired.
    if "{{GIF_LIBRARY_CATEGORIES}}" in gif_reference:
        import gif_library_select as _GL
        gif_reference = gif_reference.replace(
            "{{GIF_LIBRARY_CATEGORIES}}", _GL.category_prompt_block())

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

    # UAT meme-library test: inject the FULL spec for only the templates Pass 1
    # selected (~600 tokens each), not the whole 20K-token library. Goes in the
    # user message rather than the cached system block because the selection
    # changes every run and would thrash the cache.
    try:
        _plan_obj   = json.loads(story_plan) if isinstance(story_plan, str) else (story_plan or {})
    except Exception:
        _plan_obj   = {}
    _meme_slugs = meme_library.collect_meme_slugs(_plan_obj)
    _meme_specs = meme_library.format_meme_specs(_meme_slugs)
    if _meme_slugs:
        print(f"  [memelib] Pass 2 spec injected for: {', '.join(_meme_slugs)}")

    user_message = (
        (game_state_block + "\n\n" if game_state_block else "")
        + recent_media_block
        + (_meme_specs + "\n" if _meme_specs else "")
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

    result = _audit_account_diversity(result)
    result = _audit_redundant_tweets(result)

    return result


# The deterministic audits moved to plan_audit.py at the repo root so that
# generate_newsletter.py can share them rather than carry a second copy.
# Re-exported here because run_uat.py and the tests reach them as G.<name>,
# and because pre_edit() above calls the two flaggers directly.
from plan_audit import (                                          # noqa: E402
    HEADLINER_ACCOUNT_CAP, INSIDER_HEADLINER_CAP, INSIDER_WIRE_ACCOUNTS,
    REDUNDANCY_THRESHOLD, MIN_CONTENT_WORDS,
    MIN_GIF_SEEDS, MIN_MEME_SEEDS,
    TWEET_CEILING, ATL_MAX, MIN_TWEETS_LEAD, MIN_TWEETS_SUPPORTING,
    audit_account_diversity as _audit_account_diversity,
    audit_redundant_tweets as _audit_redundant_tweets,
    audit_media_seeds, audit_redundancy, backfill_gif_seeds,
    count_headliner_accounts, effective_cap, enforce_tweet_budget,
)

# ---------------------------------------------------------------------------
# Pass 6 — Editor
# ---------------------------------------------------------------------------



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
