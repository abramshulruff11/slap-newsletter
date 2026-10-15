"""
SLAP — GIF vision tagger (SLA-133).

Tags each verified GIF in prompts/gif_library.DRAFT.json with the moods it fits
(multi-select, all 22) and the SLA-132 situations (prompts/gif_taxonomy.DRAFT.json) by LOOKING at it: frames sampled
across the animation are stitched into one strip and sent to Claude with the
GIF's Giphy metadata. Writes proposals to uat/output/gif_tags.json. It never
touches the library; merging waits for the accuracy check (review_gif_tags.py).

WHY A FRAME STRIP, NOT THE GIF
    The API reads an animated GIF as its FIRST frame only, and a reaction GIF's
    payoff is usually in the middle or at the end. FRAME_COUNT frames, spread
    evenly, go side by side in one image.

WHAT IS NEVER KEPT
    The GIF and its frames live in memory for one request and are dropped. The
    only thing written is the ID plus our tags, so the library stays a list of
    links (the accepted Giphy-terms position, CLAUDE.md Known Issues).

ENTITIES WITHOUT FACE RECOGNITION
    Players, teams and figures come only from Giphy metadata (channel, title,
    slug) and what is legible in the frames (jersey numbers, logos, captions).
    The prompt says so and the schema makes the model cite its evidence.

NETWORK
    Needs api.giphy.com, media.giphy.com and the Anthropic API, so it runs on
    Abram's machine (or a CI job), not in a cloud session. Everything below
    the network calls is pure and covered offline by
    uat/tests/test_gif_tagger.py.

Usage:
    python -X utf8 uat/gif_tagger.py --limit 5        # try a handful first
    python -X utf8 uat/gif_tagger.py                  # every untagged verified GIF
    python -X utf8 uat/gif_tagger.py --ids ID1,ID2 --retag
    python -X utf8 uat/gif_tagger.py --ids ID1,ID2 --retag --model claude-opus-5-5
    python -X utf8 uat/gif_tagger.py --effort low     # default medium
    python -X utf8 uat/gif_tagger.py --dry-run        # print the prompt, no calls
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageSequence

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
TAXONOMY_PATH = REPO_ROOT / "prompts" / "gif_taxonomy.DRAFT.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "gif_tags.json"
ENV_PATH = REPO_ROOT / ".env"

# Sonnet 5.5, decided 2026-10-15: the same model does the later bulk tagging
# (SLA-134), so the accuracy check has to measure it. --model claude-opus-5-5
# retags failures with Opus to tell model quality from taxonomy quality.
MODEL = "claude-sonnet-5-5"
# Set explicitly: Sonnet 5.5 defaults to high. Try --effort low if medium passes.
EFFORT = "medium"
MAX_TOKENS = 16000
# A refused request reruns on a fallback model; tagging a sports GIF should
# never be refused, but a refusal must not end the batch. Sonnet 5.5 accepts
# only the "default" form, and only on the Claude API.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
SDK_MAX_RETRIES = 4
REQUEST_TIMEOUT = 180.0
# $ per million tokens (input, output), from the claude-api skill's table
# (2026-10-06). Used only to print an estimate; re-check before a large run.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PRICE_IN, PRICE_OUT = PRICES[MODEL]

FRAME_COUNT = 6
FRAME_HEIGHT = 180          # px per frame in the strip; ~1.1K input tokens total
GIPHY_BY_ID_URL = "https://api.giphy.com/v1/gifs/{gif_id}?api_key={key}"
# Small rendition: enough to read a jersey number, small to download.
RENDITION_PREFERENCE = ("fixed_height", "downsized", "downsized_medium", "original")

NONE_ID = "none"
INTENSITIES = ["low", "medium", "high"]
READINESS = ["ready", "needs_context", "not_a_reaction"]
REJECT_REASONS = ["walkout_or_intro", "interview_or_talking", "text_overlay_dominates",
                  "low_quality", "off_topic", "not_reaction_usable", "other"]
CONFIDENCE = ["high", "medium", "low"]


# ---------------------------------------------------------------------------
# Pure pieces (tested offline)
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verified_entries(library: dict) -> list[dict]:
    """Every verified GIF, with its current category attached."""
    out = []
    for cat, block in library.get("categories", {}).items():
        for g in block.get("gifs", []):
            if g.get("status") == "verified":
                out.append({**g, "category": cat})
    return out


def subcategory_ids(taxonomy: dict) -> list[str]:
    return [s["id"] for s in taxonomy["subcategories"]]


def mood_ids(library: dict, taxonomy: dict) -> list[str]:
    """
    The library's moods plus any the taxonomy proposes ("relief (NEW)" -> "relief").
    Situations only cover what Pass 1 asked for; moods like denial_copium or
    financial_pain get no Pass 1 demand but the writer uses them directly, so
    every GIF is also tagged with the moods it fits.
    """
    moods = list(library.get("categories", {}))
    for s in taxonomy["subcategories"]:
        m = s["mood"].split(" (")[0]
        if m not in moods:
            moods.append(m)
    return moods


# Moods SLA-132 proposed that the library does not have yet (docs/gif_taxonomy.md).
NEW_MOODS = {
    "relief": "The exhale - a scare that turned out fine, a drought finally over, tears after a pressure win.",
    "anticipation": "Nervous excitement before a big game or fight; scoreboard-watching in a tight race.",
}


def mood_block(library: dict, taxonomy: dict) -> str:
    cats = library.get("categories", {})
    lines = []
    for m in mood_ids(library, taxonomy):
        desc = cats.get(m, {}).get("description") or NEW_MOODS.get(m, "")
        lines.append(f"- {m}: {desc}")
    return "\n".join(lines)


def taxonomy_block(taxonomy: dict) -> str:
    """The subcategory list as the model sees it, grouped by mood."""
    by_mood: dict[str, list[dict]] = {}
    for s in taxonomy["subcategories"]:
        by_mood.setdefault(s["mood"], []).append(s)
    lines = []
    for mood in sorted(by_mood):
        lines.append(f"## {mood}")
        for s in sorted(by_mood[mood], key=lambda x: x["id"]):
            lines.append(f"- {s['id']}: {s['description']} Use when: {s['use_when']}")
    return "\n".join(lines)


def output_schema(ids: list[str], moods: list[str]) -> dict:
    """Structured-output schema. Subcategory ids are an enum so a typo is impossible."""
    sub_item = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "enum": ids + [NONE_ID]},
            "fit": {"type": "string", "enum": ["strong", "partial"]},
        },
        "required": ["id", "fit"],
        "additionalProperties": False,
    }
    str_list = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "what_happens": {"type": "string"},
            "usable": {"type": "boolean"},
            "moods": {"type": "array", "items": {"type": "string", "enum": moods}},
            "reject_reason": {"type": "string", "enum": REJECT_REASONS + [NONE_ID]},
            "subcategories": {"type": "array", "items": sub_item},
            "intensity": {"type": "string", "enum": INTENSITIES},
            "reaction_readiness": {"type": "string", "enum": READINESS},
            "caption_text": {"type": "string"},
            "entities": {
                "type": "object",
                "properties": {
                    "players": str_list, "teams": str_list, "figures": str_list,
                    "league": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["players", "teams", "figures", "league", "evidence"],
                "additionalProperties": False,
            },
            "confidence": {"type": "string", "enum": CONFIDENCE},
        },
        "required": ["what_happens", "usable", "moods", "reject_reason", "subcategories",
                     "intensity", "reaction_readiness", "caption_text", "entities",
                     "confidence"],
        "additionalProperties": False,
    }


SYSTEM_TEMPLATE = """\
You tag reaction GIFs for SLAP, a daily sports newsletter written in a group-chat \
voice. The writer asks for a GIF by SITUATION ("smug vindication after being \
doubted", "season slipping away"). Your tags decide which GIFs it can get, so a \
wrong tag puts the wrong joke in front of readers.

