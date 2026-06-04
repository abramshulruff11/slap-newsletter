"""
Substack-from-CI connectivity test, via a residential proxy + browser impersonation.

GitHub Actions runs on datacenter IPs, which Cloudflare blocks with a JS
challenge ("Just a moment..."). To get through we (1) route requests through a
residential-IP proxy so Cloudflare sees a normal home user, and (2) impersonate
a real Chrome TLS fingerprint via curl_cffi so the client doesn't look like a
bot. Combined with the valid Substack session cookie, this typically passes
without needing a real browser.

Env (set as GitHub Actions secrets):
  SUBSTACK_COOKIES_STRING    your Substack cookie header string
  SUBSTACK_PUBLICATION_URL   e.g. https://yourpub.substack.com
  PROXY_URL                  residential proxy, e.g. http://user:pass@host:port
                             (if unset, goes direct -- expected to FAIL from CI)

Creates a throwaway draft then DELETES it. Never publishes.
"""

import os
import sys
import traceback


def _install_session(proxy_url: str | None) -> str:
    """Patch the Session that python-substack builds, to use curl_cffi (+proxy)."""
    import substack.api as sapi
    from curl_cffi import requests as creq

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    sapi.requests.Session = lambda: creq.Session(impersonate="chrome", proxies=proxies)
    return "proxy + curl_cffi(chrome)" if proxy_url else "DIRECT + curl_cffi(chrome)"


def main() -> int:
    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub = os.getenv("SUBSTACK_PUBLICATION_URL")
    proxy = os.getenv("PROXY_URL")
    if not cookies or not pub:
        print("FAIL: missing SUBSTACK_COOKIES_STRING or SUBSTACK_PUBLICATION_URL secret")
        return 2
    if not proxy:
        print("WARNING: PROXY_URL not set -- testing DIRECT (expected to fail from CI).")

    try:
        mode = _install_session(proxy)
        print(f"Mode: {mode}")

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

        print("\nSUBSTACK CI TEST: PASS — Actions reached Substack through the proxy. Integration is viable.")
        return 0
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print("\nSUBSTACK CI TEST: FAIL")
        print(f"  error: {type(e).__name__}: {msg[:400]}")
        if "Just a moment" in msg or "challenge" in msg.lower() or "403" in msg:
            print("  diagnosis: still hitting Cloudflare. The proxy IP may not be residential,")
            print("  or Cloudflare is serving a JS challenge -> we'd escalate to a real browser.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
