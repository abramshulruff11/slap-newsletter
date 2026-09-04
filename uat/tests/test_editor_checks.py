"""
Run:  python -X utf8 uat/tests/test_editor_checks.py

Locks what the editor pass is allowed to do.

2026-09-04: Checks 2, 6 and 8 were removed because all three only inserted an
HTML comment for a human to read, and nobody reads them. Check 8 was RESTORED
the same day. Removing it threw away factual verification, which is the most
valuable thing this pass does — the problem was never the check, it was that
finding a risky claim and writing a comment about it leaves the claim in the
newsletter. Check 8 now fixes or cuts.

This guards four things a future edit could quietly undo:
  1. the flag-only checks (2, 6) stay removed, in BOTH prompt copies
  2. Check 8 stays present, and stays fix-or-cut rather than flag
  3. the ground truth actually reaches the editor, so Check 8 can honour the
     "a claim the ground truth confirms is sourced" rule it promises
  4. the published file carries no HTML comments, while the archived draft
     still does

No API calls, no network.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PROMPTS = [REPO / "prompts" / "editor_prompt.txt",
           REPO / "uat" / "prompts" / "editor_prompt.txt"]


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("EDITOR PROMPT — what the editor may and may not do")
print("=" * 66)

for path in PROMPTS:
    text = path.read_text(encoding="utf-8")
    label = path.relative_to(REPO).as_posix()
    nums = [int(n) for n in re.findall(r'^CHECK (\d+) —', text, re.M)]
    check(f"{label}: check numbers", nums, [1, 3, 4, 5, 7, 8, 9])

    # Nothing writes a comment for a human any more.
    check(f"{label}: no VERIFY flag format", "EDITOR FLAG: VERIFY" in text, False)
    check(f"{label}: no HYPERBOLE flag", "EDITOR FLAG: HYPERBOLE" in text, False)
    check(f"{label}: no TWEET URL flag", "EDITOR FLAG: TWEET URL" in text, False)

    # Check 8 is present and ACTS. Deleting it, or turning it back into an
    # annotation, is the regression this block exists to catch.
    check(f"{label}: check 8 present and fix-or-cut",
          bool(re.search(r'^CHECK 8 — UNSOURCED FACTUAL CLAIMS \(fix or cut', text, re.M)),
          True)
    for cat in ("CONFERENCE / DIVISION PLACEMENT", "TITLE AND CHAMPIONSHIP CLAIMS",
                "HISTORICAL FIRSTS", "SPECIFIC PERFORMANCE NUMBERS"):
        check(f"{label}: check 8 covers {cat.lower()}", cat in text, True)
    check(f"{label}: check 8 spares tweet-sourced numbers",
          "SAME section" in text and "LEAVE IT" in text, True)
    check(f"{label}: check 8 names the ground truth as a source",
          "GROUND TRUTH block" in text, True)

    # The checks that change the draft, or act on Python-computed flags.
    check(f"{label}: banned phrases kept", "BANNED PHRASES" in text, True)
    check(f"{label}: account-cap flags still acted on",
          "EDITOR FLAG: ACCOUNT CAP" in text, True)
    check(f"{label}: ATL zero-commentary kept", "ZERO COMMENTARY" in text, True)
    check(f"{label}: punching down still auto-removes",
          "EDITOR FLAG: PUNCHING DOWN" in text, True)
    check(f"{label}: ground-truth flags consumed",
          "FACT FLAG" in text and "COHERENCE FLAG" in text, True)
    check(f"{label}: obituary rule names no dead check",
          bool(re.search(r'ONLY Check 6', text)), False)
    check(f"{label}: editor authors no new flags",
          "Do NOT author any new" in text, True)

# The two copies may differ in exactly ONE place: the block that teaches UAT
# about highlight-placeholder, which production has no Pass 1B to produce. Any
# second difference means an edit reached one tree and not the other — the
# prompt-drift problem promote.py exists to stop.
prod, uat = (p.read_text(encoding="utf-8").split("\n") for p in PROMPTS)
diffs = [op for op in difflib.SequenceMatcher(None, prod, uat).get_opcodes()
         if op[0] != "equal"]
check("exactly one divergent block between the two copies", len(diffs), 1)
tag, _i1, _i2, j1, j2 = diffs[0]
check("that block is an insertion into the UAT copy", tag, "insert")
check("and it is the highlight-placeholder rules",
      "highlight-placeholder" in "\n".join(uat[j1:j2]), True)

print()
print("=" * 66)
print("GROUND TRUTH REACHES THE EDITOR — Check 8's second source")
print("=" * 66)

# Check 8 promises that a claim the ground truth confirms is sourced. That is
# only true if the ground truth is in the editor's message. Until 2026-09-04
# it was not: the editor could compare a number only against nearby tweets.
rc = (REPO / "runner_common.py").read_text(encoding="utf-8")
body = rc.split("def run_pass6")[1]
signature = body[:body.index("->")]
check("run_pass6 accepts game_state", "game_state" in signature, True)
check("the summary is built for the editor",
      "format_game_state_summary(game_state or {})" in body, True)
check("it rides in the user message, not the cached system block",
      body.index("ground_truth") < body.index('"role": "user"')
      and "cache_control" not in body[body.index("ground_truth"):body.index('"role": "user"')],
      True)
check("a missing ground truth is reported, not silent",
      "no ground truth available" in body, True)
for caller, path in (("prod", "generate_newsletter.py"), ("uat", "uat/run_uat.py")):
    txt = (REPO / path).read_text(encoding="utf-8")
    check(f"{caller} passes game_state to Pass 6",
          bool(re.search(r'run_pass6\([^)]*game_state', txt)), True)

print()
print("=" * 66)
print("OUTPUT FILES — comments in the archive, not in the published issue")
print("=" * 66)

src = (REPO / "generate_newsletter.py").read_text(encoding="utf-8")
check("substack output strips comments",
      bool(re.search(r"substack_html = re\.sub\(r'<!--\.\*\?-->'", src)), True)
check("draft output does NOT strip comments",
      "DRAFT_TEMPLATE.format(content=body)" in src, True)

body_html = (
    '<h1>Lead</h1>\n<p>Prose.</p>\n'
    '<blockquote class="tweet"><strong>@a</strong><br>t<br>'
    '<a href="https://twitter.com/a/status/1">View tweet</a></blockquote>\n'
    '<!-- EDITOR FLAG: ACCOUNT CAP — @a appears 3x. -->\n'
    '<!-- FACT FLAG [HIGH]: series score "3-2" may not match\ngame_state. -->\n'
    '<p>More prose.</p>'
)
stripped = re.sub(r'<!--.*?-->', '', body_html, flags=re.DOTALL)
check("no comment survives the strip", "<!--" in stripped, False)
check("multi-line comment removed too", "FACT FLAG" in stripped, False)
check("prose survives", stripped.count("<p>"), 2)
check("tweet survives", 'class="tweet"' in stripped, True)
check("tweet href untouched", "twitter.com/a/status/1" in stripped, True)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
