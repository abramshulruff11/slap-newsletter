"""
Run:  python -X utf8 uat/tests/test_gif_library_tags.py

SLA-137: the vision tagger's proposals (uat/output/gif_tags.json, SLA-133) are
merged into prompts/gif_library.DRAFT.json by uat/apply_gif_tags.py. This locks:

1. The merge is honest: every proposal reached its entry, unchanged, and a
   second run changes nothing (so re-running after SLA-134's bulk tagging is
   safe). Formatting goes through library_json, so the file round-trips.
2. The library's _meta.tag_vocabulary is the TAGGER's vocabulary. The studio
   validates hand edits against it; if the two drift, the page would accept a
   tag the tagger can never produce, or refuse one it does.
3. "rejected" is never selected, by any reader: the category menu Pass 2 sees,
   pick_entry(), and the old decisions applier, which replays every verdict a
   browser ever stored and so could quietly put a rejected GIF back.

No network, no API calls.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "uat"))

import apply_gif_decisions as AD  # noqa: E402
import apply_gif_tags as AT  # noqa: E402
import gif_library_select as GL  # noqa: E402
import gif_tagger as GT  # noqa: E402
from library_json import dumps_matching_style  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


RAW = AT.LIBRARY_PATH.read_text(encoding="utf-8")
LIBRARY = json.loads(RAW)
TAXONOMY = GT.load_json(AT.TAXONOMY_PATH)
TAGS = GT.load_json(AT.TAGS_PATH)["tags"]
ENTRIES = {g["id"]: (cat, g) for cat, b in LIBRARY["categories"].items() for g in b["gifs"]}

print("\n1. The merge")
check_true("the tag proposals are in the checkout", len(TAGS) > 0)
check("every proposal has a library entry", [i for i in TAGS if i not in ENTRIES], [])
mismatch = [i for i, t in TAGS.items() if i in ENTRIES
            and any(ENTRIES[i][1].get(k) != v for k, v in AT.entry_fields(t).items())]
check("every merged entry carries its proposal's tags exactly", mismatch, [])
rejected = sorted(i for i, t in TAGS.items() if not t["usable"])
check("every GIF the tagger rejected is status 'rejected'",
      [i for i in rejected if ENTRIES[i][1]["status"] != "rejected"], [])
check("...with the tagger's reason",
      [i for i in rejected if ENTRIES[i][1].get("reject_reason") != TAGS[i]["reject_reason"]], [])
check("nothing else is rejected",
      sorted(i for i, (_, g) in ENTRIES.items() if g.get("status") == "rejected"), rejected)
check("a reject_reason appears only on a rejected GIF",
      [i for i, (_, g) in ENTRIES.items() if ("reject_reason" in g) != (g.get("status") == "rejected")], [])
check_true("no tagged entry has a 'none' situation",
           all(s["id"] != GT.NONE_ID for _, g in ENTRIES.values() for s in g.get("subcategories", [])))

again = copy.deepcopy(LIBRARY)
AT.apply_tags(again, TAGS, TAXONOMY)
check_true("re-applying the same tags changes nothing",
           dumps_matching_style(again, RAW) == dumps_matching_style(LIBRARY, RAW))
check_true("the library round-trips through library_json",
           dumps_matching_style(LIBRARY, RAW) == RAW.replace("\r\n", "\n"))

# A later retag that reverses a rejection puts the GIF back, without its reason.
lib = copy.deepcopy(LIBRARY)
gid = rejected[0]
AT.apply_tags(lib, {gid: {**TAGS[gid], "usable": True, "reject_reason": "none"}}, TAXONOMY)
g = next(x for b in lib["categories"].values() for x in b["gifs"] if x["id"] == gid)
check("a retag that accepts a rejected GIF verifies it again", (g["status"], "reject_reason" in g), ("verified", False))
# ...but never promotes a candidate or un-retires a retired one.
cand = next(i for i, (_, x) in ENTRIES.items() if x["status"] in ("candidate", "retired"))
lib = copy.deepcopy(LIBRARY)
before = ENTRIES[cand][1]["status"]
AT.apply_tags(lib, {cand: TAGS[next(iter(TAGS))]}, TAXONOMY)
g = next(x for b in lib["categories"].values() for x in b["gifs"] if x["id"] == cand)
check(f"tagging a {before} GIF leaves its status alone", g["status"], before)

print("\n2. Vocabulary")
vocab = LIBRARY["_meta"]["tag_vocabulary"]
check("library vocabulary == the tagger's", vocab, AT.vocabulary(LIBRARY, TAXONOMY))
check("situations == the taxonomy's ids", vocab["subcategories"], GT.subcategory_ids(TAXONOMY))
bad = [(i, k, v) for i, (_, g) in ENTRIES.items() if "moods" in g
       for k, v in [("intensity", g["intensity"]), ("reaction_readiness", g["reaction_readiness"]),
                    ("confidence", g["confidence"])] if v not in vocab[k]]
bad += [(i, "mood", m) for i, (_, g) in ENTRIES.items() for m in g.get("moods", []) if m not in vocab["moods"]]
bad += [(i, "situation", s["id"]) for i, (_, g) in ENTRIES.items()
        for s in g.get("subcategories", []) if s["id"] not in vocab["subcategories"] or s["fit"] not in vocab["fit"]]
check("every stored tag is in the vocabulary", bad, [])
check_true("'rejected' is a documented state", "rejected" in LIBRARY["_meta"]["verification_states"])

print("\n3. Rejected is never selected")
check("pick_entry never returns a rejected GIF",
      [g["id"] for cat in LIBRARY["categories"] for _ in range(40)
       for g in [GL.pick_entry(LIBRARY, cat, [], set())[0]] if g and g["status"] != "verified"], [])
_, verified = GL.eligible_entries(LIBRARY, "join_the_failure", [])
check_true("eligible_entries drops rejected", all(g["status"] == "verified" for g in verified))
menu = GL.category_prompt_block(LIBRARY)
n_jtf = sum(1 for g in LIBRARY["categories"]["join_the_failure"]["gifs"] if g["status"] == "verified")
check_true("the category menu counts only verified GIFs", f"`join_the_failure` ({n_jtf} available)" in menu)
check("every category keeps at least one verified GIF",
      [c for c, b in LIBRARY["categories"].items() if not any(g["status"] == "verified" for g in b["gifs"])], [])

lib = copy.deepcopy(LIBRARY)
applied, skipped = AD.apply_decisions(lib, [{"id": gid, "new_status": "verified"},
                                            {"id": cand, "new_status": "retired"}])
g = next(x for b in lib["categories"].values() for x in b["gifs"] if x["id"] == gid)
check("a replayed 'verified' decision cannot un-reject a GIF", g["status"], "rejected")
check_true("...and says why it skipped it", any(i == gid and "rejected" in why for i, why in skipped))
check("other decisions still apply", [a[0] for a in applied], [cand])

print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — 0 network requests, 0 API calls")
print("=" * 72)
