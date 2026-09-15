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
import re
import shutil
from datetime import datetime
from pathlib import Path

# Matches a JSON array whose every element is a plain string, as json.dumps
# renders it across multiple lines. An array of objects opens with '{' and so
# never matches.
_STRING_ARRAY = re.compile(r'\[\n((?:\s*"(?:[^"\\]|\\.)*",?\n)+)\s*\]')


def dumps_preserving_style(obj) -> str:
    """json.dumps(indent=2), but keep arrays of plain strings on one line.

    The library is hand-formatted with compact tags[]. Letting json.dumps
    expand them turns a 19-status-change edit into a 2,000-line reflow that
    buries what actually changed — and the diff is the only record of a
    review pass.
    """
    text = json.dumps(obj, indent=2, ensure_ascii=False)

    def collapse(match):
        items = [line.strip() for line in match.group(1).splitlines() if line.strip()]
        return "[" + " ".join(items) + "]"

    return _STRING_ARRAY.sub(collapse, text)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
DEFAULT_DECISIONS_PATH = Path(__file__).resolve().parent / "gif_decisions.json"

# Longest first, so a shorter variant never eats part of a longer one.
EYEBALL_MARKERS = (
    "NOT yet eyeballed by Abram — needs light review before flipping to verified.",
    "NOT yet eyeballed by Abram.",
    "NOT yet eyeballed.",
)


def clear_eyeball_marker(note: str) -> str:
    """Strip the 'nobody has looked at this' sentence from a note.

    Every decision in the file means a human just looked at the clip, so the
    note must stop claiming otherwise — otherwise the entry stays in the
    review queue forever and the backlog never shrinks.
    """
    for marker in EYEBALL_MARKERS:
        note = note.replace(marker, "")
    return " ".join(note.split())


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
        note = entry.get("note")
        if note:
            cleared = clear_eyeball_marker(note)
            if cleared:
                entry["note"] = cleared
            else:
                entry.pop("note", None)
        applied.append((gif_id, entry.get("label", ""), old_status_in_file, new_status))

    print(f"\n{len(applied)} change(s) to apply:")
    for gif_id, label, old, new in applied:
        change = "confirmed as-is, note cleared" if old == new else f"{old} -> {new}"
        print(f"  {gif_id:<24} {change:<30} ({label})")

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

    LIBRARY_PATH.write_text(dumps_preserving_style(library), encoding="utf-8")
    print(f"Applied {len(applied)} change(s) to {LIBRARY_PATH}")


if __name__ == "__main__":
    main()
