"""
Run:  python -X utf8 uat/tests/test_text_box_score.py

Locks box_score/text_box_score.py -- box scores rendered as monospace TEXT for
Substack (SLA-17).

Why text and not a table: Substack has no table. A ProseMirror `table` node is
STORED without complaint (draft_body is an unvalidated blob, so a read-back
proves nothing) and then crashes the editor on load -- blank page, "Something
has gone wrong". Verified against the live API 2026-09-19, draft 216481923.
`code_block` is a real Substack block type, renders monospace, and inherits the
reader's theme, which is the thing an image can never do.

The whole risk of this approach is WIDTH. A code block does not wrap, it
scrolls sideways, so one line over budget drags the entire block off-screen on
a phone. Every check below is ultimately about that.

The trimming order is declared (STAT_PRIORITY) rather than incidental: columns
are dropped from the BOTTOM so the reader loses AVG before RBI, never a random
column. That is the same discipline nfl_standings.py uses for its breakpoints.

No API calls, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "box_score"))

import text_box_score as tbs  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


# ---------------------------------------------------------------- name fitting
print("\nabbreviate_name")
check(tbs.abbreviate_name("D. Schneemann", "CF") == "D.Schneemann CF",
      "a real long-ish name survives intact at NAME_LIMIT=16")
check(tbs.NAME_LIMIT == 16,
      "NAME_LIMIT is 16: widest that keeps the K column on every fixture game")
check(tbs.abbreviate_name("A. Verylongsurnamehere", "1B").endswith("1B"),
      "position survives even when the surname must be cut")
check(len(tbs.abbreviate_name("A. Verylongsurnamehere", "1B")) <= tbs.NAME_LIMIT,
      "long names are truncated to NAME_LIMIT")
check("…" in tbs.abbreviate_name("A. Verylongsurnamehere", "1B"),
      "truncation is marked with an ellipsis, not silent")
check(tbs.abbreviate_name("", "") == "?",
      "a missing name degrades to '?' rather than crashing")

# ------------------------------------------------------------ column trimming
print("\nfit_columns")
hdrs = ["Batter", "AB", "R", "H", "RBI", "BB", "K", "AVG"]
rows = [["D.Schneemann CF", "3", "1", "1", "1", "1", "1", ".246"]]

kept, body, dropped = tbs.fit_columns(hdrs, rows, budget=34)
check(len(kept) < len(hdrs), "an over-budget table loses columns")
check(dropped and dropped[0] == "AVG",
      "the FIRST column dropped is the last in priority order (AVG)")
check(kept[0] == "Batter", "the label column is never dropped")
check(all(len(r) == len(kept) for r in body),
      "rows stay the same arity as the headers after trimming")

wide_kept, _, wide_dropped = tbs.fit_columns(hdrs, rows, budget=200)
check(wide_dropped == [] and len(wide_kept) == len(hdrs),
      "nothing is dropped when the budget is generous")

tiny_kept, _, _ = tbs.fit_columns(hdrs, rows, budget=1)
check(len(tiny_kept) >= 2,
      "an impossible budget still leaves a label and one stat, never zero")

# --------------------------------------------------------------- table render
print("\nrender_table")
table = tbs.render_table(["CLE Batting", "AB", "R"],
                         [["D.Schneemann CF", "3", "1"], ["J.Ramirez 3B", "4", "0"]])
lines = table.splitlines()
check(lines[0].startswith("CLE Batting"), "header row comes first")
check(set(lines[1]) == {"-"}, "a rule separates header from body")
check(lines[2].startswith("D.Schneemann CF"), "label column is left-aligned")
check(lines[2].endswith("1"), "stat columns are right-aligned to the edge")
check(len({len(l.rstrip()) for l in lines[2:]}) >= 1, "body rows render")

# ------------------------------------------------------------- real game data
print("\nreal fixture data (MLB)")
with open(REPO / "uat/fixtures/game_state.json", encoding="utf-8") as f:
    gs = json.load(f)

games = [g for g in gs["sports"]["mlb"]["yesterday_games"] if g.get("box_score")]
check(len(games) > 0, "fixture actually contains MLB box scores to test against")

rendered = [tbs.render_game(g, ["batting", "pitching"], limit=9) for g in games]

over = [(g.get("matchup"), tbs.widest_line(r))
        for g, r in zip(games, rendered) if tbs.widest_line(r) > tbs.MOBILE_BUDGET]
check(not over, f"EVERY game fits the {tbs.MOBILE_BUDGET}-char budget (over: {over[:3]})")

check(all("Batting" in r for r in rendered),
      "batting is labelled, so it cannot be confused with pitching")
check(all("Pitching" in r for r in rendered),
      "pitching is labelled too")
check(all(r.count("Batting") == 2 and r.count("Pitching") == 2 for r in rendered),
      "both sides of every game are rendered, not just the away team")

# ------------------------------------------------------------- header fitting
print("\ngame_header")
long_game = {"away_team": "Cleveland Guardians", "home_team": "Detroit Tigers",
             "away_abbr": "CLE", "home_abbr": "DET", "away_score": 3, "home_score": 1}
check(len(tbs.game_header(long_game, budget=34)) <= 34,
      "a long matchup falls back to abbreviations to stay in budget")
check(tbs.game_header(long_game, budget=34) == "CLE 3 @ DET 1",
      "the fallback uses the feed's own abbreviations")
check(tbs.game_header(long_game, budget=200) ==
      "Cleveland Guardians 3 @ Detroit Tigers 1",
      "full names are kept when they fit")

no_abbr = dict(long_game)
no_abbr.pop("away_abbr")
no_abbr.pop("home_abbr")
check("Cleveland Guardians" in tbs.game_header(no_abbr, budget=34),
      "a missing abbreviation degrades to the full name, never to blank")

# -------------------------------------------------------- football dimensions
print("\nfootball (narrower than MLB -- the worst case is MLB batting)")
fb_game = {
    "away_team": "Jacksonville Jaguars", "home_team": "Cincinnati Bengals",
    "away_abbr": "JAX", "home_abbr": "CIN", "away_score": 27, "home_score": 33,
    "box_score": {
        "away": {"team": "JAX", "passing": [
            {"name": "T. Lawrence", "pos": "QB",
             "stats": {"C/ATT": "24/38", "YDS": "284", "TD": "2", "INT": "1"}}]},
        "home": {"team": "CIN", "passing": [
            {"name": "J. Burrow", "pos": "QB",
             "stats": {"C/ATT": "31/45", "YDS": "312", "TD": "3", "INT": "0"}}]},
    },
}
fb = tbs.render_game(fb_game, ["passing", "rushing", "receiving"])
check(tbs.widest_line(fb) <= tbs.MOBILE_BUDGET,
      f"a football game fits the budget ({tbs.widest_line(fb)} chars)")
check("C/ATT" in fb, "football keeps C/ATT, its widest and most important column")
check("INT" in fb, "5 football columns fit untrimmed, unlike MLB's 8")

# -------------------------------------------------------------- node wrapping
print("\ncode_block_node")
node = tbs.code_block_node("x\ny")
check(node["type"] == "code_block", "wraps in the node type Substack accepts")
check(node["content"][0]["text"] == "x\ny", "text is carried verbatim")
check(node["type"] != "table", "explicitly NOT a table node -- that crashes the editor")

# ------------------------------------------------------------------ edge cases
print("\nedge cases")
check(tbs.render_side({"team": "X"}, "batting") == "",
      "a side with no players renders empty, not a header with no rows")
check(tbs.render_side({"team": "X", "batting": [{"name": "A. B", "stats": {}}]},
                      "batting") == "",
      "players with no usable stat columns render empty")
check(tbs.widest_line("") == 0, "widest_line handles empty input")

empty_game = {"away_team": "A", "home_team": "B", "away_score": 0,
              "home_score": 0, "box_score": {}}
check(tbs.render_game(empty_game, ["batting"]).startswith("A 0 @ B 0"),
      "a game with no box score still renders its header rather than crashing")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("All text box score checks passed.")
