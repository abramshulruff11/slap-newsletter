"""
SLA-17 go/no-go: can a Datawrapper table be embedded in a Substack draft
built through the API?

Why this probe exists
---------------------
Datawrapper is the only responsive, dark-mode-aware way to put tabular data in
a Substack post, and Substack officially supports it. But Substack's auto-embed
fires when a human PASTES a link into the editor -- that is client-side
behaviour. Our pipeline never pastes; it appends ProseMirror nodes through the
API (`twitter2`, `youtube2`, `captionedImage`). Whether any node reachable that
way produces a Datawrapper embed is unknown, and it decides the whole approach.

Two lessons from the table probe are built into the design:

  1. READ-BACK PROVES NOTHING. Substack stores draft_body as an unvalidated
     blob and hands back whatever you sent, so "the node survived" is not
     evidence. Only opening the draft is.
  2. A BAD NODE CAN CRASH THE EDITOR. A `table` node made its draft unopenable
     -- blank page, "Something has gone wrong". If every candidate shared one
     draft, a single crashing node would take the others down with it and we
     would learn nothing.

So each candidate node type gets ITS OWN DRAFT. One crashing candidate costs
one draft, not the experiment.

What it does
------------
  1. Creates and publishes a real Datawrapper table from box score data.
  2. Creates one Substack draft per candidate embed node, each holding a
     heading naming the candidate plus that node.
  3. Prints every draft id and what it contains, and deletes nothing.

Env:
  DATAWRAPPER_API_TOKEN   needs FOUR scopes: chart:read, chart:write,
                          theme:read AND visualization:read. Create, data and
                          config succeed with only the two chart scopes --
                          PUBLISH is what 403s "Insufficient scope", so a
                          half-scoped token looks fine until the last call.
  SUBSTACK_COOKIES_STRING, SUBSTACK_PUBLICATION_URL, PROXY_URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "box_score"))

import text_box_score as tbs  # noqa: E402

DW_API = "https://api.datawrapper.de/v3"


# --------------------------------------------------------------------------
# Datawrapper. Hand-rolled over urllib: the pipeline already carries enough
# dependencies, and this is four calls.
# --------------------------------------------------------------------------

def dw_request(method: str, path: str, token: str,
               body: Optional[bytes] = None,
               content_type: str = "application/json") -> Tuple[int, str]:
    """One Datawrapper call. Returns (status, body) and never raises for HTTP
    errors -- the probe's whole job is to report what came back."""
    req = urllib.request.Request(f"{DW_API}{path}", data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def box_score_csv(game: Dict) -> Tuple[str, str]:
    """(title, csv) for one team's batting line -- real data, so the table
    under test is the actual product rather than a toy."""
    box = game.get("box_score") or {}
    side = box.get("away") or {}
    cols = ["AB", "R", "H", "RBI", "BB", "K", "AVG"]
    rows = ["Batter," + ",".join(cols)]
    for p in (side.get("batting") or [])[:9]:
        st = p.get("stats") or {}
        name = f'{p.get("name", "?")} {p.get("pos", "")}'.strip().replace(",", "")
        rows.append(name + "," + ",".join(str(st.get(c, "")) for c in cols))
    title = f'{game.get("away_team", "?")} at {game.get("home_team", "?")} — batting'
    return title, "\n".join(rows)


def make_datawrapper_table(token: str, title: str, csv: str) -> Optional[Dict]:
    """Create -> data -> configure -> publish. Prints each step's status."""
    print("\n[datawrapper] creating table...")
    status, body = dw_request(
        "POST", "/charts", token,
        json.dumps({"title": title, "type": "tables"}).encode("utf-8"))
    print(f"  POST /charts -> {status}")
    if status not in (200, 201):
        print(f"  body: {body[:500]}")
        return None
    chart = json.loads(body)
    cid = chart.get("id")
    print(f"  chart id={cid}")

    print("[datawrapper] uploading data...")
    status, body = dw_request("PUT", f"/charts/{cid}/data", token,
                              csv.encode("utf-8"), content_type="text/csv")
    print(f"  PUT /charts/{cid}/data -> {status}")
    if status not in (200, 201, 204):
        print(f"  body: {body[:500]}")

    # autoDarkMode is the whole point of choosing Datawrapper: the embed
    # follows the reader's own colour scheme, which an image cannot.
    print("[datawrapper] configuring (auto dark mode, responsive)...")
    meta = {
        "metadata": {
            "publish": {"autoDarkMode": True, "embed-height": 0},
            "describe": {"intro": "", "byline": "", "source-name": "ESPN"},
            "visualize": {"perPage": 25, "showHeader": True, "striped": True},
        }
    }
    status, body = dw_request("PATCH", f"/charts/{cid}", token,
                              json.dumps(meta).encode("utf-8"))
    print(f"  PATCH /charts/{cid} -> {status}")
    if status not in (200, 201, 204):
        print(f"  body: {body[:500]}")

    print("[datawrapper] publishing...")
    status, body = dw_request("POST", f"/charts/{cid}/publish", token)
    print(f"  POST /charts/{cid}/publish -> {status}")
    if status not in (200, 201, 204):
        print(f"  body: {body[:500]}")
        return None

    status, body = dw_request("GET", f"/charts/{cid}", token)
    info = json.loads(body) if status == 200 else {}
    public_url = (info.get("publicUrl")
                  or (info.get("publicUrlExport") if isinstance(info, dict) else None)
                  or f"https://datawrapper.dwcdn.net/{cid}/")
    return {
        "id": cid,
        "public_url": public_url,
        # The "visualization only" form Substack's own docs tell you to paste.
        "embed_url": f"https://datawrapper.dwcdn.net/{cid}/",
    }


# --------------------------------------------------------------------------
# Substack
# --------------------------------------------------------------------------

def _install_session(proxy_url):
    import substack.api as sapi
    from curl_cffi import requests as creq

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    sapi.requests.Session = lambda: creq.Session(impersonate="chrome", proxies=proxies)
    return "proxy + curl_cffi(chrome)" if proxy_url else "DIRECT + curl_cffi(chrome)"


def candidates(url: str) -> List[Tuple[str, str, Optional[Dict]]]:
    """(label, note, node) for each way we might get an embed.

    `None` for the node means "a plain paragraph holding the bare URL" -- the
    closest thing to what a human pasting the link produces, and the one that
    needs no guess about Substack's schema at all.
    """
    return [
        ("paragraph-url",
         "bare URL in a paragraph — mirrors pasting; needs no schema guess",
         None),
        ("iframe",
         "generic iframe node",
         {"type": "iframe", "attrs": {"src": url}}),
        ("embed",
         "generic embed node",
         {"type": "embed", "attrs": {"url": url, "src": url}}),
        ("datawrapper",
         "a node named after the provider",
         {"type": "datawrapper", "attrs": {"url": url, "src": url}}),
        ("iframely",
         "Substack has used Iframely for generic embeds",
         {"type": "iframely", "attrs": {"url": url, "src": url}}),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="SLA-17 Datawrapper go/no-go probe")
    ap.add_argument("--game-state", default="uat/fixtures/game_state.json")
    ap.add_argument("--chart-url", default=None,
                    help="skip Datawrapper and probe Substack with this URL")
    args = ap.parse_args()

    dw_token = os.getenv("DATAWRAPPER_API_TOKEN")
    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    proxy = os.getenv("PROXY_URL")

    embed_url = args.chart_url
    if not embed_url:
        if not dw_token:
            print("FAIL: DATAWRAPPER_API_TOKEN is not set")
            return 2
        with open(args.game_state, encoding="utf-8") as f:
            gs = json.load(f)
        game = next((g for g in gs["sports"]["mlb"]["yesterday_games"]
                     if g.get("box_score")), None)
        if not game:
            print("FAIL: no MLB box score in the game state")
            return 2
        title, csv = box_score_csv(game)
        print(f"Table under test: {title}")
        print("CSV preview:")
        for line in csv.splitlines()[:4]:
            print(f"    {line}")

        chart = make_datawrapper_table(dw_token, title, csv)
        if not chart:
            print("\nNO-GO (Datawrapper side): could not create/publish a table.")
            print("If the failing call was POST /publish with 'Insufficient")
            print("scope', the token needs all FOUR of: chart:read,")
            print("chart:write, theme:read, visualization:read. The first three")
            print("calls pass on the chart scopes alone, so the token looks")
            print("correct right up until it does not.")
            return 1
        embed_url = chart["embed_url"]
        print(f"\nDatawrapper OK. chart id={chart['id']}")
        print(f"  public url: {chart['public_url']}")
        print(f"  embed url:  {embed_url}")

    if not cookies or not pub:
        print("\n(Substack secrets missing — stopping after the Datawrapper half.)")
        return 0

    # ---- Substack: one draft per candidate, so one crash costs one draft ----
    try:
        print(f"\nMode: {_install_session(proxy)}")
        from substack import Api
        from substack.post import Post

        api = Api(cookies_string=cookies, publication_url=pub)
        uid = api.get_user_id()
        print(f"Auth OK, user_id={uid}\n")

        results = []
        for label, note, node in candidates(embed_url):
            post = Post(f"[SLA-17 DW] {label}", note, uid)
            post.paragraph(content=[{
                "content": f"Candidate: {label}. {note}. "
                           f"If the embed is missing below, this node does nothing."
            }])
            if node is None:
                post.paragraph(content=[{"content": embed_url}])
            else:
                post.draft_body["content"].append(node)
            post.paragraph(content=[{"content": f"(end of {label})"}])
            try:
                draft = api.post_draft(post.get_draft())
                did = draft.get("id")
                results.append((label, did, note))
                print(f"  {label:<16} draft {did}")
            except Exception as e:  # noqa: BLE001
                results.append((label, f"FAILED: {type(e).__name__}", note))
                print(f"  {label:<16} FAILED: {type(e).__name__}: {str(e)[:160]}")

        base = pub.rstrip("/")
        print("\n" + "=" * 64)
        print("OPEN EACH OF THESE. A draft that is blank or errors means that")
        print("node crashes the editor, exactly as the `table` node did.")
        print("=" * 64)
        for label, did, note in results:
            print(f"  {label:<16} {base}/publish/post/{did}")
        print(f"\nDatawrapper embed url under test: {embed_url}")
        print("Nothing was published. Delete drafts with "
              "table_probe.py --delete-id <id>.")
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"\nFAILED: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
