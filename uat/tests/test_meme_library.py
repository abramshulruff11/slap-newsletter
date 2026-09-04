"""
Run:  python -X utf8 uat/tests/test_meme_library.py

prompts/meme_library.DRAFT.json is the ONLY thing that tells the model which
meme to pick and what to write in each caption box. This test keeps it
internally consistent, because a single source of truth that disagrees with
itself is worse than two sources that don't.

WHY IT EXISTS
    Panel order and box counts were corrected against real Imgflip renders on
    2026-08-27 and 2026-09-01. Those commits updated box_count, boxes[],
    subject and selector_line — and left valence, worked_example and
    anti_example describing the OLD mapping. Nothing noticed, because nothing
    compared the fields to each other.

    format_meme_specs() prints all of them to the writer, and pass2_writer.txt
    calls the VALENCE RULE a hard requirement. So for distracted-boyfriend the
    writer was told "box 2 MUST name the subject" one line above "box 1: the
    SUBJECT". Its worked example put three captions in the wrong three slots.
    Eight more templates demonstrated a caption shape shorter than the
    template, and meme_box_check.py drops a short meme rather than shipping a
    blank panel — so imitating the example lost the meme entirely.

    Same class of failure as the runner half-ports: a correction applied to
    some fields and not the others, invisible to every existing check.

No API calls, no network.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import generate_memes as gm  # noqa: E402
import meme_library  # noqa: E402

LIB = json.loads((REPO / "prompts" / "meme_library.DRAFT.json").read_text(encoding="utf-8"))
TEMPLATES = LIB["templates"]

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def note(msg):
    print(f"        {msg}")


print("=" * 70)
print("MEME LIBRARY — internal consistency")
print("=" * 70)
print(f"  {len(TEMPLATES)} templates")

# --- 1. box_count is the contract every other field must honour -------------
bad = []
for t in TEMPLATES:
    n = len(t.get("boxes") or [])
    if n != t["box_count"]:
        bad.append(f"{t['slug']} box_count={t['box_count']} but {n} boxes[]")
check("boxes[] length matches box_count everywhere", bad, [])

bad = [t["slug"] for t in TEMPLATES
       if [b["index"] for b in t["boxes"]] != list(range(t["box_count"]))]
check("box indexes are 0..n-1 with no gaps", bad, [])

# --- 2. everything must agree on WHERE THE SUBJECT GOES ---------------------
# The failure that shipped: valence said "Box 2 MUST name the subject" one line
# above "box 1: the SUBJECT". Three fields encode this and all three must match.
#
# The library's convention is an UPPERCASE "SUBJECT" wherever it means the
# caption slot that carries the subject's name. Lowercase "subject" is just
# prose about the person ("the option the subject is waving off") and is not a
# slot marker — matching case-insensitively here produces nothing but noise.
def subject_boxes_from_purposes(t) -> set:
    return {b["index"] for b in t["boxes"] if "SUBJECT" in b["purpose"]}


def subject_box_from_placement(t):
    m = re.match(r'box:(\d+)$', ((t.get("subject") or {}).get("placement") or ""))
    return int(m.group(1)) if m else None


def subject_boxes_from_valence(t) -> set:
    return {int(i) for i in re.findall(
        r'[Bb]ox (\d+)[^.;]{0,40}?\bMUST (?:be|name) the SUBJECT', t.get("valence", ""))}


bad = []
for t in TEMPLATES:
    placed = subject_box_from_placement(t)
    from_boxes = subject_boxes_from_purposes(t)
    from_valence = subject_boxes_from_valence(t)
    if placed is None:
        # placement "copy": the subject is named in the prose, so no box may
        # claim to be the subject slot.
        if from_boxes:
            bad.append(f"{t['slug']}: placement is 'copy' but boxes {sorted(from_boxes)} "
                       f"claim to be the SUBJECT slot")
    else:
        if from_boxes != {placed}:
            bad.append(f"{t['slug']}: placement says box {placed}, boxes[] marks "
                       f"{sorted(from_boxes) or 'none'}")
        if from_valence and from_valence != {placed}:
            bad.append(f"{t['slug']}: valence puts the SUBJECT in box "
                       f"{sorted(from_valence)}, placement says {placed}")
check("valence, boxes[] and subject.placement agree on the subject slot", bad, [])

bad = []
for t in TEMPLATES:
    cited = {int(i) for i in re.findall(r'[Bb]ox (\d+)', t.get("valence", ""))}
    over = {i for i in cited if i >= t["box_count"]}
    if over:
        bad.append(f"{t['slug']}: valence cites box {sorted(over)} of {t['box_count']}")
check("no valence rule cites a box the template does not have", bad, [])

# --- 3. subject.placement must point at a box that says SUBJECT -------------
bad = []
for t in TEMPLATES:
    sub = t.get("subject") or {}
    m = re.match(r'box:(\d+)$', sub.get("placement", "") or "")
    if not m:
        continue
    i = int(m.group(1))
    purposes = {b["index"]: b["purpose"].lower() for b in t["boxes"]}
    if i not in purposes:
        bad.append(f"{t['slug']}: placement box:{i} does not exist")
    elif "subject" not in purposes[i]:
        bad.append(f"{t['slug']}: placement box:{i} but that box is not the subject")
check("subject.placement agrees with boxes[]", bad, [])

# --- 4. the worked example must demonstrate a SHIPPABLE caption set ---------
# The writer is told the worked example shows the shape. If the shape is short,
# meme_box_check.strip_short_memes() deletes the meme the writer built from it.
bad = []
for t in TEMPLATES:
    boxes = (t.get("worked_example") or {}).get("boxes")
    if not boxes:
        continue
    expanded = len(gm._expand_boxes(t["slug"], list(boxes)))
    if expanded != t["box_count"]:
        bad.append(f"{t['slug']}: worked example gives {len(boxes)} caption(s) "
                   f"-> {expanded} panel(s), template has {t['box_count']}")
check("every worked example fills the template", bad, [])

# The same set, run through the real guard, must survive it.
html = "".join(
    f'<div class="meme-placeholder" data-template="{t["slug"]}" '
    f'data-boxes="{"||".join((t.get("worked_example") or {}).get("boxes") or ["x"])}"></div>'
    for t in TEMPLATES if (t.get("worked_example") or {}).get("boxes")
)
import meme_box_check as MB  # noqa: E402
expected = {t["slug"]: t["box_count"] for t in TEMPLATES}
findings, _ = MB.check_html(html, gm.CURATED_TEMPLATES)
# Score against the library's own counts, not Imgflip (no network in tests).
short = []
for f in findings:
    want = expected.get(f["slug"])
    if want is not None and f["expanded"] != want:
        short.append(f"{f['slug']} {f['expanded']}/{want}")
check("no worked example would be dropped by meme_box_check", short, [])

# --- 5. the code and the library agree on template ids ----------------------
bad = [f"{t['slug']}: library {t['template_id']} vs code "
       f"{gm.CURATED_TEMPLATES.get(t['slug'])}"
       for t in TEMPLATES if str(t["template_id"]) != str(gm.CURATED_TEMPLATES.get(t["slug"]))]
check("template_id matches generate_memes.CURATED_TEMPLATES", bad, [])
check("library covers every curated slug",
      sorted(set(gm.CURATED_TEMPLATES) - {t["slug"] for t in TEMPLATES}), [])
check("library adds no slug the code cannot render",
      sorted({t["slug"] for t in TEMPLATES} - set(gm.CURATED_TEMPLATES)), [])

# --- 6. the generated selector index is in sync -----------------------------
on_disk = (REPO / "prompts" / "meme_selector_index.txt").read_text(encoding="utf-8")
check("meme_selector_index.txt is freshly generated from the library",
      on_disk == meme_library.build_selector_index(), True)

idx_slugs = set(re.findall(r'^\s+- (\S+) \(\d+ boxes', on_disk, re.M))
check("every template appears in the index",
      sorted({t["slug"] for t in TEMPLATES} - idx_slugs), [])
bad = []
for t in TEMPLATES:
    m = re.search(rf'^\s+- {re.escape(t["slug"])} \((\d+) boxes', on_disk, re.M)
    if m and int(m.group(1)) != t["box_count"]:
        bad.append(f"{t['slug']}: index says {m.group(1)}, library says {t['box_count']}")
check("index box counts match the library", bad, [])

# --- 7. what the writer is actually handed -----------------------------------
# format_meme_specs is the only channel for caption guidance. It must state the
# box count, and for a subject-in-a-box template it must name that box.
spec = meme_library.format_meme_specs([t["slug"] for t in TEMPLATES])
bad = [t["slug"] for t in TEMPLATES
       if f"### {t['slug']}  ({t['box_count']} caption boxes)" not in spec]
check("every spec states the true box count", bad, [])
bad = [t["slug"] for t in TEMPLATES
       if (t.get("subject") or {}).get("placement", "").startswith("box:")
       and f"SUBJECT RULE ({t['subject']['placement']})" not in spec]
check("every box-placed subject rule reaches the writer", bad, [])

print()
if failures:
    print("=" * 70)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    print("=" * 70)
    raise SystemExit(1)
print("=" * 70)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 70)
