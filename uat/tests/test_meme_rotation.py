"""
Run:  python -X utf8 uat/tests/test_meme_rotation.py

Locks meme template rotation, which until 2026-09-04 did not exist.

Pass 1 chooses the template. It was never shown what ran recently — only Pass 2
got the "RECENTLY USED MEDIA" block, and by then the slug was fixed. The only
rotation signal in the whole pipeline was this line, printed by
generate_memes.process_newsletter AFTER the meme had been made:

    [memes] ⚠ 'drake' used in last 7 days — consider varying template

It fired on 2026-08-31 (trade-offer) and twice on 2026-09-04 (drake,
two-buttons), and nothing acted on it any of those times.

Now: Pass 1 is told what is cooling down, and anything it still picks is
swapped for another template driven by the SAME comedic engine, so the joke it
planned survives with a different picture. A repeat beats no meme, so when the
engine has nothing free the original is kept and the report says so.

No API calls, no network.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import meme_library  # noqa: E402

LIB = json.loads((REPO / "prompts" / "meme_library.DRAFT.json").read_text(encoding="utf-8"))
BY_ENGINE: dict = {}
for _t in LIB["templates"]:
    BY_ENGINE.setdefault(_t["engine"], []).append(_t["slug"])

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def today(n=0):
    return (date.today() - timedelta(days=n)).isoformat()


print("=" * 70)
print("RECENT SLUGS — reading meme_history.json")
print("=" * 70)

hist = [
    {"date": today(0), "slug": "drake"},
    {"date": today(6), "slug": "two-buttons"},
    {"date": today(8), "slug": "gru-plan"},          # outside the window
    {"date": "not-a-date", "slug": "expanding-brain"},
    {"slug": "no-date-at-all"},
    "junk row",
    {"date": today(1)},                               # no slug
]
check("only slugs inside the window", meme_library.recently_used_slugs(hist),
      {"drake", "two-buttons"})
check("a shorter window excludes more",
      meme_library.recently_used_slugs(hist, days=3), {"drake"})
check("empty history is empty", meme_library.recently_used_slugs([]), set())
check("None history does not raise", meme_library.recently_used_slugs(None), set())

print()
print("=" * 70)
print("COOLDOWN BLOCK — what Pass 1 is told")
print("=" * 70)

check("no block when nothing is cooling", meme_library.format_cooldown_block(set()), "")
blk = meme_library.format_cooldown_block({"drake", "two-buttons"})
check("names every cooled slug",
      all(s in blk for s in ("drake", "two-buttons")), True)
check("explains that picking one is pointless", "swapped automatically" in blk, True)
check("block is sorted (stable prompt, cache-friendly)",
      blk.index("drake") < blk.index("two-buttons"), True)

print()
print("=" * 70)
print("SWAP — same engine, never a duplicate, never a cooled replacement")
print("=" * 70)


def plan_with(*slugs):
    stories = [{"headline": f"story {i}", "meme_template": s}
               for i, s in enumerate(slugs)]
    return {"lead_story": stories[0], "supporting_stories": stories[1:],
            "around_the_league": {"tweets": []}}


# drake's engine has other members, so a cooled drake is replaceable.
drake_engine = [t["engine"] for t in LIB["templates"] if t["slug"] == "drake"][0]
mates = [s for s in BY_ENGINE[drake_engine] if s != "drake"]
check("drake's engine has alternatives to swap to", bool(mates), True)

p = plan_with("drake")
swaps = meme_library.swap_cooled_templates(p, {"drake"})
new_slug = p["lead_story"]["meme_template"]
check("cooled template was replaced", new_slug != "drake", True)
check("replacement is in the same engine", new_slug in mates, True)
check("swap reported", [(o, n) for _h, o, n in swaps], [("drake", new_slug)])

# A template that is NOT cooling down is left alone.
p = plan_with("drake")
meme_library.swap_cooled_templates(p, {"expanding-brain"})
check("untouched when not cooling", p["lead_story"]["meme_template"], "drake")

# The replacement must not itself be cooling down.
p = plan_with("drake")
meme_library.swap_cooled_templates(p, {"drake"} | set(mates[:-1]))
check("replacement avoids other cooled slugs",
      p["lead_story"]["meme_template"], mates[-1])

# Nothing free in the engine -> keep the original, and say so.
p = plan_with("drake")
swaps = meme_library.swap_cooled_templates(p, {"drake"} | set(mates))
check("a repeat beats no meme", p["lead_story"]["meme_template"], "drake")
check("kept-original is reported as such", [n for _h, _o, n in swaps], [None])

# Two stories must not both land on the same replacement.
engine_slugs = BY_ENGINE[drake_engine]
if len(engine_slugs) >= 3:
    a, b = engine_slugs[0], engine_slugs[1]
    p = plan_with(a, b)
    meme_library.swap_cooled_templates(p, {a, b})
    picked = [p["lead_story"]["meme_template"],
              p["supporting_stories"][0]["meme_template"]]
    check("no duplicate template inside one issue", len(set(picked)), len(picked))

# Malformed plans must not break the pipeline between Pass 1 and Pass 2.
for junk in ({}, {"lead_story": None, "supporting_stories": None},
             {"lead_story": {"meme_template": None}, "supporting_stories": ["x", None]},
             None):
    meme_library.swap_cooled_templates(junk, {"drake"})
check("malformed plans do not raise", True, True)

# Every possible swap target is a slug the renderer knows.
import generate_memes as gm  # noqa: E402
bad = []
for t in LIB["templates"]:
    for alt in meme_library.engine_alternatives(t["slug"]):
        if alt not in gm.CURATED_TEMPLATES:
            bad.append(f"{t['slug']} -> {alt}")
check("every swap target is renderable", bad, [])
check("engine_alternatives never returns the slug itself",
      [t["slug"] for t in LIB["templates"]
       if t["slug"] in meme_library.engine_alternatives(t["slug"])], [])

print()
print("=" * 70)
print("WIRING — both runners ask for it and act on it")
print("=" * 70)

for label, runner, entry in (
        ("prod", "generate_newsletter.py", "generate_newsletter.py"),
        ("uat", "uat/generate_newsletter_uat.py", "uat/run_uat.py")):
    rsrc = (REPO / runner).read_text(encoding="utf-8")
    esrc = (REPO / entry).read_text(encoding="utf-8")
    check(f"{label}: run_pass1 takes recent_meme_slugs",
          bool(re.search(r'def run_pass1\([^)]*recent_meme_slugs', rsrc, re.S)), True)
    check(f"{label}: cooldown block reaches Pass 1's message",
          "format_cooldown_block" in rsrc and "+ cooldown_block" in rsrc, True)
    check(f"{label}: the swap runs before the plan is serialised",
          rsrc.index("swap_cooled_templates")
          < rsrc.index("story_plan_raw = json.dumps"), True)
    check(f"{label}: caller supplies the history",
          "recently_used_slugs" in esrc and "recent_meme_slugs=" in esrc, True)

print()
if failures:
    print("=" * 70)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 70)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 70)
