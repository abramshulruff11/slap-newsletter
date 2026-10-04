"""
Run:  python -X utf8 uat/tests/test_db_lag.py

SLA-120: the sports database lags a day; ESPN doesn't.

College football reaches slap-sports-db about once a day, so on a Sunday
morning none of Saturday's games are stored. Before this, the history block
then showed facts one game behind: "4 straight wins this season" for a Florida
that had just lost, "last win over the Florida Gators: 2023" for a Missouri
that had just beaten them. Now, when a team's stored record plus yesterday's
result equals ESPN's record after the game, the database has every earlier
game and the facts are brought up to date; when it doesn't, streaks, starts
and head-to-head are left out. (The football bundles' half is in
test_football_bundle.py.) No API calls, no network, no database.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import fetch_sports_data as fsd  # noqa: E402
import history_source as hs  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


print("ESPN's records are captured from the scoreboard")
comp = {"records": [{"type": "total", "summary": "4-1"}, {"type": "home", "summary": "3-0"},
                    {"type": "vsconf", "summary": "1-1"}]}
check("by type", fsd._competitor_records(comp), {"total": "4-1", "home": "3-0", "vsconf": "1-1"})
check("none: empty", fsd._competitor_records({}), {})
ev = {"id": "1", "date": "2026-10-03T19:50Z", "season": {"type": 2, "year": 2026}, "competitions": [{
    "neutralSite": True, "status": {"type": {"completed": True, "description": "Final"}},
    "competitors": [{"homeAway": "home", "score": "45", "winner": True, "team": {"displayName": "Missouri Tigers"},
                     "records": [{"type": "total", "summary": "4-1"}]},
                    {"homeAway": "away", "score": "17", "team": {"displayName": "Florida Gators"},
                     "records": [{"type": "total", "summary": "4-1"}]}]}]}
g = fsd.parse_game(ev)
check("parse_game keeps them, and the neutral-site flag",
      (g["home_records"], g["away_records"], g["neutral_site"]), ({"total": "4-1"}, {"total": "4-1"}, True))

print("Reading ESPN's records")
check("4-1", hs.parse_record("4-1"), (4, 1, 0))
check("2-1-1", hs.parse_record("2-1-1"), (2, 1, 1))
check("junk", (hs.parse_record(""), hs.parse_record(None), hs.parse_record("4-1 (2-0)")), (None, None, None))
check("results", (hs.game_result(g, "home"), hs.game_result(g, "away")), ("W", "L"))
check("not final: no result", hs.game_result(dict(g, completed=False), "home"), None)
check("a tie", hs.game_result({"completed": True, "home_score": 3, "away_score": 3}, "home"), "T")
check("record going in = after minus the result", hs.espn_record_before({"total": "4-1"}, "W"), (3, 1, 0))
check("...for a loss", hs.espn_record_before({"total": "4-1"}, "L"), (4, 0, 0))
check("an impossible one (a 0-0 record after a win) is unknown", hs.espn_record_before({"total": "0-0"}, "W"), None)
check("no record: unknown", hs.espn_record_before({}, "W"), None)

print("Advancing a streak by yesterday's result")
rows = [{"year": 2015, "r": "W", "len": 6, "is_last": False},
        {"year": 2026, "r": "W", "len": 4, "is_last": True}]
won = hs.advance_streaks(rows, 2026, "W")
check("a win grows it", next(r["len"] for r in won if r["is_last"]), 5)
check("...and doesn't touch the input", rows[1]["len"], 4)
lost = hs.advance_streaks(rows, 2026, "L")
check("a loss ends it: the new streak is 1", [(r["r"], r["len"]) for r in lost if r["is_last"]], [("L", 1)])
check("...which is too short to list (THE CASE: no '4 straight wins' for a team that just lost)",
      hs.streak_fact(lost, 2026, 1869), None)
check("before the fix, the stale rows said it", hs.streak_fact(rows, 2026, 1869) is not None, True)
check("a tie ends a streak too, and starts none", [r for r in hs.advance_streaks(rows, 2026, "T") if r["is_last"]], [])

print("Advancing a start")
st = [{"year": 2026, "w": 4, "l": 0, "t": 0, "g": 5}, {"year": 2020, "w": 4, "l": 1, "t": 0, "g": 5}]
check("yesterday's loss is added to this season's row only",
      [(r["w"], r["l"]) for r in hs.advance_start(st, 2026, "L")], [(4, 1), (4, 1)])

print("Is the database otherwise caught up?")
teams = {"Missouri Tigers": {"franchise_id": 1}, "Florida Gators": {"franchise_id": 2},
         "Rutgers Scarlet Knights": {"franchise_id": 3}, "Iowa Hawkeyes": {"franchise_id": 4}}
rows = [{"name": "Missouri Tigers", "result": "W", "records": {"total": "4-1"}},
        {"name": "Florida Gators", "result": "L", "records": {"total": "4-1"}},
        {"name": "Rutgers Scarlet Knights", "result": "L", "records": {"total": "1-4"}},
        {"name": "Iowa Hawkeyes", "result": "W", "records": {"total": "5-0"}},
        {"name": "Unknown U", "result": "W", "records": {"total": "1-0"}}]
current = [{"franchise_id": 1, "w": 3, "l": 1, "t": 0, "stored_today": False},
           {"franchise_id": 2, "w": 4, "l": 0, "t": 0, "stored_today": False},
           {"franchise_id": 3, "w": 0, "l": 3, "t": 0, "stored_today": False},   # a game missing
           {"franchise_id": 4, "w": 5, "l": 0, "t": 0, "stored_today": True}]
lag, unv = hs.catch_up(rows, teams, current)
check("caught up: yesterday's result is all that's missing", lag, {1: "W", 2: "L"})
check("one game short of ESPN: unverified", unv, {3})
check("stored already, or unknown to the database: neither", (4 in lag or 4 in unv), False)
check("no ESPN record: unverified",
      hs.catch_up([{"name": "Missouri Tigers", "result": "W", "records": {}}], teams, current), ({}, {1}))
check("a team with no stored games this season, on its first game: caught up",
      hs.catch_up([{"name": "Missouri Tigers", "result": "W", "records": {"total": "1-0"}}], teams, []),
      ({1: "W"}, set()))
dh = [{"name": "Missouri Tigers", "result": "W", "records": {"total": "4-1"}},
      {"name": "Missouri Tigers", "result": "W", "records": {"total": "5-1"}}]
check("two games yesterday, neither stored (a doubleheader): unverified, never half-advanced",
      hs.catch_up(dh, teams, current), ({}, {1}))

print("build_history, with a team a game behind")


class Conn:
    """A regular-season NBA night: the Knicks (5 straight wins, 6-0) beat the
    Pacers yesterday, a game the database doesn't hold yet."""
    def __init__(self, knicks_total):
        self.total, self.ran = knicks_total, []

    def execute(self, sql, params=None):
        self.ran.append((sql, dict(params or {})))
        rows = []
        if sql is hs.CURRENT_TEAMS:
            rows = [{"team_id": 1, "franchise_id": 1, "full_name": "New York Knicks", "nickname": "Knicks",
                     "location": "New York"},
                    {"team_id": 2, "franchise_id": 2, "full_name": "Indiana Pacers", "nickname": "Pacers",
                     "location": "Indiana"}]
        elif sql is hs.SEASON:
            rows = [{"year": 2026, "first_year": 1946}]
        elif sql is hs.CURRENT_RECORDS:
            rows = [{"franchise_id": 1, "w": 6, "l": 0, "t": 0, "stored_today": False},
                    {"franchise_id": 2, "w": 3, "l": 3, "t": 0, "stored_today": False}]
        elif sql is hs.STREAKS:
            rows = [{"franchise_id": 1, "year": 2026, "r": "W", "len": 6, "is_last": True},
                    {"franchise_id": 1, "year": 2012, "r": "W", "len": 7, "is_last": False},
                    {"franchise_id": 2, "year": 2026, "r": "W", "len": 3, "is_last": True},
                    {"franchise_id": 2, "year": 2015, "r": "W", "len": 3, "is_last": False}]
        elif sql is hs.STARTS:
            g = 6 + (1 in params["lag_f"])
            rows = [{"franchise_id": 1, "year": 2026, "w": 6, "l": 0, "t": 0, "g": g},
                    {"franchise_id": 1, "year": 1999, "w": g, "l": 0, "t": 0, "g": g}]
        elif sql is hs.HEAD_TO_HEAD:
            rows = [{"franchise_id": 1, "opp": 2, "last_win": date(2019, 3, 1), "last_road_win": None,
                     "won_today": False, "first_meeting": 1977},
                    {"franchise_id": 2, "opp": 1, "last_win": date(2025, 1, 1), "last_road_win": None,
                     "won_today": False, "first_meeting": 1977}]
        cols = list(rows[0]) if rows else ["x"]

        class Cur:
            description = [type("C", (), {"name": c}) for c in cols]

            def fetchall(self_inner):
                return [tuple(r[c] for c in cols) for r in rows]
        return Cur()


