"""
PROBE SCRIPT — gif_library_expand_probe.py
Run this locally (python gif_library_expand_probe.py), paste the terminal
output back to Claude. This is a throwaway diagnostic, not production code —
per the probe-before-building pattern, output gets reviewed before anything
touches the real library file.

What it does:
  For each category in gif_library.DRAFT.json, hits Giphy's real search API
  using that category's tags as search terms, and prints candidate GIFs
  (id, title, giphy page URL for eyeballing) that AREN'T already in the
  library. This replaces the manual "guess a famous meme name and hope
  Google indexed the individual Giphy page" approach, which had a low hit
  rate — this calls the actual API, so every result is a real, valid,
  currently-live Giphy asset.

What it does NOT do:
  - Does not write to gif_library.DRAFT.json. Read-only against Giphy.
  - Does not judge GIF quality/fit — that's still your light-review pass.
    This just widens the pool of real candidates fast.
  - Does not dedupe against retired ids by content (only by id) — a
    re-upload of the retired Ben Affleck clip under a different id would
    slip through. Worth an eyeball, not worth automating yet.

Usage:
  python gif_library_expand_probe.py
  python gif_library_expand_probe.py --category escalation   (single category)
  python gif_library_expand_probe.py --limit 15               (default 10)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # fine if .env is already exported into the shell

API_KEY = os.getenv("GIPHY_API_KEY")
LIBRARY_PATH = Path(__file__).resolve().parent / "prompts" / "gif_library.DRAFT.json"


def load_library() -> dict:
    if not LIBRARY_PATH.exists():
        print(f"ERROR: library not found at {LIBRARY_PATH}")
        sys.exit(1)
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def search_giphy(query: str, limit: int = 10) -> list[dict]:
    """Real Giphy search — same endpoint the production pipeline already
    uses for fallback search, so results are guaranteed valid/live."""
    url = (
        f"https://api.giphy.com/v1/gifs/search"
        f"?api_key={API_KEY}&q={quote(query)}&limit={limit}&rating=pg-13"
    )
    req = Request(url, headers={"User-Agent": "SLAP-Newsletter-Probe/1.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        print(f"    ERROR searching '{query}': {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default=None, help="Only probe this one category")
    parser.add_argument("--limit", type=int, default=10, help="Results per search term")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: GIPHY_API_KEY not found in .env")
        sys.exit(1)

    library = load_library()
    categories = library.get("categories", {})

    if args.category:
        if args.category not in categories:
            print(f"ERROR: '{args.category}' not found. Options: {list(categories)}")
            sys.exit(1)
        categories = {args.category: categories[args.category]}

    # Build a flat set of every id already in the library (any status),
    # so we only print genuinely NEW candidates, not stuff you already have.
    existing_ids = set()
    for cat in library.get("categories", {}).values():
        for gif in cat.get("gifs", []):
            existing_ids.add(gif.get("id"))

    for cat_name, cat in categories.items():
        existing_count = len(cat.get("gifs", []))
        print(f"\n{'='*70}")
        print(f"CATEGORY: {cat_name}  (currently {existing_count} entries)")
        print(f"use_when: {cat.get('use_when', '')}")
        print(f"{'='*70}")

        # Build search terms from existing tags in this category — reuses
        # your own taxonomy instead of me guessing new vocabulary.
        all_tags = set()
        for gif in cat.get("gifs", []):
            all_tags.update(gif.get("tags", []))
        # Fall back to the category name itself if somehow no tags yet
        search_terms = sorted(all_tags) if all_tags else [cat_name.replace("_", " ")]

        seen_this_category = set()
        for term in search_terms[:4]:  # cap terms per category to keep this fast
            print(f"\n  -- searching: \"{term}\" --")
            results = search_giphy(term, limit=args.limit)
            new_count = 0
            for r in results:
                gid = r.get("id")
                if not gid or gid in existing_ids or gid in seen_this_category:
                    continue
                seen_this_category.add(gid)
                new_count += 1
                title = r.get("title", "(no title)")
                username = r.get("username", "") or r.get("user", {}).get("username", "")
                page_url = r.get("url", f"https://giphy.com/gifs/{gid}")
                print(f"    id: {gid}")
                print(f"      title: {title}")
                if username:
                    print(f"      uploader: {username}")
                print(f"      review at: {page_url}")
            if new_count == 0:
                print("    (no new candidates)")
            time.sleep(0.3)  # be polite to the API

    print(f"\n{'='*70}")
    print("Done. Paste this whole output back to Claude for review + tagging.")
    print("Nothing was written — this only read from Giphy's API.")


if __name__ == "__main__":
    main()
