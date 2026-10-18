"""
SLAP — Apply GIF vision tags to the library (SLA-137)

Reads uat/output/gif_tags.json (written by uat/gif_tagger.py) and merges each
GIF's tags into its entry in prompts/gif_library.DRAFT.json:

    moods            every mood the GIF fits (multi-select)
    subcategories    [{id, fit}] -- the SLA-132 situations, fit strong/partial
    intensity        low / medium / high
    reaction_readiness   ready / needs_context / not_a_reaction
    caption_text     text burned into the GIF ("" when none)
    what_happens     one-line description of the clip
    entities         {players, teams, figures, league, evidence}
    confidence       high / medium / low
    tagged_by        {model, effort, at}

A GIF the tagger judged unusable gets status "rejected" plus its
reject_reason. It is NOT deleted: selection only ever picks "verified", so it
stops being used, and it stays in the file to be checked (two of the first
nine look like library mislabels).

The allowed values are written to _meta.tag_vocabulary, taken from the tagger
itself, so the library studio validates against the same lists the tagger
was constrained to. uat/tests/test_gif_library_tags.py checks the two agree.

Formatting is preserved through library_json.dumps_matching_style(). Running
it twice changes nothing the second time.

Usage:
    python -X utf8 uat/apply_gif_tags.py --dry-run
    python -X utf8 uat/apply_gif_tags.py
    python -X utf8 uat/apply_gif_tags.py --tags path/to/gif_tags.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "uat"))
from library_json import dumps_matching_style  # noqa: E402
import gif_tagger as GT  # noqa: E402

LIBRARY_PATH = GT.LIBRARY_PATH
TAXONOMY_PATH = GT.TAXONOMY_PATH
TAGS_PATH = GT.OUTPUT_PATH

# Order in which the fields are written into each entry, after its own keys.
TAG_FIELDS = ["moods", "subcategories", "intensity", "reaction_readiness", "caption_text",
              "what_happens", "entities", "confidence", "tagged_by"]

REJECTED_STATE = ("The vision tagger (SLA-133) judged this GIF unusable as a reaction; "
                  "reject_reason says why. Never selected. Kept, not deleted, so it can be "
                  "checked (it may be a library mislabel) and is never re-proposed.")

TAG_FIELDS_NOTE = ("SLA-137: tags from uat/gif_tagger.py (Claude vision over a 6-frame strip + "
                   "Giphy metadata), merged by uat/apply_gif_tags.py. Accepted on Abram's "
                   "spot-check 2026-10-15; the scored 85% review was waived, so there is no "
                   "accuracy number. moods: multi-select from tag_vocabulary.moods. "
                   "subcategories: SLA-132 situations (prompts/gif_taxonomy.DRAFT.json) with fit "
                   "strong/partial; empty for a mood-only GIF. Not yet read by selection (SLA-138).")


def vocabulary(library: dict, taxonomy: dict) -> dict:
    """The allowed values, straight from the tagger's own constants."""
    return {
        "moods": GT.mood_ids(library, taxonomy),
        "subcategories": GT.subcategory_ids(taxonomy),
        "fit": ["strong", "partial"],
        "intensity": list(GT.INTENSITIES),
        "reaction_readiness": list(GT.READINESS),
        "confidence": list(GT.CONFIDENCE),
        "reject_reason": list(GT.REJECT_REASONS),
    }


def entry_fields(tag: dict) -> dict:
    """The library fields for one tagger proposal."""
    return {
        "moods": list(tag.get("moods", [])),
        "subcategories": [{"id": s["id"], "fit": s["fit"]}
                          for s in tag.get("subcategories", []) if s.get("id") != GT.NONE_ID],
        "intensity": tag["intensity"],
        "reaction_readiness": tag["reaction_readiness"],
        "caption_text": tag.get("caption_text", ""),
        "what_happens": tag.get("what_happens", ""),
        "entities": {k: tag["entities"][k]
                     for k in ("players", "teams", "figures", "league", "evidence")},
        "confidence": tag["confidence"],
        "tagged_by": {"model": tag.get("served_by", ""), "effort": tag.get("effort", ""),
                      "at": tag.get("tagged_at", "")},
    }


def apply_tags(library: dict, tags: dict, taxonomy: dict) -> dict:
    """Merge tags into library in place. Returns a report."""
    report = {"tagged": 0, "rejected": [], "missing": [], "moved": []}
    where = {g["id"]: (cat, g) for cat, block in library.get("categories", {}).items()
             for g in block.get("gifs", [])}

    meta = library.setdefault("_meta", {})
    meta.setdefault("verification_states", {})["rejected"] = REJECTED_STATE
    meta["tag_fields"] = TAG_FIELDS_NOTE
    meta["tag_vocabulary"] = vocabulary(library, taxonomy)

    for gif_id, tag in tags.items():
        if gif_id not in where:
            report["missing"].append(gif_id)
            continue
        cat, g = where[gif_id]
        if tag.get("category") and tag["category"] != cat:
            report["moved"].append((gif_id, tag["category"], cat))
        for k in TAG_FIELDS + ["reject_reason"]:
            g.pop(k, None)                       # re-applying rewrites, in a fixed order
        g.update(entry_fields(tag))
        if tag.get("usable", True):
            if g.get("status") == "rejected":    # a retag reversed the verdict
                g["status"] = "verified"
        else:
            g["status"] = "rejected"
            g["reject_reason"] = tag.get("reject_reason", "other")
            report["rejected"].append((gif_id, cat, g["reject_reason"], g.get("label", "")))
        report["tagged"] += 1
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tags", type=Path, default=TAGS_PATH)
    ap.add_argument("--dry-run", action="store_true", help="report, do not write")
    args = ap.parse_args()

    if not args.tags.exists():
        print(f"No tags file at {args.tags}. Run uat/gif_tagger.py first.")
        return 1
    raw = LIBRARY_PATH.read_text(encoding="utf-8")
    library = json.loads(raw)
    tags = GT.load_json(args.tags).get("tags", {})
    taxonomy = GT.load_json(TAXONOMY_PATH)

    report = apply_tags(library, tags, taxonomy)
    print(f"Tagged {report['tagged']} of {len(tags)} proposals.")
    print(f"Rejected ({len(report['rejected'])}) -> status 'rejected':")
    for gif_id, cat, why, label in report["rejected"]:
        print(f"  {gif_id:22s} {cat:22s} {why:24s} {label}")
    if report["missing"]:
        print(f"Not in the library (skipped): {', '.join(report['missing'])}")
    for gif_id, was, now in report["moved"]:
        print(f"  note: {gif_id} was tagged under '{was}', now lives in '{now}'")

    out = dumps_matching_style(library, raw)
    if args.dry_run:
        print("\n--dry-run: library not written.")
        return 0
    LIBRARY_PATH.write_text(out, encoding="utf-8")   # newline translation matches the file
    print(f"\nWrote {LIBRARY_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
