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
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dry-run-not-a-real-key")
sys.path.insert(0, str(REPO))

import anthropic  # noqa: E402

def _has_meme_index(text: str) -> bool:
    """The injected library index, identified by content rather than its title.

    Requires a real engine heading AND at least one 'slug (N boxes, subject: ...)'
    line — a renamed header stays green, an empty or unsubstituted placeholder
    does not.
    """
    import re as _re
    return (_re.search(r'^## [a-z_]+$', text, _re.M) is not None
            and _re.search(r'^\s+- [a-z0-9-]+ \(\d+ boxes, subject: ', text, _re.M) is not None
            and "{{MEME_SELECTOR_INDEX}}" not in text)

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
check("meme library index in Pass 1 system prompt", _has_meme_index(systext), True)
check("engines listed", "subject_abandons_sensible_for_funky" in systext, True)

props = p1["tools"][0]["input_schema"]["properties"]["lead_story"]["properties"]
for field in ("beats", "gif_tier", "meme_template", "meme_subject"):
    check(f"tool schema exposes {field}", field in props, True)
beat_props = props["beats"]["items"]["properties"]
check("beat has angle/landing/media",
      sorted(beat_props) == ["angle", "landing", "media"], True)

check("max_tokens raised for beats-shaped plans", p1["max_tokens"], 32768)

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
check("meme template index substituted into writer prompt",
      "{{MEME_SELECTOR_INDEX}}" in p2sys, False)
check("meme library index actually present in the writer prompt",
      _has_meme_index(p2sys), True)
# The index is static, so it belongs in the cached block; the per-run spec does
# not. Both are asserted because putting them the wrong way round either
# thrashes the cache daily or caches a selection that changes every run.
check("index is in the CACHED system block (it never changes)",
      any(_has_meme_index(b["text"]) and b.get("cache_control")
          for b in p2["system"]), True)
check("no hand-written caption table survives in the writer prompt",
      "Captions you write" in p2sys, False)
check("GIF categories actually present", "CATEGORY:" in p2sys or "category" in p2sys.lower(), True)
check("per-run spec NOT in cached system block",
      "### distracted-boyfriend" in p2sys, False)

print()
print("=" * 70)
print("PRODUCTION WIRING VERIFIED — 0 API calls")
print("=" * 70)
