#!/usr/bin/env python3
"""
SLA-58 — the four source checks that could not run from the cloud sandbox.
Tracked as SLA-63.

Every live sports API is denied at CONNECT by that environment's egress policy,
so NHL/CFBD/CBBD depth stayed documentation-stated in docs/sports-source-evaluation.md.
This answers all four from a normal network. Stdlib only, no installs.

  1. NHL      how far back do standings and results actually go?
  2. CBBD     earliest season with games?  <-- can move a recommendation
  3. CFBD     is history tier-gated, or only call volume?
  4. CFBD     does /roster paginate by year alone, or need a team?
              <-- decides whether the whole epic is free or needs one $5 month

    python3 verify_sla58.py

Check 1 needs no key. Checks 2-4 do (both free, ~1 min to get):
    CFBD: https://collegefootballdata.com/key
    CBBD: https://collegebasketballdata.com/key

    CFBD_API_KEY=xxx CBBD_API_KEY=yyy python3 verify_sla58.py

Note: CFBD and CBBD share one monthly call pool per key. This script spends
about 13 calls against a free-tier pool of 1,000.

Transport failures are reported LOUDLY (a "!" line plus HTTP 0) rather than
returning an empty result. That distinction matters: every source evaluated
here fails silently, so a check that cannot tell "no data" from "blocked"
would answer the CBBD depth question wrong, and confidently.
"""

import json
import os
import sys
import urllib.error
import urllib.request

UA = {"User-Agent": "SLAP-SLA58-verify/1.0"}
TIMEOUT = 25

# CBBD /games truncates each response at this many rows. CFBD does not -- it
# returned 3,745 games for 2025 in one call -- so this is CBBD-specific.
PAGE_CAP = 3000


def get(url, key=None):
    """-> (status, parsed_json_or_None). Never raises."""
    req = urllib.request.Request(url, headers=dict(UA))
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:                      # DNS, TLS, timeout, egress block
        print(f"      ! {type(e).__name__}: {e}")
        return 0, None


