"""Manual-only: can GitHub Actions actually reach our Nitter sources?

Local verification runs from a residential IP. The daily pipeline runs from a
GitHub Actions datacenter IP, and every surviving Nitter mirror sits behind
Cloudflare or an Anubis wall -- the same class of block that 403'd the Substack
integration until it was routed through a residential proxy. A mirror that works
from a laptop is therefore NOT evidence it works in CI.

This probes each configured base directly (no retries, no backoff) and prints a
verdict per base. It fetches nothing else and writes nothing.
"""

import socket
import sys
import time

import feedparser

from fetch_content import (
    BROWSER_UA,
    NITTER_BASE,
    NITTER_FALLBACKS,
    _feed_belongs_to,
)

socket.setdefaulttimeout(20)

PROBE_HANDLES = ["AdamSchefter", "ESPN"]


def probe(base: str, handle: str) -> tuple[str, str]:
    t0 = time.monotonic()
    try:
        feed = feedparser.parse(f"{base}/{handle}/rss?limit=50", agent=BROWSER_UA)
    except Exception as exc:                       # noqa: BLE001 -- report, don't raise
        return "ERROR", f"{type(exc).__name__}: {exc}"

    status = getattr(feed, "status", None)
    n = len(feed.entries)
    elapsed = time.monotonic() - t0
    title = (getattr(feed, "feed", {}) or {}).get("title") or ""

    if n and _feed_belongs_to(feed, handle):
        return "OK", f"HTTP {status}, {n} entries, {elapsed:.1f}s -- {title!r}"
    if n:
        return "NOTICE", f"HTTP {status}, {n} entries but title is {title!r} (not this handle)"
    if status == 403:
        return "BLOCKED", f"HTTP 403 -- bot wall. Title: {title!r}"
    if status == 200 and feed.bozo:
        return "CHALLENGE", f"HTTP 200 but not RSS -- challenge page. Title: {title!r}"
    return "EMPTY", f"HTTP {status}, 0 entries, bozo={bool(feed.bozo)}"


def main() -> int:
    bases = [NITTER_BASE] + NITTER_FALLBACKS
    print(f"Probing {len(bases)} base(s) from this runner's IP:\n")

    any_ok = False
    for base in bases:
        for handle in PROBE_HANDLES:
            verdict, detail = probe(base, handle)
            if verdict == "OK":
                any_ok = True
            print(f"  [{verdict:9s}] {base}  @{handle}\n              {detail}")
        print()

    if any_ok:
        print("VERDICT: at least one base serves real feeds from CI. "
              "The daily run should get tweets.")
        return 0

    print("VERDICT: NO base is usable from this runner. The daily run will "
          "degrade to headline-only.\n"
          "If these same bases work from a laptop, this is an IP-reputation block "
          "(datacenter vs residential), not an outage -- the fix is routing the "
          "Nitter fetch through PROXY_URL the way substack_poc/publish.py does, "
          "not swapping mirrors.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
