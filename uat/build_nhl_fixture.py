"""Build uat/fixtures/nhl/games.json from live ESPN: real NHL games, parsed by
the production functions, for the offline SLA-148 tests. Needs network; the
tests do not. Re-run only to refresh the fixture.

The committed games.json also holds a regular-season shootout (PIT @ CAR, 2026-03-10) and a
comeback (NSH @ SEA, 2026-03-10), picked from March by ESPN's status detail ("Final/SO") and by
nhl_bundle.flow(); this script covers the postseason games and does not reproduce those two."""
import json, sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_sports_data as F

def games_on(d):
    return [g for g in F.fetch_scoreboard("hockey", "nhl", d) if g.get("completed")]

def with_box(g):
    s = F.fetch_game_summary("hockey", "nhl", g["game_id"])
    g = dict(g); g["box_score"] = F.parse_box_score(s, "nhl"); return g

want = {"regulation", "ot", "so", "playoff", "shutout", "comeback"}
got = {}
d = datetime.date(2026, 5, 10)
days = 0
while want - set(got) and days < 120:
    for g in games_on(d):
        tags = []
        if g.get("playoffs"): tags.append("playoff")
        if g["status"].endswith("so") or "shootout" in g["status"]: tags.append("so")
        elif g.get("overtime"): tags.append("ot")
        else: tags.append("regulation")
        if min(g["home_score"], g["away_score"]) == 0: tags.append("shutout")
        for t in tags:
            if t in want and t not in got:
                got[t] = with_box(g); print(t, g["matchup"], d)
    d -= datetime.timedelta(days=1); days += 1
Path(__file__).parent.joinpath("fixtures/nhl/games.json").write_text(
    json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8")
print("saved", sorted(got))
