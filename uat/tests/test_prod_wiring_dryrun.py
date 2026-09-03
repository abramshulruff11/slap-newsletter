"""
Run:  python -X utf8 uat/tests/test_prod_wiring_dryrun.py

Dry run of PR-B through the REAL generate_newsletter.py code path with the
Anthropic client stubbed. Makes zero API calls and writes nothing.

Exists because nothing had ever exercised production's pipeline with the meme
library, beats, or the GIF library wired in — and the alternative first
exercise was the 2:17 AM scheduled run.

Proves, in production code:
  1. the meme selector index reaches Pass 1's system prompt
  2. Pass 1's tool schema exposes beats, gif_tier, meme_template, meme_subject
  3. Pass 1 asks for enough output tokens for a beats-shaped plan
  4. the chosen templates' specs reach Pass 2's user message, and unchosen ones do not
  5. the GIF library's category menu is substituted into the writer prompt
     (a literal "{{GIF_LIBRARY_CATEGORIES}}" reaching the model is the bug)
  6. the per-run meme spec stays OUT of the cached system block
"""
import os
import sys
import json
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dry-run-not-a-real-key")
sys.path.insert(0, str(REPO))

import anthropic  # noqa: E402

CAPTURED = {"pass1": None, "pass2": None}


class _Blk:
    def __init__(self, text):
        self.type, self.text, self.name = "text", text, None


class _ToolBlk:
    def __init__(self, name, inp):
        self.type, self.name, self.input, self.id = "tool_use", name, inp, "t1"


class _Usage:
    input_tokens = output_tokens = 0
    cache_read_input_tokens = cache_creation_input_tokens = 0


STORY_PLAN = {
    "date": "2026-08-31",
    "story_log": [],
    "lead_story": {
        "topic": "qb", "headline": "The Pivot", "tweets": [], "research_notes": "n",
        "gif_concept": "quiet disbelief", "gif_tier": 1,
        "meme_concept": "they moved on", "meme_template": "distracted-boyfriend",
        "meme_subject": "The Vikings",
        "beats": [{"angle": "a", "landing": "the room goes quiet", "media": []}],
    },
    "supporting_stories": [{
        "topic": "rivals", "headline": "Common Ground", "tweets": [], "research_notes": "n",
        "gif_concept": "", "meme_concept": "both agree",
        "meme_template": "epic-handshake", "meme_subject": "Two fanbases",
        "beats": [{"angle": "b", "landing": "an unlikely handshake", "media": []}],
    }],
    "around_the_league": {"tweets": []},
    "account_distribution": {},
}


class FakeMessages:
    # Production uses messages.create() for every pass, and BOTH passes carry
    # tools — Pass 1 has submit_story_plan, Pass 2 has web_search. Keying off
    # the mere presence of `tools` made this stub answer Pass 2 with a tool_use
    # block, which spun prod's Pass 2 retry loop forever. Match the name.
    def create(self, **kw):
        r = types.SimpleNamespace()
        r.usage = _Usage()
        names = {t.get("name") for t in (kw.get("tools") or []) if isinstance(t, dict)}
        if "submit_story_plan" in names:
            CAPTURED["pass1"] = kw
            r.content = [_ToolBlk("submit_story_plan", STORY_PLAN)]
            r.stop_reason = "tool_use"
        else:
            CAPTURED["pass2"] = kw
            r.content = [_Blk("<h1>x</h1>")]
            r.stop_reason = "end_turn"
        return r

    def stream(self, **kw):
        CAPTURED["pass1"] = kw
        class FakeStream:
            def __iter__(self_):
                return iter(())
            def get_final_message(self_):
                r = types.SimpleNamespace()
                r.content = [_ToolBlk("submit_story_plan", STORY_PLAN)]
                r.usage, r.stop_reason = _Usage(), "tool_use"
                return r
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
        return FakeStream()


class FakeClient:
    def __init__(self, *a, **k):
        self.messages = FakeMessages()


anthropic.Anthropic = FakeClient
import generate_newsletter as GN  # noqa: E402
GN.client = None

raw = GN.load_json(REPO / "uat" / "fixtures" / "raw_content.json")
game_state = GN.load_json(REPO / "uat" / "fixtures" / "game_state.json")
client = FakeClient()


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 70)
print("PASS 1 — production")
print("=" * 70)
plan_json = GN.run_pass1(raw, [], client, game_state)

p1 = CAPTURED["pass1"]
systext = "".join(b["text"] if isinstance(b, dict) else str(b) for b in p1["system"])
check("meme selector index in system prompt", "MEME SELECTOR INDEX" in systext, True)
check("engines listed", "subject_abandons_sensible_for_funky" in systext, True)

props = p1["tools"][0]["input_schema"]["properties"]["lead_story"]["properties"]
for field in ("beats", "gif_tier", "meme_template", "meme_subject"):
    check(f"tool schema exposes {field}", field in props, True)
beat_props = props["beats"]["items"]["properties"]
check("beat has angle/landing/media",
      sorted(beat_props) == ["angle", "landing", "media"], True)

check("max_tokens raised for beats-shaped plans", p1["max_tokens"], 32768)

# --- §2.1 video filter + the PURE UPDATE FILTER's forcing function -----------
# Both of these existed only in UAT until 2026-09-03 while prod ran the SAME
# prompt asserting them. Nothing could see it: promote.py diffs prompts only,
# and no test looked at what Pass 1 was actually handed. These assertions are
# that missing eye — they read the real payload prod built, not a copy of it.

check("tool schema requires filter_stats",
      "filter_stats" in p1["tools"][0]["input_schema"]["required"], True)
