"""
probe_leaders2.py  —  run from the repo root:
    python probe_leaders2.py

Shows the top-level categories structure and prints indexed totals
so we can see the column order ESPN uses.
"""
import json
from urllib.request import urlopen, Request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.espn.com",
    "Referer": "https://www.espn.com/",
}

url = (
    "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
    "/statistics/byathlete?region=us&lang=en&contentorigin=espn"
    "&isqualified=true&category=batting&sort=batting.homeRuns:desc"
    "&season=2026&seasontype=2&limit=3"
)

data = json.loads(urlopen(Request(url, headers=HEADERS), timeout=15).read())

# ── Top-level categories ──────────────────────────────────────────────────
print("=== TOP-LEVEL categories[] ===")
top_cats = data.get("categories", [])
print(f"count: {len(top_cats)}")
for i, tc in enumerate(top_cats):
    print(f"\n  [{i}] keys: {sorted(tc.keys())}")
    print(f"       name:        {tc.get('name')!r}")
    print(f"       displayName: {tc.get('displayName')!r}")
    for key in ("names", "labels", "abbreviations", "abbrs"):
        v = tc.get(key)
        if v:
            print(f"       {key}: {v}")

# ── Per-athlete totals with index numbers ─────────────────────────────────
print("\n\n=== PER-ATHLETE totals (indexed) for first 3 athletes ===")
for item in data.get("athletes", [])[:3]:
    ath = item.get("athlete", {})
    name = ath.get("shortName", "?")
    team = ath.get("teamShortName", "?")
    print(f"\n  {name} ({team})")
    for cat in item.get("categories", []):
        if cat.get("name") != "batting":
            continue
        totals = cat.get("totals", [])
        values = cat.get("values", [])
        ranks  = cat.get("ranks", [])
        print(f"    totals  (len={len(totals)}): {list(enumerate(totals))}")
        print(f"    values  (len={len(values)}): {list(enumerate(values))}")
        print(f"    ranks   (len={len(ranks)}):  {list(enumerate(ranks))}")

# ── Also check pitching for ERA ───────────────────────────────────────────
print("\n\n=== PITCHING category (ERA sort) ===")
url2 = (
    "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
    "/statistics/byathlete?region=us&lang=en&contentorigin=espn"
    "&isqualified=true&category=pitching&sort=pitching.ERA:asc"
    "&season=2026&seasontype=2&limit=2"
)
data2 = json.loads(urlopen(Request(url2, headers=HEADERS), timeout=15).read())
top_cats2 = data2.get("categories", [])
print(f"Top-level categories count: {len(top_cats2)}")
for tc in top_cats2:
    for key in ("names", "labels", "abbreviations"):
        v = tc.get(key)
        if v:
            print(f"  [{tc.get('name')}] {key}: {v}")

for item in data2.get("athletes", [])[:2]:
    ath = item.get("athlete", {})
    print(f"\n  {ath.get('shortName','?')} ({ath.get('teamShortName','?')})")
    for cat in item.get("categories", []):
        if cat.get("name") != "pitching":
            continue
        print(f"    totals: {list(enumerate(cat.get('totals',[])))}")
