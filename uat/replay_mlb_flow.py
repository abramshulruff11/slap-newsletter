"""
Run:  python -X utf8 uat/replay_mlb_flow.py --cache <dir> [--build] [--since 2026-04-01]

SLA-142: replay Check 3D (MLB game-flow claims) over every archived issue.

Each archived issue's MLB games are rebuilt from ESPN as they were that
morning (yesterday's scoreboard, every completed game's summary, through
fetch_sports_data's own parse functions) and cached as one game_state per
issue in --cache. Then every section of the archived final draft is run
through claim_validator.check_mlb_flow_claims, and each sentence with a
game-flow claim is printed with its verdict, the HIGH ones with their flag,
for a person to read: a HIGH that is actually right is a bug.

--build fetches what the cache lacks (needs ESPN; about 15 summaries per
day). Without it, only cached days are replayed. No API calls.
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

import claim_validator as cv  # noqa: E402


def build(day: str, dest: Path) -> None:
    import fetch_sports_data as f
    y = date.fromisoformat(day) - timedelta(days=1)
    games = f.fetch_scoreboard("baseball", "mlb", y)
    for g in games:
        if g.get("completed") and g.get("game_id"):
            s = f.fetch_game_summary("baseball", "mlb", g["game_id"])
            if s:
                try:
                    g["box_score"] = f.parse_box_score(s, "mlb")
                except Exception as e:  # noqa: BLE001
                    print(f"  {day} {g['game_id']}: parse failed: {e}")
            time.sleep(0.2)
    gs = {"as_of_date": day, "yesterday_date": y.isoformat(),
          "sports": {"mlb": {"label": "MLB", "yesterday_games": games, "today_games": []}}}
    dest.write_text(json.dumps(gs, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--since", default="2026-04-01")
    ap.add_argument("--quiet", action="store_true", help="print HIGH sentences only")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    days = sorted(p.name for p in (REPO / "archive").iterdir()
                  if p.is_dir() and p.name[:2] == "20" and p.name >= args.since)
    tally, issues = Counter(), 0
    for day in days:
        draft = REPO / "archive" / day / "newsletter_draft.html"
        cached = args.cache / f"{day}.json"
        if not draft.exists():
            continue
        if not cached.exists():
            if not args.build:
                continue
            build(day, cached)
        gs = json.loads(cached.read_text(encoding="utf-8"))
        issues += 1
        for heading, sec in cv.split_into_sections(draft.read_text(encoding="utf-8")):
            if heading == "__preamble__":
                continue
            outcomes: list = []
            flags = cv.check_mlb_flow_claims(cv.own_text(sec), gs, outcomes)
            for (level, quoted, *_), flag in zip(
                    outcomes, [None] * len(outcomes)):
                tally[level] += 1
                if level == "HIGH" or not args.quiet:
                    print(f"{day} | {level:9} | {heading[:40]} | {quoted}")
            for fl in flags:
                print("      " + fl.strip()[:600])
    print()
    print(f"{issues} issue(s) replayed: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    main()
