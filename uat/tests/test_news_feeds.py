"""
Run:  python -X utf8 uat/tests/test_news_feeds.py

Locks the headline-supply fix from SLA-153 (the 2026-08-24 ESPN outage):

  1. THE LADDER. ESPN's WAF challenges a User-Agent claiming a stale Chrome
     (HTTP 202, empty body). fetch_one_news_feed walks NEWS_UA_LADDER, which
     never claims Chrome, and falls back to the proxy only as a last resort.
  2. THE HEALTH SIGNAL. Each feed is labelled ok / ok_proxy / blocked / empty /
     error and persisted in raw_content.json as news_feed_health, measured on
     entries the FEED returned, not on headlines that survive the 24h filter
     (an out-of-season feed is healthy but all-stale).
  3. THE REPORT. verify_run.py turns a degraded supply into a WARNING that the
     daily status email carries. It must not fail the run: a thinner issue beats
     none, and warnings do not downgrade the verdict (SLA-52).

No API calls, no network: feedparser and the proxy are stubbed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import proxy_session  # noqa: E402
import fetch_content as FC  # noqa: E402


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


def _rss(age_hours: float) -> bytes:
    when = format_datetime(datetime.now(timezone.utc) - timedelta(hours=age_hours))
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            f"<item><title>Headline</title><description>d</description>"
            f"<pubDate>{when}</pubDate></item></channel></rss>").encode()


_real_parse = FC.feedparser.parse


class _Walled:
    """What CI saw from ESPN: HTTP 202, no entries, not bozo."""
    entries, bozo, status, feed = [], False, 202, {}


class _Empty:
    """A real feed with nothing in it: HTTP 200, parses cleanly."""
    entries, bozo, status, feed = [], False, 200, {}


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


def install(parse):
    FC.feedparser.parse = parse


def proxy(resp):
    proxy_session._SESSION = types.SimpleNamespace(
        get=lambda url, timeout=20, headers=None: resp(headers))
    os.environ["PROXY_URL"] = "http://user:pass@proxy.example:1"


def no_proxy():
    os.environ.pop("PROXY_URL", None)
    proxy_session._SESSION = None


print("=" * 66)
print("THE LADDER — fetch_one_news_feed")
print("=" * 66)

check("no ladder entry claims Chrome",
      any("Chrome" in ua for ua in FC.NEWS_UA_LADDER), False)

tried: list[str] = []


def walled_until(n):
    """Walled on the first n UAs, then a real feed."""
    def parse(src, agent=None):
        if isinstance(src, (bytes, bytearray)):
            return _real_parse(src)
        tried.append(agent)
        return _Walled() if len(tried) <= n else _real_parse(_rss(1))
    return parse


no_proxy()
install(walled_until(0))
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("clean feed -> ok", (label, len(entries)), ("ok", 1))
check("only the first UA was used", tried, [FC.NEWS_UA_LADDER[0]])

tried.clear()
install(walled_until(1))
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("walled on the first UA, second gets through -> ok", (label, len(entries)), ("ok", 1))
check("walked past the challenge", tried, FC.NEWS_UA_LADDER[:2])

tried.clear()
install(walled_until(99))
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("walled on every UA, no proxy -> blocked", (label, entries), ("blocked", []))
check("every ladder UA was tried", tried, FC.NEWS_UA_LADDER)

seen_ua = []
proxy(lambda headers: (seen_ua.append((headers or {}).get("User-Agent")),
                       _Resp(200, _rss(1)))[1])
tried.clear()
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("walled on every UA, proxy answers -> ok_proxy", (label, len(entries)), ("ok_proxy", 1))
check("proxy request did not claim Chrome",
      [("Chrome" in (u or "")) for u in seen_ua], [False])

proxy(lambda headers: _Resp(403))
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("proxy 403 too -> still blocked, never a crash", (label, entries), ("blocked", []))

tried.clear()


def parse_empty(src, agent=None):
    tried.append(agent)
    return _Empty()


install(parse_empty)
no_proxy()
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("HTTP 200 with no items -> empty, not blocked", (label, entries), ("empty", []))
check("an empty feed does not burn the whole ladder", len(tried), 1)


def parse_boom(src, agent=None):
    raise OSError("network down")


install(parse_boom)
entries, label = FC.fetch_one_news_feed("https://x/rss")
check("transport failure -> error, never a crash", (label, entries), ("error", []))

print()
print("=" * 66)
print("HEALTH — fetch_news and the persisted summary")
print("=" * 66)

# An out-of-season feed: items exist, all older than 24h.
FC.NEWS_FEEDS = {f"Feed {i}": f"https://x/{i}" for i in range(7)}
install(lambda src, agent=None: _real_parse(_rss(72)))
no_proxy()
heads = FC.fetch_news()
s = FC.news_feed_health_summary()
check("all-stale feeds yield 0 headlines", len(heads), 0)
check("...but are healthy (health is the feed, not the 24h filter)",
      (s["healthy"], s["degraded"]), (7, False))

# The 2026-08-24 shape: six ESPN feeds walled, CBS fine.
FC.NEWS_FEEDS = {**{f"ESPN {i}": f"https://espn/{i}" for i in range(6)},
                 "CBS Sports": "https://cbs/1"}


def espn_wall(src, agent=None):
    if isinstance(src, (bytes, bytearray)):
        return _real_parse(src)
    return _Walled() if "espn" in src else _real_parse(_rss(1))


install(espn_wall)
heads = FC.fetch_news()
s = FC.news_feed_health_summary()
check("CBS-only supply: 1 headline", len(heads), 1)
check("six ESPN feeds blocked", sorted({v for k, v in s["feeds"].items()
                                        if k.startswith("ESPN")}), ["blocked"])
check("1 of 7 healthy -> degraded", (s["healthy"], s["total"], s["degraded"]), (1, 7, True))

# One flaky feed is routine, not an outage.
FC.NEWS_FEEDS = {f"Feed {i}": f"https://x/{i}" for i in range(7)}
install(lambda src, agent=None: _Walled() if str(src).endswith("/0") else _real_parse(_rss(1)))
FC.fetch_news()
s = FC.news_feed_health_summary()
check("one dead feed of 7 is not degraded", (s["healthy"], s["degraded"]), (6, False))

# Walled directly, rescued by the proxy: healthy, and the label says how.
FC.NEWS_FEEDS = {f"Feed {i}": f"https://x/{i}" for i in range(7)}
install(lambda src, agent=None: _real_parse(src) if isinstance(src, (bytes, bytearray))
        else _Walled())
proxy(lambda headers: _Resp(200, _rss(1)))
FC.fetch_news()
s = FC.news_feed_health_summary()
check("proxy rescue counts as healthy", (s["healthy"], s["degraded"]), (7, False))
check("...and is labelled ok_proxy", set(s["feeds"].values()), {"ok_proxy"})
no_proxy()
install(_real_parse)

check("summary is JSON-serialisable", bool(json.dumps(s)), True)

print()
print("=" * 66)
print("THE REPORT — verify_run.py warns, never fails")
print("=" * 66)


def gate(health) -> tuple[int, str]:
    """verify_run.py on a real archived issue, with a given raw_content.json."""
    day = REPO / "archive" / "2026-09-04"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for name in ("verify_run.py", "run_status.py"):
            shutil.copy(REPO / name, t / name)
        shutil.copy(day / "newsletter_draft.html", t / "newsletter_draft.html")
        shutil.copy(day / "newsletter_substack.html", t / "newsletter_substack.html")
        (t / "run_status.json").write_text(
            json.dumps({"date": "x", "email_sent": True}), encoding="utf-8")
        if health is not None:
            (t / "raw_content.json").write_text(
                json.dumps({"news_headlines": [], "tweets": [],
                            "news_feed_health": health}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-X", "utf8", "verify_run.py"], cwd=t,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout


if (REPO / "archive" / "2026-09-04" / "newsletter_substack.html").exists():
    degraded = {"feeds": {"ESPN NFL": "blocked", "ESPN NBA": "blocked",
                          "ESPN MLB": "blocked", "CBS Sports": "ok"},
                "healthy": 1, "total": 4, "min_expected": 5, "degraded": True}
    code, out = gate(degraded)
    check("degraded supply does NOT fail the gate", code, 0)
    check("...but is named as a warning", "headline supply degraded" in out, True)
    check("...and names the dead feeds", "ESPN NFL" in out, True)

    code, out = gate({**degraded, "degraded": False, "healthy": 4})
    check("healthy supply -> no warning", "headline supply degraded" in out, False)

    code, out = gate(None)
    check("no raw_content.json (older run) -> no warning, no crash",
          (code, "headline supply degraded" in out), (0, False))
else:
    print("  (archive/2026-09-04 not present here; verify_run check skipped)")

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
