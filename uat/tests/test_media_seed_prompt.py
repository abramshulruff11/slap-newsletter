"""
Run:  python -X utf8 uat/tests/test_media_seed_prompt.py

Locks the Pass 1 media-seeding instructions to the floor plan_audit.py enforces.

SLA-76 (2026-09-23): 20 of 22 issues from 09-02 to 09-23 seeded fewer than
MIN_MEME_SEEDS memes, and every seeded meme rendered — the loss was entirely in
Pass 1's plan. The prompt stated the floor ("at least 3 stories with a
meme_concept") and, thirty lines later, a "70% GIFs, 30% memes" balance with
"most stories should ... leave meme_concept empty". With five stories that
ratio is 1-2 memes, which is what shipped. GIFs had no competing instruction
and hit their floor every day.

This guards four things, in BOTH prompt copies:
  1. the floors the prompt states match MIN_GIF_SEEDS / MIN_MEME_SEEDS
  2. no percentage split between GIFs and memes comes back (any ratio sits
     below the floor on a four- or five-story day)
  3. no sentence tells Pass 1 to default meme_concept to empty
  4. Pass 1 knows the writer's meme ceiling, and that ceiling is never below
     the floor -- otherwise "seed more memes" becomes "ship a meme page"

The subject gate is deliberately NOT touched: it is the one legitimate reason
to fall short, and "reported, never fabricated" still holds.

No API calls, no network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import plan_audit  # noqa: E402

PROMPTS = [REPO / "prompts" / "pass1_story_selector.txt",
           REPO / "uat" / "prompts" / "pass1_story_selector.txt"]
WRITERS = [REPO / "prompts" / "pass2_writer.txt",
           REPO / "uat" / "prompts" / "pass2_writer.txt"]


def _writer_ceiling(path: Path) -> int | None:
    """Upper bound of pass2_writer.txt's 'Max 2-3 generated memes per issue'."""
    m = re.search(r"Max (\d+)(?:-(\d+))? generated memes per issue",
                  path.read_text(encoding="utf-8"))
    return int(m.group(2) or m.group(1)) if m else None


_ceilings = {_writer_ceiling(p) for p in WRITERS}
if len(_ceilings) != 1 or None in _ceilings:
    raise AssertionError(f"writer meme ceiling unreadable or differs: {_ceilings}")
WRITER_CEILING = _ceilings.pop()


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("PASS 1 MEDIA SEEDING — one consistent instruction on meme count")
print("=" * 66)

for path in PROMPTS:
    text = path.read_text(encoding="utf-8")
    label = path.relative_to(REPO).as_posix()
    flat = re.sub(r"\s+", " ", text)

    m = re.search(r"at least (\d+) stories with a gif_concept", flat)
    check(f"{label}: GIF floor stated", int(m.group(1)) if m else None,
          plan_audit.MIN_GIF_SEEDS)
    m = re.search(r"at least (\d+) stories with a meme_concept", flat)
    check(f"{label}: meme floor stated", int(m.group(1)) if m else None,
          plan_audit.MIN_MEME_SEEDS)

    ratios = re.findall(r"\d+\s*% (?:GIFs|memes)", flat, re.I)
    check(f"{label}: no GIF/meme percentage split", ratios, [])

    defaults_empty = re.findall(
        r"[^.]*(?:most|usually|by default)[^.]*leave meme_concept empty[^.]*\.",
        flat, re.I)
    check(f"{label}: no 'leave meme_concept empty' default", defaults_empty, [])

    check(f"{label}: subject gate still present",
          "THE SUBJECT REQUIREMENT IS A GATE" in text, True)

    # Pass 2 follows the plan one-for-one (every seed rendered, 09-02 -> 09-23),
    # so the writer's "max N memes" does not cap an over-seeded plan in
    # practice. Pass 1 has to know the ceiling itself, and it has to be the
    # writer's number.
    m = re.search(r"writer caps an issue at (\d+) memes", flat)
    check(f"{label}: states the writer's meme ceiling",
          int(m.group(1)) if m else None, WRITER_CEILING)

print()
print("-" * 66)
print("PASS 2 MEME CEILING — never below the Pass 1 floor")
print("-" * 66)
check("writer ceiling >= MIN_MEME_SEEDS",
      WRITER_CEILING >= plan_audit.MIN_MEME_SEEDS, True)

print("\nall checks passed")
