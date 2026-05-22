"""
probe_leaders.py  —  run from the repo root:
    python probe_leaders.py

Hits the ESPN byathlete endpoint for MLB batting (homeRuns) and dumps
the raw structure so we can see exactly what field names and value paths
ESPN is returning. Paste the full output back to Claude.
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

# ── 1. byathlete endpoint (the fallback path) ─────────────────────────────
url = (
    "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
    "/statistics/byathlete?region=us&lang=en&contentorigin=espn"
    "&isqualified=true&category=batting&sort=batting.homeRuns:desc"
    "&season=2026&seasontype=2&limit=5"
)
print("=== BYATHLETE ENDPOINT ===")
print(f"URL: {url}\n")

req  = Request(url, headers=HEADERS)
data = json.loads(urlopen(req, timeout=15).read())

print(f"Top-level keys: {sorted(data.keys())}\n")

athletes = data.get("athletes", [])
print(f"athletes[] count: {len(athletes)}\n")

if athletes:
    first = athletes[0]
    print(f"athletes[0] top-level keys: {sorted(first.keys())}\n")

    # Check for displayValue / value at athlete level
    print(f"athletes[0].displayValue = {first.get('displayValue')!r}")
    print(f"athletes[0].value        = {first.get('value')!r}\n")

    # Athlete sub-object
    ath = first.get("athlete", {})
    print(f"athletes[0].athlete keys: {sorted(ath.keys())}")
    print(f"  shortName: {ath.get('shortName')!r}")
    print(f"  teamShortName: {ath.get('teamShortName')!r}")
    team = ath.get("team", {}) or {}
    print(f"  team.abbreviation: {team.get('abbreviation')!r}\n")

    # categories array (per-athlete stat groups)
    cats = first.get("categories", [])
    print(f"athletes[0].categories[] count: {len(cats)}")
    for i, cat in enumerate(cats):
        print(f"\n  categories[{i}] keys: {sorted(cat.keys())}")
        print(f"    name:          {cat.get('name')!r}")
        print(f"    names:         {cat.get('names', cat.get('labels', []))[:15]}")
        print(f"    values:        {cat.get('values', [])[:15]}")
        print(f"    totals:        {cat.get('totals', [])[:15]}")
        print(f"    displayValues: {cat.get('displayValues', [])[:15]}")

    # Show top 5 athletes name + every value field
    print("\n=== TOP 5 ATHLETES (name / team / all value paths) ===")
    for item in athletes[:5]:
        ath2 = item.get("athlete", {})
        name = ath2.get("shortName", ath2.get("displayName", "?"))
        team2 = (ath2.get("teamShortName", "")
                 or (ath2.get("team") or {}).get("abbreviation", "")
                 or (item.get("team") or {}).get("abbreviation", ""))
        dv = item.get("displayValue", "MISSING")
        v  = item.get("value", "MISSING")
        cats2 = item.get("categories", [])
        print(f"\n  {name} ({team2})")
        print(f"    .displayValue = {dv!r}")
        print(f"    .value        = {v!r}")
        for ci, cat in enumerate(cats2):
            names = cat.get("names", cat.get("labels", []))
            vals  = cat.get("values", [])
            tots  = cat.get("totals", [])
            dvs   = cat.get("displayValues", [])
            # Find HR index
            for key in ("HR", "homeRuns"):
                if key in names:
                    idx = names.index(key)
                    print(f"    categories[{ci}] '{key}' idx={idx} → "
                          f"values={vals[idx] if idx < len(vals) else 'OOB'!r}, "
                          f"totals={tots[idx] if idx < len(tots) else 'OOB'!r}, "
                          f"displayValues={dvs[idx] if idx < len(dvs) else 'OOB'!r}")

# ── 2. v3 leaders endpoint ────────────────────────────────────────────────
print("\n\n=== V3 LEADERS ENDPOINT ===")
v3_url = (
    "https://site.api.espn.com/apis/site/v3/sports/baseball/mlb"
    "/leaders?season=2026&seasontype=2"
)
print(f"URL: {v3_url}\n")

req3  = Request(v3_url, headers=HEADERS)
try:
    d3 = json.loads(urlopen(req3, timeout=15).read())
    print(f"Top-level keys: {sorted(d3.keys())}")
    cats3 = d3.get("categories", d3.get("leaders", []))
    print(f"categories/leaders count: {len(cats3)}")
    if cats3 and isinstance(cats3[0], dict):
        c = cats3[0]
        print(f"\nFirst category keys: {sorted(c.keys())}")
        print(f"  name: {c.get('name')!r}")
        leaders = c.get("leaders", c.get("athletes", []))
        print(f"  leaders count: {len(leaders)}")
        if leaders:
            first_l = leaders[0]
            print(f"  leaders[0] keys: {sorted(first_l.keys())}")
            print(f"  leaders[0].displayValue = {first_l.get('displayValue')!r}")
            print(f"  leaders[0].value        = {first_l.get('value')!r}")
            ath_l = first_l.get("athlete", {})
            print(f"  leaders[0].athlete.shortName = {ath_l.get('shortName')!r}")
except Exception as e:
    print(f"v3 error: {e}")
