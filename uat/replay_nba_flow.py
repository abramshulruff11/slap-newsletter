"""
Run:  python -X utf8 uat/replay_nba_flow.py [--from 2026-04-20] [--to 2026-10-19] [--show HIGH]

SLA-147: replays Pass 3's NBA game-flow check (Check 3G) over archived issues.
For each issue date it rebuilds the previous day's NBA games exactly as the
fetch step does (scoreboard, then each game's ESPN summary through
parse_box_score, so the play log is the production one), then runs
check_nba_flow_claims on that issue's archived final draft. Prints confirmed /
HIGH counts and every HIGH in full, so each can be read by hand: a HIGH that
is actually right is a bug. Also prints how big the NBA GAME FLOW block would
have been each day (the cap is nba_bundle.BUDGET).

Needs network (ESPN); no database, no Claude calls. Responses are cached in
uat/output/replay_cache/, since a past day never changes. Not a test: tests
are offline.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

import claim_validator as cv  # noqa: E402
import fetch_sports_data as fsd  # noqa: E402
import nba_bundle as nb  # noqa: E402

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CACHE = REPO / "uat" / "output" / "replay_cache"


def _cached(name: str, url: str, params: dict | None = None) -> dict:
    path = CACHE / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in range(4):
        try:
            data = requests.get(url, params=params, timeout=30).json()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def games_for(day: date) -> list[dict]:
    sb = _cached(f"nba_{day.isoformat()}", f"{BASE}/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 50})
    out = []
    for event in sb.get("events") or []:
        g = fsd.parse_game(event)
        if not g or not g.get("completed"):
            continue
        summary = _cached(f"nba_summary_{g['game_id']}", f"{BASE}/summary", {"event": g["game_id"]})
        g["box_score"] = fsd.parse_box_score(summary, "nba")
        out.append(g)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-04-20")
    ap.add_argument("--to", dest="end", default=date.today().isoformat())
    ap.add_argument("--show", default="HIGH", help="levels to print in full: HIGH, confirmed, all")
    args = ap.parse_args()
    show = {"HIGH", "confirmed"} if args.show == "all" else set(args.show.split(","))
    totals: Counter = Counter()
    sizes: list[int] = []
    unbundled = games = 0
    d, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while d <= end:
        draft = REPO / "archive" / d.isoformat() / "newsletter_draft.html"
        if draft.exists():
            gs_games = games_for(d - timedelta(days=1))
            gs = {"as_of_date": d.isoformat(), "sports": {"nba": {"label": "NBA", "yesterday_games": gs_games}}}
            n_games, n_bundles = len(gs_games), len(nb.bundles(gs))
            games += n_games
            unbundled += n_games - n_bundles
            if n_bundles:
                sizes.append(len("\n".join(nb.summary_lines(gs))))
                day: Counter = Counter()
                for heading, sec in cv.split_into_sections(draft.read_text(encoding="utf-8")):
                    if heading == "__preamble__":
                        continue
                    out: list = []
                    cv.check_nba_flow_claims(cv.own_text(sec), gs, out)
                    for level, quoted, sentence, kind, fig in out:
                        day[level] += 1
                        if level in show:
                            print(f"  {d} {level:9} [{heading[:40]}] {quoted}")
                totals.update(day)
                print(f"{d}: {n_bundles}/{n_games} game(s) bundled, block {sizes[-1]:,} chars; "
                      + (", ".join(f"{k} {v}" for k, v in sorted(day.items())) or "no game-flow claims"))
        d += timedelta(days=1)
    print(f"\nGAMES: {games}, with no usable play log: {unbundled}")
    if sizes:
        print(f"BLOCK SIZE: max {max(sizes):,}, mean {sum(sizes) // len(sizes):,} chars (cap {nb.BUDGET:,})")
    print("TOTAL: " + (", ".join(f"{k} {v}" for k, v in sorted(totals.items())) or "no claims"))


if __name__ == "__main__":
    main()
