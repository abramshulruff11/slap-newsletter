"""
SLAP UAT — curated GIF library renderer.

DESIGN (hybrid, decided 2026-08-25)
    Pass 2's writer decides, per GIF, whether the joke is a SPECIFIC named
    person/moment or a GENERIC emotional reaction, and emits one of:

        <div class="gif-placeholder" data-library-category="cockiness_smugness"></div>
        <div class="gif-placeholder">GIF: Kyrgios underhand serve</div>

    The first is served from prompts/gif_library.DRAFT.json (human-verified
    entries only). The second falls through to the existing live-Giphy-search
    path in generate_newsletter_uat.embed_gifs_in_html(), unchanged.

WHY THE WRITER PICKS A CATEGORY, NOT A GIF
    Showing an LLM all 12 entries in a category and asking it to choose gets
    you the same "best" one every issue — which is how the retired Ben Affleck
    GIF ran in 3 of 4 issues and motivated this library. Rotation belongs in
    code. The writer supplies the beat; this module picks a non-cooldown entry.
    That also means NO extra model call: routing is free.

COOLDOWN
    gif_history.json keys on url + search_term, which cannot express "this
    library id was used 3 days ago". Library uses log an extra "library_id"
    field; entries stay backward compatible with the existing search-path
    reader and with format_recent_media_block(). Per-entry cooldown_days
    overrides in the library (14 for Vince McMahon, 10 for Crying Jordan, ...)
    are honored; otherwise _meta.cooldown_days_default applies.

Nothing here writes to a production file.
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

import gif_url_cache as GC

UAT_DIR = Path(__file__).resolve().parent
REPO_ROOT = UAT_DIR.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"

GIPHY_BY_ID_URL = "https://api.giphy.com/v1/gifs/{gif_id}?api_key={key}"


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

def load_library() -> dict:
    if not LIBRARY_PATH.exists():
        return {"_meta": {}, "categories": {}}
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def category_prompt_block(library: dict | None = None) -> str:
    """
    Render the category menu injected into Pass 2's prompt.

    Deliberately omits individual entries — the writer picks a beat, not a GIF.
    Keeps the block small enough to cache.
    """
    library = library or load_library()
    lines = []
    for name, cat in library.get("categories", {}).items():
        n_verified = sum(1 for g in cat.get("gifs", []) if g.get("status") == "verified")
        if not n_verified:
            continue                      # nothing selectable — don't advertise it
        lines.append(f"- `{name}` ({n_verified} available) — {cat.get('description','')}")
        if cat.get("use_when"):
            lines.append(f"    USE WHEN: {cat['use_when']}")
        if cat.get("do_not_use_when"):
            lines.append(f"    NOT WHEN: {cat['do_not_use_when']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

def _recent_library_ids(history: list, default_days: int, per_id_days: dict) -> set:
    """ids whose most recent use is still inside their own cooldown window."""
    today = date.today()
    blocked = set()
    for entry in history:
        gid = entry.get("library_id")
        if not gid:
            continue
        try:
            used = datetime.strptime(entry.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        window = per_id_days.get(gid, default_days)
        if used >= today - timedelta(days=window):
            blocked.add(gid)
    return blocked


def eligible_entries(library: dict, category: str, history: list) -> tuple[list, list]:
    """
    Returns (eligible, all_verified) for a category.

    eligible = verified AND not inside its cooldown window.
    """
    cat = library.get("categories", {}).get(category)
    if not cat:
        return [], []
    verified = [g for g in cat.get("gifs", []) if g.get("status") == "verified"]
    default_days = library.get("_meta", {}).get("cooldown_days_default", 7)
    per_id = {g["id"]: g["cooldown_days"] for g in verified if g.get("cooldown_days")}
    blocked = _recent_library_ids(history, default_days, per_id)
    return [g for g in verified if g["id"] not in blocked], verified


def pick_entry(library: dict, category: str, history: list, used_this_run: set):
    """
    Choose one entry for a category.

    Prefers entries not in cooldown and not already used in this issue. If
    everything is in cooldown the freshest-eligible rule relaxes rather than
    dropping the GIF — a repeat beats a hole, and the report surfaces it.
    """
    eligible, verified = eligible_entries(library, category, history)
    pool = [g for g in eligible if g["id"] not in used_this_run]
    relaxed = False
    if not pool:
        pool = [g for g in verified if g["id"] not in used_this_run]
        relaxed = bool(pool)
    if not pool:
        return None, relaxed
    return random.choice(pool), relaxed


# ---------------------------------------------------------------------------
# ID -> URL resolution
# ---------------------------------------------------------------------------

def fetch_gif_url(gif_id: str, api_key: str) -> str | None:
    """
    Hit Giphy's get-by-ID endpoint, mirroring the search path's images
    preference order. Returns None on any failure.

    Prefer resolve_gif_url(), which layers the on-disk cache over this.
    """
    url = GIPHY_BY_ID_URL.format(gif_id=urllib.parse.quote(gif_id), key=api_key)
    req = urllib.request.Request(url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            images = json.loads(resp.read())["data"]["images"]
    except Exception as e:                                   # noqa: BLE001
        print(f"  [gif-lib] resolve failed for {gif_id}: {e}")
        return None
    for key in ("downsized_medium", "downsized", "original"):
        candidate = (images.get(key) or {}).get("url")
        if candidate:
            return candidate
    return None


def resolve_gif_url(gif_id: str, api_key: str, cache: dict | None = None) -> str | None:
    """Cache-backed resolution. Pass a cache dict to batch and save once."""
    local = cache if cache is not None else GC.load_cache()
    url, _source = GC.resolve(gif_id, lambda g: fetch_gif_url(g, api_key), local)
    if cache is None and url:
        GC.save_cache(local)
    return url


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_library_gifs(html: str, api_key: str, history: list) -> tuple[str, list, dict]:
    """
    Replace every data-library-category placeholder with a real <img>.

    Returns (html, new_history_entries, stats). Placeholders that cannot be
    served are removed, never left pointing at nothing.
    """
    library = load_library()
    soup = BeautifulSoup(html, "html.parser")
    placeholders = soup.find_all("div", attrs={"data-library-category": True})

    stats = {"requested": 0, "rendered": 0, "dropped": 0,
             "unknown_category": [], "relaxed_cooldown": 0,
             "sources": {"cache": 0, "api": 0, "stale": 0, "miss": 0}}
    if not placeholders:
        return html, [], stats

    url_cache = GC.load_cache()

    used_this_run: set = set()
    new_entries: list = []
    today = date.today().isoformat()

    for div in placeholders:
        stats["requested"] += 1
        category = (div.get("data-library-category") or "").strip()

        if category not in library.get("categories", {}):
            stats["unknown_category"].append(category)
            div.decompose()
            stats["dropped"] += 1
            continue

        entry, relaxed = pick_entry(library, category, history, used_this_run)
        if not entry:
            print(f"  [gif-lib] no available entry for '{category}' — dropped")
            div.decompose()
            stats["dropped"] += 1
            continue

        url, source = GC.resolve(entry["id"],
                                 lambda g: fetch_gif_url(g, api_key), url_cache)
        stats["sources"][source] = stats["sources"].get(source, 0) + 1
        if not url:
            div.decompose()
            stats["dropped"] += 1
            continue

        img = soup.new_tag("img")
        img["src"] = url
        img["alt"] = entry.get("label", category)
        img["style"] = "max-width:100%; border-radius:4px;"
        div.replace_with(img)

        used_this_run.add(entry["id"])
        stats["rendered"] += 1
        if relaxed:
            stats["relaxed_cooldown"] += 1
        # search_term keeps the entry readable by the existing history reader
        # and by format_recent_media_block()'s concept de-duplication.
        new_entries.append({
            "date": today,
            "url": url,
            "search_term": f"[library:{category}] {entry.get('label','')}",
            "library_id": entry["id"],
            "library_category": category,
        })
        print(f"  [gif-lib] {category:26s} -> {entry.get('label','')}"
              f"{'  (cooldown relaxed)' if relaxed else ''}")

    GC.save_cache(url_cache)
    return str(soup), new_entries, stats


def enforce_search_budget(html: str, cap: int) -> tuple[str, dict]:
    """
    Hard-cap Tier 3 (live-search) GIFs per issue.

    The tier prompt asks the writer to justify each search GIF, but an LLM asked
    to justify a choice will always produce a justification — that is a
    formality, not a constraint. The real limit is enforced here: keep the first
    `cap` search placeholders in document order, drop the rest.

    Dropping costs a media slot, which is deliberate — the shortfall shows up in
    the media-mix report rather than being absorbed silently as more live search.
    """
    soup = BeautifulSoup(html, "html.parser")
    search_divs = [d for d in soup.find_all("div", class_="gif-placeholder")
                   if not d.get("data-library-category")]

    reasons = [(d.get("data-tier3-reason") or "").strip() for d in search_divs]
    stats = {"requested": len(search_divs), "kept": 0, "dropped": 0,
             "reasons": reasons, "missing_reason": 0}

    for i, div in enumerate(search_divs):
        if not (div.get("data-tier3-reason") or "").strip():
            stats["missing_reason"] += 1
        if i < cap:
            stats["kept"] += 1
        else:
            div.decompose()
            stats["dropped"] += 1

    return (str(soup) if stats["dropped"] else html), stats


def count_planned_tier3(story_plan_path: Path) -> tuple[int, list]:
    """
    How many Tier 3 GIFs did Pass 1 actually ask for?

    This is the compliance baseline: if Pass 1 seeded 2 Tier 3 concepts and the
    writer produced 0 search placeholders, it silently downgraded them into
    generic library picks — the exact failure this tier field exists to stop.
    """
    if not story_plan_path.exists():
        return 0, []
    try:
        plan = json.loads(story_plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0, []

    stories = [plan.get("lead_story") or {}] + list(plan.get("supporting_stories") or [])
    seeds = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        if int(s.get("gif_tier") or 1) == 3 and (s.get("gif_concept") or "").strip():
            seeds.append((s.get("headline", "(untitled)"), s["gif_concept"]))
    return len(seeds), seeds


def report_tiers(lib_stats: dict, search_stats: dict, cap: int,
                 planned_tier3: int = 0, planned_seeds: list | None = None) -> None:
    """Print the Tier 1 / Tier 3 split so the balance is visible every run."""
    if planned_tier3:
        got = search_stats.get("requested", 0)
        verdict = "ok" if got >= planned_tier3 else "DOWNGRADED"
        print(f"  [tiers] Pass 1 asked for {planned_tier3} Tier 3 GIF(s); "
              f"writer produced {got}  {verdict}")
        if got < planned_tier3:
            for headline, concept in (planned_seeds or []):
                print(f"  [tiers]   ⚠ tier-3 seed not honored: {headline[:44]} "
                      f"-> {concept[:60]}")
    t1 = lib_stats.get("rendered", 0)
    t3 = search_stats.get("kept", 0)
    total = t1 + t3
    share = (t1 / total * 100) if total else 0.0
    print(f"  [tiers] Tier 1 library: {t1}   Tier 3 search: {t3}/{cap} cap"
          f"   ({share:.0f}% library)")
    if search_stats.get("dropped"):
        print(f"  [tiers] ⚠ {search_stats['dropped']} search GIF(s) dropped — "
              f"over the {cap}-per-issue Tier 3 budget")
    if search_stats.get("missing_reason"):
        print(f"  [tiers] ⚠ {search_stats['missing_reason']} search GIF(s) had no "
              f"data-tier3-reason — writer skipped the justification")
    for r in search_stats.get("reasons", []):
        if r:
            print(f"  [tiers]   tier3 reason: {r[:110]}")


def report(stats: dict, search_count: int | None = None) -> None:
    if not stats["requested"] and not search_count:
        return
    print(f"  [gif-lib] {stats['rendered']}/{stats['requested']} library GIF(s) rendered")
    if stats["dropped"]:
        print(f"  [gif-lib] ⚠ {stats['dropped']} dropped (unavailable or unresolvable)")
    if stats["unknown_category"]:
        bad = ", ".join(sorted(set(stats["unknown_category"])))
        print(f"  [gif-lib] ⚠ writer used unknown category name(s): {bad}")
    if stats["relaxed_cooldown"]:
        print(f"  [gif-lib] ⚠ {stats['relaxed_cooldown']} pick(s) had to reuse a "
              f"cooling-down entry — category may be too thin")
    src = stats.get("sources") or {}
    if any(src.values()):
        print(f"  [gif-lib] url source: {GC.summarize(src)}")
    if src.get("stale"):
        print(f"  [gif-lib] ⚠ {src['stale']} URL(s) served from STALE cache — "
              f"Giphy unreachable; GIFs kept rather than dropped")
