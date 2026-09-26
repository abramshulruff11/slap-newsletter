"""
SLA-17 housekeeping: delete the probe artifacts left on Substack and Datawrapper.

The investigation left things in two places that the UI cannot always clear:

  * Substack drafts. One of them carried a ProseMirror `table` node, which
    crashes the editor on load -- a draft you cannot open is a draft you
    cannot delete by hand. The API needs no editor.
  * Datawrapper charts. Harmless, but they accumulate, and an account full of
    "Cleveland Guardians at Detroit Tigers" is its own small mess.

Deletion is irreversible, so this prints exactly what it is about to remove,
reports each item separately, and treats an already-gone item as success
rather than an error -- re-running after a partial failure must be safe.

Usage:
    python substack_poc/cleanup_probe_artifacts.py \
        --drafts 216824200,217583103 --charts Lmmot,pobG5

Env: SUBSTACK_COOKIES_STRING, SUBSTACK_PUBLICATION_URL, PROXY_URL,
     DATAWRAPPER_API_TOKEN (chart:write)
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import List

sys.path.insert(0, os.path.dirname(__file__))


def _split(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _gone(exc: Exception) -> bool:
    """A 404 means someone already deleted it. That is the desired end state,
    not a failure -- otherwise a re-run after a partial sweep goes red."""
    s = str(exc).lower()
    return "404" in s or "not found" in s


def delete_drafts(ids: List[str]) -> int:
    if not ids:
        return 0
    failures = 0
    try:
        from publish import make_api  # retries transient proxy failures
        api = make_api()
        print(f"Substack auth OK (user_id={api.get_user_id()})")
    except Exception as e:  # noqa: BLE001
        print(f"Substack auth FAILED: {type(e).__name__}: {str(e)[:200]}")
        return len(ids)

    for did in ids:
        try:
            api.delete_draft(int(did))
            print(f"  deleted draft {did}")
        except Exception as e:  # noqa: BLE001
            if _gone(e):
                print(f"  draft {did} already gone")
            else:
                print(f"  draft {did} FAILED: {type(e).__name__}: {str(e)[:160]}")
                failures += 1
    return failures


def delete_charts(ids: List[str], token: str) -> int:
    if not ids:
        return 0
    if not token:
        print("DATAWRAPPER_API_TOKEN not set — skipping charts")
        return len(ids)
    from datawrapper_probe import dw_request

    failures = 0
    for cid in ids:
        status, body = dw_request("DELETE", f"/charts/{cid}", token)
        if status in (200, 204):
            print(f"  deleted chart {cid}")
        elif status == 404:
            print(f"  chart {cid} already gone")
        else:
            print(f"  chart {cid} FAILED: {status} {body[:160]}")
            failures += 1
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete SLA-17 probe artifacts")
    ap.add_argument("--drafts", default="", help="comma-separated Substack draft ids")
    ap.add_argument("--charts", default="", help="comma-separated Datawrapper chart ids")
    args = ap.parse_args()

    drafts, charts = _split(args.drafts), _split(args.charts)
    if not drafts and not charts:
        print("Nothing to do: pass --drafts and/or --charts")
        return 2

    print("About to permanently delete:")
    print(f"  {len(drafts)} Substack draft(s): {', '.join(drafts) or '(none)'}")
    print(f"  {len(charts)} Datawrapper chart(s): {', '.join(charts) or '(none)'}")
    print()

    failures = 0
    if drafts:
        print("Substack drafts:")
        failures += delete_drafts(drafts)
    if charts:
        print("Datawrapper charts:")
        failures += delete_charts(charts, os.getenv("DATAWRAPPER_API_TOKEN"))

    print()
    if failures:
        print(f"{failures} item(s) could not be deleted — see above.")
        return 1
    print("All requested artifacts are gone.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
