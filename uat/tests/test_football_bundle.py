"""
Run:  python -X utf8 uat/tests/test_football_bundle.py

SLA-116: one connected fact bundle per football game.

football_bundle.py builds, per NFL / college game, records and AP ranks going
in, standings after, the series, upsets and the team's history, from
slap-sports-db (SLA-115's views) and ESPN's game log. Its SQL was run against
the live database and read by hand on the 2026-09-26 Saturday and 2026-09-27
Sunday (Florida's last win over an AP top-5 team before beating Ole Miss:
2020-11-07; Patriots-Jaguars playoffs 4-1). This locks what turns rows into
sentences, the stale-game rule, the budget, story matching, and fail-soft.
No API calls, no network, no database: a fake connection answers each query.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import football_bundle as fb  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


def has(label, text, *needles):
    check(label, all(n in (text or "") for n in needles), True)


def lacks(label, text, *needles):
    check(label, any(n in (text or "") for n in needles), False)


D = date.fromisoformat

print("Small pieces")
check("Thursday night kickoff is Thursday in the East", fb.et_date("2026-09-18T00:15Z"), D("2026-09-17"))
check("a Saturday noon kickoff", fb.et_date("2026-09-26T16:00Z"), D("2026-09-26"))
check("records drop a zero tie count", (fb.record(3, 1), fb.record(3, 1, 1)), ("3-1", "3-1-1"))
check("ordinals", [fb.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21)],
      ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st"])
check("upset buckets", [fb.bucket_for(r) for r in (1, 5, 6, 10, 11, 25)], [5, 5, 10, 10, 25, 25])
check("bucket words", (fb.bucket_words(10), fb.bucket_words(25)), ("an AP top-10 team", "an AP-ranked team"))


# ---------------------------------------------------------------------------
# Polls and upsets
# ---------------------------------------------------------------------------

def poll_rows():
    """AP 2024 weeks 1-3 and 2020 weeks 1-2, with a skipped week in 2020
    (Sep 13-19 has no poll), plus one CFP ranking."""
    rows = []
    for year, weeks in ((2024, [("2024-08-24", "2024-08-31"), ("2024-09-01", "2024-09-07"),
                                ("2024-09-08", "2024-09-14")]),
                        (2020, [("2020-09-01", "2020-09-12"), ("2020-09-20", "2020-09-26")])):
        for a, b in weeks:
            rows += [{"poll": fb.AP, "year": year, "team_id": 100, "franchise_id": 1, "valid_from": D(a),
                      "valid_to": D(b), "rank": 4},
                     {"poll": fb.AP, "year": year, "team_id": 200, "franchise_id": 2, "valid_from": D(a),
                      "valid_to": D(b), "rank": 18}]
    rows.append({"poll": fb.CFP, "year": 2024, "team_id": 100, "franchise_id": 1, "valid_from": D("2024-09-08"),
                 "valid_to": D("2024-09-14"), "rank": 6})
    return rows


polls = fb.Polls(poll_rows())
print("Polls")
check("rank on a covered date", polls.rank(fb.AP, 100, D("2024-09-05")), 4)
check("CFP kept apart", (polls.rank(fb.CFP, 100, D("2024-09-10")), polls.rank(fb.CFP, 100, D("2024-09-05"))), (6, None))
check("unranked on a covered date", polls.rank(fb.AP, 999, D("2024-09-05")), None)
check("a covered date", polls.is_covered(2024, D("2024-09-05")), True)
check("the skipped week is NOT covered", polls.is_covered(2020, D("2020-09-15")), False)
check("the AP poll's first year", polls.first_year, 2020)


def gm(day, opp, result, year=None):
    return {"franchise_id": 9, "year": year or int(day[:4]), "game_date": D(day), "opp_team_id": opp, "result": result}


print("Last win over a ranked team")
state, g = fb.last_win_over_ranked([gm("2024-09-07", 100, "W"), gm("2024-09-14", 200, "W")], polls, 10)
check("the newest qualifying win, with the rank it beat", (state, g["game_date"], g["opp_rank"]), ("ok", D("2024-09-07"), 4))
state, g = fb.last_win_over_ranked([gm("2024-09-14", 200, "W")], polls, 25)
check("No. 18 counts as 'an AP-ranked team'", (state, g["opp_rank"]), ("ok", 18))
check("never: no win over a top-10 team in the polled years",
      fb.last_win_over_ranked([gm("2024-09-14", 200, "W"), gm("2024-09-07", 100, "L")], polls, 10), ("never", None))
check("a win in a week with no poll makes it unknowable",
      fb.last_win_over_ranked([gm("2024-09-14", 200, "W"), gm("2020-09-15", 300, "W"), gm("2020-09-05", 100, "W")],
                              polls, 10), ("unknown", None))
check("losses in a pollless week don't matter for wins",
      fb.last_win_over_ranked([gm("2020-09-15", 300, "L"), gm("2020-09-05", 100, "W")], polls, 10)[0], "ok")
check("before the AP poll existed: not counted, not 'unknown'",
      fb.last_win_over_ranked([gm("2019-11-02", 100, "W")], polls, 10), ("never", None))

print("Last loss to an unranked team")
state, g = fb.last_loss_to_unranked([gm("2024-09-14", 200, "L"), gm("2024-09-07", 999, "L")], polls)
check("No. 18 is ranked; 999 isn't", (state, g["game_date"]), ("ok", D("2024-09-07")))
check("a loss in a pollless week: unknowable", fb.last_loss_to_unranked([gm("2020-09-15", 999, "L")], polls),
      ("unknown", None))


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def srow(**kw):
    base = {"reg_w": 0, "reg_l": 0, "reg_t": 0, "post_w": 0, "post_l": 0, "first_meeting": D("1990-09-01"),
            "last_date": None}
    return {**base, **kw}


print("Series")
line = fb.series_line(srow(reg_w=70, reg_l=30, reg_t=4, last_date=D("2025-10-11"), last_result="W",
                           last_for=34, last_against=16, last_side="away", last_type="regular"),
                      "Ohio State", "Illinois", regular_from=1869, post_from=None, through="", college=True)
check("college: leader first, bowls named as missing, last meeting with its place",
      line, "Series: Ohio State 70-30-4 (regular season since at least 1869; bowls not stored). "
            "Last meeting before this: 2025-10-11, Ohio State won 34-16, at Illinois.")
line = fb.series_line(srow(reg_w=3, reg_l=4, post_w=1, post_l=4, last_date=D("2024-01-13"), last_result="L",
                           last_for=7, last_against=26, last_side="away", last_type="postseason"),
                      "Miami Dolphins", "Kansas City Chiefs", regular_from=1999, post_from=1933, through="",
                      college=False)
has("NFL: the trailing side reads from the leader; playoffs separate with their own depth", line,
    "Kansas City Chiefs 4-3 (regular season since at least 1999)",
    "playoffs Kansas City Chiefs 4-1 (since at least 1933)",
    "2024-01-13 (playoffs), Kansas City Chiefs won 26-7, at Kansas City Chiefs")
check("never met", fb.series_line(None, "Texas", "Tennessee", regular_from=1869, post_from=None,
                                  through="", college=True),
      "Series: no earlier meeting in the stored games (regular season since at least 1869; bowls not stored).")
has("stale: the counts say they stop at last season",
    fb.series_line(srow(reg_w=2, reg_l=2), "A", "B", regular_from=1869, post_from=None,
                   through="through last season", college=True),
    "tied 2-2", "through last season")
has("only playoff meetings", fb.series_line(srow(post_w=1), "A", "B", regular_from=1999, post_from=1933,
                                            through="", college=False),
    "no regular-season meeting", "playoffs A 1-0")


# ---------------------------------------------------------------------------
# NFL standings and records, from ESPN's log
# ---------------------------------------------------------------------------

def nfl(day, home, away, hs, as_, utc="17:00Z"):
    return {"date": f"{day}T{utc}", "season_year": 2026, "season_type": 2, "completed": True,
            "home_abbr": home, "away_abbr": away, "home_score": hs, "away_score": as_,
            "home_team": home, "away_team": away}


log = [nfl("2026-09-13", "BUF", "MIA", 30, 10), nfl("2026-09-13", "NE", "NYJ", 17, 20),
       nfl("2026-09-20", "MIA", "NE", 21, 24), nfl("2026-09-20", "NYJ", "BUF", 3, 27),
       nfl("2026-09-27", "BUF", "NE", 20, 23), nfl("2026-09-27", "NYJ", "MIA", 9, 9)]
print("NFL")
check("record going in counts only earlier games", fb.nfl_record_before(log, "BUF", D("2026-09-27"), 2026), (2, 0, 0))
check("week 1: 0-0, not nothing", fb.nfl_record_before(log, "BUF", D("2026-09-13"), 2026), (0, 0, 0))
check("a tie is kept", fb.nfl_record_before(log + [nfl("2026-10-04", "BUF", "NYJ", 1, 0)], "NYJ", D("2026-10-04"), 2026),
      (1, 1, 1))
thursday = [nfl("2026-10-02", "BUF", "MIA", 20, 10, utc="00:15Z")]    # 8:15 PM ET Thursday 10-01
check("a Thursday-night game (Friday UTC) is Thursday's: not before itself",
      fb.nfl_record_before(thursday, "BUF", D("2026-10-01"), 2026), (0, 0, 0))
check("...and counted by Friday", fb.nfl_record_before(thursday, "BUF", D("2026-10-02"), 2026), (1, 0, 0))
after = fb.nfl_standings_after(log, "NE", D("2026-09-27"))
has("after: place and record, tied teams say so", after, "2-1", "in the AFC East")
check("games back for a trailing team", fb.nfl_standings_after(log, "MIA", D("2026-09-27")).endswith("1.5 games back"), True)
check("a future game in the log doesn't count", fb.nfl_standings_after(log, "BUF", D("2026-09-20")), "2-0, 1st in the AFC East")


# ---------------------------------------------------------------------------
# The whole build, against a fake database
# ---------------------------------------------------------------------------

class Cursor:
    def __init__(self, rows):
        self._rows = rows
        names = list(rows[0]) if rows else ["x"]
        self.description = [type("C", (), {"name": n}) for n in names]

    def fetchall(self):
        return [tuple(r.values()) for r in self._rows]


class FakeConn:
    """Answers each module query from canned rows; records the parameters."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def execute(self, sql, params=None):
        for q, fn in self.answers.items():
            if sql is q:
                self.calls.append((q, params))
                return Cursor(fn(params or {}))
        raise AssertionError("unexpected query: " + sql[:60])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


