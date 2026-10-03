"""
Run:  python -X utf8 uat/tests/test_history_claims.py

SLA-109: Pass 3 CHECKS history claims against the HISTORICAL CONTEXT block.

SLA-108 put verified team history (streaks, starts, droughts kept apart,
head-to-head, polls) in the ground truth. Pass 3 now finds "since YEAR",
"N-year drought", "first ... since" and "longest ... since" in our own prose
and resolves each against it, the SLA-65 way:

  - a listed fact agrees           -> confirmed, the draft is untouched
  - the ONE team the sentence names has a fact of that kind, and it
    disagrees                      -> FACT FLAG [HIGH] quoting the fact,
                                      which editor Check 9 corrects
  - anything else                  -> FACT FLAG [LOW], RULE 3 downgrades it

HIGH is narrow on purpose: facts are only listed when notable, so "no fact"
never means "false", and a two-team sentence could mean either team. Any HIGH
that is actually right is a bug. The first case is the one RULE 3 was written
for: the 2026-06-01 lead's "53-year Finals drought" (53 is the title drought;
the Finals drought was 27).

This locks the fact parser against every sentence template history_source
writes, the decision table, the guards that keep HIGH honest, and the prompt
lines that point at it. No API calls, no network, no database.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import history_source as hs                                        # noqa: E402
from claim_validator import check_history_claims, validate_claims  # noqa: E402

FAILS = []


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    if got != want:
        FAILS.append(label)
    print(f"  [{status}] {label}" + ("" if got == want else f"\n         got:  {got!r}\n         want: {want!r}"))


def has(label, text, *needles):
    check(label, all(n in (text or "") for n in needles), True)


# ---------------------------------------------------------------------------
print("The parser reads back every sentence history_source writes")
# ---------------------------------------------------------------------------
NBA, NFL, MLB = hs.LEAGUES["nba"], hs.LEAGUES["nfl"], hs.LEAGUES["mlb"]


def parsed(line):
    return [(f["kind"], f["last"], f["floor"], f["never"]) for f in hs.parse_facts(line)]


knicks = hs.drought_fact(NBA, {"last_title": 1972, "last_title_game": 1998}, None, {"last_winning": 2024},
                         2025, 1946, None)
check("Knicks droughts, kept apart", parsed(knicks),
      [("title", [1972, 1973], None, False), ("title_game", [1998, 1999], None, False),
       ("winning", [2024, 2025], None, False)])
browns = hs.drought_fact(NFL, {"last_title": 1964, "last_title_game": None}, {"last_playoffs": 2023}, None,
                         2026, 1999, 1999)
check("NFL: never in a title game, no winning season in the data", parsed(browns),
      [("title", [1964], None, False), ("title_game", None, None, True),
       ("playoff", [2023], None, False), ("winning", None, 1999, False)])
check("no title in franchise history", parsed(hs.drought_fact(MLB, {}, None, {"last_winning": 2025}, 2026, 1876, None))[0],
      ("title", None, None, True))
check("NFL: no playoff appearance in the data",
      parsed(hs.drought_fact(NFL, {"last_title": 1965, "last_title_game": 1993}, None, {"last_winning": 2025},
                             2026, 1999, 1999))[2], ("playoff", None, 1999, False))

st = hs.streak_fact([{"year": 2026, "r": "W", "len": 9, "is_last": True},
                     {"year": 2015, "r": "W", "len": 9, "is_last": False}], 2026, 1876, "mlb")
check("streak with a past equal", parsed(st), [("streak_W", [2015], None, False)])
check("streak length is kept", hs.parse_facts(st)[0]["length"], 9)
st = hs.streak_fact([{"year": 2026, "r": "L", "len": 6, "is_last": True},
                     {"year": 1999, "r": "L", "len": 2, "is_last": False}], 2026, 1999, "nfl")
check("streak with no equal in the data: a floor", parsed(st), [("streak_L", None, 1999, False)])
st = hs.streak_fact([{"year": 2026, "r": "W", "len": 7, "is_last": True},
                     {"year": 1970, "r": "W", "len": 2, "is_last": False}], 2026, 1876, "mlb")
check("streak in franchise history: a floor at the franchise's start", parsed(st),
      [("streak_W", None, 1970, False)])

start = lambda y, w, l, g: {"year": y, "w": w, "l": l, "t": 0, "g": g}
sf = hs.start_fact([start(2026, 6, 0, 6), start(2013, 6, 0, 6), start(2020, 3, 3, 6)], 2026, 1999)
check("best start with a past equal", parsed(sf), [("start_best", [2013], None, False)])
check("start record is kept", hs.parse_facts(sf)[0]["record"], "6-0")
sf = hs.start_fact([start(2026, 0, 5, 5), start(1999, 2, 3, 5)], 2026, 1999)
check("worst start with no equal: a floor", parsed(sf), [("start_worst", None, 1999, False)])

pf = hs.poll_fact({"rank": 4, "last_ranked": 2010, "last_this_high": 1998, "first_year": 1936}, 2026)
check("poll facts", parsed(pf), [("ranked", [2010], None, False), ("ranked_high", [1998], None, False)])
pf = hs.poll_fact({"rank": 20, "last_ranked": None, "last_this_high": None, "first_year": 1936}, 2026)
check("poll floors", parsed(pf), [("ranked", None, 1936, False), ("ranked_high", None, 1936, False)])

h = hs.h2h_fact({"first_meeting": 2002, "last_win": date(2006, 10, 15), "last_road_win": None},
                "Buffalo Bills", "Buffalo", 2026, 1999)
check("head-to-head after a loss", [(f["kind"], f["last"], f["floor"], f.get("opponent") or f.get("city"))
                                    for f in hs.parse_facts(h)],
      [("h2h", [2006], None, "Buffalo Bills"), ("h2h_at", None, 2002, "Buffalo")])

# ---------------------------------------------------------------------------
print("A team that ended a drought yesterday sees the drought")
# ---------------------------------------------------------------------------
# Before SLA-109 yesterday's own win was the "last win", so "first win over
# the Chiefs since 2018" could never be sourced: the fact vanished exactly
# when the writer wanted it.
h = hs.h2h_fact({"first_meeting": 1999, "last_win": date(2018, 9, 16), "last_road_win": None,
                 "won_today": True}, "Kansas City Chiefs", "Kansas City", 2026, 1999)
check("won yesterday: the drought it ended", h,
      "beat the Kansas City Chiefs yesterday, their first win over them since 2018-09-16; "
      "won at Kansas City yesterday, their first win there since at least 1999.")
check("...and it parses", [(f["kind"], f["last"], f["floor"]) for f in hs.parse_facts(h)],
      [("h2h", [2018], None), ("h2h_at", None, 1999)])
check("won yesterday after a recent win: nothing to say",
      hs.h2h_fact({"first_meeting": 1999, "last_win": date(2025, 10, 1), "last_road_win": date(2024, 9, 1),
                   "won_today": True}, "Chicago Bears", None, 2026, 1999), None)
has("the query keeps yesterday's game out of 'last win'", hs.HEAD_TO_HEAD,
    "game_date < %(day)s::date", "AS won_today")

check("every parsed fact is a known kind", {f["kind"] for line in
      [knicks, browns, st, sf, pf, h] for f in hs.parse_facts(line)} <= {
      "title", "title_game", "playoff", "winning", "streak_W", "streak_L", "start_best", "start_worst",
      "ranked", "ranked_high", "h2h", "h2h_at"}, True)


# ---------------------------------------------------------------------------
print("The decision table")
# ---------------------------------------------------------------------------
def team(name, opp, *facts):
    return {"team": name, "opponent": opp, "facts": list(facts)}


GS = {"yesterday_date": "2026-03-14", "history": {"status": "ok", "as_of": "2026-03-14", "leagues": {
    "nba": {"label": "NBA", "season": 2025, "game_data_from": 1946, "teams": [
        team("New York Knicks", "Boston Celtics",
             "7 straight wins this season; last 7+ within a season: 2013-14.",
             "last NBA title: 1972-73; last NBA Finals appearance: 1998-99; last winning season: 2024-25."),
        team("Boston Celtics", "New York Knicks",
             "last NBA title: 2023-24; last NBA Finals appearance: 2023-24; last winning season: 2024-25.")]},
    "nfl": {"label": "NFL", "season": 2025, "game_data_from": 1999, "teams": [
        team("Buffalo Bills", "Kansas City Chiefs",
             "beat the Kansas City Chiefs yesterday, their first win over them since 2018-09-16; "
             "won at Kansas City yesterday, their first win there since at least 1999.",
             "last league title: 1965; last championship game appearance: 1993; "
             "last playoff appearance: 2024; last winning season: 2024."),
        team("Kansas City Chiefs", "Buffalo Bills",
             "3 straight losses this season, their longest within a season since at least 1999 (game data starts 1999).",
             "last league title: 2023; last championship game appearance: 2024; "
             "last playoff appearance: 2024; last winning season: 2024.")]},
    "ncaafb": {"label": "college football", "season": 2025, "game_data_from": 1869, "teams": [
        team("Michigan State Spartans", "Indiana Hoosiers",
             "AP No. 12 this week, first season ranked since 2021, last ranked this high in 2015.",
             "last national title: 1966; last winning season: 2023.")]}}}}


def verdict(text, gs=GS):
    out = []
    flags = check_history_claims(text, gs, out)
    check_len = len(flags) == sum(1 for v, *_ in out if v != "confirmed")
    if not check_len:
        FAILS.append(f"flag count for {text!r}")
    return [v for v, *_ in out]


check("THE CASE: 'a 53-year Finals drought' (53 is the title drought)",
      verdict("The Knicks ended a 53-year Finals drought."), ["HIGH"])
flag = check_history_claims("The Knicks ended a 53-year Finals drought.", GS)[0]
has("...and the flag names the real figure, in its kind", flag,
    "FACT FLAG [HIGH]", "last NBA Finals appearance: 1998-99", "27 years before this issue")
check("'first title since 1973': the title drought, confirmed",
      verdict("The Knicks are chasing their first title since 1973."), ["confirmed"])
check("'a 53-year title drought': confirmed", verdict("The Knicks' 53-year title drought goes on."), ["confirmed"])
check("'first Finals in 27 years': confirmed",
      verdict("The Knicks are back in the Finals for the first time in 27 years."), ["confirmed"])
check("'since '99' reads as 1999", verdict("Their first Finals trip since '99 for the Knicks."), ["confirmed"])
check("a season label claim ('since 1998-99') matches the season",
      verdict("The Knicks' first Finals appearance since 1998-99."), ["confirmed"])
check("streak claim matching the fact", verdict("The Knicks have won 7 straight, their longest streak since 2014."),
      ["confirmed"])
check("streak claim with the wrong year: HIGH",
      verdict("The Knicks have won 7 straight, their longest streak since 2019."), ["HIGH"])
check("streak claim about a different length: not this fact, LOW",
      verdict("The Knicks have won 5 straight, their longest streak since 2019."), ["LOW"])
check("'none since at least 1999', claim of 2005: HIGH",
      verdict("The Chiefs have lost 3 in a row for the first time since 2005."), ["HIGH"])
check("'none since at least 1999', claim of 1987: past the data, LOW",
      verdict("The Chiefs have lost 3 in a row for the first time since 1987."), ["LOW"])
check("head-to-head: the drought yesterday's win ended",
      verdict("The Bills beat the Chiefs for the first time since 2018."), ["confirmed"])
check("head-to-head: wrong year, HIGH", verdict("The Bills beat the Chiefs for the first time since 2012."), ["HIGH"])
check("venue: 'none since at least 1999', claim of 2010: HIGH",
      verdict("The Bills won in Kansas City for the first time since 2010."), ["HIGH"])
SOX = {"history": {"status": "ok", "as_of": "2026-07-31", "leagues": {"mlb": {
    "label": "MLB", "season": 2026, "game_data_from": 1876, "teams": [team("Boston Red Sox", "Los Angeles Dodgers",
        "won at Los Angeles yesterday, their first win there since 2016-08-05.")]}}}}
check("replay 2026-08-01: 'Red Sox Win in LA for the First Time Since 2016'",
      verdict("Red Sox Win in LA for the First Time Since 2016", SOX), ["confirmed"])
check("...and 'their first win in LA in ten years'",
      verdict("The Red Sox won again. It's also their first win in LA in ten years.", SOX), ["confirmed"])
check("'in Lansing' is not 'in LA'",
      verdict("The Red Sox won. Their first win in Lansing since 2012.", SOX), ["LOW"])
check("NFL: the 1993 season's Super Bowl was played in 1994",
      verdict("The Bills are eyeing their first Super Bowl appearance since 1994."), ["confirmed"])
check("NFL: a pre-Super Bowl title is not a Super Bowl claim",
      verdict("The Bills' first Super Bowl title since 1970 would be their first ever."), ["LOW"])
check("NFL: 'haven't missed the playoffs since' is the inverse of 'last appearance': LOW",
      verdict("The Bills haven't missed the playoffs since 2019."), ["LOW"])
check("NFL: a playoff claim older than an old fact: HIGH",
      verdict("The Chiefs are back in the playoffs for the first time since 2010.",
              {"history": {"status": "ok", "as_of": "2026-01-05", "leagues": {"nfl": {"label": "NFL",
               "season": 2025, "game_data_from": 1999, "teams": [team("Kansas City Chiefs", "Denver Broncos",
               "last league title: 2023; last championship game appearance: 2024; "
               "last playoff appearance: 2018; last winning season: 2024.")]}}}}), ["HIGH"])
check("college: poll", verdict("Michigan State is ranked for the first time since 2021."), ["confirmed"])
check("college: 'Michigan' is not Michigan State",
      verdict("Michigan won its first national title since 1997."), ["LOW"])

print("What keeps HIGH honest")
check("two teams named, both with the fact: never HIGH",
      verdict("The Knicks and the Celtics: neither had reached the Finals since 2010."), ["LOW"])
check("no team in the sentence: the section's team may confirm...",
      verdict("The Knicks rolled again.\nTheir first title since 1973 is in reach."), ["confirmed"])
check("...but never convict", verdict("The Knicks rolled again.\nIt is their first Finals since 2010."), ["LOW"])
check("a player's claim is out of scope: LOW",
      verdict("The Knicks won; it was his first 40-point game since 2019."), ["LOW"])
check("a player's 'win over' is not the team's", verdict("Brunson got his first win over the Celtics since 2010."), ["LOW"])
check("a series win is not a title or Finals fact: LOW",
      verdict("The Knicks won their first playoff series since 2000."), ["LOW"])
check("conference finals are not the Finals: LOW",
      verdict("The Knicks reached the conference finals for the first time since 2000."), ["LOW"])
check("'five straight winning seasons' is not a game streak: LOW",
      verdict("The Knicks have five straight winning seasons for the first time since 1995."), ["LOW"])
JAYS = {"history": {"status": "ok", "as_of": "2026-08-02", "leagues": {"mlb": {
    "label": "MLB", "season": 2026, "game_data_from": 1876, "teams": [team("Toronto Blue Jays", "Boston Red Sox",
        "last World Series title: 1993; last World Series appearance: 2025; last winning season: 2025.")]}}}}
check("replay 2026-08-03: last October's 'first World Series since 1993' is that very trip: LOW, not HIGH",
      verdict("Gausman came out of the bullpen last October. Sent the Blue Jays to their first World Series "
              "since 1993.", JAYS), ["LOW"])
check("...while an old fact still convicts an older claim",
      verdict("The Blue Jays have their first World Series title since 1985 in sight.", JAYS), ["HIGH"])
check("replay 2026-06-01: a superlative measures something, it is no Finals drought: LOW, never confirmed",
      verdict("The Knicks rolled. Biggest Finals court redesign in 17 years.",
              {"history": {"status": "ok", "as_of": "2026-05-31", "leagues": {"nba": {"label": "NBA",
               "season": 2025, "game_data_from": 1946, "teams": [team("Orlando Magic", None,
               "no NBA title in franchise history; last NBA Finals appearance: 2008-09.")]}}}}), ["LOW"])
check("'missed' is a whole word: 'Mississippi' is not the inverse-fact guard",
      verdict("The Knicks, back from Mississippi, are in their first Finals since 1999."), ["confirmed"])
check("a team the block doesn't know: LOW", verdict("The Lakers won their first title since 2020."), ["LOW"])
check("not a history claim: nothing", verdict("The league has existed since 1997."), [])
check("a decade is relative framing: nothing", verdict("The Knicks' first Finals since the '90s."), [])
check("'since the 1990s' likewise", verdict("The Knicks' first Finals since the 1990s."), [])
check("no block: every claim LOW", verdict("The Knicks ended a 53-year Finals drought.",
                                           {"history": {"status": "unavailable"}}), ["LOW"])

print("Pass 3 end to end")
html = ("<h1>Lead</h1><p>The Knicks ended a 53-year Finals drought -- and \"wow\".</p>"
        "<blockquote class=\"twitter-tweet\"><p>Knicks: first Finals since 1980!</p></blockquote>"
        "<!-- EDITOR NOTE: first since 1960 -->"
        "<h2>Around the League</h2><p>The Knicks' first title since 1973 is close.</p>")
tmp = REPO / "uat" / "tests" / "_history_claims_gs.json"
import json                                                        # noqa: E402
tmp.write_text(json.dumps(GS), encoding="utf-8")
try:
    out, n = validate_claims(html, tmp)
finally:
    tmp.unlink()
check("one flag: the drought; the tweet and an old comment are not our prose", n, 1)
flag = out[out.index("FACT FLAG"):out.index("-->", out.index("FACT FLAG"))]
check("the quoted sentence can't close the comment early", "--" in flag, False)
check("...nor carry a double quote", '"' in flag.split(": ", 1)[1].split(" makes")[0].strip('"'), False)

# ---------------------------------------------------------------------------
print("SLA-110: a team in the postseason gets its droughts")
# ---------------------------------------------------------------------------
# Until SLA-110 a team that played a playoff game got no facts at all, so
# the exact Knicks setup (a Finals claim in June) could never be checked.
PLAYOFF_GS = {"yesterday_date": "2026-05-30", "sports": {"nba": {"yesterday_games": [
    {"home_team": "New York Knicks", "away_team": "Indiana Pacers", "completed": True, "playoffs": True,
     "home_id": "18", "away_id": "11"}]}, "mlb": {"yesterday_games": [
    {"home_team": "New York Yankees", "away_team": "Boston Red Sox", "completed": True, "playoffs": False,
     "home_id": "10", "away_id": "2"}]}}}
tip = hs.teams_in_play(PLAYOFF_GS)
check("a playoff game's teams are in play", [r["name"] for r in tip["nba"]], ["New York Knicks", "Indiana Pacers"])
check("...marked postseason", [r["postseason"] for r in tip["nba"]], [True, True])
check("a regular-season game's are not", [r["postseason"] for r in tip["mlb"]], [False, False])
check("a postseason team's droughts drop the winning-season line (its story is the title round)",
      hs.drought_fact(NBA, {"last_title": 1972, "last_title_game": 1998}, None, {"last_winning": 2024},
                      2025, 1946, None, postseason=True),
      "last NBA title: 1972-73; last NBA Finals appearance: 1998-99.")
check("NFL keeps 'last playoff appearance' separate from the title and Super Bowl facts",
      parsed(hs.drought_fact(NFL, {"last_title": 1965, "last_title_game": 1993}, {"last_playoffs": 2024}, None,
                             2025, 1999, 1999, postseason=True)),
      [("title", [1965], None, False), ("title_game", [1993], None, False), ("playoff", [2024], None, False)])


class FakeConn:
    """Answers history_source's queries with canned rows and records what ran."""
    def __init__(self):
        self.ran = []

    def execute(self, sql, params=None):
        self.ran.append(sql)
        rows = []
        if sql is hs.CURRENT_TEAMS:
            rows = {"nba": [{"team_id": 1, "franchise_id": 1, "full_name": "New York Knicks", "nickname": "Knicks",
                             "location": "New York"},
                            {"team_id": 2, "franchise_id": 2, "full_name": "Indiana Pacers", "nickname": "Pacers",
                             "location": "Indiana"}]}.get(params["league"], [])
        elif sql is hs.SEASON:
            self.season_days = params["days"]
            rows = [{"year": 2025, "first_year": 1946}]
        elif sql is hs.TITLES:
            rows = [{"franchise_id": 1, "last_title": 1972, "last_title_game": 1998, "first_title_year": 1947},
                    {"franchise_id": 2, "last_title": None, "last_title_game": 1999, "first_title_year": 1947}]
        cols = list(rows[0]) if rows else ["x"]

        class Cur:
            description = [type("C", (), {"name": c}) for c in cols]
            def fetchall(self_inner):
                return [tuple(r[c] for c in cols) for r in rows]
        return Cur()


