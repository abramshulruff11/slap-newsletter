"""
Box scores as monospace TEXT, for Substack (SLA-17).

Why this exists
---------------
Substack has no table. Not in the editor, not via the API: a ProseMirror
`table` node is stored happily (draft_body is an unvalidated blob) and then
CRASHES the editor on load -- blank page, "Something has gone wrong". Verified
against the live API on 2026-09-19, draft 216481923. See substack_poc/table_probe.py.

Images were the fallback, and they are what the ticket complains about: an
image is rasterized at render time, so it cannot follow the reader's theme.
`box_score/build_box_score.py` hardcodes `background:#fff; color:#000`, which
in dark mode is a white slab.

`code_block` IS a real Substack block type (it survived the same probe AND is
in Substack's own editor docs), it renders monospace, and it inherits the
reader's theme. So: render the agate as fixed-width text and ship that.

The width problem
-----------------
A code block does not wrap -- it scrolls sideways. On a phone that is the whole
ballgame. Measured budget below; anything wider gets columns cut, in a declared
priority order, the same way nfl_standings.py trims by breakpoint. Columns are
always cut from the BOTTOM of the priority list, so the reader loses the least
important stat first, never a random one.

Natural widths on real data (2026-09-19 slate): MLB batting is the worst case
at 8 columns / 44 chars. Football is only 5 columns (name + 4 stats, per
_FB_GROUP_KEYS in fetch_sports_data.py) and fits untrimmed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# Characters that fit in a Substack code block before it scrolls sideways.
# Substack's monospace runs ~7.8px/char and the block's padding costs ~40px,
# so the requirement is 7.8*N + 40 <= the phone's content width. A 375px phone
# after page margins gives ~343px, which puts the ceiling at 38 characters.
#
# Measured against the fixture slate, 38 is also where the stat columns stop
# improving for a while: 34 keeps AB/R/H/RBI/K, 36 is inconsistent, 38 adds BB
# on every game, and the next gain (AVG) is not until 42 -- well past the
# phone. So 38 buys a real column at no cost to the narrowest reader.
#
# This does NOT fix desktop, where the block is narrower than the content
# column. A code block does not reflow, so one fixed width cannot suit both;
# the width is set by the smallest device that has to work.
MOBILE_BUDGET = 38

# Gap between columns. Two spaces reads as a column break without the width
# cost of a pipe-and-padding border.
GUTTER = "  "

# Per-category column order, most important FIRST. Trimming drops from the end.
# The leading label column (batter/pitcher/player name) is never dropped.
STAT_PRIORITY: Dict[str, List[str]] = {
    "batting":   ["AB", "R", "H", "RBI", "K", "BB", "AVG"],
    "pitching":  ["IP", "H", "ER", "K", "BB", "ERA"],
    "passing":   ["C/ATT", "YDS", "TD", "INT"],
    "rushing":   ["CAR", "YDS", "TD", "LONG"],
    "receiving": ["REC", "YDS", "TD", "LONG"],
}


# Width of the label column. Measured against the fixture slate, holding the
# stat columns constant: 14 -> 12 truncated names, 15 -> 6, 16 -> 3, and 17 ->
# 0 but the K column starts getting trimmed off some games to pay for it.
# Trading a real stat for a longer surname is the wrong way round, so 16.
NAME_LIMIT = 16


def abbreviate_name(name: str, pos: str = "", limit: int = NAME_LIMIT) -> str:
    """'D. Schneemann' + 'CF' -> 'D.Schneemann CF', trimmed to fit.

    ESPN already gives us 'F. Last'. We drop the space after the initial (free
    character, still readable) and truncate the surname before ever dropping
    the position, which is what tells a reader who batted where.
    """
    n = (name or "?").replace(". ", ".")
    p = (pos or "").strip()
    room = limit - (len(p) + 1 if p else 0)
    if room < 3:
        room, p = limit, ""
    if len(n) > room:
        n = n[: max(1, room - 1)] + "…"
    return f"{n} {p}".strip() if p else n


def fit_columns(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    budget: int = MOBILE_BUDGET,
) -> tuple[List[str], List[List[str]], List[str]]:
    """Drop trailing columns until the widest line fits `budget`.

    Returns (headers, rows, dropped). Column 0 is the label and is never
    dropped -- a stat line with no name is not a box score.
    """
    hdrs = list(headers)
    body = [list(r) for r in rows]
    dropped: List[str] = []

    def width(h, b) -> int:
        if not h:
            return 0
        widths = [
            max(len(str(h[i])), *(len(str(r[i])) for r in b)) if b else len(str(h[i]))
            for i in range(len(h))
        ]
        return sum(widths) + len(GUTTER) * (len(widths) - 1)

    while len(hdrs) > 2 and width(hdrs, body) > budget:
        dropped.append(str(hdrs[-1]))
        hdrs = hdrs[:-1]
        body = [r[:-1] for r in body]

    return hdrs, body, dropped


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    budget: int = MOBILE_BUDGET,
) -> str:
    """Fixed-width text table: label column left, stats right-aligned."""
    hdrs, body, _ = fit_columns(headers, rows, budget)
    if not hdrs:
        return ""
    widths = [
        max(len(str(hdrs[i])), *(len(str(r[i])) for r in body)) if body else len(str(hdrs[i]))
        for i in range(len(hdrs))
    ]

    def line(cells) -> str:
        out = [str(cells[0]).ljust(widths[0])]
        out += [str(c).rjust(widths[i + 1]) for i, c in enumerate(cells[1:])]
        return GUTTER.join(out).rstrip()

    parts = [line(hdrs), "-" * min(sum(widths) + len(GUTTER) * (len(widths) - 1), budget)]
    parts += [line(r) for r in body]
    return "\n".join(parts)


# Below this many active batters a table is not worth collapsing -- a
# two-line box score reads as broken rather than tight.
MIN_ROWS_BEFORE_ROLLUP = 3

# Categories where a quiet player can be rolled into a footnote, and the test
# for "quiet". Relievers who faced two batters are not the story of the game
# any more than an 0-for-4 is -- the fixture slate averages 4.2 pitchers a
# side, most of them fractional innings.
ROLLUP_CATEGORIES = {"batting", "pitching"}


def _as_num(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def did_something(player: Dict, category: str = "batting") -> bool:
    """Did this player do anything a reader would look for?

    A 0-for-4 is real and a purist box score prints it, but across a 15-game
    slate those lines are most of the height and none of the news. Same for a
    reliever who recorded one out. Neither is dropped -- both roll into a
    footnote, which is the agate convention and keeps the box score complete.
    """
    st = player.get("stats") or {}
    if category == "pitching":
        # A full inning, or any run allowed, or a strikeout: that is a line
        # worth its own row. One-out cameos are not.
        return (_as_num(st.get("IP")) >= 1.0
                or _as_num(st.get("ER")) > 0
                or _as_num(st.get("K")) >= 2)
    return any(_as_num(st.get(k)) > 0 for k in ("H", "R", "RBI", "BB"))


def wrap_to(text: str, budget: int, indent: str = "  ") -> List[str]:
    """Hard-wrap a footnote. A code block does not wrap, so we must."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        # NB: no .strip() on the trial -- it would eat the hanging indent on
        # every continuation line after the first word was appended to it.
        trial = f"{cur} {w}" if cur else w
        if len(trial) > budget and cur:
            lines.append(cur.rstrip())
            cur = indent + w
        else:
            cur = trial
    if cur:
        lines.append(cur.rstrip())
    return lines