UGA, WIS, PSU, OU = 61, 275, 213, 201      # CFBD ids
TEAMS = {"61": (1, 11, "Georgia Bulldogs", "Bulldogs", "Georgia"),
         "201": (2, 12, "Oklahoma Sooners", "Sooners", "Oklahoma"),
         "275": (3, 13, "Wisconsin Badgers", "Badgers", "Wisconsin"),
         "213": (4, 14, "Penn State Nittany Lions", "Nittany Lions", "Penn State")}
DAY = "2026-09-26"


def season_games(stale_team=None):
    """Each team's 2026 games through the day. Wisconsin 2-1 and Penn State
    3-0 going in; Georgia 3-0, Oklahoma 2-1. One SEC/Big Ten game each."""
    def row(tid, d, res, w, l, conf, opp_conf, opp=900):
        return {"team_id": tid, "franchise_id": tid + 10, "opp_team_id": opp, "game_date": D(d),
                "season_type": "regular", "result": res, "venue_side": "home", "score_for": 1, "score_against": 0,
                "wins_before": w, "losses_before": l, "ties_before": 0, "group_id": conf, "conference_id": conf,
                "conference": {1: "SEC", 2: "Big Ten"}[conf], "opp_conference_id": opp_conf}
    rows = [row(3, "2026-09-12", "W", 1, 0, 2, 2), row(3, "2026-09-19", "L", 1, 0, 2, 9),
            row(3, DAY, "W", 2, 1, 2, 2, opp=4),
            row(4, "2026-09-19", "W", 2, 0, 2, 9), row(4, DAY, "L", 3, 0, 2, 2, opp=3),
            row(1, "2026-09-19", "W", 2, 0, 1, 1), row(1, DAY, "W", 3, 0, 1, 1, opp=2),
            row(2, "2026-09-19", "L", 2, 0, 1, 9), row(2, DAY, "L", 2, 1, 1, 1, opp=1)]
    return [r for r in rows if not (r["team_id"] == stale_team and r["game_date"] == D(DAY))]


