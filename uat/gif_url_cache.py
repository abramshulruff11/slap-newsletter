"""
SLAP UAT — resolved GIF URL cache.

WHY THIS EXISTS
    Library entries store a bare Giphy ID; the embeddable URL must be resolved
    at render time via Giphy's get-by-ID endpoint. That made every run and every
    review-page regeneration hit the API once per entry — review_gifs.py alone
    burns one call per library entry (205 as of 2026-08-26). On 2026-08-26 that
    exhausted the daily quota and a full UAT run resolved 0/8 library GIFs: all
    eight were dropped and the issue's media mix collapsed to 18%.

    A dropped GIF is a worse failure than the staleness the library replaced, so
    resolution must not depend on the API being reachable at render time.

BEHAVIOUR
    - Fresh cache hit           -> no API call.
    - Miss or stale             -> API call, result cached.
    - API failure with ANY entry in cache (even stale) -> serve the stale URL.
      A possibly-old URL beats no GIF; the caller logs that it happened.
    - API failure with no cache -> None, caller drops the placeholder.

Giphy media URLs embed a context-dependent cid blob, so they are not guaranteed
eternal; entries are refreshed after TTL_DAYS rather than trusted forever.

Nothing here writes to a production file — the cache lives in uat/output/.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

UAT_DIR = Path(__file__).resolve().parent
CACHE_PATH = UAT_DIR / "output" / "gif_url_cache.json"

TTL_DAYS = 14


def load_cache(path: Path | None = None) -> dict:
    path = path or CACHE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: dict, path: Path | None = None) -> None:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_fresh(entry: dict) -> bool:
    try:
        stamped = datetime.strptime(entry.get("resolved_at", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return stamped >= date.today() - timedelta(days=TTL_DAYS)


def resolve(gif_id: str, fetch, cache: dict) -> tuple[str | None, str]:
    """
    Resolve one ID against the cache, falling back to `fetch(gif_id) -> url|None`.

    `cache` is mutated in place so a caller can resolve a batch and save once.
    Returns (url, source) where source is one of:
        "cache"  — fresh hit, no API call
        "api"    — fetched and cached
        "stale"  — API failed, served a cached URL past its TTL
        "miss"   — API failed and nothing cached; caller must drop it
    """
    entry = cache.get(gif_id)
    if entry and _is_fresh(entry) and entry.get("url"):
        return entry["url"], "cache"

    url = fetch(gif_id)
    if url:
        cache[gif_id] = {"url": url, "resolved_at": date.today().isoformat()}
        return url, "api"

    if entry and entry.get("url"):
        return entry["url"], "stale"
    return None, "miss"


def summarize(counts: dict) -> str:
    """One-line source breakdown for run logs."""
    parts = [f"{k}={counts[k]}" for k in ("cache", "api", "stale", "miss") if counts.get(k)]
    return ", ".join(parts) if parts else "none"
