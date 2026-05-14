"""
SLAP Newsletter — Claim Validator (Pass 3)

Validates factual claims in the newsletter draft against game_state.json.
Injects FACT FLAG and COHERENCE FLAG HTML comments for Pass 7 (Editor) to resolve.

This is a deterministic Python script — no LLM calls, no API cost.
Called from generate_newsletter.py after Pass 2 (Writer).

Can also be run standalone for testing:
  python claim_validator.py --input newsletter_draft.html
"""

import json
import re
from pathlib import Path

SCRIPT_DIR      = Path(__file__).resolve().parent
GAME_STATE_PATH = SCRIPT_DIR / "game_state.json"


# ---------------------------------------------------------------------------
# Regex patterns for claim extraction
# ---------------------------------------------------------------------------

# "Game 5", "Game Five", "G5" — catches game number references in prose
GAME_NUMBER_RE = re.compile(
    r'\bGame\s+(\d+|one|two|three|four|five|six|seven)\b',
    re.IGNORECASE,
)

WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7,
}

# Series score: "3-2 lead", "leads 3-2", "up 2-1", "tied 2-2", "2-2 series"
SERIES_SCORE_RE = re.compile(
    r'(?:'
    r'\b([0-3])-([0-3])\s*(?:lead|series|advantage|deficit)\b'  # "3-2 lead"
    r'|'
    r'\b(?:leads?|trails?|up|down|tied|even)\s+([0-3])-([0-3])\b'  # "leads 3-2"
    r')',
    re.IGNORECASE,
)

# Elimination language — high-risk claim
ELIMINATION_RE = re.compile(
    r'\belimination\s+game\b'
    r'|\bwin.or.go.home\b'
    r'|\bmust.win\s+game\b'
    r'|\bseason\s+on\s+the\s+line\b'
    r'|\bback\s+against\s+the\s+wall\b',
    re.IGNORECASE,
)

# "Series over / ends / done" — dangerous when series is still live
SERIES_OVER_RE = re.compile(
    r'\bseries\s+(?:is\s+)?over\b'
    r'|\bseries\s+ends?\b'
    r'|\bseries\s+done\b',
    re.IGNORECASE,
)

# Defending champion language — training data may be stale
DEFENDING_CHAMP_RE = re.compile(
    r'\bdefending\s+champ(?:ion)?s?\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def strip_tags(html: str) -> str:
    """Remove all HTML tags, return plain text."""
    return re.sub(r'<[^>]+>', '', html)


def split_into_sections(html: str) -> list[tuple[str, str]]:
    """
    Split HTML at every <h1>/<h2> boundary.
    Returns list of (heading_text, section_html) tuples.
    Content before the first heading is labeled '__preamble__'.
    """
    parts = re.split(r'(?=<h[12][\s>])', html, flags=re.IGNORECASE)
    sections: list[tuple[str, str]] = []
    for part in parts:
        if not part.strip():
            continue
        match = re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, re.IGNORECASE | re.DOTALL)
        if match:
            heading = strip_tags(match.group(1)).strip()
        else:
            heading = "__preamble__"
        sections.append((heading, part))
    return sections


def inject_flag_after_heading(section_html: str, comment: str) -> str:
    """Insert a flag HTML comment immediately after the section's heading tag."""
    heading_end = re.search(r'</h[12]>', section_html, re.IGNORECASE)
    if heading_end:
        pos = heading_end.end()
        return section_html[:pos] + comment + section_html[pos:]
    # No heading found — prepend
    return comment + section_html


# ---------------------------------------------------------------------------
# Game state helpers
# ---------------------------------------------------------------------------