conn = FakeConn()
block = hs.build_history(conn, {"yesterday_date": "2026-05-30", "sports": {"nba": PLAYOFF_GS["sports"]["nba"]}})
knx = block["leagues"]["nba"]["teams"][0]
check("the Knicks, in the postseason, get their droughts", knx["facts"],
      ["last NBA title: 1972-73; last NBA Finals appearance: 1998-99."])
check("...and are marked postseason", knx["postseason"], True)
check("a postseason game finds its season weeks after the regular season ended", conn.season_days, 250)
check("a playoff-only day never builds the regular-season game table (no streaks, starts, head-to-head)",
      any(sql is hs.BUILD_SIDES or sql is hs.STREAKS for sql in conn.ran), False)
summary = "\n".join(hs.summary_lines({"history": block}))
has("the block says the droughts are as of before this postseason", summary,
    "New York Knicks (in the postseason; titles and appearances are before this one): last NBA title: 1972-73")

GS_POST = {"history": block}
check("THE CASE, live: the Knicks in the postseason, 'a 53-year Finals drought'",
      verdict("The Knicks ended a 53-year Finals drought.", GS_POST), ["HIGH"])
check("...'their first Finals since 1999': confirmed",
      verdict("The Knicks are in their first Finals since 1999.", GS_POST), ["confirmed"])