def answers(stale_team=None):
    return {
        fb.CFB_TEAMS: lambda p: [{"external_id": k, "team_id": v[0], "franchise_id": v[1], "full_name": v[2],
                                  "nickname": v[3], "location": v[4]} for k, v in TEAMS.items() if k in p["ids"]],
        fb.FIRST_YEARS: lambda p: [{"regular": 1869, "postseason": None}],
        fb.POLL_RANGES: lambda p: [
            {"poll": fb.AP, "year": 2026, "team_id": 4, "franchise_id": 14, "valid_from": D("2026-09-20"),
             "valid_to": D(DAY), "rank": 13},
            {"poll": fb.AP, "year": 2026, "team_id": 1, "franchise_id": 11, "valid_from": D("2026-09-20"),
             "valid_to": D(DAY), "rank": 2},
            {"poll": fb.AP, "year": 2025, "team_id": 77, "franchise_id": 77, "valid_from": D("2025-11-02"),
             "valid_to": D("2025-11-08"), "rank": 24},
            {"poll": fb.AP, "year": 2025, "team_id": 4, "franchise_id": 14, "valid_from": D("2025-10-12"),
             "valid_to": D("2025-10-18"), "rank": 5}],
        fb.SEASON_GAMES: lambda p: season_games(stale_team),
        fb.SERIES: lambda p: [{"franchise_id": f, "opp_franchise_id": o, "reg_w": 9, "reg_l": 12, "reg_t": 0,
                               "post_w": 0, "post_l": 0, "first_meeting": D("1953-10-03"),
                               "last_date": D("2024-10-26"), "last_result": "L", "last_for": 13,
                               "last_against": 28, "last_side": "home", "last_type": "regular"}
                              for f, o in zip(p["pf"], p["po"])],
        fb.UPSET_GAMES: lambda p: [
            {"franchise_id": 13, "year": 2025, "game_date": D("2025-11-08"), "opp_team_id": 77, "result": "W"},
            {"franchise_id": 14, "year": 2025, "game_date": D("2025-10-18"), "opp_team_id": 500, "result": "L"}],
        fb.PLAYOFF_WINS: lambda p: [],
        fb.NAME_POOL: lambda p: [{"league_id": "nfl", "full_name": "Pittsburgh Steelers", "nickname": "Steelers",
                                  "location": "Pittsburgh"},
                                 {"league_id": "mlb", "full_name": "Milwaukee Brewers", "nickname": "Brewers",
                                  "location": "Milwaukee"}],
    }


