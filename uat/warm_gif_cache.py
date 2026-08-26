"""
SLAP UAT — pre-resolve every verified library GIF into the URL cache.

Run this once after a review round (or any time the library gains verified
entries). Afterwards a normal UAT run resolves its GIFs from disk and makes no
Giphy get-by-ID calls at all, so a rate-limited or unreachable Giphy can no
longer blank the issue's library GIFs.

    python uat/warm_gif_cache.py            # only entries missing/stale
    python uat/warm_gif_cache.py --all      # re-resolve everything

Safe to re-run: already-fresh entries are skipped, so it costs nothing.
Stops early on repeated failures rather than burning the remaining quota.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

UAT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(UAT_DIR))

from dotenv import load_dotenv                                # noqa: E402

import gif_library_select as GL                               # noqa: E402
import gif_url_cache as GC                                    # noqa: E402

CONSECUTIVE_FAILURE_LIMIT = 5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="re-resolve every entry, ignoring cache freshness")
    args = ap.parse_args()

    load_dotenv(UAT_DIR.parent / ".env")
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        raise SystemExit("GIPHY_API_KEY not found in .env")

    library = GL.load_library()
    verified = [g for cat in library.get("categories", {}).values()
                for g in cat.get("gifs", [])
                if g.get("status") == "verified"]

    cache = {} if args.all else GC.load_cache()
    print(f"{len(verified)} verified entries; cache holds {len(GC.load_cache())}")

    counts: dict = {}
    consecutive_failures = 0
    for i, entry in enumerate(verified, 1):
        url, source = GC.resolve(entry["id"],
                                 lambda g: GL.fetch_gif_url(g, api_key), cache)
        counts[source] = counts.get(source, 0) + 1

        if source == "api":
            time.sleep(0.05)          # be polite only when we actually called out
        if source == "miss":
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                print(f"  stopping at {i}/{len(verified)} — "
                      f"{CONSECUTIVE_FAILURE_LIMIT} consecutive failures "
                      f"(likely rate-limited). Progress so far is saved.")
                break
        else:
            consecutive_failures = 0

    GC.save_cache(cache)
    print(f"cache now holds {len(cache)} URL(s)")
    print(f"source breakdown: {GC.summarize(counts)}")
    if counts.get("miss"):
        print(f"⚠ {counts['miss']} entry/entries could not be resolved — "
              f"re-run once the quota resets to fill the gaps")


if __name__ == "__main__":
    main()
