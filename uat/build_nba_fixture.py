"""Build uat/fixtures/nba/games.json from live ESPN: real NBA games, parsed by
the production functions, for the offline SLA-147 tests. Needs network; the
tests do not. Re-run only to refresh the fixture.

It keeps the four 2026-10-21 games whose ESPN running score is wrong on some
plays (MIN @ MIA, CHI @ TOR, GSW @ LAL, PHX @ POR: the score stalls or goes
backwards), because they are why fetch_sports_data._parse_nba_plays tallies the
score itself. The rest are picked from the postseason and April by what they
look like: an overtime game, a playoff game, a big comeback, a wire-to-wire game.
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_sports_data as F  # noqa: E402
import game_flow  # noqa: E402

STALE = {"Minnesota Timberwolves", "Chicago Bulls", "Golden State Warriors", "Phoenix Suns"}


def games_on(d):
    return [g for g in F.fetch_scoreboard("basketball", "nba", d) if g.get("completed")]


def with_box(g):
    s = F.fetch_game_summary("basketball", "nba", g["game_id"])
    g = dict(g)
    g["box_score"] = F.parse_box_score(s, "nba")
    return g


def flow_of(g):
    pl = g["box_score"]["agate"]["plays"]
    return game_flow.build([{"period": x["period"], "clock": x["clock"], "away": x["away"], "home": x["home"]}
                            for x in pl], (g["away_score"], g["home_score"]))


got = {}
for g in games_on(datetime.date(2026, 10, 21)):
    if g["away_team"] in STALE:
        got["stale_" + g["away_abbr"].lower()] = with_box(g)
        print("stale", g["matchup"])

want = {"ot", "playoff", "comeback", "wire"}
d = datetime.date(2026, 5, 20)
days = 0
while want - set(got) and days < 150:
    for g in games_on(d):
        tags = []
        if g.get("playoffs"):
            tags.append("playoff")
        if g.get("overtime"):
            tags.append("ot")
        todo = [t for t in tags if t not in got]
        probe = None
        if todo or {"comeback", "wire"} - set(got):
            probe = with_box(g)
            f = flow_of(probe)
            if f and f["winner"]:
                if f["max_deficit"][f["winner"]][0] >= 15:
                    tags.append("comeback")
                if f["wire_to_wire"]:
                    tags.append("wire")
        for t in tags:
            if t in want and t not in got:
                got[t] = probe or with_box(g)
                print(t, g["matchup"], d)
    d -= datetime.timedelta(days=1)
    days += 1
Path(__file__).parent.joinpath("fixtures/nba").mkdir(parents=True, exist_ok=True)
Path(__file__).parent.joinpath("fixtures/nba/games.json").write_text(
    json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8")
print("saved", sorted(got))