def cfb_game(gid, home, away, hid, aid, hs, as_, hrank=None, arank=None):
    return {"game_id": gid, "date": f"{DAY}T19:30Z", "season_year": 2026, "season_type": 2, "completed": True,
            "home_team": home, "away_team": away, "home_id": str(hid), "away_id": str(aid),
            "home_score": hs, "away_score": as_, "home_rank": hrank, "away_rank": arank, "playoffs": False}


GS = {"yesterday_date": DAY, "sports": {"ncaafb": {"yesterday_games": [
    cfb_game("g1", "Penn State Nittany Lions", "Wisconsin Badgers", PSU, WIS, 20, 24, hrank=13),
    cfb_game("g2", "Georgia Bulldogs", "Oklahoma Sooners", UGA, OU, 41, 13, hrank=2)]}},
    "history": {"status": "ok", "leagues": {"ncaafb": {"teams": [
        {"team": "Wisconsin Badgers", "facts": ["beat the Penn State Nittany Lions yesterday, their first win "
                                                "over them since 2011-11-26."]}]}}}}

print("A college Saturday, both games stored")
conn = FakeConn(answers())
block = fb.build_bundles(conn, GS)
games = {b["game_id"]: b for b in block["sports"]["ncaafb"]["games"]}
w = games["g1"]
check("the winner is named first, with the rank it carried in",
      w["result"], "Wisconsin Badgers 24, No. 13 Penn State Nittany Lions 20 (Sat 2026-09-26, at Penn State)")
text = "\n".join(w["lines"])
has("records going in, ranks from the AP poll", text,
    "Going in: Wisconsin 2-1, unranked in the AP poll; Penn State 3-0, AP No. 13.")
has("records and conference records after", text, "After: Wisconsin 3-1 (2-0 Big Ten); Penn State 3-1 (0-1 Big Ten).")
has("the series, from the winner's side", text, "Series: Penn State 12-9 (regular season since at least 1869")
has("the upset, with the winner's last win over a ranked team", text,
    "Upset: Wisconsin (unranked) beat No. 13 Penn State; their last regular-season win over an AP-ranked team "
    "before this: 2025-11-08 (over No. 24).")
has("and the loser's last loss to an unranked team", text,
    "Penn State's last regular-season loss to an unranked team before this: 2025-10-18.")
check("history folded in, verbatim and apart", w["history"],
      ["Wisconsin Badgers history: beat the Penn State Nittany Lions yesterday, their first win over them since 2011-11-26."])
check("flags", (w["ranked"], w["upset"], w["vouched"], w["best_rank"]), (True, True, True, 13))
lacks("no upset line when the favourite wins", "\n".join(games["g2"]["lines"]), "Upset")
series_calls = [p for q, p in conn.calls if q is fb.SERIES]
check("one series query for the whole slate, not one per game", len(series_calls), 1)
check("...with this season included", series_calls[0]["max_year"], 2026)
check("the block is JSON (it goes into game_state.json)", bool(json.dumps(block)), True)

