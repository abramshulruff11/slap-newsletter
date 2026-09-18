"""
Run:  python -X utf8 uat/tests/test_team_match.py

Locks team_match.section_matches_game and its use in highlights.inject_highlights
(grounding brief Workstream A1): a section must not match a game on a bare,
possibly cross-sport nickname substring.

Real failure this reproduces: an Arizona Cardinals (NFL) section matched a
Chicago White Sox @ St. Louis Cardinals (MLB) game on the word "Cardinals",
so the MLB highlight clip landed inside the NFL story.

No API calls, no network (inject_highlights is called with no YOUTUBE_API_KEY,
which no-ops before any fetch).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import team_match  # noqa: E402
import highlights  # noqa: E402


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("team_match.section_matches_game")
print("=" * 66)

cardinals_nfl_section = (
    "Kyler Murray and the Arizona Cardinals opened the season with a debut "
    "win, the Cardinals defense forcing three turnovers."
)
check("NFL Cardinals section does NOT match an MLB White Sox @ Cardinals game",
      team_match.section_matches_game(
          cardinals_nfl_section, "Chicago White Sox", "St. Louis Cardinals"),
      False)

mets_section = (
    "Pete Alonso is now an Orioles first baseman, but Mets fans watched him "
    "take his first at-bat at Citi Field as an opposing player."
)
check("Mets/Orioles section matches Orioles @ Mets (both nicknames present)",
      team_match.section_matches_game(mets_section, "Baltimore Orioles", "New York Mets"),
      True)

full_name_only_section = (
    "The St. Louis Cardinals rallied late to take the series opener on the road."
)
check("full name alone is sufficient even without the other team's nickname",
      team_match.section_matches_game(
          full_name_only_section, "Chicago White Sox", "St. Louis Cardinals"),
      True)

check("bare single nickname alone is never sufficient",
      team_match.section_matches_game("The Cardinals won again.",
                                       "Chicago White Sox", "St. Louis Cardinals"),
      False)

print()
print("=" * 66)
print("highlights._find_target_section (the actual inject_highlights code path)")
print("=" * 66)

body = (
    "<h2>Cardinals Storm Back</h2>"
    "<p>Kyler Murray and the Arizona Cardinals opened the season with a "
    "debut win, the Cardinals defense forcing three turnovers.</p>"
    "<h2>Around the League</h2><p>Other stuff.</p>"
)
heads = list(highlights._H2_RE.finditer(body))
sections = []
for i, m in enumerate(heads):
    end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
    htext = highlights._TAG_RE.sub(" ", m.group(1)).strip().lower()
    sec_text = highlights._TAG_RE.sub(" ", body[m.start():end]).lower()
    sections.append((htext, sec_text, m.start(), end, i))

check("MLB White Sox @ Cardinals game finds NO section in an NFL Cardinals story",
      highlights._find_target_section(sections, "Chicago White Sox", "St. Louis Cardinals", set()),
      None)
check("an NFL Cardinals @ someone game DOES match its own section",
      highlights._find_target_section(sections, "Someone Else", "Arizona Cardinals", set())
      is not None, True)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