def nba_gs(knicks_total, pacers_total="3-4"):
    return {"yesterday_date": "2026-11-20", "sports": {"nba": {"yesterday_games": [{
        "game_id": "n1", "completed": True, "home_team": "New York Knicks", "away_team": "Indiana Pacers",
        "home_score": 110, "away_score": 100, "playoffs": False,
        "home_records": {"total": knicks_total}, "away_records": {"total": pacers_total}}]}}}


conn = Conn("7-0")
block = hs.build_history(conn, nba_gs("7-0"))
knx = " ".join(next(t for t in block["leagues"]["nba"]["teams"] if t["team"] == "New York Knicks")["facts"])
pac = " ".join(next(t for t in block["leagues"]["nba"]["teams"] if t["team"] == "Indiana Pacers")["facts"])
check("caught up: the streak includes yesterday", "7 straight wins this season" in knx, True)
check("...the start too, compared through 7 games (both teams caught up)",
      ("7-0 after 7 games" in knx, [p["lag_f"] for s, p in conn.ran if s is hs.STARTS]), (True, [[1, 2]]))
check("...and yesterday's win is the head-to-head news",
      "beat the Indiana Pacers yesterday, their first win over them since 2019-03-01" in knx, True)
check("the loser's streak ended: no '3 straight wins'", "straight" in pac, False)
conn = Conn("8-0")
block = hs.build_history(conn, nba_gs("8-0", "3-4"))
knx_facts = next(t for t in block["leagues"]["nba"]["teams"] if t["team"] == "New York Knicks")["facts"]
check("ESPN disagrees (a game missing): no streak, start or head-to-head, droughts stay",
      [f for f in knx_facts if "straight" in f or "after" in f or "Pacers" in f], [])
check("...and nothing is advanced", [p["lag_f"] for s, p in conn.ran if s is hs.STARTS], [[2]])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
