"""
UAT-only: capture a fresh frozen fixture WITH media flags.

Mirrors production fetch_content.py (same feeds, same 24h window, same output
shape) with one addition: it reads the Nitter RSS <description>, which carries
the media markup, and records media_kind / has_video per tweet. Production
keeps only <title>, so that signal is lost there.

Writes ONLY to uat/fixtures/. It never touches the repo-root raw_content.json.

Run once, commit the result, then never run it again — every UAT run must read
the same frozen input so differences come from prompt changes, not a new news
day. Re-running produces a different day's content and invalidates comparisons.

    python uat/fetch_fixture.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime

import feedparser

# Windows consoles default to cp1252 and choke on the box-drawing/emoji glyphs
# used throughout this project's output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UAT_DIR     = Path(__file__).resolve().parent
REPO_ROOT   = UAT_DIR.parent
FIXTURE_OUT = UAT_DIR / "fixtures" / "raw_content_enriched.json"

NITTER_BASE = "https://nitter.net"
STATUS_RE   = re.compile(r"/status/(\d+)")


def _load_production_config() -> tuple[dict, list[str]]:
    """Read NEWS_FEEDS and TWITTER_HANDLES out of production fetch_content.py.

    Parsed rather than imported: importing would run module-level code, and
    copying the lists here would let them drift out of sync with production.
    """
    src = (REPO_ROOT / "fetch_content.py").read_text(encoding="utf-8")
    ns: dict = {}
    for name in ("NEWS_FEEDS", "TWITTER_HANDLES"):
        m = re.search(rf"^{name}\s*=\s*([\{{\[].*?^[\}}\]])", src, re.S | re.M)
        if not m:
            raise SystemExit(f"Could not parse {name} from fetch_content.py")
        ns[name] = eval(m.group(1))          # literal list/dict from our own repo
    return ns["NEWS_FEEDS"], ns["TWITTER_HANDLES"]


def classify(summary: str) -> str:
    if re.search(r">\s*Video\s*<|amplify_video_thumb|ext_tw_video", summary):
        return "video"
    if "tweet_video_thumb" in summary:
        return "gif"
    if "<img" in summary:
        return "image"
    return "text"


def entry_dt(entry):
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
    return None


def within_24h(dt) -> bool:
    return dt is not None and (datetime.now(timezone.utc) - dt) <= timedelta(hours=24)


def nitter_to_twitter(url: str) -> str:
    return url.replace("https://nitter.net/", "https://twitter.com/") \
              .replace("http://nitter.net/", "https://twitter.com/")


def main() -> None:
    news_feeds, handles = _load_production_config()
    print(f"Mirroring production config: {len(news_feeds)} news feeds, "
          f"{len(handles)} handles\n")

    print("Fetching news headlines...")
    headlines = []
    for name, url in news_feeds.items():
        feed = feedparser.parse(url)
        n = 0
        for e in feed.entries:
            dt = entry_dt(e)
            if not within_24h(dt):
                continue
            headlines.append({
                "title":       e.get("title", ""),
                "description": e.get("summary", ""),
                "source":      name,
                "pubDate":     dt.isoformat(),
            })
            n += 1
        print(f"  {name:<28} {n}")
    print(f"  -- {len(headlines)} headlines\n")

    print("Fetching tweets via Nitter RSS (with media detection)...")
    tweets = []
    failed = []
    for i, handle in enumerate(handles, 1):
        feed = feedparser.parse(f"{NITTER_BASE}/{handle}/rss?limit=50")
        entries = getattr(feed, "entries", []) or []
        if not entries:
            failed.append(handle)
        n = 0
        for e in entries:
            dt = entry_dt(e)
            if not within_24h(dt):
                continue
            summary = str(e.get("summary", ""))
            kind    = classify(summary)
            link    = nitter_to_twitter(e.get("link", ""))
            tweets.append({
                "account":       handle,
                "text":          e.get("title", ""),
                "link":          link,
                "pubDate":       dt.isoformat(),
                # --- UAT additions ---
                "media_kind":    kind,
                "has_video":     kind in ("video", "gif"),
                "detect_source": "nitter_rss",
                "status_id":     (STATUS_RE.search(link).group(1)
                                  if STATUS_RE.search(link) else ""),
            })
            n += 1
        if n:
            print(f"  [{i:>2}/{len(handles)}] @{handle:<20} {n}")
    print(f"  -- {len(tweets)} tweets\n")

    if not tweets:
        raise SystemExit("No tweets fetched — Nitter may be down. Fixture not written.")

    out = {"news_headlines": headlines, "tweets": tweets}
    FIXTURE_OUT.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    kinds = Counter(t["media_kind"] for t in tweets)
    vids  = sum(1 for t in tweets if t["has_video"])
    print("=== FIXTURE CAPTURED ===")
    print(f"  Headlines      : {len(headlines)}")
    print(f"  Tweets         : {len(tweets)}")
    print(f"  Media kinds    : {dict(kinds)}")
    print(f"  has_video      : {vids}/{len(tweets)} ({vids/len(tweets)*100:.1f}%)")
    print(f"  Detection      : 100% nitter_rss (high confidence)")
    if failed:
        print(f"  Empty feeds    : {len(failed)} -> {', '.join(failed[:8])}"
              + (" ..." if len(failed) > 8 else ""))
    print(f"\n  Wrote {FIXTURE_OUT.relative_to(REPO_ROOT)}")
    print("  Commit this file. Do not re-run — the fixture must stay frozen.")


if __name__ == "__main__":
    main()
