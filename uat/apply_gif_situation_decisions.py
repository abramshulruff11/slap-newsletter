"""
SLAP — add reviewed situation GIFs to the library (SLA-141).

Two steps, around the tagger:

  1. add     gif_situation_decisions.json (exported from
             uat/gif_situation_review.html) -> every ACCEPTED id becomes a
             verified entry, filed under its situation's mood category.
  2. --refile  after uat/gif_tagger.py + uat/apply_gif_tags.py: a GIF whose
             situation belongs to a mood the library has no category for
             (relief, anticipation; docs/gif_taxonomy.md) moves to the first
             of its own tagged moods that IS a category.

WHY NO NEW CATEGORIES
    Pass 2's category menu is built from the library's categories
    (gif_library_select.category_prompt_block) in production too. Adding
    "relief" and "anticipation" would change production's prompt, and SLA-141
    is UAT only. Situation-aware selection searches every category for a
    situation (SLA-138), so where a situation GIF is filed only matters to the
    mood-only fallback.

Rejected ids are never added; an id already in the library (any status) is
left as it is. Formatting goes through library_json.dumps_matching_style().
Running either step twice changes nothing the second time.

Usage:
    python -X utf8 uat/apply_gif_situation_decisions.py --decisions PATH --dry-run
    python -X utf8 uat/apply_gif_situation_decisions.py --decisions PATH --exclude ID1,ID2
    python -X utf8 uat/apply_gif_situation_decisions.py --refile
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from library_json import dumps_matching_style  # noqa: E402

LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
TAXONOMY_PATH = REPO_ROOT / "prompts" / "gif_taxonomy.DRAFT.json"
DECISIONS_PATH = Path(__file__).resolve().parent / "gif_situation_decisions.json"
CANDIDATES_PATH = Path(__file__).resolve().parent / "output" / "gif_situation_candidates.json"

NOTE_PREFIX = "SLA-141:"
# Where a GIF waits until --refile, when its situation's mood is not a
# category. The tagger is shown this as "our current mood category".
HOLDING = {"relief": "celebratory", "anticipation": "escalation"}


def situation_moods(taxonomy: dict) -> dict[str, str]:
    """situation id -> its mood, with the taxonomy's ' (NEW)' marker dropped."""
    return {s["id"]: s["mood"].split(" (")[0] for s in taxonomy["subcategories"]}


def home_category(situation: str, taxonomy: dict, library: dict) -> str:
    mood = situation_moods(taxonomy)[situation]
    if mood in library["categories"]:
        return mood
    return HOLDING[mood]


def add_accepted(library: dict, decisions: list[dict], taxonomy: dict,
                 queries: dict[str, str], exclude: set[str], date: str) -> dict:
    report = {"added": [], "already": [], "excluded": [], "rejected": 0}
    existing = {g["id"] for c in library["categories"].values() for g in c.get("gifs", [])}
    for d in decisions:
        if d.get("decision") != "accept":
            report["rejected"] += 1
            continue
        gid, situation = d["id"], d["situation"]
        if gid in exclude:
            report["excluded"].append(gid)
            continue
        if gid in existing:
            report["already"].append(gid)
            continue
        cat = home_category(situation, taxonomy, library)
        q = queries.get(gid, "")
        library["categories"][cat]["gifs"].append({
            "id": gid,
            "label": d.get("title") or f"(untitled, {situation})",
            "status": "verified",
            "note": (f"{NOTE_PREFIX} sourced for {situation}"
                     + (f" (Giphy search “{q}”)" if q else "")
                     + f"; accepted by Abram {date}."),
            "tags": [situation.replace("_", "-")],
        })
        existing.add(gid)
        report["added"].append((gid, cat, situation))
    return report


def refile(library: dict, taxonomy: dict) -> list[tuple[str, str, str]]:
    """Move SLA-141 GIFs out of a holding category into their first tagged
    mood that is a real category. Untagged or rejected GIFs stay put."""
    moods = situation_moods(taxonomy)
    moves = []
    cats = library["categories"]
    for cat_name in list(cats):
        keep = []
        for g in cats[cat_name]["gifs"]:
            target = None
            sit = (g.get("tags") or [""])[0].replace("-", "_")
            if (str(g.get("note", "")).startswith(NOTE_PREFIX) and g.get("status") == "verified"
                    and moods.get(sit) in HOLDING and g.get("moods")):
                target = next((m for m in g["moods"] if m in cats), None)
            if target and target != cat_name:
                cats[target]["gifs"].append(g)
                moves.append((g["id"], cat_name, target))
            else:
                keep.append(g)
        cats[cat_name]["gifs"] = keep
    return moves


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--decisions", type=Path, default=DECISIONS_PATH)
    ap.add_argument("--exclude", default="", help="comma-separated accepted ids to leave out")
    ap.add_argument("--date", default="2026-10-19", help="review date for the entry note")
    ap.add_argument("--refile", action="store_true", help="step 2: move holding-category GIFs by their tags")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raw = LIBRARY_PATH.read_text(encoding="utf-8")
    library = json.loads(raw)
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))

    if args.refile:
        moves = refile(library, taxonomy)
        for gid, was, now in moves:
            print(f"  {gid:22s} {was:14s} -> {now}")
        print(f"{len(moves)} GIF(s) refiled.")
    else:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))["decisions"]
        queries = {}
        if CANDIDATES_PATH.exists():
            queries = {c["id"]: c["query"] for c in
                       json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))["candidates"]}
        exclude = {x.strip() for x in args.exclude.split(",") if x.strip()}
        report = add_accepted(library, decisions, taxonomy, queries, exclude, args.date)
        per = {}
        for _, cat, sit in report["added"]:
            per[(sit, cat)] = per.get((sit, cat), 0) + 1
        for (sit, cat), n in sorted(per.items()):
            print(f"  {sit:28s} -> {cat:20s} {n}")
        print(f"Added {len(report['added'])}; already in the library {len(report['already'])}; "
              f"excluded {len(report['excluded'])}; rejected in review {report['rejected']}.")

    if args.dry_run:
        print("--dry-run: library not written.")
        return 0
    LIBRARY_PATH.write_text(dumps_matching_style(library, raw), encoding="utf-8")
    print(f"Wrote {LIBRARY_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
