"""
Sport-aware team-name matching, shared by highlights.py and (per the grounding
brief) the future B2 fact-sheet game resolver. One copy, imported everywhere a
game needs to be matched against prose — never re-implemented per caller.

The bug this exists to fix: `nickname in text` is a plain substring check
satisfied by either team alone, so an Arizona Cardinals (NFL) section matched
a Chicago White Sox @ St. Louis Cardinals (MLB) game on the word "Cardinals",
and White Sox/Cardinals highlight clips landed inside the NFL story.

Rule: a text matches a game only if BOTH team nicknames appear, or EITHER
team's full name appears — never a single bare nickname on its own. That
already covers every known cross-sport collision (Cardinals, Giants, Rangers,
Kings, Panthers, Jets) without special-casing them, since a lone "Cardinals"
never satisfies "both nicknames" and is not a full name.
"""

from __future__ import annotations

import re
import unicodedata

# Nicknames shared by more than one league. The both-nicknames-or-full-name
# rule below already excludes a bare single-nickname match for every team,
# not just these, so this set is documentation of the known collisions
# rather than a second matching path. Extend by generating from ESPN team
# lists across leagues (see the grounding brief, Workstream A1).
COLLISION_NICKNAMES = {"cardinals", "giants", "rangers", "kings", "panthers", "jets"}

# Nicknames that are two words, so a last-token split would break them.
MULTIWORD_NICKNAMES = {
    "red sox", "white sox", "blue jays", "maple leafs",
    "golden knights", "blue jackets",
}


def norm(s: str) -> str:
    """Lowercase + strip diacritics, matching highlights._norm."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def nickname(full_name: str) -> str:
    """Last word of a team's full name, e.g. 'St. Louis Cardinals' -> 'Cardinals',
    with multiword nicknames ('Red Sox') kept together."""
    parts = (full_name or "").split()
    if len(parts) >= 2 and " ".join(parts[-2:]).lower() in MULTIWORD_NICKNAMES:
        return " ".join(parts[-2:])
    return parts[-1] if parts else ""


def _word_hit(text_norm: str, phrase: str) -> bool:
    phrase = norm(phrase)
    if not phrase:
        return False
    return re.search(rf"\b{re.escape(phrase)}\b", text_norm) is not None


def section_matches_game(text: str, away_team: str, home_team: str) -> bool:
    """True if `text` is about the game between away_team and home_team.

    Requires both team nicknames to appear, or either team's full name —
    never a single bare nickname, which is what let a Cardinals/Giants/
    Rangers/Kings/Panthers/Jets nickname cross sports.
    """
    t = norm(text)
    if _word_hit(t, away_team) or _word_hit(t, home_team):
        return True
    an, hn = nickname(away_team), nickname(home_team)
    return _word_hit(t, an) and _word_hit(t, hn)
