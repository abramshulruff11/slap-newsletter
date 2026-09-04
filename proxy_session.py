"""
Residential-proxy HTTP session, shared by the fetchers.

GitHub Actions runs from datacenter IPs that ESPN (and Cloudflare, for
Substack) reject. substack_poc/publish.py already routes around that with the
residential proxy in PROXY_URL and a Chrome-impersonating curl_cffi session.
This module is the same trick for the ESPN scoreboard API and the ESPN RSS
feeds, which have been 403/202-blocked on every CI run since mid-August 2026
while looking healthy from a laptop.

Usage is deliberately fallback-only: callers try a direct request first (free)
and only go through the proxy when the direct request is blocked, so proxy
bandwidth is spent on the handful of calls that need it.

get_session() returns None when PROXY_URL is unset (local runs from a
residential IP need no proxy), so every caller degrades to its old behaviour.
"""

from __future__ import annotations

import os

_SESSION = None
_LOGGED = False


def proxy_url() -> str:
    return (os.getenv("PROXY_URL") or "").strip()


def get_session():
    """A curl_cffi Session routed through PROXY_URL, or None if unset."""
    global _SESSION, _LOGGED
    proxy = proxy_url()
    if not proxy:
        return None
    if _SESSION is None:
        from curl_cffi import requests as creq  # listed in requirements.txt

        _SESSION = creq.Session(
            impersonate="chrome",
            proxies={"http": proxy, "https": proxy},
        )
        if not _LOGGED:
            print("  [proxy] PROXY_URL set — blocked requests will retry via the residential proxy")
            _LOGGED = True
    return _SESSION


def get_via_proxy(url: str, timeout: int = 20, headers: dict | None = None):
    """
    GET `url` through the proxy. Returns the curl_cffi response, or None when
    no proxy is configured or the request itself failed. Callers check
    `.status_code` themselves so a 403 from the proxy path is visible too.
    """
    sess = get_session()
    if sess is None:
        return None
    try:
        return sess.get(url, timeout=timeout, headers=headers or {})
    except Exception as e:  # noqa: BLE001 -- transport failure; caller logs
        print(f"      [proxy] request failed: {type(e).__name__}: {str(e)[:80]}")
        return None
