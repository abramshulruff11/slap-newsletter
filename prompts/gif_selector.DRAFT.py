"""
DRAFT SKETCH — gif_selector.py
Not wired into generate_newsletter.py yet. Mirrors existing code style/patterns
in that file (fetch_giphy_candidates, cost_summary, etc.) so it can be dropped
in with minimal friction once the library is populated and reviewed.

Pipeline position (proposed):
  Pass 1 already seeds `gif_concept` per story.
  This module runs AFTER Pass 2 (writer), same place embed_gifs_in_html()
  runs today — but instead of a placeholder SEARCH TERM, it reads the
  gif_concept and:
    1. Finds category + cooldown-eligible candidates from gif_library.json
    2. Asks Sonnet to pick the best match (or NO_MATCH)
    3. If NO_MATCH -> falls through to the EXISTING fetch_giphy_candidates()
       live-search path unchanged. This module never replaces the fallback,
       only sits in front of it.

Open items before this is real (not sketched here, needs your call):
  - Writer prompt change: gif_concept needs to become a genuine free-text
    "moment description," not a slug. Check current pass1_story_selector.txt
    wording for gif_concept — may already be close, may need a tweak.
  - gif_history.json currently keys on "url" + "search_term", not "id".
    Cooldown-by-id needs a new field going forward (id_history or reuse of
    "search_term" as the id) — small migration, not a rewrite.
  - This sketch calls Sonnet even when the category has only 1-2 eligible
    entries, which is wasteful once thin categories exist. Fine for now
    since library is small; revisit once categories are fleshed out.
  - Category routing isn't sketched here: something has to decide WHICH
    category a gif_concept belongs to before get_eligible_entries() can run.
    Simplest option is folding that into the same Sonnet call (send all
    category names + use_when/do_not_use_when, let it pick category AND
    entry in one shot) rather than a separate routing step. Worth deciding
    before wiring this in.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote

NO_MATCH = "NO_MATCH"


# ---------------------------------------------------------------------------
# 1. ID -> URL resolution (Giphy get-by-ID API)
#    Mirrors fetch_giphy_candidates()'s response parsing exactly, so the
#    same images.downsized_medium / downsized / original preference order
#    is used whether a GIF came from search or from the library.
# ---------------------------------------------------------------------------

def resolve_gif_by_id(gif_id: str, api_key: str) -> str | None:
    """
    Resolve a stored library ID to a live, embeddable URL via Giphy's
    get-by-ID endpoint. Returns None on any failure (network, bad id,
    Giphy takedown of that asset, etc.) so the caller can fall back to
    live search rather than crash the run.

    PROBED 2026-08-23: confirmed the naive i.giphy.com/media/<id>/giphy.gif
    URL does NOT work — Giphy requires a context-dependent cid blob in the
    real media URL. The get-by-ID API sidesteps that entirely by returning
    the same fully-formed `images` object search already returns.
    """
    try:
        api_url = f"https://api.giphy.com/v1/gifs/{quote(gif_id)}?api_key={api_key}"
        req = Request(api_url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            images = data.get("data", {}).get("images", {})
            for size_key in ("downsized_medium", "downsized", "original"):
                if size_key in images and images[size_key].get("url"):
                    return images[size_key]["url"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 2. Library loading + cooldown filtering
# ---------------------------------------------------------------------------

def load_gif_library(prompts_dir: Path) -> dict:
    path = prompts_dir / "gif_library.json"  # NOTE: drop the .DRAFT suffix on promotion
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_eligible_entries(library: dict, category: str, id_history: list[dict],
                          allow_candidates: bool = False) -> list[dict]:
    """
    Return verified (or candidate, if explicitly allowed) entries in a
    category that are not currently in cooldown.

    id_history: list of {"date": iso-str, "id": gif-id} — see migration
    note at top of file re: gif_history.json not currently storing this.
    """
    cat = library.get("categories", {}).get(category)
    if not cat:
        return []

    today = date.today()
    cooldown_default = library.get("_meta", {}).get("cooldown_days_default", 7)

    # Build a quick lookup: id -> most recent use date
    last_used = {}
    for entry in id_history:
        try:
            d = date.fromisoformat(entry["date"])
        except Exception:
            continue
        gid = entry.get("id")
        if gid and (gid not in last_used or d > last_used[gid]):
            last_used[gid] = d

    eligible = []
    for gif in cat.get("gifs", []):
        status = gif.get("status")
        if status == "retired":
            continue
        if status == "candidate" and not allow_candidates:
            continue
        if status not in ("verified", "candidate"):
            continue

        cooldown = gif.get("cooldown_days", cooldown_default)
        last = last_used.get(gif["id"])
        if last and (today - last).days < cooldown:
            continue  # still cooling down

        eligible.append(gif)

    return eligible


# ---------------------------------------------------------------------------
# 3. LLM selector — Sonnet matches concept -> library entry
# ---------------------------------------------------------------------------

SELECTOR_SYSTEM_PROMPT = """You are matching a newsletter moment to the single best GIF from a small curated list. You are NOT searching or inventing — you may only return an id that appears in the candidate list below, or NO_MATCH if nothing genuinely fits.