check("...'first title since 1973' stays a title claim: confirmed",
      verdict("The Knicks are four wins from their first title since 1973.", GS_POST), ["confirmed"])
check("...'first postseason series win since 2000' is round history (SLA-77): LOW",
      verdict("The Knicks won their first playoff series since 2000.", GS_POST), ["LOW"])

CANES = {"history": {"status": "ok", "as_of": "2026-05-09", "leagues": {"nhl": {"label": "NHL", "season": 2025,
    "game_data_from": 1917, "teams": [team("Carolina Hurricanes", "Philadelphia Flyers",
        "last Stanley Cup: 2005-06; last Stanley Cup Final appearance: 2005-06.")]}}}}
check("replay 2026-05-10: a conference-finals aside doesn't hide the title claim after it",
      verdict("The Hurricanes, who reached the Eastern Conference finals for the third time in four years, "
              "are looking for their first Stanley Cup since 2006.", CANES), ["confirmed"])
check("...while a conference-finals claim itself stays LOW",
      verdict("The Hurricanes reached the Eastern Conference finals for the first time since 2009.", CANES), ["LOW"])
check("replay 2026-06-12: 'their first Cup since 2006' is the Stanley Cup",
      verdict("The Hurricanes won again. One more win and they get their first Cup since 2006.", CANES), ["confirmed"])