def _surname(name: str) -> str:
    """'D. Schneemann' -> 'Schneemann'. The footnote has no room for initials
    or positions -- those cost a line per two players at this width."""
    return (name or "?").split()[-1]


def rollup_line(players: Sequence[Dict], budget: int,
                category: str = "batting") -> List[str]:
    """'Also batted: Hoskins 0-4, Bazzana 0-4', wrapped to the budget."""
    if not players:
        return []
    bits = []
    for p in players:
        st = p.get("stats") or {}
        if category == "pitching":
            bits.append(f'{_surname(p.get("name", "?"))} {st.get("IP", "0")}ip')
        else:
            bits.append(f'{_surname(p.get("name", "?"))} '
                        f'{st.get("H", "0")}-{st.get("AB", "0")}')
    label = "Also pitched: " if category == "pitching" else "Also batted: "
    return wrap_to(label + ", ".join(bits), budget)


def render_side(side: Dict, category: str, budget: int = MOBILE_BUDGET,
                limit: Optional[int] = None) -> str:
    """One team's lines for one stat category (batting, passing, ...)."""
    players = side.get(category) or []
    if limit:
        players = players[:limit]
    if not players:
        return ""

    # Quiet batters become a footnote instead of a row each.
    quiet: List[Dict] = []
    if category in ROLLUP_CATEGORIES:
        active = [p for p in players if did_something(p, category)]
        if len(active) >= MIN_ROWS_BEFORE_ROLLUP:
            quiet = [p for p in players if not did_something(p, category)]
            players = active

    order = STAT_PRIORITY.get(category, [])
    present = [c for c in order if any(c in (p.get("stats") or {}) for p in players)]
    if not present:
        return ""

    # The label column header names BOTH the team and what the rows are --
    # 'CLE' over batting and 'CLE' over pitching is unreadable, and the image
    # renderer had the same problem until _STAT_TABLE_LABELS fixed it.
    headers = [f'{side.get("team", "")} {category.title()}'.strip()] + present
    rows = []
    for p in players:
        st = p.get("stats") or {}
        rows.append(
            [abbreviate_name(p.get("name", "?"), p.get("pos", ""))]
            + [str(st.get(c, "-")) for c in present]
        )
    table = render_table(headers, rows, budget)
    extra = rollup_line(quiet, budget, category)
    return "\n".join([table] + extra) if extra else table


