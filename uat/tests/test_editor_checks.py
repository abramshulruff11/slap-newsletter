"""
Run:  python -X utf8 uat/tests/test_editor_checks.py

Locks what the editor pass is allowed to do, after the 2026-09-04 cut.

Checks 2, 6 and 8 only ever inserted a comment for a human to read, and nobody
read them — Check 8 alone wrote 7-40 VERIFY flags an issue. They are gone. The
numbers stay as gaps so references elsewhere (plan_audit.py, CLAUDE.md) keep
pointing at the right check.

This guards three things a future edit could quietly undo:
  1. the removed checks stay removed, in BOTH prompt copies
  2. the checks that fix things, and the ones that act on Python-computed
     flags, stay present
  3. the published file carries no HTML comments, while the archived draft
     still does

No API calls, no network.
"""
from __future__ import annotations

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
print("EDITOR PROMPT — removed checks stay removed")
print("=" * 66)

for path in PROMPTS:
    text = path.read_text(encoding="utf-8")
    label = path.relative_to(REPO).as_posix()
    nums = [int(n) for n in re.findall(r'^CHECK (\d+) —', text, re.M)]
    check(f"{label}: check numbers", nums, [1, 3, 4, 5, 7, 9])

    # The flag-only checks, by the text that made each one produce output.
    check(f"{label}: no VERIFY STAT flag", "VERIFY STAT" in text, False)
    check(f"{label}: no VERIFY TITLE CLAIM flag", "VERIFY TITLE CLAIM" in text, False)
    check(f"{label}: no CONFERENCE/DIVISION flag",
          "VERIFY CONFERENCE/DIVISION" in text, False)
    check(f"{label}: no HYPERBOLE flag", "EDITOR FLAG: HYPERBOLE" in text, False)
    check(f"{label}: no TWEET URL flag", "EDITOR FLAG: TWEET URL" in text, False)

    # The checks that actually change the draft, or act on Python-computed flags.
    check(f"{label}: banned phrases kept", "BANNED PHRASES" in text, True)
    check(f"{label}: account-cap flags still acted on",
          "EDITOR FLAG: ACCOUNT CAP" in text, True)
    check(f"{label}: ATL zero-commentary kept", "ZERO COMMENTARY" in text, True)
    check(f"{label}: punching down still auto-removes",
          "EDITOR FLAG: PUNCHING DOWN" in text, True)
    check(f"{label}: ground-truth flags now consumed",
          "FACT FLAG" in text and "COHERENCE FLAG" in text, True)

    # The obituary carve-out named two checks that no longer exist.
    check(f"{label}: obituary rule names no dead check",
          bool(re.search(r'ONLY Check 6|Check 8 \(factual', text)), False)
    check(f"{label}: editor authors no new flags",
          "Do NOT author any new" in text, True)

# The two copies may differ in exactly ONE place: the block that teaches UAT
# about highlight-placeholder, which production has no Pass 1B to produce. Any
# second difference means an edit reached one tree and not the other — the
# prompt-drift problem promote.py exists to stop.
import difflib  # noqa: E402

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
print("OUTPUT FILES — comments in the archive, not in the published issue")
print("=" * 66)

src = (REPO / "generate_newsletter.py").read_text(encoding="utf-8")
check("substack output strips comments",
      bool(re.search(r"substack_html = re\.sub\(r'<!--\.\*\?-->'", src)), True)
check("draft output does NOT strip comments",
      "DRAFT_TEMPLATE.format(content=body)" in src, True)

# The strip itself, on a body shaped like a real one.
body = (
    '<h1>Lead</h1>\n<p>Prose.</p>\n'
    '<blockquote class="tweet"><strong>@a</strong><br>t<br>'
    '<a href="https://twitter.com/a/status/1">View tweet</a></blockquote>\n'
    '<!-- EDITOR FLAG: ACCOUNT CAP — @a appears 3x. -->\n'
    '<!-- FACT FLAG [HIGH]: series score "3-2" may not match\ngame_state. -->\n'
    '<p>More prose.</p>'
)
stripped = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)
check("no comment survives the strip", "<!--" in stripped, False)
check("multi-line comment removed too", "FACT FLAG" in stripped, False)
check("prose survives", stripped.count("<p>"), 2)
check("tweet survives", 'class="tweet"' in stripped, True)
check("tweet href untouched", "twitter.com/a/status/1" in stripped, True)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