check("'World Cup' is never the Stanley Cup",
      verdict("The Hurricanes' first World Cup since 1990.", CANES), ["LOW"])

MLB_POST = {"history": {"status": "ok", "as_of": "2026-10-02", "leagues": {"mlb": {"label": "MLB", "season": 2026,
    "game_data_from": 1876, "teams": [team("Seattle Mariners", "Toronto Blue Jays",
        "no World Series title in franchise history; no World Series appearance in franchise history.")]}}}}
check("'won the pennant' is a World Series appearance: never in franchise history, so HIGH",
      verdict("The Mariners won their first pennant since 2001.", MLB_POST), ["HIGH"])
check("'first World Series title' with no title ever: HIGH",
      verdict("The Mariners are chasing their first World Series title since 1995.", MLB_POST), ["HIGH"])
check("'first postseason appearance since' has no MLB fact (no playoff games stored): LOW",
      verdict("The Mariners are in the postseason for the first time since 2022.", MLB_POST), ["LOW"])

# ---------------------------------------------------------------------------
print("SLA-111: teams the day's stories name get facts too")
# ---------------------------------------------------------------------------
plan = {"lead_story": {"topic": "Knicks headed to the Finals", "headline": "The Knicks are back",
                       "tweets": [{"url": "https://x.com/a/status/1", "account": "a",
                                   "text": "Boston Celtics fans in shambles"}],
                       "beats": [{"angle": "No. 7 Miami's quarterback", "landing": "Georgia Tech too"}]},
        "story_log": [{"topic_key": "new-york-giants-trade", "title": "Giants of the game"}]}