def game_header(game: Dict, budget: int = MOBILE_BUDGET) -> str:
    """'Cleveland Guardians 3 @ Detroit Tigers 1', or the abbreviated form.

    Degrades the same way the columns do: full names when they fit, ESPN's own
    abbreviations when they don't. A code block does not wrap, so a header over
    budget would scroll the whole block sideways. Abbreviations come from the
    feed (home_abbr/away_abbr) rather than a city->nickname table, which would
    be one more hand-kept mapping to drift -- and 'Sox' is ambiguous anyway.
    """
    aw_s, hm_s = game.get("away_score", ""), game.get("home_score", "")
    full = f'{game.get("away_team", "?")} {aw_s} @ {game.get("home_team", "?")} {hm_s}'.strip()
    if len(full) <= budget:
        return full
    return f'{game.get("away_abbr") or game.get("away_team", "?")} {aw_s} @ ' \
           f'{game.get("home_abbr") or game.get("home_team", "?")} {hm_s}'.strip()


def render_game(game: Dict, categories: Sequence[str],
                budget: int = MOBILE_BUDGET, limit: Optional[int] = None) -> str:
    """A full game: score header, then each side's categories."""
    box = game.get("box_score") or {}
    # No rule under the header: the code block draws its own border, so an
    # '=====' line is redundant chrome costing one line per game.
    chunks = [game_header(game, budget)]
    for key in ("away", "home"):
        side = box.get(key) or {}
        for cat in categories:
            block = render_side(side, cat, budget, limit)
            if block:
                chunks.append("")
                chunks.append(block)
    return "\n".join(chunks)


def code_block_node(text: str) -> Dict:
    """Wrap rendered text in the ProseMirror node Substack actually accepts."""
    return {"type": "code_block", "content": [{"type": "text", "text": text}]}


def widest_line(text: str) -> int:
    return max((len(l) for l in text.splitlines()), default=0)


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Render box scores as monospace text")
    ap.add_argument("--game-state", default="uat/fixtures/game_state.json")
    ap.add_argument("--sport", default="mlb")
    ap.add_argument("--budget", type=int, default=MOBILE_BUDGET)
    ap.add_argument("--games", type=int, default=2)
    a = ap.parse_args()

    with open(a.game_state, encoding="utf-8") as f:
        gs = json.load(f)

    cats = (["batting", "pitching"] if a.sport == "mlb"
            else ["passing", "rushing", "receiving"])
    shown = 0
    for g in gs.get("sports", {}).get(a.sport, {}).get("yesterday_games", []):
        if not g.get("box_score"):
            continue
        out = render_game(g, cats, a.budget, limit=9)
        print(out)
        print(f"\n[widest line: {widest_line(out)} chars, budget {a.budget}]\n")
        shown += 1
        if shown >= a.games:
            break
    if not shown:
        print(f"no {a.sport} box scores in {a.game_state}")