print("Stale = unknown: Penn State's game is not in the database yet")
conn = FakeConn(answers(stale_team=4))
block = fb.build_bundles(conn, GS)
w = next(b for b in block["sports"]["ncaafb"]["games"] if b["game_id"] == "g1")
text = "\n".join(w["lines"])
lacks("no records, no conference records, no upset", text, "2-1", "3-0", "Big Ten", "Upset")
has("ranks still come from the poll", text, "Penn State: AP No. 13")
has("it says why", text, "not in the database yet")
has("series counts stop at last season", text, "through last season")
check("the series query was told so", [p for q, p in conn.calls if q is fb.SERIES][0]["max_year"], 2025)
check("no upset query at all", any(q is fb.UPSET_GAMES for q, _ in conn.calls), False)
check("flag", w["vouched"], False)

print("An NFL playoff game (the replay can't reach one before January)")
nfl_answers = {
    fb.NFL_TEAMS: lambda p: [{"team_id": 4, "franchise_id": 40, "full_name": "Buffalo Bills", "nickname": "Bills",
                              "location": "Buffalo", "abbrev": "BUF"},
                             {"team_id": 16, "franchise_id": 160, "full_name": "Kansas City Chiefs",
                              "nickname": "Chiefs", "location": "Kansas City", "abbrev": "KC"}],
    fb.FIRST_YEARS: lambda p: [{"regular": 1999, "postseason": 1933}],
    fb.SEASON_GAMES: lambda p: [],
    fb.SERIES: lambda p: [{"franchise_id": 160, "opp_franchise_id": 40, "reg_w": 7, "reg_l": 12, "reg_t": 0,
                           "post_w": 5, "post_l": 2, "first_meeting": D("1967-01-01"), "last_date": None}],
    # A wild-card win THIS postseason (2026 season) the stale rule must not trust.
    fb.PLAYOFF_WINS: lambda p: [{"franchise_id": 160, "year": 2024}, {"franchise_id": 160, "year": 2026}],
    fb.NAME_POOL: lambda p: [],
}
playoff_gs = {"yesterday_date": "2027-01-24", "sports": {"nfl": {
    "yesterday_games": [{"game_id": "p1", "date": "2027-01-24T23:30Z", "season_year": 2026, "season_type": 3,
                         "playoffs": True, "completed": True, "home_team": "Kansas City Chiefs",
                         "away_team": "Buffalo Bills", "home_abbr": "KC", "away_abbr": "BUF",
                         "home_score": 27, "away_score": 24}],
    "season_games": [nfl("2026-12-27", "KC", "BUF", 30, 20)]}}}
conn = FakeConn(nfl_answers)
pb = fb.build_bundles(conn, playoff_gs)["sports"]["nfl"]["games"][0]
pt = "\n".join(pb["lines"])
has("result says playoffs", pb["result"], "Kansas City Chiefs 27, Buffalo Bills 24", "playoffs")
has("records going in are the regular season's", pt, "Kansas City Chiefs 1-0 in the regular season")
lacks("no division standings in January", pt, "in the AFC")
has("not stored yet: last playoff win before THIS POSTSEASON, never a false 'before this game'", pt,
    "Last playoff win before this postseason: Kansas City Chiefs 2024 season; "
    "Buffalo Bills none since at least 1933 (playoff data starts 1933).")
has("the playoff series has its own depth", pt, "playoffs Kansas City Chiefs 5-2 (since at least 1933)")
check("the NFL game counts as stale-proof only when stored: not stored here", pb["vouched"], False)
lacks("...and this postseason's earlier win is not presented as known", pt, "2026 season")

print("Fail soft")
check("no SPORTS_DB_URL: unavailable", fb.fetch_bundles(GS, url="")["status"], "unavailable")


def boom(url):
    raise RuntimeError("connection to postgresql://u:s3cr3tpw@db.example.com failed")


r = fb.fetch_bundles(GS, url="postgresql://u:s3cr3tpw@db.example.com/x", connect=boom)
check("a dead database: unavailable, never raised", r["status"], "unavailable")
check("the password never reaches the reason", "s3cr3tpw" in r["reason"], False)
check("no football yesterday: ok, nothing to say, no connection made",
      fb.fetch_bundles({"yesterday_date": DAY, "sports": {}}, url="postgresql://x", connect=boom)["sports"], {})