txt = hs.story_text(plan)
check("story text keeps the plan's own words", "Knicks headed to the Finals" in txt, True)
check("...but not the tweets it quotes", "Celtics" in txt, False)
check("...nor topic keys", "new-york-giants" in txt, False)

POOL = {
    "nba": [{"team_id": 1, "franchise_id": 1, "full_name": "New York Knicks", "nickname": "Knicks", "location": "New York"},
            {"team_id": 2, "franchise_id": 2, "full_name": "Miami Heat", "nickname": "Heat", "location": "Miami"}],
    "nfl": [{"team_id": 3, "franchise_id": 3, "full_name": "New York Giants", "nickname": "Giants", "location": "New York"}],
    "mlb": [{"team_id": 4, "franchise_id": 4, "full_name": "San Francisco Giants", "nickname": "Giants",
             "location": "San Francisco"}],
    "ncaafb": [{"team_id": 5, "franchise_id": 5, "full_name": "Georgia Bulldogs", "nickname": "Bulldogs", "location": "Georgia"},
               {"team_id": 6, "franchise_id": 6, "full_name": "Miami Hurricanes", "nickname": "Hurricanes",
                "location": "Miami"}]}


def picks(text):
    return sorted(t["full_name"] for rows in hs.named_teams(text, POOL).values() for t in rows)


