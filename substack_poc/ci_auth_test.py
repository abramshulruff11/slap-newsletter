"""
Minimal Substack-from-CI connectivity test.

Purpose: determine whether GitHub Actions (a datacenter IP) can reach Substack's
API, or whether Cloudflare blocks it with a 403 the way it blocked the old
Playwright approach. Self-contained: only needs `python-substack` + stdlib.

Reads two env vars (set as GitHub Actions secrets):
  SUBSTACK_COOKIES_STRING    your Substack cookie header string
  SUBSTACK_PUBLICATION_URL   e.g. https://yourpub.substack.com

It authenticates, creates a tiny throwaway draft, then DELETES it. Nothing is
published. Prints a clear PASS / FAIL line the Actions log can show.
"""

import os
import sys
import traceback


def main() -> int:
    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not cookies or not pub:
        print("FAIL: missing SUBSTACK_COOKIES_STRING or SUBSTACK_PUBLICATION_URL secret")
        return 2

    try:
        from substack import Api
        from substack.post import Post

        print("Authenticating to Substack from this runner...")
        api = Api(cookies_string=cookies, publication_url=pub)
        uid = api.get_user_id()
        print(f"  auth OK, user_id={uid}")

        post = Post("CI connectivity test (safe to ignore)", "", uid)
        post.paragraph(content=[{"content": "Automated CI reachability test. Will be deleted."}])
        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id")
        print(f"  created draft id={draft_id}")

        api.delete_draft(draft_id)
        print(f"  deleted draft id={draft_id}")

        print("\nSUBSTACK CI TEST: PASS — Actions can reach Substack. Integration is viable.")
        return 0
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print("\nSUBSTACK CI TEST: FAIL")
        print(f"  error: {type(e).__name__}: {msg[:500]}")
        if "403" in msg or "cloudflare" in msg.lower() or "<!DOCTYPE html>" in msg:
            print("  diagnosis: looks like a Cloudflare / datacenter-IP block (403).")
            print("  -> Actions can't post directly; we'll need a residential proxy or another runner.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
