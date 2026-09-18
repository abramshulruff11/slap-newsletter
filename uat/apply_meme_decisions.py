"""
SLAP — Apply Meme Review Decisions

Reads a meme_decisions.json file (exported from uat/meme_review.html via the
"Export Decisions" button) and writes each status change into
prompts/meme_library.DRAFT.json. The meme counterpart to
uat/apply_gif_decisions.py.

This DOES modify prompts/meme_library.DRAFT.json. It prints every change
before writing and makes a timestamped backup first.

TWO THINGS IT DOES BEYOND SETTING status
  1. Retiring is load-bearing. meme_library.active_templates() drops retired
     templates from the Pass 1 menu, from valid_slugs() and from rotation
     swaps — so prompts/meme_selector_index.txt, which is generated from the
     library, goes stale the moment a status changes. This regenerates it in
     the same run; uat/tests/test_meme_library.py fails if it drifts.
  2. It introduces the "verified" tier the library's _meta says does not
     exist yet, and rewrites that _meta sentence the first time a template is
     verified, so the file stops describing a state it has left.

Usage:
    python -X utf8 uat/apply_meme_decisions.py
    python -X utf8 uat/apply_meme_decisions.py --file path/to/meme_decisions.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

UAT_DIR = Path(__file__).resolve().parent
REPO_ROOT = UAT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from library_json import dumps_matching_style  # noqa: E402
import meme_library  # noqa: E402

LIBRARY_PATH = REPO_ROOT / "prompts" / "meme_library.DRAFT.json"
DEFAULT_DECISIONS_PATH = UAT_DIR / "meme_decisions.json"

VALID_STATUSES = {"candidate", "verified", "retired"}

_META_STALE = ("Every entry is 'candidate' (AI-authored, awaiting review). "
               "No 'verified' tier exists yet.")
_META_FRESH = ("Reviewed via uat/review_memes.py: 'verified' means a human "
               "confirmed the box semantics, 'retired' removes the template from "
               "the Pass 1 menu, valid_slugs() and rotation swaps.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=DEFAULT_DECISIONS_PATH,
                        help="Path to the exported meme_decisions.json "
                             "(default: uat/meme_decisions.json)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"No decisions file found at {args.file}")
        print("Export one from uat/meme_review.html first (the 'Export Decisions' "
              "button downloads meme_decisions.json — save it into uat/ or pass --file).")
        return 0

    payload = json.loads(args.file.read_text(encoding="utf-8"))
    decisions = payload.get("decisions", [])
    if not decisions:
        print("Decisions file has no entries — nothing to do.")
        return 0

    raw = LIBRARY_PATH.read_text(encoding="utf-8")
    library = json.loads(raw)
    by_slug = {t["slug"]: t for t in library.get("templates", [])}

    applied, skipped = [], []
    for d in decisions:
        slug = d.get("slug") or d.get("id")
        new_status = d.get("new_status")
        if new_status not in VALID_STATUSES:
            skipped.append((slug, f"unknown status {new_status!r}"))
            continue
        entry = by_slug.get(slug)
        if entry is None:
            skipped.append((slug, "not found in library — may have been renamed/removed"))
            continue
        old = entry.get("status")
        entry["status"] = new_status
        applied.append((slug, old, new_status))

    print(f"\n{len(applied)} change(s) to apply:")
    for slug, old, new in applied:
        change = "confirmed as-is" if old == new else f"{old} -> {new}"
        print(f"  {slug:<42} {change}")

    if skipped:
        print(f"\n{len(skipped)} skipped:")
        for slug, reason in skipped:
            print(f"  {slug}: {reason}")

    if not applied:
        print("\nNothing to write.")
        return 0

    retiring = [s for s, _o, n in applied if n == "retired"]
    if retiring:
        print(f"\n{len(retiring)} template(s) will be RETIRED — removed from the Pass 1 "
              f"menu, valid_slugs() and rotation swaps:")
        for slug in retiring:
            print(f"  {slug}")
        remaining = sum(1 for t in library["templates"] if t.get("status") != "retired")
        print(f"  -> {remaining} template(s) left selectable.")

    confirm = input(f"\nWrite {len(applied)} change(s) to {LIBRARY_PATH.name}? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted — no changes written.")
        return 0

    backup = LIBRARY_PATH.with_name(
        f"{LIBRARY_PATH.stem}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(LIBRARY_PATH, backup)
    print(f"Backup written: {backup.name}")

    meta = library.get("_meta", {})
    if any(n == "verified" for _s, _o, n in applied) and meta.get("status_field") == _META_STALE:
        meta["status_field"] = _META_FRESH
        print("_meta.status_field updated — the 'verified' tier now exists.")

    LIBRARY_PATH.write_text(dumps_matching_style(library, raw), encoding="utf-8")
    print(f"Applied {len(applied)} change(s) to {LIBRARY_PATH}")

    # The index is a projection of the library; a status change restates it.
    # load_meme_library caches by path, so drop the cache before regenerating
    # or the index would be rebuilt from the pre-write copy.
    meme_library._cache.clear()
    index_path = meme_library.write_selector_index()  # snapshot only; Pass 1 builds its own
    try:
        shown = index_path.relative_to(REPO_ROOT)
    except ValueError:  # a redirected path (tests) must not fail a completed write
        shown = index_path
    print(f"Regenerated {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