check("a unique nickname names its team", picks("The Knicks are rolling."), ["New York Knicks"])
check("'Giants' alone is two teams: neither", picks("The Giants won."), [])
check("...the full name is one", picks("The New York Giants won."), ["New York Giants"])
check("a nickname must be capitalised as a name", picks("giants of the game"), [])
check("a school by its name", picks("Georgia rolled."), ["Georgia Bulldogs"])
check("...but not the start of another school", picks("Georgia Tech rolled."), [])
check("a school sharing a pro city needs its ranking", picks("Miami won again."), [])
check("...and with it, counts", picks("No. 7 Miami won again."), ["Miami Hurricanes"])


class StoryConn:
    """Canned answers for build_story_facts: the NBA out of season (unless
    told otherwise), with the 2025-26 title in the database, as it is today."""
    def __init__(self, nba_in_season=False):
        self.nba_in_season, self.ran = nba_in_season, []

    def execute(self, sql, params=None):
        self.ran.append((sql, dict(params or {})))
        rows = []
        if sql is hs.CURRENT_TEAMS:
            rows = POOL.get(params["league"], [])
        elif sql is hs.CFB_RANKED:
            rows = POOL["ncaafb"]
        elif sql is hs.SEASON:
            if params["league"] == "nba" and (params["days"] == 400 or self.nba_in_season):
                rows = [{"year": 2025, "first_year": 1946}]
        elif sql is hs.DECIDED:
            rows = [{"one": 1}] if params["year"] == 2025 else []
        elif sql is hs.WINNING:
            rows = [{"franchise_id": 1, "last_winning": 2025}]
        elif sql is hs.TITLES:
            won = params["year"] > 2025            # the Knicks' 2025-26 title, once it counts
            rows = [{"franchise_id": 1, "last_title": 2025 if won else 1972,
                     "last_title_game": 2025 if won else 1998, "first_title_year": 1947}]
        cols = list(rows[0]) if rows else ["x"]

        class Cur:
            description = [type("C", (), {"name": c}) for c in cols]

            def fetchall(self_inner):
                return [tuple(r[c] for c in cols) for r in rows]
        return Cur()


def story_block(day, text, conn=None, have=()):
    gs = {"yesterday_date": day, "history": {"status": "ok", "as_of": day, "leagues": {}}}
    for name in have:
        gs["history"]["leagues"].setdefault("nba", {"label": "NBA", "season": 2025, "game_data_from": 1946,
                                                    "teams": []})["teams"].append(team(name, None))
    n = hs.build_story_facts(conn or StoryConn(), gs, text)
    return gs, n


