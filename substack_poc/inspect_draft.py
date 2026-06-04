"""
Read-only helper to reverse-engineer how Substack represents a tweet embed.

Substack doesn't document its draft body schema, so to embed tweets correctly
we need to see one real example from your account. This script never creates,
edits, publishes, or deletes anything -- it only reads.

Workflow to capture the tweet-embed node:

  1. In the Substack web editor, start a NEW draft and paste a single tweet URL
     on its own line so it converts to an embed card. Save the draft.
  2. Run:   python inspect_draft.py list
     ...to find that draft's id (most recent at the top).
  3. Run:   python inspect_draft.py dump <draft_id>
     ...and copy the highlighted non-text node(s) back to me.

Optionally also run:   python inspect_draft.py embed https://twitter.com/.../status/123
which probes Substack's /publication/embed endpoint for that URL.

Auth is read from .env exactly like publish.py.
"""

from __future__ import annotations

import json
import sys

from publish import make_api

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Node types we already know how to build; anything else is what we're hunting.
KNOWN_TYPES = {"paragraph", "heading", "captionedImage", "horizontal_rule"}


def cmd_list(api) -> None:
    drafts = api.get_drafts(limit=15)
    items = drafts if isinstance(drafts, list) else drafts.get("posts", drafts)
    print(f"{'id':>12}  title")
    print("-" * 50)
    for d in items:
        print(f"{str(d.get('id')):>12}  {d.get('draft_title') or d.get('title') or '(untitled)'}")


def cmd_dump(api, draft_id: str) -> None:
    draft = api.get_draft(draft_id)
    body_raw = draft.get("draft_body")
    body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
    nodes = body.get("content", [])
    print(f"draft {draft_id}: {len(nodes)} top-level nodes\n")
    print("=== full body JSON ===")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    interesting = [n for n in nodes if n.get("type") not in KNOWN_TYPES]
    print("\n=== NON-TEXT / EMBED NODES (this is what we need) ===")
    if interesting:
        for n in interesting:
            print(json.dumps(n, indent=2, ensure_ascii=False))
    else:
        print("(none found -- did the tweet convert to an embed card before saving?)")


def cmd_embed(api, url: str) -> None:
    print(f"GET /publication/embed?url={url}\n")
    try:
        print(json.dumps(api.publication_embed(url), indent=2, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 -- surface whatever Substack returns
        print(f"embed probe failed: {type(e).__name__}: {e}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    cmd = sys.argv[1]
    api = make_api()
    if cmd == "list":
        cmd_list(api)
    elif cmd == "dump" and len(sys.argv) >= 3:
        cmd_dump(api, sys.argv[2])
    elif cmd == "embed" and len(sys.argv) >= 3:
        cmd_embed(api, sys.argv[2])
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