The image is a strip of {frames} frames sampled left to right across the GIF's \
animation. Judge the whole arc; the payoff is often in the later frames.

Moods (pick every mood the GIF fits, one to three):

{moods}

Situations (pick the ones this GIF genuinely sells, at most three; "strong" \
means a reader would get the joke instantly, "partial" means it works with the \
right sentence above it; if none fit, return exactly one entry with id "none"):

{taxonomy}

Rules:
- Tag what the GIF shows, not what its title claims.
- Entities: name a player, team or public figure ONLY when the Giphy metadata \
(channel, title, slug) or something legible in the frames (a jersey number, a \
logo, on-screen text) supports it, and say which in "evidence". Never identify \
anyone from their face or build. When unsure, leave the list empty.
- "league": nba, wnba, nfl, mlb, nhl, ncaafb, ncaab, soccer, ufc, boxing, \
tennis, golf, other_sport, or none (not sports footage).
- usable=false with a reject_reason when the GIF can't work as a reaction: a \
walkout or intro, someone just talking, text covering the action, too blurry, \
or nothing reaction-worthy happening. Otherwise reject_reason is "none".
- caption_text: any words burned into the frames, verbatim, or "".
- what_happens: one plain sentence describing the action across the frames.
- Situations are narrower than moods and cover only what the writer asks for \
most; many good GIFs fit a mood and no situation. That is fine: tag the moods \
and return "none" for situations rather than stretching one.
- confidence: how sure you are of the mood and situation tags.
"""


def system_prompt(library: dict, taxonomy: dict) -> str:
    return SYSTEM_TEMPLATE.format(frames=FRAME_COUNT, moods=mood_block(library, taxonomy),
                                  taxonomy=taxonomy_block(taxonomy))


def metadata_text(entry: dict, giphy: dict | None) -> str:
    g = giphy or {}
    user = g.get("user") or {}
    uploader = g.get("username", "") or user.get("username", "")
    if user.get("display_name"):
        uploader = f"{uploader} ({user['display_name']})".strip()
    lines = [
        f"Our label: {entry.get('label', '')}",
        f"Our current mood category: {entry.get('category', '')}",
        f"Giphy title: {g.get('title', '')}",
        f"Giphy slug: {g.get('slug', '')}",
        f"Giphy channel/uploader: {uploader}",
        f"Source page: {g.get('source_tld', '')}",
    ]
    return "\n".join(lines)


def pick_rendition(giphy: dict) -> str | None:
    images = giphy.get("images") or {}
    for key in RENDITION_PREFERENCE:
        url = (images.get(key) or {}).get("url")
        if url:
            return url
    return None


def frame_strip(gif_bytes: bytes, count: int = FRAME_COUNT,
                height: int = FRAME_HEIGHT) -> tuple[bytes, int]:
    """
    Sample `count` frames evenly across the animation, scale each to `height`,
    and lay them side by side. Returns (PNG bytes, frames actually used).
    A still image yields a one-frame strip rather than six copies of itself.
    """
    with Image.open(io.BytesIO(gif_bytes)) as im:
        frames = [f.convert("RGB") for f in ImageSequence.Iterator(im)]
    total = len(frames)
    if total == 0:
        raise ValueError("no frames")
    n = min(count, total)
    # Evenly spaced, always including the first and last frame.
    idx = [0] if n == 1 else [round(i * (total - 1) / (n - 1)) for i in range(n)]
    picked = []
    for i in idx:
        f = frames[i]
        w = max(1, round(f.width * height / f.height))
        picked.append(f.resize((w, height)))
    gap = 4
    strip = Image.new("RGB", (sum(p.width for p in picked) + gap * (n - 1), height), "white")
    x = 0
    for p in picked:
        strip.paste(p, (x, 0))
        x += p.width + gap
    out = io.BytesIO()
    strip.save(out, format="PNG", optimize=True)
    return out.getvalue(), n


def build_request(entry: dict, giphy: dict | None, strip_png: bytes,
                  library: dict, taxonomy: dict,
                  model: str = MODEL, effort: str = EFFORT) -> dict:
    """Keyword arguments for client.beta.messages.create()."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": output_schema(subcategory_ids(taxonomy),
                                                                mood_ids(library, taxonomy))},
        },
        # Identical for every GIF in a run, so it caches after the first call.
        "system": [{"type": "text", "text": system_prompt(library, taxonomy),
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(strip_png).decode("ascii")}},
                {"type": "text", "text": metadata_text(entry, giphy)},
            ],
        }],
    }