gs, n = story_block("2026-05-31", "The Knicks ended a 53-year Finals drought.")
knx = gs["history"]["leagues"]["nba"]["teams"][0]
check("THE CASE, as it happened: the Knicks hadn't played, but the story names them", n, 1)
check("...out of season, title not yet on the calendar: droughts BEFORE 2025-26", knx["facts"],
      ["last NBA title: 1972-73; last NBA Finals appearance: 1998-99; last winning season: 2025-26."])
has("...and the block says where they stop", "\n".join(hs.summary_lines(gs)),
    "New York Knicks (named in today's stories; titles and appearances before the 2025-26 postseason)")
check("...so the 2026-06-01 sentence is caught: HIGH",
      verdict("The Knicks ended a 53-year Finals drought.", gs), ["HIGH"])
gs, _ = story_block("2026-07-01", "The Knicks parade.")
check("once the calendar says the title is decided, it counts",
      gs["history"]["leagues"]["nba"]["teams"][0]["facts"],
      ["last NBA title: 2025-26; last NBA Finals appearance: 2025-26; last winning season: 2025-26."])
check("...and the 'before' tag goes", gs["history"]["leagues"]["nba"]["teams"][0]["before"], None)
gs, n = story_block("2026-05-31", "The Knicks again.", have=["New York Knicks"])
check("a team already in the block (it played) is not added twice", n, 0)
conn = StoryConn(nba_in_season=True)
gs, _ = story_block("2026-01-15", "The Knicks again.", conn)
check("in season: streaks and starts are computed for a story team too",
      any(sql is hs.BUILD_SIDES for sql, _ in conn.ran), True)
check("...and its titles count only seasons before this one",
      next(p for sql, p in conn.ran if sql is hs.TITLES)["year"], 2025)
_cap = hs.MAX_STORY_TEAMS
hs.MAX_STORY_TEAMS = 1
gs, n = story_block("2026-05-31", "The Knicks and the Heat.")
hs.MAX_STORY_TEAMS = _cap
check("a busy day is capped", n, 1)

print("SLA-111: never fatal")
check("no database today: nothing",
      hs.extend_for_stories({"history": {"status": "unavailable"}}, plan, url="x"),
      "history: no database today, story teams skipped")
check("no URL: nothing", hs.extend_for_stories({"history": {"status": "ok"}}, plan, url=""),
      "history: SPORTS_DB_URL is not set, story teams skipped")


def boom(url):
    raise RuntimeError(f"could not connect to {url}")


gs = {"yesterday_date": "2026-05-31", "history": {"status": "ok", "as_of": "2026-05-31", "leagues": {}}}
msg = hs.extend_for_stories(gs, plan, url="postgresql://u:s3cret@h/db", connect=boom)
check("a failed connection is reported, not raised", msg.startswith("history: story teams skipped"), True)
check("...without the password", "s3cret" in msg, False)
check("...and the block is untouched", gs["history"]["leagues"], {})

print("SLA-111: Pass 3 reads the runner's game_state, not just the file")
gs, _ = story_block("2026-05-31", "The Knicks.")
out, n = validate_claims("<h1>Lead</h1><p>The Knicks ended a 53-year Finals drought.</p>",
                         REPO / "does-not-exist.json", game_state=gs)
check("the story team's fact reaches Pass 3", n, 1)
check("...as a HIGH", "FACT FLAG [HIGH]" in out, True)
for runner, call in (("generate_newsletter.py", "validate_claims(draft_html, GAME_STATE_PATH, game_state=game_state)"),
                     ("uat/run_uat.py", "validate_claims(html, G.GAME_STATE_PATH, game_state=game_state)")):
    src = (REPO / runner).read_text(encoding="utf-8")
    has(f"{runner}: extends history after Pass 1 and hands it to Pass 3", src,
        "history_source.extend_for_stories(game_state, story_plan)", call)

# ---------------------------------------------------------------------------
print("SLA-112: the final draft is re-checked after the editor")
# ---------------------------------------------------------------------------
from claim_validator import final_history_check     # noqa: E402
import pipeline_status as PS                          # noqa: E402
import email_newsletter as EN                         # noqa: E402


def status_with(**over):
    """Every declared pre-email stage passed (as test_pipeline_status builds it), then overridden."""
    base = {"date": "2026-10-03", "run_started": "2026-10-03T02:17:00-04:00", "email_sent": True,
            "stages": [{"name": s.name, "ok": True, "exit_code": 0, "critical": s.critical, "seconds": 5}
                       for s in PS.PIPELINE_STAGES if not s.after_email]}
    base.update(over)
    return base


EDITED = ("<h1>Lead</h1><p>The Knicks won again. The Knicks ended a 53-year Finals drought. What a night.</p>"
          "<h2>Around the League</h2><p>The Knicks are chasing their first title since 1973.</p>"
          "<blockquote class=\"twitter-tweet\"><p>Knicks: first Finals since 1980!</p></blockquote>")
out, rep = final_history_check(EDITED, GS, autocut=False)
check("THE CASE: an editor that leaves a contradicted claim in is caught",
      [(r["level"], r["section"]) for r in rep], [("HIGH", "Lead")])