def load_game_state(path: Path = GAME_STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_yesterday_playoff_games(game_state: dict) -> list[dict]:
    """Return all completed playoff games from yesterday across all sports."""
    results = []
    for sport_data in game_state.get("sports", {}).values():
        for game in sport_data.get("yesterday_games", []):
            if game.get("completed") and game.get("playoffs") and game.get("series"):
                results.append(game)
    return results


def extract_game_number(text: str) -> int | None:
    """Pull the first game number mentioned in text. Returns int or None."""
    match = GAME_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return WORD_TO_NUM.get(raw.lower())


# ---------------------------------------------------------------------------
# Section validator
# ---------------------------------------------------------------------------

def validate_section(heading: str, section_html: str, game_state: dict) -> tuple[str, int]:
    """
    Run all checks against a single newsletter section.
    Returns (annotated_html, flag_count).
    """
    if heading == "__preamble__":
        return section_html, 0

    plain_text     = strip_tags(section_html)
    playoff_games  = get_yesterday_playoff_games(game_state)
    flags: list[str] = []

    # ── CHECK 1: "Series over" + future game number in same section ──────────
    # This is the exact pattern that produced the 5/14 bug.
    series_over_match = SERIES_OVER_RE.search(plain_text)
    game_number_match = GAME_NUMBER_RE.search(plain_text)

    if series_over_match and game_number_match:
        # Find a playoff game where series is still live
        live_game = next(
            (g for g in playoff_games if not g["series"].get("series_over", True)),
            None,
        )
        if live_game:
            s = live_game["series"]
            claimed_game = extract_game_number(plain_text)
            flags.append(
                f'\n<!-- COHERENCE FLAG: Section contains "series over" language but also references '
                f'Game {claimed_game}. game_state shows series is ONGOING: {s.get("summary", "see game_state.json")}. '
                f'The series is NOT over — remove "series over" language or correct it. '
                f'Next game: Game {s.get("next_game_number", "?")}. -->'
            )

    # ── CHECK 2: "Elimination game" claim validation ──────────────────────────
    if ELIMINATION_RE.search(plain_text):
        # Was yesterday's game actually an elimination game for any team?
        any_true_elim = any(
            g["series"].get("elimination_game_for_home") or
            g["series"].get("elimination_game_for_away")
            for g in playoff_games
        )

        if playoff_games and not any_true_elim:
            # There are playoff games but none were elimination games
            game = playoff_games[0]
            s    = game["series"]
            flags.append(
                f'\n<!-- FACT FLAG [HIGH]: "Elimination game" language detected '
                f'but game_state shows this was NOT an elimination game. '
                f'Series state: {s.get("summary", "unknown")} '
                f'(home wins: {s.get("home_wins", "?")}, away wins: {s.get("away_wins", "?")}, '
                f'series over: {s.get("series_over", "?")}). '
                f'Remove or rewrite this claim. -->'
            )
        elif not playoff_games and ELIMINATION_RE.search(plain_text):
            # Elimination claim but no playoff games in game_state at all
            flags.append(
                f'\n<!-- FACT FLAG [MEDIUM]: "Elimination game" language detected '
                f'but game_state has no playoff game data to verify against. '
                f'Manually verify before publishing. -->'
            )

    # ── CHECK 3: Defending champion language ─────────────────────────────────
    if DEFENDING_CHAMP_RE.search(plain_text):
        flags.append(
            f'\n<!-- FACT FLAG [LOW]: "Defending champion" language detected. '
            f'Training data may be stale — verify current title holder manually '
            f'before publishing. -->'
        )

    # ── CHECK 4: Series score claim vs game_state ─────────────────────────────
    score_matches = SERIES_SCORE_RE.findall(plain_text)
    for match in score_matches:
        # match groups: (g1, g2, g3, g4) from the two alternation branches
        if match[0] and match[1]:
            a, b = int(match[0]), int(match[1])
        elif match[2] and match[3]:
            a, b = int(match[2]), int(match[3])
        else:
            continue

        claimed = tuple(sorted([a, b], reverse=True))  # (higher, lower)

        for game in playoff_games:
            s = game["series"]
            actual = tuple(sorted([s.get("home_wins", 0), s.get("away_wins", 0)], reverse=True))
            if claimed != actual:
                flags.append(
                    f'\n<!-- FACT FLAG [HIGH]: Series score "{a}-{b}" in draft may not match '
                    f'game_state: {s.get("summary", f"{actual[0]}-{actual[1]}")}. '
                    f'Verify and correct. -->'
                )
                break  # one flag per section is enough for score mismatches

    # ── Inject all flags ──────────────────────────────────────────────────────
    if not flags:
        return section_html, 0

    annotated = section_html
    for flag in flags:
        annotated = inject_flag_after_heading(annotated, flag)

    return annotated, len(flags)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_claims(
    html: str,
    game_state_path: Path = GAME_STATE_PATH,
) -> tuple[str, int]:
    """
    Validate factual claims in newsletter HTML against game_state.json.

    Args:
        html: Full newsletter HTML string (from Pass 2 output).
        game_state_path: Path to game_state.json produced by fetch_sports_data.py.

    Returns:
        (annotated_html, total_flag_count)
        annotated_html has FACT FLAG / COHERENCE FLAG HTML comments injected.
        Pass 7 (Editor) resolves all flags before publish.
    """
    print("\n── PASS 3: Claim Validator ─────────────────────────")

    game_state = load_game_state(game_state_path)

    if not game_state:
        print("  ⚠ game_state.json not found — run fetch_sports_data.py first")
        print("    Skipping claim validation (no ground truth data available)")
        return html, 0

    sports_count = len(game_state.get("sports", {}))
    playoff_games = get_yesterday_playoff_games(game_state)
    print(f"  Loaded game_state: {sports_count} sport(s), {len(playoff_games)} playoff game(s) from yesterday")

    sections   = split_into_sections(html)
    parts      = []
    total_flags = 0

    for heading, section_html in sections:
        annotated, count = validate_section(heading, section_html, game_state)
        parts.append(annotated)
        total_flags += count

    if total_flags:
        print(f"  ⚠ {total_flags} flag(s) inserted — Pass 7 (Editor) will resolve")
    else:
        print(f"  ✓ No claim flags raised")

    return "".join(parts), total_flags


# ---------------------------------------------------------------------------
# Standalone runner (for testing)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SLAP Claim Validator — validates newsletter claims against game_state.json"
    )
    parser.add_argument(
        "--input", default="newsletter_draft.html",
        help="Input HTML file (default: newsletter_draft.html)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: overwrites input)",
    )
    args = parser.parse_args()

    input_path  = SCRIPT_DIR / args.input
    output_path = SCRIPT_DIR / (args.output or args.input)

    if not input_path.exists():
        print(f"✗ Input file not found: {input_path}")
        return

    html = input_path.read_text(encoding="utf-8")
    annotated, flag_count = validate_claims(html)
    output_path.write_text(annotated, encoding="utf-8")

    if flag_count:
        print(f"\n⚠ {flag_count} flag(s) written to {output_path.name}")
        print(f"  Search 'FLAG' in the file to find them.")
    else:
        print(f"\n✓ Clean — no flags. Output: {output_path.name}")


if __name__ == "__main__":
    main()
