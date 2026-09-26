"""
SLA-17: which dark-mode setting makes a Datawrapper embed match a Substack page?

The embed probe settled the go/no-go: the `datawrapper` node renders a real
table in a draft built through the API. What it left open is that the table
came back DARK on a LIGHT page.

Two explanations, and they need different fixes:

  * The iframe and the host page read different signals. Datawrapper's
    autoDarkMode follows the READER'S OS `prefers-color-scheme`; Substack's
    page follows Substack's own theme. When those disagree, they clash.
  * Or it was the test harness: DevTools' "Emulate prefers-color-scheme:
    dark" applies to the whole tab, iframes included, while Substack's editor
    chrome ignores it -- which would produce the same picture artificially.

Rather than ask which it was, this puts all three settings of the SAME table
in ONE draft. Whatever the browser is doing, it does it to all three equally,
so the one that matches the page is the answer:

    1. auto          — follows the reader's OS (what we shipped in the probe)
    2. ?dark=false   — pinned light
    3. ?dark=true    — pinned dark

One draft, not three: these must be compared under identical conditions, and
the `datawrapper` node is now known not to crash the editor, so the
one-draft-per-candidate caution that governed the earlier probe does not
apply here.

Env: DATAWRAPPER_API_TOKEN, SUBSTACK_COOKIES_STRING,
     SUBSTACK_PUBLICATION_URL, PROXY_URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "box_score"))

from datawrapper_probe import box_score_csv, make_datawrapper_table  # noqa: E402

# The node the probe proved works. Named after the provider, which is not
# where I would have bet -- `paragraph-url` rendered nothing.
EMBED_NODE_TYPE = "datawrapper"

VARIANTS = [
    ("1. auto (follows YOUR OS setting)", ""),
    ("2. ?dark=false (pinned light)", "?dark=false"),
    ("3. ?dark=true (pinned dark)", "?dark=true"),
]


def embed_node(url: str) -> dict:
    return {"type": EMBED_NODE_TYPE, "attrs": {"url": url, "src": url}}


def main() -> int:
    ap = argparse.ArgumentParser(description="SLA-17 Datawrapper dark-mode test")
    ap.add_argument("--game-state", default="uat/fixtures/game_state.json")
    ap.add_argument("--chart-id", default=None,
                    help="reuse an existing published chart instead of making one")
    args = ap.parse_args()

    token = os.getenv("DATAWRAPPER_API_TOKEN")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not token or not pub:
        print("FAIL: need DATAWRAPPER_API_TOKEN and SUBSTACK_PUBLICATION_URL")
        return 2

    chart_id = args.chart_id
    if not chart_id:
        with open(args.game_state, encoding="utf-8") as f:
            gs = json.load(f)
        game = next((g for g in gs["sports"]["mlb"]["yesterday_games"]
                     if g.get("box_score")), None)
        if not game:
            print("FAIL: no MLB box score in the game state")
            return 2
        title, csv = box_score_csv(game)
        chart = make_datawrapper_table(token, title, csv)
        if not chart:
            print("\nFAILED on the Datawrapper side.")
            return 1
        chart_id = chart["id"]

    base = f"https://datawrapper.dwcdn.net/{chart_id}/"
    print(f"\nChart {chart_id}. Three embeds of the SAME table:")
    for label, suffix in VARIANTS:
        print(f"  {label:<38} {base}{suffix}")

    try:
        from publish import make_api
        from substack.post import Post

        api = make_api()
        uid = api.get_user_id()
        print(f"\nSubstack auth OK, user_id={uid}")

        post = Post("[SLA-17] Datawrapper dark mode — which one matches?",
                    "The same table, three dark-mode settings.", uid)
        post.paragraph(content=[{
            "content": "CLOSE DEVTOOLS AND HARD-REFRESH FIRST — an emulated "
                       "colour scheme applies to iframes but not to Substack's "
                       "own chrome, which is exactly the mismatch under test. "
                       "Then: which of the three below matches the page "
                       "background? Check on desktop and on a phone."
        }])
        for label, suffix in VARIANTS:
            post.heading(content=[{"content": label}], level=2)
            post.draft_body["content"].append(embed_node(f"{base}{suffix}"))

        post.paragraph(content=[{
            "content": "If 1 matches, auto is correct and the earlier dark "
                       "table was the DevTools emulation. If 1 clashes but 2 "
                       "matches, pin it light. If the page itself is dark and "
                       "3 matches, pin it dark."
        }])

        draft = api.post_draft(post.get_draft())
        did = draft.get("id")
        print(f"\nCreated draft id={did}")
        print(f"  {pub.rstrip('/')}/publish/post/{did}")
        print(f"\nCleanup: cleanup_probe_artifacts.py --drafts {did} "
              f"--charts {chart_id}")
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"\nFAILED: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
