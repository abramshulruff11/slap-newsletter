"""
SLA-17 probe: does Substack's server accept a native table node?

Background
----------
Substack's editor exposes no table control, and `python-substack==0.1.22` has
no table support (grep for "table" in that package returns nothing). But the
library is NOT the constraint: `Post.add()` has no type whitelist -- it falls
through to `else` and appends `{"type": <anything>}` -- and `publish.py`'s
`_append_image()` already pushes a raw node straight onto
`post.draft_body["content"]`. So we can hand Substack any ProseMirror node we
like.

The open question is what the SERVER does with it: accept, reject the save, or
silently strip the node. That is not answerable by reading code, only by
sending one and reading it back. This script does exactly that.

What it sends
-------------
One draft containing several candidate renderings of the SAME real box score,
each preceded by a marker paragraph so it can be located in the read-back:

  1. table / table_row / table_cell   -- prosemirror-tables' own snake_case schema
  2. table / tableRow / tableCell     -- the camelCase convention (TipTap et al)
  3. code_block                       -- monospace agate; the "structured text" fallback
  4. paragraph                        -- control, proves the draft saved at all

What it reports
---------------
For each candidate: STORED (node type present in the read-back) or STRIPPED
(gone). Exit code is 0 whenever the probe itself ran.

!! READ-BACK IS NOT A TEST OF SUPPORT. Answered 2026-09-19, the hard way. !!

This probe was run and reported STORED for every candidate -- including BOTH
`table_row`/`table_cell` AND `tableRow`/`tableCell` at once, which no single
ProseMirror schema can accept. That was the tell: Substack stores `draft_body`
as an opaque blob and does not validate it on save, so a read-back returns
whatever you sent and can never distinguish a supported node from a fatal one.

Opening the resulting draft in the Substack editor showed a BLANK page and
"Something has gone wrong. Please refresh the page and try again." The unknown
`table` node crashes the editor on load. Substack has no table support; the
draft is not merely unrendered, it is unopenable.

So: STORED means "the server accepted the bytes", nothing more. The only real
test is opening the draft in a browser. Keep that in mind before reading a
green-looking result off this script -- it is the same trap as the 2026-09-01
GIF placeholders, which persisted perfectly and rendered as empty divs.

The draft is left in place on purpose so it can be opened and looked at. It is
never published. Delete it by hand, or re-run with --delete.

Env (same secrets as substack-ci-test.yml):
  SUBSTACK_COOKIES_STRING, SUBSTACK_PUBLICATION_URL, PROXY_URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Dict, List, Optional


# --------------------------------------------------------------------------
# Session setup (mirrors ci_auth_test.py / publish.py: residential proxy +
# Chrome TLS impersonation, or Cloudflare 403s the datacenter IP).
# --------------------------------------------------------------------------

def _install_session(proxy_url: Optional[str]) -> str:
    import substack.api as sapi
    from curl_cffi import requests as creq

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    sapi.requests.Session = lambda: creq.Session(impersonate="chrome", proxies=proxies)
    return "proxy + curl_cffi(chrome)" if proxy_url else "DIRECT + curl_cffi(chrome)"


# --------------------------------------------------------------------------
# Real box score data -> rows. We probe with real content rather than "foo/bar"
# so the draft doubles as a look-at-it preview of the actual product.
# --------------------------------------------------------------------------

def load_box_rows(game_state_path: str) -> Dict:
    """Pull one real MLB batting line out of game_state.json.

    Returns {"title": str, "headers": [...], "rows": [[...]]}. Falls back to a
    small hardcoded table if the fixture is missing, so the probe still runs.
    """
    fallback = {
        "title": "Sample box score",
        "headers": ["Batter", "AB", "R", "H", "RBI", "BB", "K", "AVG"],
        "rows": [["No fixture found", "-", "-", "-", "-", "-", "-", "-"]],
    }
    try:
        with open(game_state_path, "r", encoding="utf-8") as f:
            gs = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not read {game_state_path}: {type(e).__name__}) -- using fallback")
        return fallback

    for g in gs.get("sports", {}).get("mlb", {}).get("yesterday_games", []):
        box = g.get("box_score") or {}
        away = box.get("away") or {}
        batting = away.get("batting") or []
        if not batting:
            continue
        cols = ["AB", "R", "H", "RBI", "BB", "K", "AVG"]
        rows = []
        for p in batting[:9]:
            st = p.get("stats", {})
            label = f'{p.get("name", "?")} {p.get("pos", "")}'.strip()
            rows.append([label] + [str(st.get(c, "-")) for c in cols])
        title = (
            f'{g.get("away_team", "?")} {g.get("away_score", "")} '
            f'@ {g.get("home_team", "?")} {g.get("home_score", "")}'
        ).strip()
        return {"title": title, "headers": ["Batter"] + cols, "rows": rows}

    return fallback


# --------------------------------------------------------------------------
# ProseMirror node builders
# --------------------------------------------------------------------------

def _text_cell(cell_type: str, text: str, header: bool = False) -> Dict:
    """One table cell wrapping a paragraph, per the ProseMirror table schema."""
    node_type = cell_type.replace("cell", "header") if header else cell_type
    return {
        "type": node_type,
        "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None},
        "content": [
            {
                "type": "paragraph",
                "content": ([{"type": "text", "text": text}] if text else []),
            }
        ],
    }


def build_table_node(box: Dict, table: str, row: str, cell: str, header_row: bool = True) -> Dict:
    """Build a ProseMirror table under a given naming convention."""
    rows: List[Dict] = []
    rows.append(
        {
            "type": row,
            "content": [_text_cell(cell, h, header=header_row) for h in box["headers"]],
        }
    )
    for r in box["rows"]:
        rows.append({"type": row, "content": [_text_cell(cell, v) for v in r]})
    return {"type": table, "content": rows}


def build_code_block_node(box: Dict) -> Dict:
    """Monospace agate -- the 'structured text' fallback, rendered as a code block."""
    widths = [
        max(len(str(box["headers"][i])), *(len(str(r[i])) for r in box["rows"]))
        for i in range(len(box["headers"]))
    ]

    def fmt(cells):
        out = [str(cells[0]).ljust(widths[0])]
        out += [str(c).rjust(widths[i + 1]) for i, c in enumerate(cells[1:])]
        return "  ".join(out)

    lines = [fmt(box["headers"]), "-" * (sum(widths) + 2 * (len(widths) - 1))]
    lines += [fmt(r) for r in box["rows"]]
    return {
        "type": "code_block",
        "content": [{"type": "text", "text": "\n".join(lines)}],
    }


def marker(text: str) -> Dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------
# Read-back analysis
# --------------------------------------------------------------------------

def collect_types(node, acc=None):
    """Every node `type` present anywhere in the returned document."""
    acc = acc if acc is not None else {}
    if isinstance(node, dict):
        t = node.get("type")
        if isinstance(t, str):
            acc[t] = acc.get(t, 0) + 1
        for v in node.values():
            collect_types(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect_types(v, acc)
    return acc


def parse_body(raw) -> Optional[Dict]:
    """draft_body comes back as a JSON string or an already-parsed dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe Substack for native table support (SLA-17)")
    ap.add_argument("--game-state", default="uat/fixtures/game_state.json",
                    help="game_state.json to pull a real box score from")
    ap.add_argument("--title", default="[SLA-17 probe] Native table test — do not publish")
    ap.add_argument("--delete", action="store_true",
                    help="delete the draft after reading it back (default: leave it)")
    ap.add_argument("--delete-id", type=int, default=None,
                    help="delete this existing draft id and exit; probes nothing. "
                         "A draft carrying a table node cannot be opened in the UI, "
                         "so this is the only way to remove one.")
    args = ap.parse_args()

    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    proxy = os.getenv("PROXY_URL")
    if not cookies or not pub:
        print("FAIL: missing SUBSTACK_COOKIES_STRING or SUBSTACK_PUBLICATION_URL")
        return 2
    if not proxy:
        print("WARNING: PROXY_URL not set -- going direct (expected to fail from CI).")

    # Deletion mode runs before anything else and probes nothing.
    if args.delete_id is not None:
        try:
            mode = _install_session(proxy)
            print(f"Mode: {mode}")
            from substack import Api
            api = Api(cookies_string=cookies, publication_url=pub)
            print(f"Auth OK, user_id={api.get_user_id()}")
            api.delete_draft(args.delete_id)
            print(f"Deleted draft id={args.delete_id}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"Delete FAILED: {type(e).__name__}: {str(e)[:300]}")
            traceback.print_exc()
            return 1

    box = load_box_rows(args.game_state)
    print(f"Box score under test: {box['title']} ({len(box['rows'])} rows)")

    # Each candidate: (label, expected node type, node)
    candidates = [
        ("1. snake_case table (prosemirror-tables)", "table",
         build_table_node(box, "table", "table_row", "table_cell")),
        ("2. camelCase table (TipTap convention)", "table",
         build_table_node(box, "table", "tableRow", "tableCell")),
        ("3. code_block (structured-text fallback)", "code_block",
         build_code_block_node(box)),
        ("4. paragraph (control)", "paragraph",
         marker("CONTROL: if this line is missing, the draft did not save.")),
    ]

    try:
        mode = _install_session(proxy)
        print(f"Mode: {mode}")

        from substack import Api
        from substack.post import Post

        api = Api(cookies_string=cookies, publication_url=pub)
        uid = api.get_user_id()
        print(f"Auth OK, user_id={uid}")

        post = Post(args.title, "SLA-17: probing whether Substack accepts a native table node.", uid)
        post.paragraph(content=[{
            "content": f"Automated probe for SLA-17. Box score: {box['title']}. "
                       "Each section below is the same data in a different node type."
        }])

        # Append each candidate straight onto draft_body, the same way
        # publish.py's _append_image() does for captionedImage.
        for label, _expected, node in candidates:
            post.draft_body["content"].append(marker(f"--- {label} ---"))
            post.draft_body["content"].append(node)

        sent_types = collect_types(post.draft_body)
        print(f"\nSending {len(post.draft_body['content'])} top-level nodes.")
        print(f"  node types sent: {dict(sorted(sent_types.items()))}")

        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id")
        print(f"  created draft id={draft_id}")

        # Read it back -- this is the actual measurement.
        fetched = api.get_draft(draft_id)
        body = parse_body(fetched.get("draft_body"))
        if body is None:
            print("\nINCONCLUSIVE: could not parse draft_body from the read-back.")
            print(f"  raw type: {type(fetched.get('draft_body')).__name__}")
            return 0

        got_types = collect_types(body)
        print(f"  node types returned: {dict(sorted(got_types.items()))}")

        print("\n" + "=" * 62)
        print("RESULT")
        print("=" * 62)
        for label, expected, _node in candidates:
            n_sent = sent_types.get(expected, 0)
            n_got = got_types.get(expected, 0)
            if n_got >= n_sent and n_sent > 0:
                verdict = "STORED"
            elif n_got == 0:
                verdict = "STRIPPED"
            else:
                verdict = f"PARTIAL ({n_got}/{n_sent})"
            print(f"  {verdict:<9} {label}  [{expected}]")

        # A schema that accepts BOTH naming conventions is not validating at all.
        both_conventions = got_types.get("table_row", 0) and got_types.get("tableRow", 0)
        print("\n" + "-" * 62)
        print("STORED != SUPPORTED. draft_body is an opaque blob; the server")
        print("returns whatever you sent. This says nothing about rendering.")
        if both_conventions:
            print("\nBoth snake_case and camelCase tables came back, which no single")
            print("ProseMirror schema can accept -- confirming there is no server-side")
            print("validation here at all.")
        print("\nKnown answer (2026-09-19): opening such a draft in the Substack")
        print("editor gives a BLANK page and 'Something has gone wrong'. The table")
        print("node crashes the editor. Substack has no table support.")
        print("\nThe only valid test is opening the draft in a browser.")
        print("-" * 62)

        pub_base = pub.rstrip("/")
        print(f"\nDraft (not published): {pub_base}/publish/post/{draft_id}")

        if args.delete:
            api.delete_draft(draft_id)
            print(f"Deleted draft id={draft_id}")
        else:
            print("Draft left in place for inspection. Delete it when you're done.")

        return 0

    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print("\nPROBE FAILED (could not complete the test)")
        print(f"  error: {type(e).__name__}: {msg[:400]}")
        if "Just a moment" in msg or "challenge" in msg.lower() or "403" in msg:
            print("  diagnosis: Cloudflare. Check PROXY_URL is a residential IP.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
