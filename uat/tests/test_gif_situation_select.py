"""
Run:  python -X utf8 uat/tests/test_gif_situation_select.py

SLA-138: GIF selection prefers the beat's SITUATION, then falls back to the
mood as before. This locks:

1. pick_entry() with no situation is the mood-only pick it always was.
2. With a situation: strong fit first, then partial, then a FRESH mood GIF,
   and only then a situation GIF inside its cooldown. Nothing already used in
   the issue is ever repeated.
3. An entity match is a tie-breaker INSIDE the situation, never a reason to
   pick a GIF from outside it.
4. render_library_gifs() reads data-library-subcategory, ignores (and names)
   an id the taxonomy lacks, and records the request in history.
5. normalize_plan() keeps a valid gif_situation on a Tier 1 seed only, so a
   made-up id can never reach a placeholder.
6. The wiring: the UAT prompts ask for a situation, both runners substitute
   {{GIF_SITUATION_INDEX}} and carry gif_situation in Pass 1's schema, and the
   menu lists exactly the library's situation vocabulary.
7. The replay (uat/replay_gif_situations.py) finds every SLA-133 placement in
   the archive, and situation-aware picks beat mood-only picks.

No network, no API calls.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "uat"))

import gif_library_select as GL  # noqa: E402
import runner_common as RC  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


def g(gid, subs=(), status="verified", **extra):
    return {"id": gid, "label": gid, "status": status,
            "subcategories": [{"id": s, "fit": f} for s, f in subs], **extra}


# A small library where every rule has exactly one entry that proves it.
LIB = {"_meta": {"cooldown_days_default": 7}, "categories": {
    "respect_awe": {"gifs": [
        g("awe_plain"),
        g("awe_partial", [("calm_dominance", "partial")]),
        g("awe_retired", [("calm_dominance", "strong")], status="retired"),
        g("awe_entity", entities={"players": ["Aaron Judge"], "teams": [], "figures": []}),
    ]},
    "cockiness_smugness": {"gifs": [
        g("smug_strong", [("calm_dominance", "strong")]),
        g("smug_strong_yankees", [("calm_dominance", "strong")],
          entities={"players": [], "teams": ["New York Yankees"], "figures": []}),
    ]},
}}
TODAY = date(2026, 10, 17)


def used(*ids, days_ago=1):
    return [{"date": date.fromordinal(TODAY.toordinal() - days_ago).isoformat(),
             "library_id": i} for i in ids]


def picks(n=200, **kw):
    out = set()
    for k in range(n):
        random.seed(k)
        r = GL.choose_entry(LIB, kw.get("category", "respect_awe"),
                            kw.get("history", []), kw.get("used", set()),
                            subcategory=kw.get("sub"), entities=kw.get("ent"),
                            today=TODAY)
        out.add((r["entry"] or {}).get("id"))
        out.add(("matched", r["matched"]))
        out.add(("relaxed", r["relaxed"]))
    return out


print("\n1. No situation = the old mood-only pick")
p = picks()
check("mood-only picks come from the category's verified entries",
      sorted(x for x in p if isinstance(x, str)), ["awe_entity", "awe_partial", "awe_plain"])
check_true("reported as a mood match", ("matched", "mood") in p)
random.seed(3)
check("pick_entry keeps its (entry, relaxed) shape",
      type(GL.pick_entry(LIB, "respect_awe", [], set())).__name__, "tuple")

print("\n2. Situation order")
p = picks(sub="calm_dominance")
check("strong fit wins, from any category",
      sorted(x for x in p if isinstance(x, str)), ["smug_strong", "smug_strong_yankees"])
check_true("reported as a situation match", ("matched", "situation") in p)
check("a retired GIF never serves a situation",
      "awe_retired" in p, False)
p = picks(sub="calm_dominance", used={"smug_strong", "smug_strong_yankees"})
check("partial fit next, once the strong ones are used in this issue",
      sorted(x for x in p if isinstance(x, str)), ["awe_partial"])
p = picks(sub="calm_dominance",
          history=used("smug_strong", "smug_strong_yankees", "awe_partial"))
check("every situation GIF cooling -> a FRESH mood GIF, not a repeat",
      sorted(x for x in p if isinstance(x, str)), ["awe_entity", "awe_plain"])
check_true("...reported as a mood fallback, not relaxed",
           ("matched", "mood") in p and ("relaxed", False) in p)
p = picks(sub="calm_dominance",
          history=used("smug_strong", "smug_strong_yankees", "awe_partial",
                       "awe_plain", "awe_entity"))
check("everything cooling -> the situation GIF is repeated (relaxed)",
      ("relaxed", True) in p and ("matched", "situation") in p, True)
check("old cooldown entries (8 days) no longer block",
      sorted(x for x in picks(sub="calm_dominance",
                              history=used("smug_strong", "smug_strong_yankees",
                                           days_ago=8)) if isinstance(x, str)),
      ["smug_strong", "smug_strong_yankees"])
p = picks(sub="no_such_situation")
check("a situation nobody is tagged with falls back to the mood",
      ("matched", "mood") in p and ("matched", "situation") not in p, True)

print("\n3. Entities break ties inside the situation only")
p = picks(sub="calm_dominance", ent=["Yankees"])
check("a team match narrows the situation pool",
      sorted(x for x in p if isinstance(x, str)), ["smug_strong_yankees"])
p = picks(sub="calm_dominance", ent=["Aaron Judge"])
check("a matching face OUTSIDE the situation is never picked over it",
      "awe_entity" in p, False)
p = picks(sub="calm_dominance", ent=["Nobody Here"])
check("no entity match -> the whole situation pool",
      sorted(x for x in p if isinstance(x, str)), ["smug_strong", "smug_strong_yankees"])
check("whole words only: a fragment of a team name is not a match",
      GL._entity_match(LIB["categories"]["cockiness_smugness"]["gifs"][1], ["ork yan"]), False)

print("\n4. render_library_gifs")
_orig = (GL.load_library, GL.GC.load_cache, GL.GC.save_cache, GL.GC.resolve)
GL.load_library = lambda: copy.deepcopy(LIB)
GL.GC.load_cache = lambda: {}
GL.GC.save_cache = lambda c: None
GL.GC.resolve = lambda gid, fetch, cache: (f"https://example.test/{gid}.gif", "cache")
try:
    random.seed(0)
    html = ('<div class="gif-placeholder" data-library-category="respect_awe" '
            'data-library-subcategory="calm_dominance" data-library-entities="Yankees"></div>'
            '<div class="gif-placeholder" data-library-category="respect_awe" '
            'data-library-subcategory="made_up_id"></div>'
            '<div class="gif-placeholder" data-library-category="respect_awe"></div>')
    out, hist, stats = GL.render_library_gifs(html, "key", [])
finally:
    GL.load_library, GL.GC.load_cache, GL.GC.save_cache, GL.GC.resolve = _orig
check("all three rendered", stats["rendered"], 3)
check("first placeholder got the situation + entity GIF", hist[0]["library_id"], "smug_strong_yankees")
check("history records the situation asked for", hist[0].get("library_subcategory"), "calm_dominance")
check("an unknown situation id is named", stats["unknown_subcategory"], ["made_up_id"])
check("...and not recorded as a situation request", "library_subcategory" in hist[1], False)
check("situation requested / matched", (stats["situation_requested"], stats["situation_matched"]), (1, 1))
check("no placeholder survives", "gif-placeholder" in out, False)

print("\n5. normalize_plan")
def story(**kw):
    return {"headline": "h", "tweets": [], "gif_concept": "closer shuts the door", **kw}
plan = RC.normalize_plan({
    "lead_story": story(gif_tier=1, gif_situation="calm_dominance"),
    "supporting_stories": [
        story(gif_tier=1, gif_situation="not_a_situation"),
        story(gif_tier=3, gif_situation="calm_dominance"),
        story(gif_tier=1),
        {"headline": "h", "tweets": [], "gif_concept": "", "gif_situation": "calm_dominance"},
    ],
    "around_the_league": {"tweets": []}})
check("a valid Tier 1 situation is kept", plan["lead_story"].get("gif_situation"), "calm_dominance")
check("an unknown id is dropped", "gif_situation" in plan["supporting_stories"][0], False)
check("a Tier 3 seed carries no situation", "gif_situation" in plan["supporting_stories"][1], False)
check("unset stays absent (an old plan is unchanged)", "gif_situation" in plan["supporting_stories"][2], False)
check("no gif_concept, no situation", "gif_situation" in plan["supporting_stories"][3], False)

print("\n6. Wiring")
real = GL.load_library()
vocab = set(real["_meta"]["tag_vocabulary"]["subcategories"])
check("the taxonomy's situations are the library's vocabulary", GL.known_situations(), vocab)
block = GL.situation_prompt_block()
check("the menu lists every situation", sorted(s for s in vocab if f"`{s}`" not in block), [])
uat_p1 = (REPO / "uat/prompts/pass1_story_selector.txt").read_text(encoding="utf-8")
uat_ref = (REPO / "uat/prompts/gif_reference.txt").read_text(encoding="utf-8")
uat_p2 = (REPO / "uat/prompts/pass2_writer.txt").read_text(encoding="utf-8")
check_true("UAT Pass 1 asks for gif_situation", "gif_situation" in uat_p1)
check_true("UAT Pass 1 carries the menu placeholder", "{{GIF_SITUATION_INDEX}}" in uat_p1)
check_true("UAT GIF reference carries the menu placeholder", "{{GIF_SITUATION_INDEX}}" in uat_ref)
check_true("UAT Tier 1 placeholder carries the situation",
           'data-library-subcategory="SITUATION_ID"' in uat_ref
           and 'data-library-subcategory="SITUATION_ID"' in uat_p2)
for runner in ("generate_newsletter.py", "uat/generate_newsletter_uat.py"):
    code = (REPO / runner).read_text(encoding="utf-8")
    check(f"{runner} substitutes the menu in Pass 1 and Pass 2",
          code.count('"{{GIF_SITUATION_INDEX}}", _GL.situation_prompt_block()'), 2)
    check_true(f"{runner} has gif_situation in Pass 1's schema",
               '"gif_situation":  {"type": "string"}' in code)

print("\n7. The replay")
import replay_gif_situations as RP  # noqa: E402
placements, unmatched = RP.build_placements(real, GL.load_taxonomy(), REPO / "archive")
check("every SLA-133 placement context is found in its issue", unmatched, [])
check("345 library GIFs shipped 09-02..10-14", len(placements), 345)
check("300 of them classified into a situation",
      sum(1 for p in placements if p["situation"]), 300)
res = RP.run(seeds=2)
check_true(f"situation-aware beats mood-only ({res['hit_rate']['after']:.0%} vs "
           f"{res['hit_rate']['before']:.0%})",
           res["hit_rate"]["after"] > res["hit_rate"]["before"] + 0.2)
check_true("never above the ceiling", res["hit_rate"]["after"] <= res["hit_rate"]["ceiling"])

print()
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED — 0 API calls")