check("...a confirmed claim is not reported", any("1973" in r["sentence"] for r in rep), False)
check("...a tweet's own claim is never checked", any("1980" in r["sentence"] for r in rep), False)
check("...the draft is noted, not rewritten", "The Knicks ended a 53-year Finals drought." in out, True)
check("...with a note after the heading", "HISTORY CLAIM LEFT IN BY THE EDITOR [HIGH]" in out, True)
check("...nothing cut by default", rep[0]["cut"], False)

out, rep = final_history_check(EDITED, GS, autocut=True)
check("the switch cuts the sentence instead", "53-year" in out, False)
check("...leaving the rest of the paragraph", "<p>The Knicks won again. What a night.</p>" in out, True)
check("...and reports it as cut", rep[0]["cut"], True)
spans = "<h1>Lead</h1><p>The Knicks ended a <b>53-year</b> Finals drought. Wow.</p>"
out, rep = final_history_check(spans, GS, autocut=True)
check("a sentence that spans markup is never mangled: noted instead of cut",
      (rep[0]["cut"], "53-year" in out, "HISTORY CLAIM LEFT IN" in out), (False, True, True))

import os                                             # noqa: E402
os.environ["SLAP_HISTORY_AUTOCUT"] = "1"
_, rep = final_history_check(EDITED, GS)
os.environ.pop("SLAP_HISTORY_AUTOCUT")
check("the switch is the SLAP_HISTORY_AUTOCUT setting", rep[0]["cut"], True)
_, rep = final_history_check(EDITED, GS)
check("...and it is off when unset", rep[0]["cut"], False)

sourced = ("<h1>Lead</h1><p>The Lakers' first title since 2020 would be huge.</p>"
           "<blockquote class=\"twitter-tweet\"><p>Lakers last won it all in 2020</p></blockquote>")
check("a claim a tweet in the same section carries is sourced (Check 8, rule 1)",
      final_history_check(sourced, GS)[1], [])
points = ("<h1>Lead</h1><p>The Lakers ended a 53-year drought.</p>"
          "<blockquote class=\"twitter-tweet\"><p>LeBron: 53 points tonight</p></blockquote>")
check("...but a tweet's '53 points' never sources a '53-year drought'",
      len(final_history_check(points, GS)[1]), 1)

print("SLA-112: it reaches the morning email")
st = status_with(history_claims=[{"level": "HIGH", "section": "Lead", "sentence": "x", "cut": False}])
check("a contradicted claim left in moves the headline to PARTIAL", PS.verdict(st)[0], "partial")
st = status_with(history_claims=[{"level": "HIGH", "section": "Lead", "sentence": "x", "cut": True}])
check("...not once it was cut", PS.verdict(st)[0], "success")
st = status_with(history_claims=[{"level": "LOW", "section": "Lead", "sentence": "x", "cut": False}])
check("an unconfirmed one is listed, not a headline (it may well be true)", PS.verdict(st)[0], "success")
panel = EN._history_claims_html({"history_claims": [
    {"level": "HIGH", "section": "Lead", "sentence": "The Knicks ended a 53-year Finals drought.", "cut": False},
    {"level": "LOW", "section": "ATL", "sentence": "Norway hadn't qualified since 1998.", "cut": False}]})
has("the email lists both, the contradicted one as such", panel, "HISTORY CLAIMS LEFT IN",
    "CONTRADICTED by the database", "unconfirmed", "53-year Finals drought", "before the noon publish")
check("...and says nothing on a clean day", EN._history_claims_html({}), "")
for runner, call in (("generate_newsletter.py", "final_history_check(final_html, game_state)"),
                     ("uat/run_uat.py", "final_history_check(html, game_state)")):
    has(f"{runner}: re-checks the final draft", (REPO / runner).read_text(encoding="utf-8"), call)
has("the workflow passes the switch from a repository variable",
    (REPO / ".github/workflows/daily-newsletter.yml").read_text(encoding="utf-8"),
    "SLAP_HISTORY_AUTOCUT: ${{ vars.SLAP_HISTORY_AUTOCUT }}")

# ---------------------------------------------------------------------------
print("The prompts point at it")
# ---------------------------------------------------------------------------
for tree in ("prompts", "uat/prompts"):
    rule3 = (REPO / tree / "rolling_feedback.txt").read_text(encoding="utf-8")
    has(f"{tree}: RULE 3 says Pass 3 checks history claims", rule3,
        "Pass 3 checks every", "write the specific figure ONLY when it is")
    editor = (REPO / tree / "editor_prompt.txt").read_text(encoding="utf-8")
    has(f"{tree}: Check 8 Category C leaves a listed fact alone", editor,
        "If HISTORICAL CONTEXT in the ground truth block lists the same fact", "same KIND")
    has(f"{tree}: Check 9 says how to act on a history flag", editor,
        "A history flag quotes the sentence", "a 27-year Finals")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