def parse_response(response, ids: list[str], moods: list[str]) -> dict:
    """
    Pull the JSON object out of a response and re-check it. Structured outputs
    guarantee the shape; this guards the cases they don't cover: a refusal, a
    truncated reply, and a "none" mixed in with real subcategories.
    """
    if response.stop_reason == "refusal":
        raise ValueError("refused")
    if response.stop_reason == "max_tokens":
        raise ValueError("truncated (max_tokens)")
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    tags = json.loads(text)
    subs = []
    seen = set()
    for s in tags.get("subcategories", []):
        sid = s.get("id")
        if sid in seen or (sid != NONE_ID and sid not in ids):
            continue
        seen.add(sid)
        subs.append({"id": sid, "fit": s.get("fit", "partial")})
    tags["subcategories"] = [s for s in subs if s["id"] != NONE_ID][:3]
    tags["moods"] = [m for m in dict.fromkeys(tags.get("moods", [])) if m in moods][:3]
    if not tags.get("usable", True):
        tags["subcategories"] = []
        tags["moods"] = []
    return tags


def cost(usage, model: str = MODEL) -> float:
    # A fallback model we have no price for is estimated at the requested one's.
    price_in, price_out = PRICES.get(model, (PRICE_IN, PRICE_OUT))
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    fresh = getattr(usage, "input_tokens", 0) or 0
    return ((fresh + 1.25 * write + 0.1 * read) * price_in
            + (getattr(usage, "output_tokens", 0) or 0) * price_out) / 1_000_000


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def env_value(name: str) -> str | None:
    if os.getenv(name):
        return os.getenv(name)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return None


