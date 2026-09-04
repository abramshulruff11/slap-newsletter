"""
Run:  python uat/tests/test_meme_wiring_dryrun.py

Dry run of the UAT meme wiring through the REAL uat/generate_newsletter_uat.py
code path, with the Anthropic client stubbed. Makes zero API calls.

Proves: (1) the selector index actually reaches Pass 1's system prompt,
(2) Pass 1's tool schema exposes meme_template/meme_subject,
(3) the chosen templates' specs actually reach Pass 2's user message,
(4) unchosen templates do not.
"""
import os, sys, json, types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dry-run-not-a-real-key")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "uat"))

import anthropic

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
    def __init__(self, text): self.type="text"; self.text=text; self.name=None
class _ToolBlk:
    def __init__(self, name, inp): self.type="tool_use"; self.name=name; self.input=inp; self.id="t1"
class _Usage:
    input_tokens=0; output_tokens=0; cache_read_input_tokens=0; cache_creation_input_tokens=0

STORY_PLAN = {
  "date":"2026-08-26",
  "story_log":[],
  "lead_story":{"topic":"QB decision","headline":"The Pivot","tweets":[],
    "research_notes":"n","gif_concept":"","meme_concept":"they moved on",
    "meme_template":"distracted-boyfriend","meme_subject":"The Vikings","beats":[]},
  "supporting_stories":[
    {"topic":"deadline","headline":"Swerve","tweets":[],"research_notes":"n",
     "gif_concept":"","meme_concept":"last second","meme_template":"epic-handshake",
     "meme_subject":"Two rival fanbases","beats":[]},
    {"topic":"league trend","headline":"Trend","tweets":[],"research_notes":"n",
     "gif_concept":"shrug","meme_concept":"","meme_template":"","meme_subject":"","beats":[]},
  ],
  "around_the_league":{"tweets":[]},
  "account_distribution":{}, "filter_stats":{"update_tweets_rejected":0},
}

# NOTE: the two templates below are chosen to cover BOTH subject placements —
# distracted-boyfriend captions its subject (box:2), epic-handshake names it in
# the copy. Keep that contrast if you swap them; left-exit-12-off-ramp used to
# serve as the "copy" case but was corrected to box:2 on 2026-08-27 when its
# real 3-box count was verified against Imgflip.
class FakeMessages:
    def create(self, **kw):
        CAPTURED["pass2"] = kw
        r = types.SimpleNamespace()
        r.content=[_Blk("<h1>x</h1>")]; r.usage=_Usage(); r.stop_reason="end_turn"
        return r
    def stream(self, **kw):
        CAPTURED["pass1"] = kw
        outer = self
        class FakeStream:
            def __iter__(self_): return iter(())      # real code does: for _ in stream: pass
            def get_final_message(self_):
                r = types.SimpleNamespace()
                r.content=[_ToolBlk("submit_story_plan", STORY_PLAN)]
                r.usage=_Usage(); r.stop_reason="tool_use"
                return r
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return FakeStream()

class FakeClient:
    def __init__(self, *a, **k): self.messages = FakeMessages()

anthropic.Anthropic = FakeClient
import generate_newsletter_uat as U
U.client = None

raw        = json.loads((REPO/"uat/fixtures/raw_content.json").read_text(encoding="utf-8"))
game_state = json.loads((REPO/"uat/fixtures/game_state.json").read_text(encoding="utf-8"))
client     = FakeClient()

print("="*70); print("PASS 1"); print("="*70)
plan_json = U.run_pass1(raw, [], client, game_state)

p1  = CAPTURED["pass1"]
sysblocks = p1["system"]
systext = "".join(b["text"] if isinstance(b,dict) else str(b) for b in sysblocks)
print(f"  system prompt: {len(systext):,} chars")
assert _has_meme_index(systext), "library index NOT in Pass 1 system prompt"
assert "subject_abandons_sensible_for_funky" in systext
assert "TEST:" in systext
print("  ✓ selector index present in Pass 1 system prompt")
n_eng = systext.count("\n## ")
print(f"  ✓ {n_eng} engines listed")

tools = p1["tools"]
props = tools[0]["input_schema"]["properties"]["lead_story"]["properties"]
assert "meme_template" in props and "meme_subject" in props, list(props)
print("  ✓ tool schema exposes meme_template + meme_subject")

print()
print("="*70); print("PASS 2"); print("="*70)
_ = U.run_pass2(plan_json, client, game_state, [])
p2 = CAPTURED["pass2"]
um = p2["messages"][0]["content"]
print(f"  user message: {len(um):,} chars")
assert "SELECTED MEME TEMPLATES" in um, "spec block NOT injected into Pass 2"
print("  ✓ spec block injected")

rendered = [l.split()[1] for l in um.splitlines() if l.startswith("### ")]
print(f"  ✓ templates specced: {rendered}")
assert rendered == ["distracted-boyfriend","epic-handshake"], rendered
assert "### drake" not in um, "unchosen template leaked into Pass 2"
print("  ✓ unchosen templates absent (no ### drake section)")
# distracted-boyfriend carries its subject in a BOX, epic-handshake in the
# COPY, so this covers both placement shapes. box:1 (not box:2) because the
# 2026-09-01 render probe showed the middle figure is index 1 — boxes 0 and 2
# are the new temptation and the thing being left behind, the reverse of what
# the library originally claimed.
assert "SUBJECT RULE (box:1)" in um and "SUBJECT RULE (copy)" in um
assert um.count("VALENCE RULE") == 2
print("  ✓ subject + valence rules surfaced for both")

spec_only = um.split("SELECTED MEME TEMPLATES")[1].split("## TODAY'S STORY PLAN")[0]
print(f"  ✓ spec block cost: {len(spec_only):,} chars ~{len(spec_only)/3.7:,.0f} tok "
      f"(full library would be ~20,300)")

p2sys = "".join(b["text"] for b in p2["system"])
# pass2_writer.txt legitimately contains a section titled "SELECTED MEME TEMPLATES
# — FOLLOW THE SPEC", so match on rendered spec CONTENT instead of the phrase.
assert "### distracted-boyfriend" not in p2sys, "rendered spec leaked into CACHED system block"
assert "VALENCE RULE:" not in p2sys, "rendered spec leaked into CACHED system block"
assert "FOLLOW THE SPEC" in p2sys, "pass2_writer.txt spec instructions missing from system prompt"
print("  ✓ static spec INSTRUCTIONS in cached system block (pass2_writer.txt)")
print("  ✓ per-run spec CONTENT only in user message — cache-safe")

print()
print("="*70)
print("DRY RUN PASSED — wiring verified through the real UAT code path, 0 API calls")
print("="*70)
