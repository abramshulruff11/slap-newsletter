"""Build uat/fixtures/ncaamb/games.json from live ESPN: real men's college
basketball games, parsed by the production functions, for the offline SLA-146
tests. Needs network; the tests do not. Re-run only to refresh the fixture.

Scans March 2026 (regular season into the NCAA tournament) for one game of each
kind: regulation, overtime, a big comeback, wire to wire, a last-possession
finish, a ranked game, an NCAA tournament game."""
import datetime, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_sports_data as F
import game_flow as gf

B = "basketball", "mens-college-basketball"


def games_on(d):
    url = f"{F.ESPN_BASE}/{B[0]}/{B[1]}/scoreboard?dates={F.format_date(d)}&groups=50&limit=300"
    data = F.fetch_url(url) or {}
    return [g for g in (F.parse_game(e) for e in data.get("events", [])) if g and g.get("completed")]


def with_flow(g):
    s = F.fetch_game_summary(*B, g["game_id"])
    g = dict(g)
    g["game_flow"] = F._parse_ncaamb_flow(s) if s else None
    return g


def kinds(g):
    fl = g.get("game_flow")
    if not fl:
        return []
    ev = [dict(period=p["period"], clock=p["clock"], away=p["away"], home=p["home"]) for p in fl["plays"]]
    f = gf.build(ev, (g["away_score"], g["home_score"]))
    if not f or not f["winner"]:
        return []
    out = ["ot" if len(fl["away_periods"]) > 2 else "regulation"]
    if f["max_deficit"][f["winner"]][0] >= 14: out.append("comeback")
    if f["wire_to_wire"]: out.append("wire")
    if abs(g["home_score"] - g["away_score"]) <= 2 and f["lead_changes"] >= 5: out.append("close")
    if g.get("home_rank") or g.get("away_rank"): out.append("ranked")
    if g.get("season_type") == 3: out.append("tournament")
    return out


want = {"regulation", "ot", "comeback", "wire", "close", "ranked", "tournament"}
got = {}
d = datetime.date(2026, 3, 22)
for _ in range(25):
    for g in games_on(d):
        if want <= set(got): break
        g["game_flow"] = None
        # cheap pre-filter: only fetch summaries while something is still missing
        g = with_flow(g)
        for t in kinds(g):
            if t in want and t not in got:
                got[t] = g; print(t, g["matchup"], d)
    d -= datetime.timedelta(days=1)
    if want <= set(got): break
Path(__file__).parent.joinpath("fixtures/ncaamb/games.json").write_text(
    json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8")
print("saved", sorted(got), "missing", sorted(want - set(got)))