Bar for a match: the GIF's tagged meaning and use_when must fit the specific moment, not just the general emotion. A technically-adjacent GIF that misses the specific situation (see each category's do_not_use_when) should get NO_MATCH, not a forced pick. A forced bad match is worse than falling through to live search."""


def select_gif(concept: str, category: str, client, model: str,
                library: dict, id_history: list[dict],
                allow_candidates: bool = False) -> dict:
    """
    Returns {"id": str, "source": "library"} on a match,
    or {"id": None, "source": "fallback"} on NO_MATCH / no eligible candidates.

    Caller is responsible for: on fallback, running the EXISTING
    fetch_giphy_candidates() search path unchanged, and for logging
    the eventual choice back into cooldown history regardless of source.
    """
    eligible = get_eligible_entries(library, category, id_history, allow_candidates)

    if not eligible:
        # Every entry in category is either absent, retired, or cooling down.
        # This is expected and fine early on -- thin categories will hit
        # this often until populated further.
        return {"id": None, "source": "fallback", "reason": "no_eligible_entries"}

    cat_meta = library.get("categories", {}).get(category, {})
    candidate_block = "\n".join(
        f"- id: {g['id']} | label: {g['label']} | tags: {', '.join(g.get('tags', []))}"
        + (f" | note: {g['note']}" if g.get("note") else "")
        for g in eligible
    )

    user_message = (
        f"MOMENT: {concept}\n\n"
        f"CATEGORY: {category}\n"
        f"Use when: {cat_meta.get('use_when', '')}\n"
        f"Do NOT use when: {cat_meta.get('do_not_use_when', '')}\n\n"
        f"CANDIDATES (only these ids are valid):\n{candidate_block}\n\n"
        "Return the single best id, or NO_MATCH."
    )

    tool_def = {
        "name": "submit_gif_choice",
        "description": "Submit the selected GIF id, or NO_MATCH.",
        "input_schema": {
            "type": "object",
            "properties": {
                "choice": {
                    "type": "string",
                    "enum": [g["id"] for g in eligible] + [NO_MATCH],
                },
                "rationale": {"type": "string", "description": "One short sentence."},
            },
            "required": ["choice", "rationale"],
        },
    }

    response = client.messages.create(
        model=model,  # Sonnet — constrained matching task, no need for Opus
        max_tokens=300,
        system=SELECTOR_SYSTEM_PROMPT,
        tools=[tool_def],
        tool_choice={"type": "tool", "name": "submit_gif_choice"},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "submit_gif_choice"),
        None,
    )
    if not tool_block:
        return {"id": None, "source": "fallback", "reason": "no_tool_call"}

    choice = tool_block.input.get("choice")
    if choice == NO_MATCH or choice not in {g["id"] for g in eligible}:
        return {"id": None, "source": "fallback", "reason": tool_block.input.get("rationale", "no_match")}

    return {"id": choice, "source": "library", "rationale": tool_block.input.get("rationale", "")}
