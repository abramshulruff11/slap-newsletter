"""
SLA-17: put real box scores into a Substack draft as code_block text.

This is the visual check that the offline suite cannot do. `test_text_box_score.py`
proves every line fits MOBILE_BUDGET, but "fits the budget" is a claim about
character counts, not about what a phone actually renders -- Substack's own
monospace stack, its code-block padding, and dark mode are all outside our
measurement. The table probe already taught this lesson the expensive way: a
draft can look perfect in JSON and be broken in the editor.

So: build a draft from real box scores, leave it in place, and look at it.

Creates a draft. Never publishes. Delete with:
    table_probe.py --delete-id <id>

Env (same secrets as substack-ci-test.yml):
  SUBSTACK_COOKIES_STRING, SUBSTACK_PUBLICATION_URL, PROXY_URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "box_score"))

import text_box_score as tbs  # noqa: E402

# Categories worth showing per sport, in reading order.
SPORT_CATEGORIES = {
    "mlb": ["batting", "pitching"],
    "nfl": ["passing", "rushing", "receiving"],
    "ncaafb": ["passing", "rushing", "receiving"],
    "nba": ["scoring"],
    "nhl": ["skaters"],
}


def _install_session(proxy_url):
    import substack.api as sapi
    from curl_cffi import requests as creq

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    sapi.requests.Session = lambda: creq.Session(impersonate="chrome", proxies=proxies)
    return "proxy + curl_cffi(chrome)" if proxy_url else "DIRECT + curl_cffi(chrome)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Draft real box scores as code blocks")
    ap.add_argument("--game-state", default="uat/fixtures/game_state.json")
    ap.add_argument("--sports", default="mlb", help="comma-separated sport keys")
    ap.add_argument("--games", type=int, default=3, help="max games per sport")
    ap.add_argument("--players", type=int, default=9, help="max players per table")
    ap.add_argument("--title", default="[SLA-17] Box scores as text — preview")
    ap.add_argument("--dry-run", action="store_true",
                    help="render and print only; no credentials, no network")
    args = ap.parse_args()

    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    proxy = os.getenv("PROXY_URL")
    if not args.dry_run and (not cookies or not pub):
        print("FAIL: missing SUBSTACK_COOKIES_STRING or SUBSTACK_PUBLICATION_URL")
        return 2

    with open(args.game_state, encoding="utf-8") as f:
        gs = json.load(f)

    # Render first so a data problem fails before we touch the network.
    blocks = []
    for sport in [s.strip() for s in args.sports.split(",") if s.strip()]:
        cats = SPORT_CATEGORIES.get(sport, ["batting"])
        games = [g for g in gs.get("sports", {}).get(sport, {}).get("yesterday_games", [])
                 if g.get("box_score")][: args.games]
        if not games:
            print(f"  ({sport}: no box scores in this game_state, skipping)")
            continue
        blocks.append(("heading", sport.upper()))
        for g in games:
            text = tbs.render_game(g, cats, limit=args.players)
            w = tbs.widest_line(text)
            flag = "" if w <= tbs.MOBILE_BUDGET else f"  <-- OVER BUDGET ({w})"
            print(f"  {sport} {g.get('matchup', '?')}: widest {w} chars{flag}")
            blocks.append(("code", text))

    if not any(k == "code" for k, _ in blocks):
        print("FAIL: nothing rendered -- check --game-state and --sports")
        return 2

    if args.dry_run:
        print("\n--- dry run: what would be drafted ---")
        for kind, payload in blocks:
            print(f"\n## {payload}" if kind == "heading" else f"\n{payload}")
        return 0

    try:
        print(f"Mode: {_install_session(proxy)}")
        from substack import Api
        from substack.post import Post

        api = Api(cookies_string=cookies, publication_url=pub)
        uid = api.get_user_id()
        print(f"Auth OK, user_id={uid}")

        post = Post(args.title,
                    "SLA-17: box scores as monospace code blocks, not images.", uid)
        post.paragraph(content=[{
            "content": "Check on a phone and in dark mode: do the columns line up, "
                       "and does any block scroll sideways?"
        }])
        for kind, payload in blocks:
            if kind == "heading":
                post.heading(content=[{"content": payload}], level=2)
            else:
                post.draft_body["content"].append(tbs.code_block_node(payload))

        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id")
        print(f"\nCreated draft id={draft_id}")
        print(f"Draft (not published): {pub.rstrip('/')}/publish/post/{draft_id}")
        print("Delete with: table_probe.py --delete-id " + str(draft_id))
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"\nFAILED: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