def fetch_giphy(gif_id: str, key: str) -> dict | None:
    url = GIPHY_BY_ID_URL.format(gif_id=urllib.parse.quote(gif_id), key=key)
    req = urllib.request.Request(url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("data")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def make_client():
    import anthropic  # deferred: --dry-run and the pure functions need no SDK
    return anthropic.Anthropic(api_key=env_value("ANTHROPIC_API_KEY"),
                               max_retries=SDK_MAX_RETRIES, timeout=REQUEST_TIMEOUT)


def save_output(data: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUTPUT_PATH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=0, help="tag at most N GIFs")
    ap.add_argument("--ids", default="", help="comma-separated Giphy ids to tag")
    ap.add_argument("--retag", action="store_true", help="re-tag ids already in the output")
    ap.add_argument("--dry-run", action="store_true", help="print the system prompt and plan; no network")
    ap.add_argument("--model", default=MODEL, choices=sorted(PRICES), help=f"default {MODEL}")
    ap.add_argument("--effort", default=EFFORT, choices=("low", "medium", "high", "xhigh", "max"),
                    help=f"default {EFFORT}")
    args = ap.parse_args(argv)
    model, effort = args.model, args.effort

    library = load_json(LIBRARY_PATH)
    taxonomy = load_json(TAXONOMY_PATH)
    ids = subcategory_ids(taxonomy)
    moods = mood_ids(library, taxonomy)
    existing = load_json(OUTPUT_PATH) if OUTPUT_PATH.exists() else {"_meta": {}, "tags": {}}

    entries = verified_entries(library)
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        entries = [e for e in entries if e["id"] in wanted]
    if not args.retag:
        entries = [e for e in entries if e["id"] not in existing["tags"]]
    if args.limit:
        entries = entries[: args.limit]

    if args.dry_run:
        print(system_prompt(library, taxonomy))
        print(f"\nWould tag {len(entries)} GIF(s) with {model} (effort {effort}).")
        return 0
    if not entries:
        print("Nothing to tag (use --retag to redo).")
        return 0

    giphy_key = env_value("GIPHY_API_KEY")
    if not giphy_key:
        raise SystemExit("GIPHY_API_KEY not set (environment or .env)")
    client = make_client()

    existing["_meta"] = {
        "model": model, "effort": effort, "frames": FRAME_COUNT,
        "taxonomy": TAXONOMY_PATH.name,
        "note": "Proposals only. Not read by the pipeline. Merge after review_gif_tags.py --score passes.",
    }
    spent = 0.0
    failed = []
    rate_limited = False
    for n, entry in enumerate(entries, 1):
        gid = entry["id"]
        print(f"[{n}/{len(entries)}] {gid}  {entry.get('label', '')[:60]}")
        try:
            try:
                giphy = fetch_giphy(gid, giphy_key)
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                # Giphy's per-key cap (the 2026-08-26 exhaustion). Every further
                # call would 429 too and may extend the lockout, so stop here.
                failed.extend(x["id"] for x in entries[n - 1:])
                rate_limited = True
                print("    !! Giphy rate limit (429): stopping. Wait for the window to reset, then re-run.")
                break
            rendition = pick_rendition(giphy or {})
            if not rendition:
                raise ValueError("no rendition URL from Giphy")
            strip, used = frame_strip(fetch_bytes(rendition))
            response = client.beta.messages.create(**build_request(entry, giphy, strip, library, taxonomy,
                                                                   model=model, effort=effort))
            del strip  # the frames go no further than this request
            tags = parse_response(response, ids, moods)
        except Exception as e:  # noqa: BLE001 — one bad GIF must not end the batch
            print(f"    !! {type(e).__name__}: {e}")
            failed.append(gid)
            continue
        served_by = getattr(response, "model", None) or model
        this_cost = cost(response.usage, served_by)
        spent += this_cost
        existing["tags"][gid] = {
            **tags,
            "label": entry.get("label", ""),
            "category": entry["category"],
            "frames_used": used,
            # Per GIF, since an --model retag of failures mixes models in one file.
            "effort": effort,
            "served_by": served_by,
            "tagged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_output(existing)  # after every GIF, so a crash loses at most one
        subs = ", ".join(f"{s['id']}({s['fit'][0]})" for s in tags["subcategories"]) or "-"
        print(f"    {tags['confidence']:<6} {'/'.join(tags['moods']) or 'REJECT'} | {subs}"
              f"  (${this_cost:.4f}, {served_by})")

    print(f"\nTagged {len(entries) - len(failed)}/{len(entries)}; est. cost ${spent:.2f}. "
          f"Output: {OUTPUT_PATH}")
    if failed:
        print("Failed (re-run to retry): " + ",".join(failed))
    return 1 if rate_limited or (failed and len(failed) == len(entries)) else 0


if __name__ == "__main__":
    sys.exit(main())
