"""
Run:  python -X utf8 uat/tests/test_ncaamb_scoreboard.py

SLA-152: men's college basketball must request the whole D1 slate
(groups=50), other leagues must keep their old request, and a March day with
the full slate must not become 100+ per-game cards. No network.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "box_score"))

import fetch_sports_data as fsd   # noqa: E402
import build_box_score as bbs     # noqa: E402

FAILURES: list[str] = []


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        FAILURES.append(msg)


urls = []
fsd.fetch_url = lambda url: (urls.append(url), {"events": []})[1]
fsd.fetch_scoreboard("basketball", "mens-college-basketball", date(2026, 1, 21))
fsd.fetch_scoreboard("basketball", "nba", date(2026, 1, 21))
check("groups=50" in urls[0] and "limit=300" in urls[0], "ncaamb asks for groups=50&limit=300")
check("groups" not in urls[1] and urls[1].endswith("&limit=50"), "other leagues unchanged")

games = [{"completed": True, "playoffs": i < 2, "away_team": f"A{i} Foo", "home_team": f"B{i} Bar",
          "away_score": 60, "home_score": 70} for i in range(120)]
data = {"label": "College Basketball", "yesterday_games": games, "standings": {}, "bracket": []}
html = bbs._render_sport_inline("ncaamb", data, in_playoffs=True)
check(html.count("Bar 70, Foo 60</div>") == 2, "only the 2 real playoff games get cards in March")
check(html.count("Bar</span>")+html.count("Bar 70</span>") == 118, "the rest still appear in the results strip")

sys.exit(1 if FAILURES else 0)
