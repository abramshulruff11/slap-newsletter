"""
CI guard for game_state.json — is the ground truth real, or hollow?

fetch_sports_data.py stamps the file with today's date no matter what ESPN
answered. From at least 2026-08-14 to 2026-09-04 every scoreboard call
returned 403 from GitHub's IP, standings still loaded, and the file looked
fresh while holding zero games. The newsletter lost its ground-truth block,
the claim validator had nothing to check, the box score images had no
scores, and highlights never matched — all under green CI.

This reads the "fetch_health" block fetch_sports_data.py now writes and:
  - prints a per-sport summary to the job log and $GITHUB_STEP_SUMMARY
  - emits a GitHub ::error:: annotation and exits 1 when the fetch was
    BLOCKED (any 403 with no proxy, or every scoreboard call failed)
  - emits ::warning:: when some calls failed but others answered

Wire it with continue-on-error so a blocked fetch is loud (a failed step and
an annotation on the run page) without blocking the email and the Substack
draft, which do not need scores to go out.

    python check_game_state.py            # exit 1 on a blocked fetch
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

GAME_STATE = Path(__file__).resolve().parent / "game_state.json"


def _summary_line(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> int:
    if not GAME_STATE.exists():
        print("::error::game_state.json missing — fetch_sports_data.py did not run")
        return 1
    data = json.loads(GAME_STATE.read_text(encoding="utf-8"))
    health = data.get("fetch_health") or {}
    sports = data.get("sports") or {}

    per_sport = health.get("per_sport_completed") or {
        k: sum(1 for g in s.get("yesterday_games", []) if g.get("completed"))
        for k, s in sports.items()
    }
    direct, via_proxy = health.get("direct_ok", 0), health.get("proxy_ok", 0)
    failed, blocked = health.get("failed", 0), health.get("blocked_no_proxy", 0)
    total_games = sum(per_sport.values())

    lines = [
        "### Sports data health",
        f"- as of: `{data.get('as_of_date', '?')}` · sports in season: {len(sports)} · "
        f"completed games yesterday: **{total_games}**",
        f"- ESPN calls: {direct} direct · {via_proxy} via proxy · {failed} failed"
        + (f" · **{blocked} blocked with no PROXY_URL**" if blocked else ""),
        "- per sport: " + (", ".join(f"{k} {v}" for k, v in per_sport.items()) or "none"),
    ]
    for ln in lines:
        print(ln)
        _summary_line(ln)

    if blocked:
        msg = (f"ESPN blocked {blocked} request(s) and PROXY_URL is not set — "
               f"game_state.json is hollow (0 games). Add the PROXY_URL secret to "
               f"the fetch steps.")
        print(f"::error::{msg}")
        _summary_line(f"> ❌ {msg}")
        return 1
    if failed and not (direct or via_proxy):
        msg = f"every ESPN request failed ({failed}); game_state.json has no real data"
        print(f"::error::{msg}")
        _summary_line(f"> ❌ {msg}")
        return 1
    if failed:
        msg = f"{failed} ESPN request(s) failed — game_state.json is partial"
        print(f"::warning::{msg}")
        _summary_line(f"> ⚠️ {msg}")
    if sports and total_games == 0:
        msg = ("0 completed games across in-season leagues with no fetch failures — "
               "an off day, or ESPN answered 200 with empty scoreboards")
        print(f"::warning::{msg}")
        _summary_line(f"> ⚠️ {msg}")
    print("✓ game_state.json health check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