check("no block, no lines", fb.summary_lines({}), [])


# ---------------------------------------------------------------------------
# Rendering: priority, budget, story matching
# ---------------------------------------------------------------------------

def bundle(gid, team_a, team_b, *, ranked=False, upset=False, best=99, lines=2, hist=1, school=None):
    return {"game_id": gid, "result": f"{team_a} 1, {team_b} 0 (Sat 2026-09-26, at {team_a})",
            "lines": [f"{gid} line {i} " + "x" * 80 for i in range(lines)],
            "history": [f"{gid} history " + "y" * 200 for _ in range(hist)],
            "ranked": ranked, "upset": upset, "best_rank": best, "vouched": True,
            "teams": [{"name": team_a, "full_name": team_a, "nickname": team_a.split()[-1],
                       "location": school or team_a.rsplit(" ", 1)[0]},
                      {"name": team_b, "full_name": team_b, "nickname": team_b.split()[-1],
                       "location": team_b.rsplit(" ", 1)[0]}]}


games = [bundle("story", "Iowa Hawkeyes", "Michigan Wolverines", ranked=True, best=17),
         bundle("pitt", "Pittsburgh Panthers", "Bucknell Bison"),
         bundle("top", "Georgia Bulldogs", "Oklahoma Sooners", ranked=True, best=2),
         bundle("upset", "Wake Forest Demon Deacons", "Louisville Cardinals", ranked=True, upset=True, best=16)]
games += [bundle(f"u{i}", f"School{i} Owls", f"Other{i} Hawks") for i in range(200)]
render_gs = {"football": {"status": "ok", "as_of": DAY, "name_pool": answers()[fb.NAME_POOL]({}),
                          "sports": {"ncaafb": {"label": "College football", "games": games}}}}
plan = {"stories": [{"headline": "Iowa stuns Michigan", "beats": [{"angle": "Pittsburgh Steelers grind one out"}]}]}

print("Priorities")
prio = fb.priorities(render_gs["football"], fb._story(plan))
check("a story team's game is tier 1", prio["story"], 1)
check("'Pittsburgh' in a Steelers story is not the Pitt Panthers", prio["pitt"], 3)
check("ranked matchups are tier 2, the rest tier 3", (prio["top"], prio["upset"], prio["u0"]), (2, 2, 3))
check("a plain-text story works too", fb.priorities(render_gs["football"], "Michigan fell at home")["story"], 1)
check("'No. 9 Pittsburgh' does name the school",
      fb.priorities(render_gs["football"], "No. 9 Pittsburgh rolled")["pitt"], 1)

print("The budget")
out = fb.summary_lines(render_gs, plan)
joined = "\n".join(out)
check("never over the cap", len(joined) <= fb.BUDGET, True)
check("the story game comes first, in full", [x.strip()[:5] for x in out[4:5]], ["Iowa "])
has("...with its history", joined, "story history")
check("upsets before other ranked games", joined.index("Wake Forest") < joined.index("Georgia Bulldogs"), True)
check("every ranked game is present", all(t in joined for t in ("Georgia Bulldogs", "Wake Forest", "Iowa")), True)
has("what didn't fit is counted", out[-1], "more football result(s) not shown (budget)")
n_shown = sum(1 for x in out if x.startswith("  ") and not x.startswith("    ") and "not shown" not in x)
check("the count adds up", n_shown + int(out[-1].split("and ")[1].split()[0]), len(games))

tight = fb.summary_lines(render_gs, plan, budget=1100)
tj = "\n".join(tight)
check("tight budget: still under it", len(tj) <= 1100, True)
has("tight budget: the story game falls back to its bundle without history", tj, "story line 0")
lacks("...and drops the history first", tj, "story history")
check("tight budget: ranked games keep their result lines", all(t in tj for t in ("Georgia", "Wake Forest")), True)

small = {"football": {"status": "ok", "as_of": DAY, "sports": {"nfl": {"label": "NFL", "games": games[:2]}}}}
sl = fb.summary_lines(small)
check("NFL games are all tier 2: full bundles on a normal Sunday",
      sum(1 for x in sl if x.startswith("    ")) , 2 * (2 + 1))
lacks("nothing dropped, nothing counted", "\n".join(sl), "not shown")
has("the header says what the block is and that it is sourced", "\n".join(sl),
    "## GROUND TRUTH: FOOTBALL GAMES", "GOING IN", "SOURCED", "since at least")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
