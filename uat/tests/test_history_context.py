"""
Run:  python -X utf8 uat/tests/test_history_context.py

SLA-108: verified team history in the GROUND TRUTH block.

history_source.py computes, for the teams in yesterday's games, streaks,
starts, title and Finals droughts, head-to-head and polls from slap-sports-db,
and RULE 3 treats each listed fact as sourced. The SQL runs against the real
database (verified by hand against record-book facts on 2026-10-03: the
Knicks' last title 1972-73 and last Finals 1998-99, the Bills' last title
1965 and last Super Bowl 1993, Georgia 2022, Texas A&M 1939). This locks
what turns its rows into sentences: the depth wording, what counts as
notable, the season labels, the cases that once produced nonsense, the
fail-soft fetch, and the prompt lines that point at the block. No API calls,
no network, no database.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import history_source as hs                           # noqa: E402
from runner_common import format_game_state_summary   # noqa: E402

FAILS = []


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    if got != want:
        FAILS.append(label)
    print(f"  [{status}] {label}" + ("" if got == want else f"\n         got:  {got!r}\n         want: {want!r}"))


def has(label, text, *needles):
    check(label, all(n in (text or "") for n in needles), True)


NBA = hs.LEAGUES["nba"]
NFL = hs.LEAGUES["nfl"]
MLB = hs.LEAGUES["mlb"]
NHL = hs.LEAGUES["nhl"]


def streaks(year, rows):
    return [{"year": y, "r": r, "len": n, "is_last": last} for y, r, n, last in rows]


print("Streaks")
check("a 2-game streak is no story",
      hs.streak_fact(streaks(2026, [(2026, "W", 2, True)]), 2026, 1999), None)
check("matched last season: not notable",
      hs.streak_fact(streaks(2026, [(2026, "W", 5, True), (2025, "W", 6, False)]), 2026, 1999), None)
check("matched long ago: the year it was last matched",
      hs.streak_fact(streaks(2026, [(2026, "W", 5, True), (2019, "W", 5, False)]), 2026, 1999),
      "5 straight wins this season; last 5+ within a season: 2019.")
check("never matched: 'since at least' the data's start",
      hs.streak_fact(streaks(2026, [(2026, "L", 7, True), (2001, "L", 4, False), (1999, "W", 3, False)]), 2026, 1999),
      "7 straight losses this season, their longest within a season since at least 1999 (game data starts 1999).")
check("a franchise younger than the data: 'in franchise history'",
      hs.streak_fact(streaks(2026, [(2026, "L", 13, True), (1976, "L", 4, False)]), 2026, 1946, "nba"),
      "13 straight losses this season, their longest within a season in franchise history (since 1976-77).")
check("a streak that isn't the current one is ignored",
      hs.streak_fact(streaks(2026, [(2026, "W", 6, False), (2026, "L", 1, True)]), 2026, 1999), None)


def start(year, w, l, t=0, g=None):
    return {"year": year, "w": w, "l": l, "t": t, "g": g if g is not None else w + l + t}


print("Starts")
check("too early in the season",
      hs.start_fact([start(2026, 2, 0)], 2026, 1999), None)
check("a good start matched recently: not notable",
      hs.start_fact([start(2026, 5, 0), start(2024, 5, 0, g=5)], 2026, 1999), None)
check("best start since a named season",
      hs.start_fact([start(2026, 5, 0), start(2013, 5, 0, g=5), start(2020, 3, 2, g=5)], 2026, 1999),
      "5-0 after 5 games; last start this good or better: 2013.")
check("worst start ever, MLB: since the data's start",
      hs.start_fact([start(2026, 1, 9), start(1876, 4, 6, g=10)], 2026, 1876),
      "1-9 after 10 games, their worst start since at least 1876 (game data starts 1876).")
check("a past season shorter than G is compared over its own games",
      hs.start_fact([start(2026, 40, 20), start(2020, 25, 15, g=60)], 2026, 1876),
      "40-20 after 60 games, their best start in franchise history (since 2020).")
check("an NBA start names the season, not the year",
      hs.start_fact([start(2025, 44, 25), start(1996, 45, 24, g=69)], 2025, 1946, "nba"),
      "44-25 after 69 games; last start this good or better: 1996-97.")
check("a middling start says nothing",
      hs.start_fact([start(2026, 5, 5), start(2010, 9, 1, g=10)], 2026, 1999), None)


print("Droughts (the Knicks failure)")
knicks = hs.drought_fact(NBA, {"last_title": 1972, "last_title_game": 1998}, None,
                         {"last_winning": 2024}, 2025, 1946, None)
check("title and Finals appearance are separate facts, as seasons",
      knicks, "last NBA title: 1972-73; last NBA Finals appearance: 1998-99; last winning season: 2024-25.")
has("never won: 'no ... in franchise history'",
    hs.drought_fact(MLB, {"last_title": None, "last_title_game": 1982}, None, {"last_winning": 2025}, 2026, 1876, None),
    "no World Series title in franchise history", "last World Series appearance: 1982")
check("NFL: playoffs from the stored playoff games",
      hs.drought_fact(NFL, {"last_title": 1957, "last_title_game": 1957}, {"last_playoffs": 2024},
                      {"last_winning": 2025}, 2026, 1999, 1933),
      "last league title: 1957; last championship game appearance: 1957; last playoff appearance: 2024; "
      "last winning season: 2025.")
has("NFL: no playoffs in the data says how far back that is",
    hs.drought_fact(NFL, {}, {}, {}, 2026, 1999, 1933), "no playoff appearance since at least 1933")
check("NHL: no 'winning season' (overtime losses make it meaningless)",
      "winning season" in hs.drought_fact(NHL, {"last_title": 1988, "last_title_game": 2003}, None,
                                          {"last_winning": 2024}, 2026, 1917, None), False)


print("Head-to-head")
check("first meeting this season: nothing (not 'no win since at least 2026')",
      hs.h2h_fact({"first_meeting": 2026, "last_win": None, "last_road_win": None}, "Georgia Bulldogs",
                  None, 2026, 1869), None)
check("a long-ago win over the opponent",
      hs.h2h_fact({"first_meeting": 2002, "last_win": date(2006, 10, 15), "last_road_win": None},
                  "Buffalo Bills", "Buffalo", 2026, 1999),
      "last win over the Buffalo Bills: 2006-10-15; no win at Buffalo since at least 2002.")
check("a recent win: nothing to say",
      hs.h2h_fact({"first_meeting": 1999, "last_win": date(2025, 11, 2), "last_road_win": date(2025, 11, 2)},
                  "Chicago Bears", "Chicago", 2026, 1999), None)
check("'since at least' never reaches before the data",
      hs.h2h_fact({"first_meeting": 1950, "last_win": None, "last_road_win": None}, "X", None, 2026, 1999),
      "no win over the X since at least 1999.")


print("Polls")
check("ranked, recently ranked as high: nothing",
      hs.poll_fact({"rank": 8, "last_ranked": 2025, "last_this_high": 2024, "first_year": 1936}, 2026), None)
check("first season ranked in years, highest in years",
      hs.poll_fact({"rank": 8, "last_ranked": 2020, "last_this_high": 2020, "first_year": 1936}, 2026),
      "AP No. 8 this week, first season ranked since 2020, last ranked this high in 2020.")
check("never ranked before",
      hs.poll_fact({"rank": 22, "last_ranked": None, "last_this_high": None, "first_year": 1936}, 2026),
      "AP No. 22 this week, first AP ranking in any season since at least 1936 (AP poll starts 1936), "
      "highest AP ranking since at least 1936.")


print("Which teams")
gs = {"sports": {
    "nfl": {"yesterday_games": [{"home_team": "Buffalo Bills", "away_team": "Detroit Lions", "home_id": "2",
                                 "away_id": "8", "completed": True, "playoffs": False}]},
    "ncaafb": {"yesterday_games": [
        {"home_team": "A", "away_team": "B", "home_rank": None, "away_rank": None, "completed": True},
        {"home_team": "C", "away_team": "D", "home_rank": 9, "away_rank": None, "completed": True}]},
    "wnba": {"yesterday_games": [{"home_team": "E", "away_team": "F", "completed": True}]}}}
tip = hs.teams_in_play(gs)
check("both sides of each game", [r["name"] for r in tip["nfl"]], ["Buffalo Bills", "Detroit Lions"])
check("college football: only games with a ranked team", [r["name"] for r in tip["ncaafb"]], ["C", "D"])
check("a league the database doesn't cover is skipped", "wnba" in tip, False)
check("names normalized: accents and apostrophes", hs.norm("Montréal Canadiens"), hs.norm("Montreal Canadiens"))
check("Hawai'i matches Hawaii", hs.norm("Hawai'i Rainbow Warriors"), hs.norm("Hawaii Rainbow Warriors"))


print("Fail soft")
check("no SPORTS_DB_URL: unavailable", hs.fetch_history({}, url="")["status"], "unavailable")


def boom(url):
    raise RuntimeError("connection to postgresql://u:s3cr3tpw@db.example.com failed")


r = hs.fetch_history(gs, url="postgresql://u:s3cr3tpw@db.example.com/x", connect=boom)
check("a dead database: unavailable, never raised", r["status"], "unavailable")
check("the password never reaches the reason", "s3cr3tpw" in r["reason"], False)


print("The GROUND TRUTH block")
block_gs = {"yesterday_date": "2026-03-15", "sports": {"nba": {"label": "NBA", "yesterday_games": [
    {"home_team": "New York Knicks", "away_team": "Utah Jazz", "home_score": 110, "away_score": 99,
     "winner": "New York Knicks", "completed": True}]}},
    "history": {"status": "ok", "as_of": "2026-03-15", "leagues": {"nba": {
        "label": "NBA", "season": 2025, "game_data_from": 1946, "unmatched": [],
        "teams": [{"team": "New York Knicks", "opponent": "Utah Jazz", "facts": [knicks]},
                  {"team": "Utah Jazz", "opponent": "New York Knicks", "facts": []}]}}}}
summary = format_game_state_summary(block_gs)
has("the block reaches every pass's ground truth", summary,
    "## GROUND TRUTH: HISTORICAL CONTEXT", "New York Knicks: last NBA title: 1972-73",
    "These are SOURCED", "since at least")
check("a team with nothing notable gets no line", "Utah Jazz:" in summary, False)
check("no history block: no section", "HISTORICAL CONTEXT" in format_game_state_summary(
    {k: v for k, v in block_gs.items() if k != "history"}), False)


print("The prompts point at it")
for tree in ("prompts", "uat/prompts"):
    rule3 = (REPO / tree / "rolling_feedback.txt").read_text(encoding="utf-8")
    has(f"{tree}: RULE 3 treats a HISTORICAL CONTEXT fact as sourced", rule3,
        "HISTORICAL CONTEXT", "IS sourced", "since at least", "Knicks failure")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
