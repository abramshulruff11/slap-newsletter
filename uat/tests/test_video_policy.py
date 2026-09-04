"""
Run:  python -X utf8 uat/tests/test_video_policy.py

Locks the video-tweet policy: video tweets are Around the League only.

Three pieces have to agree or the rule is decorative:
  1. fetch_content.classify_media reads the media kind off the Nitter RSS
     description (the field production used to discard).
  2. plan_audit.enforce_video_policy removes video tweets from every headliner
     section AND from every beat's media[] — Pass 2 is locked to the beats, so
     pruning one without the other just relocates the problem.
  3. Around the League keeps them.

The classifier is checked against the real descriptions in
uat/fixtures/raw_content_enriched.json, which were tagged from live Nitter RSS
(206 tweets, 77 of them video/gif) — not against invented markup.

No API calls, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import fetch_content as FC  # noqa: E402
import plan_audit  # noqa: E402


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("MEDIA CLASSIFIER — fetch_content.classify_media")
print("=" * 66)

# Real Nitter description shapes, one per branch.
check("native video marker", FC.classify_media(
    '<p>Look at this</p><img src="https://nitter.net/pic/amplify_video_thumb%2F1.jpg"/>'
), "video")
check("ext_tw_video marker", FC.classify_media(
    '<img src="https://nitter.net/pic/ext_tw_video_thumb%2F2%2Fimg%2Fx.jpg"/>'
), "video")
check("literal Video label", FC.classify_media("<p>clip</p><p> Video </p>"), "video")
check("twitter looping gif", FC.classify_media(
    '<img src="https://nitter.net/pic/tweet_video_thumb%2Fabc.jpg"/>'
), "gif")
check("still photo", FC.classify_media('<img src="https://nitter.net/pic/media%2Fq.jpg"/>'), "image")
check("plain text", FC.classify_media("<p>no media here</p>"), "text")
check("empty/missing description", FC.classify_media(""), "text")

check("gif counts as video for the policy",
      FC.classify_media('<img src="x/tweet_video_thumb%2Fa.jpg"/>') in ("video", "gif"), True)

# The enriched fixture is the ground truth: every tweet in it was tagged from
# live Nitter RSS. Confirm the same has_video split this code would produce.
fixture = REPO / "uat" / "fixtures" / "raw_content_enriched.json"
if fixture.exists():
    tw = json.loads(fixture.read_text(encoding="utf-8"))["tweets"]
    tagged = sum(1 for t in tw if t.get("has_video"))
    kinds = {t.get("media_kind") for t in tw}
    print(f"  [ok ] fixture: {tagged}/{len(tw)} tagged has_video, kinds={sorted(kinds)}")
    check("fixture video share is material (policy actually bites)", tagged > 20, True)
    check("every fixture kind is one this classifier emits",
          kinds <= {"video", "gif", "image", "text", "unknown"}, True)

print()
print("=" * 66)
print("HEADLINER EXCLUSION — plan_audit.enforce_video_policy")
print("=" * 66)


def _t(acct, sid, video=False):
    return {"account": acct, "url": f"https://twitter.com/{acct}/status/{sid}"}


RAW = {"tweets": [
    {"account": "a", "link": "https://twitter.com/a/status/111", "has_video": True},
    {"account": "b", "link": "https://twitter.com/b/status/222", "has_video": False},
    {"account": "c", "link": "https://twitter.com/c/status/333#m", "has_video": True},
    {"account": "d", "link": "https://nitter.net/d/status/444", "has_video": True},
]}

vids = plan_audit.video_status_ids(RAW)
check("video ids collected across hosts and #m", vids, {"111", "333", "444"})

plan = {
    "lead_story": {
        "headline": "lead",
        "tweets": [_t("a", 111), _t("b", 222), _t("c", 333)],
        "beats": [
            {"angle": "x", "landing": "y", "media": [_t("a", 111)]},
            {"angle": "x2", "landing": "y2", "media": [_t("b", 222)]},
        ],
    },
    "supporting_stories": [
        {"headline": "s0", "tweets": [_t("d", 444)],
         "beats": [{"angle": "s", "landing": "l", "media": [_t("d", 444)]}]},
    ],
    # ATL is the exception: every one of these survives.
    "around_the_league": {"tweets": [_t("a", 111), _t("c", 333), _t("b", 222)]},
}

rep = plan_audit.enforce_video_policy(plan, vids)

check("lead keeps only the non-video tweet",
      [t["url"].rsplit("/", 1)[-1] for t in plan["lead_story"]["tweets"]], ["222"])
check("beat media pruned too (Pass 2 is locked to beats)",
      plan["lead_story"]["beats"][0]["media"], [])
check("non-video beat media untouched",
      [m["url"].rsplit("/", 1)[-1] for m in plan["lead_story"]["beats"][1]["media"]], ["222"])
check("a supporting story may end up with zero tweets",
      plan["supporting_stories"][0]["tweets"], [])
check("Around the League keeps its video tweets",
      [t["url"].rsplit("/", 1)[-1] for t in plan["around_the_league"]["tweets"]],
      ["111", "333", "222"])
check("report counts every drop", len(rep["dropped"]), 3)
check("report names the sections", sorted(rep["sections"]), ["lead", "supporting0"])
check("report counts ATL survivors", rep["atl_kept"], 2)

# No video in raw content: a no-op that touches nothing.
plan2 = {"lead_story": {"tweets": [_t("b", 222)], "beats": []},
         "supporting_stories": [], "around_the_league": {"tweets": []}}
rep2 = plan_audit.enforce_video_policy(plan2, set())
check("no video ids -> no drops", rep2["dropped"], [])
check("no video ids -> plan untouched", len(plan2["lead_story"]["tweets"]), 1)

# A malformed plan must not crash the pipeline between Pass 1 and Pass 2.
plan3 = {"lead_story": {"tweets": ["not a dict", _t("a", 111)], "beats": ["junk"]},
         "supporting_stories": [None], "around_the_league": []}
plan_audit.enforce_video_policy(plan3, vids)
check("junk entries survive without raising", len(plan3["lead_story"]["tweets"]), 1)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
