"""
Run:  python -X utf8 uat/replay_football_claims.py [--from 2026-09-05] [--to 2026-10-04] [--show LOW]

SLA-117: replays Pass 3's football rank and record check (Check 3C) over
archived issues. For each issue date it rebuilds the previous day's NFL and
college football games from ESPN's scoreboard (ranks and records as of that
game) and the football bundles from slap-sports-db (AP/CFP ranks), then runs
check_football_claims on that issue's archived final draft. Prints confirmed /
HIGH / LOW counts and every HIGH in full, so each can be read by hand: a HIGH
that is actually right is a bug.

Needs network (ESPN) and SPORTS_DB_URL (or ../slap-sports-db/.env's
DATABASE_URL) plus psycopg; makes no Claude calls. Without the database it
still runs, on ESPN alone. Not a test: tests are offline. SLA-130 reuses it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

import claim_validator as cv  # noqa: E402
import fetch_sports_data as fsd  # noqa: E402
import football_bundle as fb  # noqa: E402

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/{}/scoreboard"
CACHE = REPO / "uat" / "output" / "replay_cache"


def _db_url() -> str:
    if os.environ.get("SPORTS_DB_URL"):
        return os.environ["SPORTS_DB_URL"]
    env = REPO.parent / "slap-sports-db" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def scoreboard(league: str, day: date) -> list[dict]:
    """Completed games that day, parsed as production parses them. Cached:
    a past day's scoreboard doesn't change."""
    path = CACHE / f"{league}_{day.isoformat()}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        params = {"dates": day.strftime("%Y%m%d"), "limit": 300}
        if league == "college-football":
            params["groups"] = 80                      # FBS
        data = requests.get(ESPN.format(league), params=params, timeout=30).json()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    games = [fsd.parse_game(e) for e in data.get("events") or []]
    return [g for g in games if g and g.get("completed")]


_SEASON_LOG: list | None = None


def nfl_season_log() -> list[dict]:
    """The NFL regular-season log so far, fetched once and cached for the
    day; nfl_standing() cuts it at each replayed day, so standings are as of
    that day (SLA-130)."""
    global _SEASON_LOG
    if _SEASON_LOG is None:
        path = CACHE / f"nfl_season_games_{date.today().isoformat()}.json"
        if path.exists():
            _SEASON_LOG = json.loads(path.read_text(encoding="utf-8"))
        else:
            _SEASON_LOG = fsd.fetch_nfl_season_games() or []
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_SEASON_LOG), encoding="utf-8")
    return _SEASON_LOG


def game_state_for(day: date, url: str) -> dict:
    gs = {"yesterday_date": day.isoformat(), "as_of_date": (day + timedelta(days=1)).isoformat(), "sports": {}}
    for key, league in (("nfl", "nfl"), ("ncaafb", "college-football")):
        games = scoreboard(league, day)
        if games:
            gs["sports"][key] = {"label": key.upper(), "yesterday_games": games}
            if key == "nfl":
                gs["sports"][key]["season_games"] = nfl_season_log()
    if url and gs["sports"]:
        gs["football"] = fb.fetch_bundles(gs, url=url)
    return gs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-09-05")
    ap.add_argument("--to", dest="end", default=date.today().isoformat())
    ap.add_argument("--show", default="HIGH", help="levels to print in full: HIGH, LOW, confirmed, all")
    args = ap.parse_args()
    show = {"HIGH", "LOW", "confirmed"} if args.show == "all" else set(args.show.split(","))
    url = _db_url()
    print(f"database: {'yes' if url else 'NO (ESPN only)'}")
    totals: Counter = Counter()
    d, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while d <= end:
        draft = REPO / "archive" / d.isoformat() / "newsletter_draft.html"
        if draft.exists():
            gs = game_state_for(d - timedelta(days=1), url)
            if gs["sports"]:
                fstat = (gs.get("football") or {}).get("status", "-")
                day: Counter = Counter()
                for heading, sec in cv.split_into_sections(draft.read_text(encoding="utf-8")):
                    if heading == "__preamble__":
                        continue
                    out: list = []
                    cv.check_football_claims(cv.own_text(sec), gs, out)
                    # SLA-130: upset and playoff-win claims go through the
                    # history check; only those kinds are counted here (the
                    # replay has no HISTORICAL CONTEXT, so the rest is noise).
                    hist: list = []
                    cv.check_history_claims(cv.own_text(sec), gs, hist)
                    for level, quoted, sentence, *_ in hist:
                        kinds = cv._claim_kinds(cv._clause(sentence)) & {"upset_W", "upset_L", "playoff_win"}
                        if kinds:
                            out.append((level, quoted, sentence, "/".join(sorted(kinds)), ""))
                    for level, quoted, sentence, kind, fig in out:
                        day[(level, kind)] += 1
                        if level in show:
                            print(f"  {d} {level:9} {kind:6} [{heading[:40]}] {quoted}")
                totals.update(day)
                print(f"{d}: bundles {fstat}; " + (", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(day.items()))
                                                   or "no rank or record claims"))
        d += timedelta(days=1)
    print("\nTOTAL: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(totals.items())))


if __name__ == "__main__":
    main()