def head(n, title):
    print(f"\n{'=' * 72}\nCHECK {n} — {title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# 1. NHL — how far back do standings and results actually go?
# ---------------------------------------------------------------------------

def check_nhl():
    head(1, "NHL official API: real historical depth")
    verdict = []

    # standings-season enumerates every season the standings endpoint knows.
    status, data = get("https://api-web.nhle.com/v1/standings-season")
    if status == 200 and data:
        seasons = sorted(
            s.get("id") for s in data.get("seasons", []) if s.get("id")
        )
        if seasons:
            print(f"  standings-season: {len(seasons)} seasons, "
                  f"{seasons[0]} … {seasons[-1]}")
            verdict.append(f"standings enumerated from {str(seasons[0])[:4]}")
            # Does the oldest one actually return rows, or is it just listed?
            #
            # Probe 02-01, NOT 04-01. The early regular seasons ended in March,
            # so April 1 falls between seasons and returns an empty table for
            # reasons that have nothing to do with coverage: 1969-04-01 gives 0
            # rows while 1969-03-15 gives a full 12-team table. This probe used
            # to say 04-01 and reported the 1917-18 season as unpopulated when
            # it is in fact complete. February is in-season in every NHL era.
            oldest = seasons[0]
            yr = int(str(oldest)[:4])
            st2, d2 = get(f"https://api-web.nhle.com/v1/standings/{yr + 1}-02-01")
            if st2 == 200 and d2:
                rows = d2.get("standings", [])
                print(f"  standings/{yr + 1}-02-01 → HTTP 200, {len(rows)} rows"
                      f"{' (EMPTY — listed but not populated)' if not rows else ''}")
                if rows:
                    r = rows[0]
                    name = (r.get("teamName") or {}).get("default", "?")
                    print(f"      sample: {name} "
                          f"{r.get('wins')}-{r.get('losses')} pts={r.get('points')}")
                    verdict.append(f"standings POPULATED back to {yr + 1}")
                else:
                    verdict.append(f"standings EMPTY at {yr + 1} — listed != populated")
    else:
        print(f"  standings-season → HTTP {status}")

    # Results: walk the modern-era boundary explicitly.
    print("\n  club-schedule-season (game results by season):")
    oldest_scored = None
    for season in ("19171918", "19271928", "19671968", "19871988", "20232024"):
        st, d = get(f"https://api-web.nhle.com/v1/club-schedule-season/MTL/{season}")
        games = len(d.get("games", [])) if (st == 200 and d) else 0
        scored = 0
        if games:
            scored = sum(
                1 for g in d["games"]
                if g.get("homeTeam", {}).get("score") is not None
            )
        flag = "" if games else "   <-- no data"
        print(f"    {season}: HTTP {st}  games={games:<4} with_scores={scored}{flag}")
        if games and scored and oldest_scored is None:
            # OLDEST, not newest: the depth question is answered by the earliest
            # season that returns scores. This used to print verdict[-2:], which
            # reported the most recent seasons probed -- the one fact nobody was
            # in any doubt about.
            oldest_scored = season

    if oldest_scored:
        verdict.append(f"results with scores back to {oldest_scored[:4]}")
    print("\n  VERDICT: " + ("; ".join(verdict) if verdict else "NO DATA — check network"))
    print("  → Replace §2.3 with the oldest season above that returns games")
    print("    WITH scores. Measured 2026-09-22: 1917-18, the full history.")


# ---------------------------------------------------------------------------
# 2. CBBD — earliest season with games?
# ---------------------------------------------------------------------------

def check_cbbd():
    head(2, "CollegeBasketballData: earliest season with games")
    key = os.environ.get("CBBD_API_KEY")
    if not key:
        print("  SKIPPED — set CBBD_API_KEY (free: https://collegebasketballdata.com/key)")
        return

    earliest = None
    # The ladder has to start BELOW the plausible floor or it cannot find one.
    # It used to start at 2003 and duly reported "earliest = 2003" -- which was
    # just its own first rung. The real floor is 1949, 54 years earlier.
    for season in (1940, 1949, 1950, 1980, 2003, 2025):
        st, d = get(f"https://api.collegebasketballdata.com/games?season={season}",
                    key=key)
        n = len(d) if (st == 200 and isinstance(d, list)) else 0
        note = ""
        if st == 401:
            note = "   <-- bad/missing key"
        elif st == 429:
            note = "   <-- QUOTA EXHAUSTED"
        elif st == 200 and n == 0:
            note = "   <-- 200 but EMPTY (before coverage starts)"
        elif n == PAGE_CAP:
            # Exactly 3000 every time is a per-response cap, not a count.
            # 1950 (1,261) and 1980 (2,822) come in under it, which is what
            # gives it away. Reading it as a count understates modern seasons.
            note = f"   <-- AT THE {PAGE_CAP}-ROW CAP (a cap, not a count)"
        print(f"    season={season}: HTTP {st}  games={n}{note}")
        if n and earliest is None:
            earliest = season
        if st in (401, 429):
            return

    print(f"\n  VERDICT: earliest season returning games in this ladder = {earliest}")
    print("  → Measured 2026-09-22: 1949 (22 games), substantial from 1950")
    print("    (1,261). 1946-48 are empty, so 1949 is a real floor. CBBD has")
    print("    real depth and NCAAMB is a Tier A sport.")
    print(f"  → Any season reading exactly {PAGE_CAP} is capped, not counted.")
    print("    Chunk with startDateRange/endDateRange or conference: Jan 2024")
    print("    alone = 1,350 games, SEC 2024 = 342. ~6 calls per modern season.")


# ---------------------------------------------------------------------------
# 3. CFBD — does the free tier cap history as well as call volume?
# ---------------------------------------------------------------------------

def check_cfbd():
    head(3, "CollegeFootballData: is history capped, or only call volume?")
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        print("  SKIPPED — set CFBD_API_KEY (free: https://collegefootballdata.com/key)")
        return

    got_old = False
    for year in (1869, 1900, 1950, 2000, 2025):
        st, d = get(
            f"https://api.collegefootballdata.com/games?year={year}&seasonType=regular",
            key=key)
        n = len(d) if (st == 200 and isinstance(d, list)) else 0
        note = ""
        if st == 401:
            note = "   <-- bad/missing key"
        elif st == 403:
            note = "   <-- FORBIDDEN: history IS tier-gated"
        elif st == 429:
            note = "   <-- QUOTA EXHAUSTED"
        print(f"    year={year}: HTTP {st}  games={n}{note}")
        if st == 200 and n and year <= 1950:
            got_old = True
        if st in (401, 429):
            return

    print()
    if got_old:
        print("  VERDICT: free tier serves pre-1950 games. History is NOT capped;")
        print("  the only free-tier constraint is the 1,000 calls/month quota.")
    else:
        print("  VERDICT: old years returned nothing. History MAY be tier-gated —")
        print("  confirm against collegefootballdata.com/api-tiers before scoping.")
    print("\n  Known from the maintainer's own tier docs (already confirmed):")
    print("    Free 1,000/mo · .edu 3,000 · $1 T1 5,000 · $5 T2 30,000 · $10 T3 75,000")
    print("    ** CFBD and CBBD SHARE one monthly pool on the same key. **")


# ---------------------------------------------------------------------------
# 4. CFBD — does /roster paginate by year alone, or does it need a team?
#    This decides whether the whole epic is free (§7.1).
# ---------------------------------------------------------------------------

def check_cfbd_roster_pagination():
    head(4, "CFBD /roster: year-only, or per-team? (decides free vs $5)")
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        print("  SKIPPED — set CFBD_API_KEY")
        return

    st, d = get("https://api.collegefootballdata.com/roster?year=2024", key=key)
    n = len(d) if (st == 200 and isinstance(d, list)) else 0
    print(f"    /roster?year=2024 (no team): HTTP {st}  players={n}")

    if st == 200 and n > 5000:
        teams = len({r.get("team") for r in d if isinstance(r, dict)})
        print(f"    distinct teams in one call: {teams}")
        print("\n  VERDICT: year-only pagination works — ~25 calls for 25 years.")
        print("  ** College rosters are FREE. The whole epic fits the free tier. **")
    elif st == 200 and n:
        print(f"\n  VERDICT: returned only {n} rows — likely capped or team-scoped.")
        print("  Assume ~130 teams x 25 years = ~3,250 calls -> one paid month ($5).")
    elif st in (400, 422):
        print("\n  VERDICT: team is REQUIRED. ~3,250 calls -> one paid month ($5).")
    else:
        print(f"\n  VERDICT: inconclusive (HTTP {st}). Re-check manually.")


if __name__ == "__main__":
    print("SLA-58 source verification — the checks the sandbox could not run")
    check_nhl()
    check_cbbd()
    check_cfbd()
    check_cfbd_roster_pagination()
    print("\nDone. Paste the output into SLA-58 and I'll fold it into "
          "docs/sports-source-evaluation.md.")
    sys.exit(0)
