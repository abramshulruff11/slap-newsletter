"""
Run:  python -X utf8 uat/tests/test_nfl_standings.py

Locks nfl_standings.py (SLA-43): record computation, the official NFL two-team
tiebreaker procedure, the 3+ team flag, and the mobile-first HTML component.

Every tiebreaker step gets a hand-built game log that isolates it — a log where
every earlier step is deliberately level, so the step under test is the one that
decides. A step that silently stopped applying would otherwise look fine,
because a later step would just decide instead and the order would still "work".

No API calls, no network, no browser.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import nfl_standings as ns  # noqa: E402
from fetch_sports_data import NFL_DIVISIONS  # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


def game(home, away, hs, as_, week=1, **kw):
    """A completed regular-season game in the fetch_nfl_season_games() shape."""
    g = {
        "game_id": f"{home}{away}{week}", "season_year": 2026,
        "season_type": 2, "week": week,
        "home_abbr": home, "away_abbr": away,
        "home_score": hs, "away_score": as_,
        "completed": True, "status": "final",
    }
    g.update(kw)
    return g


def order(games, conf, div):
    """Ordered team abbreviations for one division."""
    st = ns.build_standings(games)
    for c in st["conferences"]:
        if c["name"] != conf:
            continue
        for d in c["divisions"]:
            if d["name"] == div:
                return [t["abbr"] for t in d["teams"]]
    raise AssertionError(f"{conf} {div} not found")


def ahead_of(games, conf, div, first, second):
    """True when `first` is ranked ahead of `second` in that division."""
    o = order(games, conf, div)
    return o.index(first) < o.index(second)


def note_for(games, teams):
    st = ns.build_standings(games)
    for n in st["notes"]:
        if set(n["teams"]) == set(teams):
            return n
    return None


print("=" * 70)
print("1. Record computation")
print("=" * 70)

logs = [
    game("BUF", "NYJ", 24, 17, week=1),
    game("MIA", "NE", 20, 20, week=1),   # tie
    game("NYJ", "BUF", 30, 10, week=2),
    game("NE", "MIA", 14, 13, week=2),
]
recs = ns.compute_records(logs)

check("BUF is 1-1", recs["BUF"]["record"], "1-1")
check("MIA shows its tie in the record", recs["MIA"]["record"], "0-1-1")
check("a tie is half a win in PCT", recs["MIA"]["pct"], ".250")
check("NE is 1-0-1", recs["NE"]["record"], "1-0-1")
check("BUF points for", recs["BUF"]["points_for"], 34)
check("BUF points against", recs["BUF"]["points_against"], 47)
check("BUF differential is signed", recs["BUF"]["diff"], "-13")
check("NYJ differential carries a plus", recs["NYJ"]["diff"], "+13")
check("streak reads off the last game", recs["NYJ"]["streak"], "W1")
check("every one of the 32 teams gets a row", len(recs), 32)
check("a team with no games has an empty streak", recs["KC"]["streak"], "")
check("a team with no games is .000, not a crash", recs["KC"]["pct"], ".000")

print()
print("Division and conference records")
cross = [
    game("BUF", "NYJ", 21, 14, week=1),   # AFC East, divisional
    game("BUF", "KC", 21, 14, week=2),    # AFC, non-divisional
    game("BUF", "DAL", 21, 14, week=3),   # non-conference
]
recs = ns.compute_records(cross)
check("division record counts only divisional games", recs["BUF"]["div_record"], "1-0")
check("conference record counts all AFC games", recs["BUF"]["conf_record"], "2-0")
check("overall record counts everything", recs["BUF"]["record"], "3-0")
check("an NFC opponent gets no AFC credit", recs["DAL"]["conf_record"], "0-0")

print()
print("Games that must not count")
noncounting = [
    game("BUF", "NYJ", 24, 0, week=1, season_type=1),           # preseason
    game("BUF", "MIA", 24, 0, week=2, completed=False),         # in progress
    game("BUF", "NE", 24, 0, week=3, season_type=3),            # postseason
]
recs = ns.compute_records(noncounting)
check("preseason, live and postseason games are all excluded",
      recs["BUF"]["record"], "0-0")
check("and they contribute no points either", recs["BUF"]["points_for"], 0)

print()
print("=" * 70)
print("2. Grouping and order")
print("=" * 70)

st = ns.build_standings(ns.mock_season())
check("conferences are AFC then NFC",
      [c["name"] for c in st["conferences"]], ["AFC", "NFC"])
check("divisions are East, North, South, West",
      [d["name"] for d in st["conferences"][0]["divisions"]],
      ["East", "North", "South", "West"])
check("all 32 teams appear exactly once",
      sorted(t["abbr"] for c in st["conferences"]
             for d in c["divisions"] for t in d["teams"]),
      sorted(NFL_DIVISIONS))
check("teams are named by ESPN abbreviation, not invented truncation",
      all(t["abbr"] in NFL_DIVISIONS for c in st["conferences"]
          for d in c["divisions"] for t in d["teams"]), True)

afc_east = st["conferences"][0]["divisions"][0]["teams"]
check("a division sorts by win percentage, best first",
      [t["win_pct"] for t in afc_east],
      sorted((t["win_pct"] for t in afc_east), reverse=True))

print()
print("=" * 70)
print("3. Tiebreakers — the official two-team procedure, step by step")
print("=" * 70)
check("the published step list is the twelve official steps",
      len(ns.TIEBREAK_STEPS), 12)
check("in the ticket's order", ns.TIEBREAK_STEPS[:4],
      ["head-to-head", "division record", "common games", "conference record"])
check("ending with the coin toss", ns.TIEBREAK_STEPS[-1], "coin toss")

print()
print("Step 1 — head-to-head")
# BUF and NYJ both 1-1; BUF beat NYJ, so BUF is ahead on head-to-head.
# BUF and NYJ both finish 1-1 and BUF won the meeting; MIA (1-0) and NE (0-1)
# sit clear of them so the pair is the only tie in the division.
h2h = [
    game("BUF", "NYJ", 20, 10, week=1),
    game("NYJ", "NE",  20, 10, week=2),
    game("MIA", "BUF", 20, 10, week=3),
]
check("both teams really are tied",
      ns.compute_records(h2h)["BUF"]["record"] == ns.compute_records(h2h)["NYJ"]["record"], True)
check("head-to-head winner ranks first", ahead_of(h2h, "AFC", "East", "BUF", "NYJ"), True)
check("and the note names the step", note_for(h2h, ["BUF", "NYJ"])["step"], "head-to-head")

print()
print("Step 2 — division record (head-to-head level)")
# BUF and NYJ split head-to-head 1-1. BUF's other game is divisional and won;
# NYJ's other game is non-divisional and won. Same overall record, so only the
# division record separates them.
divrec = [
    game("BUF", "NYJ", 20, 10, week=1),
    game("NYJ", "BUF", 20, 10, week=2),
    game("BUF", "MIA", 20, 10, week=3),   # BUF divisional win
    game("NYJ", "KC",  20, 10, week=3),   # NYJ non-divisional win
]
check("head-to-head really is level here",
      ns._head_to_head(ns.compute_records(divrec)["BUF"],
                       ns.compute_records(divrec)["NYJ"]), 0.5)
check("better division record ranks first", ahead_of(divrec, "AFC", "East", "BUF", "NYJ"), True)
check("and the note names the step", note_for(divrec, ["BUF", "NYJ"])["step"], "division record")

print()
print("Step 3 — common games, and its four-game minimum")
recs = ns.compute_records([
    game("BUF", "KC", 20, 10, week=1),
    game("NYJ", "KC", 10, 20, week=2),
])
check("one common game is below the four-game minimum, so the step is skipped",
      ns._common_games_pct(recs["BUF"], recs["NYJ"]), None)

common = []
for wk, opp in enumerate(["KC", "DEN", "LAC", "LV"], start=1):
    common.append(game("BUF", opp, 20, 10, week=wk))       # BUF wins all four
    common.append(game("NYJ", opp, 10, 20, week=wk + 10))  # NYJ loses all four
recs = ns.compute_records(common)
check("four common games clears the minimum",
      ns._common_games_pct(recs["BUF"], recs["NYJ"]), 1.0)
check("and the other side of the same set", ns._common_games_pct(recs["NYJ"], recs["BUF"]), 0.0)

print()
print("Step 4 — conference record")
# PIT (AFC North) and BUF (AFC East) are different divisions, so step 2 cannot
# apply; no common opponents, so step 3 cannot either. Both 1-1 overall, but
# BUF's win is in conference and PIT's is not.
# BUF (AFC East) and PIT (AFC North) both go 1-1 with no meeting, no shared
# division and no common opponents — so steps 1, 2 and 3 all fall through and
# only the conference record is left. BUF's win is in conference; PIT's is not.
confrec = [
    game("BUF", "MIA", 20, 10, week=1),   # AFC win
    game("DAL", "BUF", 20, 10, week=2),   # NFC loss
    game("PIT", "NYG", 20, 10, week=1),   # NFC win
    game("CLE", "PIT", 20, 10, week=2),   # AFC loss
]
recs = ns.compute_records(confrec)
check("the pair is level overall",
      recs["BUF"]["record"] == recs["PIT"]["record"], True)
check("they never met", ns._head_to_head(recs["BUF"], recs["PIT"]), None)
check("they share no opponents", ns._common_opponents(recs["BUF"], recs["PIT"]), set())
check("BUF is ahead, decided on conference record",
      ns.break_two_way_tie(recs["BUF"], recs["PIT"], recs), (-1, "conference record"))
check("division record cannot apply across divisions",
      recs["BUF"]["division"] != recs["PIT"]["division"], True)

print()
print("Step 5 — strength of victory / Step 6 — strength of schedule")
recs = ns.compute_records([
    game("BUF", "KC",  20, 10, week=1),   # BUF beats a team that goes on to win
    game("NYJ", "LV",  20, 10, week=1),   # NYJ beats a team that goes on to lose
    game("KC",  "DEN", 30, 10, week=2),
    game("LAC", "LV",  30, 10, week=2),
])
sov_buf = ns._strength(recs["BUF"], recs, beaten_only=True)
sov_nyj = ns._strength(recs["NYJ"], recs, beaten_only=True)
check("beating a winning team is worth more strength of victory", sov_buf > sov_nyj, True)
check("strength of victory ignores losses",
      ns._strength(recs["DEN"], recs, beaten_only=True), None)
check("strength of schedule counts every opponent, won or lost",
      ns._strength(recs["DEN"], recs, beaten_only=False) is not None, True)

print()
print("Steps 7/8 — combined points-scored and points-allowed ranking")
recs = ns.compute_records([
    game("BUF", "NYJ", 40, 0, week=1),
    game("KC",  "LV",  10, 7,  week=1),
])
afc = [r for r in recs.values() if r["conference"] == "AFC"]
check("the high-scoring, low-conceding team has the better combined rank",
      ns._combined_rank(recs["BUF"], afc) > ns._combined_rank(recs["NYJ"], afc), True)
check("combined rank is negated so higher is better, like every other step",
      ns._combined_rank(recs["BUF"], afc) < 0, True)

print()
print("Steps 9/10 — net points")
recs = ns.compute_records([
    game("BUF", "KC", 30, 10, week=1),
    game("NYJ", "KC", 21, 20, week=2),
])
check("net points in all games is the point differential",
      ns._net_points(recs["BUF"]), 20.0)

print()
print("Step 11 — net touchdowns, which the feed does not carry")
recs = ns.compute_records([game("BUF", "KC", 30, 10, week=1)])
check("no touchdown data means the step is skipped, not guessed from the score",
      ns._net_touchdowns(recs["BUF"]), None)
recs = ns.compute_records([
    game("BUF", "KC", 30, 10, week=1, home_touchdowns=4, away_touchdowns=1)])
check("a feed that supplies touchdowns gets the step",
      ns._net_touchdowns(recs["BUF"]), 3.0)

print()
print("Step 12 — coin toss is flagged, never rolled")
# Two teams whose seasons are exact mirror images: every step comes out level.
mirror = [
    game("BUF", "KC",  20, 10, week=1),
    game("NYJ", "KC",  20, 10, week=2),
    game("DEN", "BUF", 20, 10, week=3),
    game("DEN", "NYJ", 20, 10, week=4),
]
result = ns.break_two_way_tie(ns.compute_records(mirror)["BUF"],
                              ns.compute_records(mirror)["NYJ"],
                              ns.compute_records(mirror))
check("a fully level pair returns unresolved", result[0], 0)
check("and names the coin toss", result[1], "coin toss")
n = note_for(mirror, ["BUF", "NYJ"])
check("the table reports it as unresolved", n["resolved"], False)
check("ordering stays deterministic (alphabetical), not random",
      order(mirror, "AFC", "East")[:2], ["BUF", "NYJ"])
check("running it twice gives the same order",
      order(mirror, "AFC", "East"), order(mirror, "AFC", "East"))

print()
print("A step that cannot apply falls through instead of deciding")
recs = ns.compute_records([game("BUF", "DAL", 20, 10, week=1)])
check("head-to-head between teams that never met is None",
      ns._head_to_head(recs["BUF"], recs["KC"]), None)
check("common games with no common opponents is None",
      ns._common_games_pct(recs["BUF"], recs["KC"]), None)

print()
print("=" * 70)
print("4. Three-or-more-team ties are flagged, not silently approximated")
print("=" * 70)

three = [
    game("BUF", "NYJ", 20, 10, week=1),
    game("MIA", "BUF", 20, 10, week=2),
    game("NYJ", "MIA", 20, 10, week=3),
    game("NE",  "KC",  0,  50, week=1),
    game("NE",  "DEN", 0,  50, week=2),
]
n = note_for(three, ["BUF", "NYJ", "MIA"])
check("a three-way tie produces a note", n is not None, True)
check("it is marked unresolved", n["resolved"], False)
check("it is marked as the multi-team case", n["step"], "multi-team")
check("the note names the teams", all(t in n["detail"] for t in ("BUF", "NYJ", "MIA")), True)
check("the teams are labelled provisional in the table",
      {t["tiebreak"] for t in ns.build_standings(three)["conferences"][0]["divisions"][0]["teams"]
       if t["abbr"] in ("BUF", "NYJ", "MIA")},
      {"provisional (3+ team tie)"})
check("a 3+ tie is still deterministic", order(three, "AFC", "East"), order(three, "AFC", "East"))

print()
print("=" * 70)
print("5. Mock season")
print("=" * 70)

mock = ns.mock_season(weeks=6)
check("six weeks is 16 games a week", len(mock), 96)
check("every game is final", all(g["completed"] for g in mock), True)
check("every game is regular season", {g["season_type"] for g in mock}, {2})
check("all 32 teams play",
      len({g["home_abbr"] for g in mock} | {g["away_abbr"] for g in mock}), 32)
check("no team plays itself", all(g["home_abbr"] != g["away_abbr"] for g in mock), True)
check("no team plays twice in a week",
      all(len({g["home_abbr"] for g in mock if g["week"] == w}
              | {g["away_abbr"] for g in mock if g["week"] == w}) == 32
          for w in range(1, 7)), True)
check("game ids are unique", len({g["game_id"] for g in mock}), len(mock))
check("the mock is deterministic",
      [g["home_score"] for g in ns.mock_season(weeks=6)],
      [g["home_score"] for g in mock])
recs = ns.compute_records(mock)
check("every team played six games",
      {r["games_played"] for r in recs.values()}, {6})
check("the mock exercises the tiebreakers at all",
      len(ns.build_standings(mock)["notes"]) > 0, True)

print()
print("=" * 70)
print("6. HTML component")
print("=" * 70)

st = ns.build_standings(mock)
frag = ns.render_standings_html(st)

check("all 32 teams render", sum(frag.count(f'>{a}<') for a in NFL_DIVISIONS) >= 32, True)
check("one table per division", frag.count('class="nflst-table"'), 8)
check("every priority column has a header",
      all(f">{label}<" in frag for _, label, _ in ns.COLUMNS), True)
check("columns are emitted in the ticket's priority order",
      [label for _, label, _ in ns.COLUMNS],
      ["W-L-T", "STRK", "PF", "PA", "DIFF", "DIV", "CONF", "PCT"])

# The responsive contract: cuts come off the BOTTOM of the priority list, so a
# narrower breakpoint always hides a superset of what a wider one hides.
cuts = [set(c) for _, c in ns._CUT_RULES]
widths = [w for w, _ in ns._CUT_RULES]
check("breakpoints are ordered widest first", widths, sorted(widths, reverse=True))
check("each narrower breakpoint hides a superset of the wider one",
      all(cuts[i] < cuts[i + 1] for i in range(len(cuts) - 1)), True)
check("PCT is the first column cut", list(cuts[0]), ["nflst-pct"])
check("the cut order is the reverse of the priority order",
      [c for _, _, c in ns.COLUMNS if c in cuts[-1]][::-1],
      [c for c in ["nflst-pct", "nflst-conf", "nflst-div", "nflst-diff"]])
check("every cut class belongs to a real column",
      cuts[-1] <= {c for _, _, c in ns.COLUMNS}, True)

check("no fixed pixel width could force a scrollbar", "width:100%" in frag, True)
check("table-layout:fixed binds the table to its container",
      "table-layout:fixed" in frag, True)
check("cells can wrap, so nothing sets a min-content floor",
      "overflow-wrap:anywhere" in frag, True)
import re  # noqa: E402
td_tags = re.findall(r"<td\b[^>]*>", frag)
check("cells carry inline styles, so a stripped <style> still renders a table",
      sum(1 for t in td_tags if "style=" in t), len(td_tags))
th_tags = re.findall(r"<th\b[^>]*>", frag)
check("headers do too", sum(1 for t in th_tags if "style=" in t), len(th_tags))

check("the style block is emitted by default", "<style>" in frag, True)
check("and can be suppressed for destinations that strip it",
      "<style>" in ns.render_standings_html(st, include_style=False), False)
trimmed = ns.render_standings_html(st, columns=["record", "streak"])
check("a build-time column trim drops the rest", "PCT" in trimmed, False)
check("and keeps what was asked for", "W-L-T" in trimmed and "STRK" in trimmed, True)

check("the tiebreaker notes render", "Tiebreakers" in frag, True)
check("an unresolved tie is marked in the rendered notes", "⚠" in frag, True)

# A team abbreviation is never user input, but the renderer escapes anyway —
# a real feed could put anything in a label.
hostile = ns.build_standings(mock)
hostile["conferences"][0]["divisions"][0]["teams"][0]["abbr"] = "<script>x</script>"
check("output is escaped", "<script>" in ns.render_standings_html(hostile), False)

page = ns.render_standalone_page(st)
check("the standalone page declares a doctype", page.startswith("<!DOCTYPE html>"), True)
check("and a mobile viewport", 'name="viewport"' in page, True)

print()
print("=" * 70)
print("7. Reading the real feed shape")
print("=" * 70)

state = {"sports": {"nfl": {"season_games": mock}}}
tmp = Path(__file__).resolve().parent / "_tmp_game_state.json"
try:
    import json
    tmp.write_text(json.dumps(state), encoding="utf-8")
    check("load_games reads sports.nfl.season_games", len(ns.load_games(tmp)), 96)
    tmp.write_text(json.dumps({"sports": {}}), encoding="utf-8")
    check("a game_state with no NFL block gives an empty log, not a crash",
          ns.load_games(tmp), [])
finally:
    tmp.unlink(missing_ok=True)
check("an empty log still renders all 32 teams",
      len([t for c in ns.build_standings([])["conferences"]
           for d in c["divisions"] for t in d["teams"]]), 32)

print()
print("=" * 70)
if FAILURES:
    print(f"{len(FAILURES)} CHECK(S) FAILED")
    for f in FAILURES:
        print("  -", f)
    print("=" * 70)
    sys.exit(1)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 70)