check("filter_stats requires the rejection count",
      p1["tools"][0]["input_schema"]["properties"]["filter_stats"]["required"],
      ["update_tweets_rejected"])

# pass1_story_selector.txt tells Pass 1 the count goes in this field. If the
# schema ever stops defining it, the prompt is asking for a slot that does not
# exist — which is exactly the state prod shipped in for five weeks.
selector = GN.load_prompt("pass1_story_selector.txt")
check("prompt and schema name the same field",
      "filter_stats.update_tweets_rejected" in selector, True)

# The video cut runs on media_kind, which fetch_content.py now tags. Replay it
# against enriched, prod-shaped input and read the payload back out.
enriched = GN.load_json(REPO / "uat" / "fixtures" / "raw_content_enriched.json")
CAPTURED.clear()
GN.run_pass1(enriched, [], client, game_state)
sent = json.loads(CAPTURED["pass1"]["messages"][0]["content"].split(
    "## TODAY'S RAW CONTENT\n\n", 1)[1].split("\n\n## RECENT STORY HISTORY", 1)[0])

n_video = sum(1 for t in enriched["tweets"] if t.get("media_kind") == "video")
check("fixture actually contains video tweets to cut", n_video > 0, True)
check("no video tweet reaches Pass 1",
      [t for t in sent["tweets"] if t["link"] in
       {v["link"] for v in enriched["tweets"] if v.get("media_kind") == "video"}], [])
check("§2.1 cut exactly the video tweets",
      len(sent["tweets"]), len(enriched["tweets"]) - n_video)

# GIF tweets are KEPT in prod. UAT cuts them (Pass 1B mines them for highlight
# clips); prod has no Pass 1B, and pass1_story_selector.txt's very next section
# says to PREFER GIF tweets because they autoplay inline. Cutting them here
# would delete what the live prompt asks the model to choose.
n_gif = sum(1 for t in enriched["tweets"] if t.get("media_kind") == "gif")
check("fixture actually contains gif tweets", n_gif > 0, True)
check("gif tweets survive the cut",
      sum(1 for t in sent["tweets"] if t["link"] in
          {g["link"] for g in enriched["tweets"] if g.get("media_kind") == "gif"}),
      n_gif)

# Media fields are routing information for the filter, not content. Leaking
# them would change Pass 1's payload shape and burn tokens on every tweet.
check("media fields stripped from the payload",
      sorted({k for t in sent["tweets"] for k in t}),
      ["account", "link", "pubDate", "text"])

# The floor must count what Pass 1 is offered, not the raw list — otherwise a
# thin, video-heavy day clears it on tweets the model never sees and then has
# to invent its way to the schema's 10-tweet ATL minimum.
thin = {"news_headlines": [], "tweets": (
    [{"account": "a", "text": "t", "link": f"https://twitter.com/a/status/{i}",
      "pubDate": "2026-09-03", "media_kind": "video", "has_video": True}
     for i in range(9)]
    + [{"account": "b", "text": "t", "link": "https://twitter.com/b/status/99",
        "pubDate": "2026-09-03", "media_kind": "text", "has_video": False}])}
# The backstop, exercised for real: hand Pass 1 back a plan naming a tweet it
# was never offered. §2.1 has to mean video cannot ship, not just that the
# model was not shown any — the whole point of this investigation was rules
# that were advisory where they read as enforced.
_video = next(t for t in enriched["tweets"] if t["media_kind"] == "video")
_text = next(t for t in enriched["tweets"] if t["media_kind"] == "text")
_slipped = dict(STORY_PLAN, lead_story=dict(
    STORY_PLAN["lead_story"],
    tweets=[{"account": _video["account"], "url": _video["link"]},
            {"account": _text["account"], "url": _text["link"]}]))
_orig, STORY_PLAN = STORY_PLAN, _slipped
try:
    _out = json.loads(GN.run_pass1(enriched, [], client, game_state))
finally:
    STORY_PLAN = _orig
_kept = [t["url"] for t in _out["lead_story"]["tweets"]]
check("backstop drops a video tweet Pass 1 was never offered",
      _video["link"] in _kept, False)
check("backstop leaves the legitimate tweet alone", _text["link"] in _kept, True)

check("degraded floor counts usable, not raw",
      (len(thin["tweets"]) >= GN.DEGRADED_TWEET_FLOOR,
       len(GN.usable_tweets(thin)) < GN.DEGRADED_TWEET_FLOOR),
      (True, True))

print()
print("=" * 70)
print("PASS 2 — production")
print("=" * 70)
_ = GN.run_pass2(plan_json, client, game_state)
p2 = CAPTURED["pass2"]
um = p2["messages"][0]["content"]

check("meme spec block injected", "SELECTED MEME TEMPLATES" in um, True)
rendered = [l.split()[1] for l in um.splitlines() if l.startswith("### ")]
check("only the chosen templates specced",
      rendered, ["distracted-boyfriend", "epic-handshake"])
check("unchosen template absent", "### drake" in um, False)

p2sys = "".join(b["text"] for b in p2["system"])
check("GIF library menu substituted into writer prompt",
      "{{GIF_LIBRARY_CATEGORIES}}" in p2sys, False)
check("GIF categories actually present", "CATEGORY:" in p2sys or "category" in p2sys.lower(), True)
check("per-run spec NOT in cached system block",
      "### distracted-boyfriend" in p2sys, False)

print()
print("=" * 70)
print("PRODUCTION WIRING VERIFIED — 0 API calls")
print("=" * 70)
