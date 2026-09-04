"""
Run:  python -X utf8 uat/tests/test_proxy_fallback.py

Locks the ESPN 403 fallback in fetch_sports_data.fetch_url and the RSS
bot-wall fallback in fetch_content.fetch_news, with the network stubbed.

No API calls, no network. Exists because the sandbox that wrote this cannot
reach ESPN at all, and because the failure this guards against — a blocked
fetch that reports itself as a quiet day — was invisible for three weeks.
"""
from __future__ import annotations

import io
import json
import os
import sys
import types
from pathlib import Path
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import proxy_session  # noqa: E402
import fetch_sports_data as FSD  # noqa: E402
import fetch_content as FC  # noqa: E402


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


def _http_403(url):
    return HTTPError(url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b""))


class _Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


def _reset_health():
    for k in ("direct_ok", "proxy_ok", "failed", "blocked_no_proxy"):
        FSD.FETCH_HEALTH[k] = 0
    FSD.FETCH_HEALTH["failed_urls"].clear()


print("=" * 66)
print("PROXY FALLBACK — fetch_sports_data.fetch_url")
print("=" * 66)

# Every direct request 403s, like GitHub's runner IP against ESPN.
def _always_403(req, timeout=15):
    raise _http_403(req.full_url)

FSD.urlopen = _always_403
FSD.time.sleep = lambda *_: None

# 1. 403 with NO proxy: fails, and says so in the health block.
os.environ.pop("PROXY_URL", None)
_reset_health()
check("no proxy -> None", FSD.fetch_url("https://site.api.espn.com/x/scoreboard"), None)
check("no proxy -> blocked_no_proxy counted", FSD.FETCH_HEALTH["blocked_no_proxy"], 1)
check("no proxy -> failed counted", FSD.FETCH_HEALTH["failed"], 1)

# 2. 403 WITH a proxy: the proxy path answers and the data comes back.
os.environ["PROXY_URL"] = "http://user:pass@proxy.example:1"
proxy_session._SESSION = types.SimpleNamespace(
    get=lambda url, timeout=20, headers=None: _Resp(200, {"events": [{"id": "1"}]})
)
_reset_health()
data = FSD.fetch_url("https://site.api.espn.com/x/scoreboard")
check("proxy -> data returned", data, {"events": [{"id": "1"}]})
check("proxy -> proxy_ok counted", FSD.FETCH_HEALTH["proxy_ok"], 1)
check("proxy -> nothing failed", FSD.FETCH_HEALTH["failed"], 0)

# 3. Proxy path itself 403s: still a failure, still counted, never a crash.
proxy_session._SESSION = types.SimpleNamespace(
    get=lambda url, timeout=20, headers=None: _Resp(403)
)
_reset_health()
check("proxy 403 -> None", FSD.fetch_url("https://site.api.espn.com/x/scoreboard"), None)
check("proxy 403 -> failed counted", FSD.FETCH_HEALTH["failed"], 1)
check("proxy 403 -> url recorded", len(FSD.FETCH_HEALTH["failed_urls"]), 1)

print()
print("=" * 66)
print("RSS BOT-WALL FALLBACK — fetch_content.fetch_news")
print("=" * 66)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>ESPN</title>
<item><title>Real headline</title><description>d</description>
<pubDate>%s</pubDate></item></channel></rss>"""
from email.utils import format_datetime  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
RSS = RSS % format_datetime(datetime.now(timezone.utc)).encode()

# Direct parse returns what CI saw: HTTP 202, no entries, not bozo.
class _EmptyFeed:
    entries, bozo, status = [], False, 202
    feed = {}

_real_parse = FC.feedparser.parse

def _fake_parse(src, agent=None):
    if isinstance(src, (bytes, bytearray)):
        return _real_parse(src)          # the proxied body, parsed for real
    return _EmptyFeed()                  # the direct call: bot wall

FC.feedparser.parse = _fake_parse
FC.NEWS_FEEDS = {"ESPN MLB": "https://www.espn.com/espn/rss/mlb/news"}

# 4. Without a proxy the bot wall stays a bot wall: zero headlines.
os.environ.pop("PROXY_URL", None)
proxy_session._SESSION = None
check("no proxy -> 0 headlines", len(FC.fetch_news()), 0)

# 5. With a proxy the body is fetched and parsed: the headline comes through.
os.environ["PROXY_URL"] = "http://user:pass@proxy.example:1"
proxy_session._SESSION = types.SimpleNamespace(
    get=lambda url, timeout=20, headers=None: _Resp(200, content=RSS)
)
heads = FC.fetch_news()
check("proxy -> 1 headline", len(heads), 1)
check("proxy -> title parsed", heads[0]["title"] if heads else None, "Real headline")

# 6. A genuine 200-with-nothing feed is NOT treated as blocked.
class _QuietFeed(_EmptyFeed):
    status = 200
check("HTTP 200 empty is not 'blocked'", FC._blocked_feed(_QuietFeed()), False)
check("HTTP 202 empty is 'blocked'", FC._blocked_feed(_EmptyFeed()), True)

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
