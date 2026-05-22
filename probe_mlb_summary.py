"""
Throwaway probe — dumps the structure of one ESPN MLB game summary so we can
see the real field names for scoring plays, team R/H/E totals, and LOB.

Run from the repo root:
    python probe_mlb_summary.py

It uses a completed game id from yesterday (Braves @ Marlins, 401815404).
If that id is stale, replace GAME_ID with any "game_id" from game_state.json's
MLB section. Prints a compact map of keys — paste the whole output back.
"""
import json
from urllib.request import urlopen, Request

GAME_ID = "401815404"  # Braves @ Marlins, 2026-05-19
URL = (f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
       f"?event={GAME_ID}")

req = Request(URL, headers={"User-Agent": "Mozilla/5.0"})
data = json.loads(urlopen(req, timeout=20).read().decode("utf-8"))

print("=== TOP-LEVEL KEYS ===")
print(sorted(data.keys()))

# 1) Where are scoring plays?
print("\n=== scoringPlays present? ===")
sp = data.get("scoringPlays")
print("scoringPlays:", "MISSING" if sp is None else f"{len(sp)} items")
if sp:
    print("  first item keys:", sorted(sp[0].keys()))
    print("  first item:", json.dumps(sp[0], indent=2)[:600])

plays = data.get("plays")
print("plays:", "MISSING" if plays is None else f"{len(plays)} items")
if plays:
    scoring = [p for p in plays if p.get("scoringPlay")]
    print(f"  plays with scoringPlay=True: {len(scoring)}")
    if scoring:
        print("  first scoring play keys:", sorted(scoring[0].keys()))
        print("  first scoring play:", json.dumps(scoring[0], indent=2)[:800])

# 2) Team totals — what stat names exist?
print("\n=== boxscore.teams[].statistics names ===")
for team in data.get("boxscore", {}).get("teams", []):
    ha = team.get("homeAway", "?")
    names = [(s.get("name"), s.get("displayValue")) for s in team.get("statistics", [])]
    print(f"  {ha}: {names}")

# 3) Batting labels — does 2B/3B exist?
print("\n=== boxscore.players[].statistics labels (batting) ===")
for team in data.get("boxscore", {}).get("players", []):
    ha = team.get("homeAway", "?")
    for sg in team.get("statistics", []):
        t = sg.get("type", "")
        t = t.get("text", "") if isinstance(t, dict) else str(t)
        labels = sg.get("labels", sg.get("names", []))
        print(f"  {ha} [{t}] labels: {labels}")
    break  # one team is enough to see labels
