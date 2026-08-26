"""
SLAP — Apply GIF Review Decisions

Reads a gif_decisions.json file (exported from uat/gif_review.html via the
"Export Decisions" button) and applies each status change to
prompts/gif_library.DRAFT.json — flipping "candidate" entries to "verified"
or "retired" based on what you clicked in the browser.

This DOES modify prompts/gif_library.DRAFT.json. It prints a summary of
every change before writing, and makes a timestamped backup copy first.

Usage:
    python uat/apply_gif_decisions.py
    python uat/apply_gif_decisions.py --file path/to/gif_decisions.json
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
DEFAULT_DECISIONS_PATH = Path(__file__).resolve().parent / "gif_decisions.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=DEFAULT_DECISIONS_PATH,
                         help="Path to the exported gif_decisions.json (default: uat/gif_decisions.json)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"No decisions file found at {args.file}")
        print("Export one from uat/gif_review.html first (the 'Export Decisions' button "
              "downloads gif_decisions.json — move/save it into uat/ or pass --file).")
        return

    payload = json.loads(args.file.read_text(encoding="utf-8"))
    decisions = payload.get("decisions", [])
    if not decisions:
        print("Decisions file has no entries — nothing to do.")
        return

    library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    categories = library.get("categories", {})

    # Index every gif entry by id for fast lookup
    by_id = {}
    for cat_key, cat in categories.items():
        for entry in cat.get("gifs", []):
            by_id[entry["id"]] = entry

    applied = []
    skipped = []

    for d in decisions:
        gif_id = d["id"]
        new_status = d["new_status"]
        entry = by_id.get(gif_id)
        if entry is None:
            skipped.append((gif_id, "not found in library — may have moved/been removed"))
            continue
        old_status_in_file = entry.get("status")
        entry["status"] = new_status
        applied.append((gif_id, entry.get("label", ""), old_status_in_file, new_status))

    print(f"\n{len(applied)} change(s) to apply:")
    for gif_id, label, old, new in applied:
        print(f"  {gif_id:<24} {old:>10} -> {new:<10} ({label})")

    if skipped:
        print(f"\n{len(skipped)} skipped (not found):")
        for gif_id, reason in skipped:
            print(f"  {gif_id}: {reason}")

    if not applied:
        print("\nNothing to write.")
        return

    confirm = input(f"\nWrite {len(applied)} change(s) to {LIBRARY_PATH.name}? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted — no changes written.")
        return

    backup_path = LIBRARY_PATH.with_name(
        f"{LIBRARY_PATH.stem}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    shutil.copy2(LIBRARY_PATH, backup_path)
    print(f"Backup written: {backup_path.name}")

    LIBRARY_PATH.write_text(
        json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Applied {len(applied)} change(s) to {LIBRARY_PATH}")


if __name__ == "__main__":
    main()
