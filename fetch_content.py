"""
SLAP Newsletter — Fetch sports news headlines (RSS) and tweets (Nitter RSS).
Filters everything to the last 24 hours and writes raw_content.json.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser


# ── Sports news RSS feeds ────────────────────────────────────────────────────

NEWS_FEEDS = {
    "ESPN Top Headlines":        "https://www.espn.com/espn/rss/news",
    "ESPN NFL":                  "https://www.espn.com/espn/rss/nfl/news",
    "ESPN NBA":                  "https://www.espn.com/espn/rss/nba/news",
    "ESPN MLB":                  "https://www.espn.com/espn/rss/mlb/news",
    "ESPN College Football":     "https://www.espn.com/espn/rss/ncf/news",
    "ESPN College Basketball":   "https://www.espn.com/espn/rss/ncb/news",
    "CBS Sports":                "https://www.cbssports.com/rss/headlines",
}

# ── Twitter accounts via Nitter RSS ──────────────────────────────────────────

TWITTER_HANDLES = [
    "SharpFootball",
    "AdamSchefter",
    "BarstoolBigCat",
    "PFTCommenter",
    "TheNBACentel",
    "BallsackSports",
    "Ihartitz",
    "HaterReport",
    "ESPN",
    "coleadamss",
    "OldTakesExposed",
    "StatMuse",
    "W_B_Rick",
    "mickjason",
    "ArtButSports",
    "JayCuda",
    "HoHighlights",       # renamed from HouseOfHighlights (old handle 404s on nitter)
    "NFLMemes",
    "SleeperHQ",
    "ghetto_gronk",
    "TalkinBaseball_",
    "PatMcAfeeShow",
    "NotBillWalton",
# --- NEW: Insiders with personality ---
    "KevinOConnor",       # renamed from KevinOConnorNBA (old handle 404s on nitter)
    "BillBarnwell",
    "JoeyMulinaro",
    "FieldYates",
    "TimBontemps",
    "JonRothstein",
    "AlbertBreer",
    "ShamsCharania",
    "JeffPassan",
    "ChrisBHaynes",
    # --- NEW: Comedy / Reaction / Clips ---
    "NOTSportsCenter",
    "SportsCenter",
    "ContextFreeCBB",
    "BackAftaThis",
    # CoveringCBB removed 2026-08-20 -- HTTP 404 on nitter, no trace of the account
    # anywhere on the web, and zero tweets across 104 archived days.
    "TrashTalkNFL",
    "ClutchPoints",
    "NBAMemes",
    # --- NEW: Wild cards ---
    "LeBatardShow",
    "CJZero",
    "TomPelissero",
    "BarstoolReags",
    "JackKennedy",        # renamed from KenJac (old handle 404s on nitter)
    # --- NEW: Added batch ---
    # NFL_Memes removed 2026-08-20 -- HTTP 404, and a typo'd duplicate of NFLMemes above,
    # which is live and has produced 159 tweets across 60 archived days.
    "OnionSports",
    "FakeSportsCentr",
    "WorldWideWob",
    "RedditCFB",
    # GoodGameCBB removed 2026-08-20 -- HTTP 404 on nitter, no trace of the account
    # anywhere on the web, and zero tweets across 104 archived days.
    "JomboyMedia",
    "CespedesBBQ",
]

NITTER_BASE = "https://nitter.net"

# Browser UA — ESPN 403s feedparser's default agent (same fix as
# fetch_sports_data.py, where non-browser UAs get blocked).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# CBS's feed injects affiliate/betting spam. Headlines whose title or
# description contains any of these (case-insensitive) are dropped.
NEWS_SPAM_KEYWORDS = (
    "promo code",
    "sportsbook",
    "betting app",
    "bonus bet",
)

# nitter.net rate-limits PER ACCOUNT, not per IP: it serves a cached feed instantly
# for accounts it has warm, and returns HTTP 429 for accounts it has to refresh from
# upstream. feedparser turns that 429 error page into an empty .entries list, which
# is indistinguishable from "this account posted nothing" unless you check .status.
# Retrying the same handle a few seconds later usually clears it -- measured 2026-08-20,
# @Ihartitz needed 5 attempts and then returned 20 entries.
TWEET_FETCH_ATTEMPTS = 4
TWEET_RETRY_BACKOFF = [5, 15, 30]   # seconds before attempts 2..4

# Surveyed 2026-08-20: xcancel.com (400), nitter.poast.org (403), lightbrd.com (403),
# nitter.privacydev.net (DNS failure), nitter.tiekoetter.com (200 but always 0 entries).
# No public mirror is currently usable, so this stays empty -- but the fetch loop reads
# it, so adding a host here is the only change needed if one comes back.
NITTER_FALLBACKS: list[str] = []

# If fewer than this many handles yield tweets, the run is degraded enough that the
# newsletter's tweet supply is compromised -- say so loudly rather than proceeding quietly.
MIN_HEALTHY_HANDLES = 12


# ── Helpers ──────────────────────────────────────────────────────────────────

def entry_published_dt(entry) -> datetime | None:
    """Extract a timezone-aware datetime from a feed entry."""
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
    return None


def is_within_last_24h(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=24)


def format_dt(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def is_retweet_or_reply(text: str) -> bool:
    """Nitter RSS prefixes retweets 'RT by @' and replies 'R to @'. Drop both."""
    t = text.lstrip()
    return t.startswith("RT by @") or t.startswith("R to @")


def is_spam_headline(title: str, description: str) -> bool:
    blob = f"{title} {description}".lower()
    return any(kw in blob for kw in NEWS_SPAM_KEYWORDS)


# ── Fetch news headlines ─────────────────────────────────────────────────────

def fetch_news() -> list[dict]:
    headlines = []
    dropped_spam = 0
    for source_name, url in NEWS_FEEDS.items():
        feed = feedparser.parse(url, agent=BROWSER_UA)
        # Surface silent feed failures — ESPN was returning zero entries
        # and nobody noticed because feedparser fails quietly.
        if feed.bozo or not feed.entries:
            print(f"  ⚠ {source_name}: {len(feed.entries)} entries "
                  f"(bozo={feed.bozo}, status={getattr(feed, 'status', '?')})")
        else:
            print(f"  {source_name}: {len(feed.entries)} entries")
        for entry in feed.entries:
            pub_dt = entry_published_dt(entry)
            if not is_within_last_24h(pub_dt):
                continue
            title = entry.get("title", "")
            description = entry.get("summary", "")
            if is_spam_headline(title, description):
                dropped_spam += 1
                continue
            headlines.append({
                "title":       title,
                "description": description,
                "source":      source_name,
                "pubDate":     format_dt(pub_dt),
            })
    if dropped_spam:
        print(f"  -- dropped {dropped_spam} betting/promo headline(s)")
    return headlines


# ── Fetch tweets via Nitter ──────────────────────────────────────────────────

def nitter_to_twitter(url: str) -> str:
    """Convert nitter.net URLs to twitter.com URLs for Substack embedding."""
    return url.replace("https://nitter.net/", "https://twitter.com/").replace("http://nitter.net/", "https://twitter.com/")


def fetch_one_handle(handle: str) -> tuple[list, str]:
    """Fetch one handle's feed, retrying past nitter's per-account rate limiting.

    Returns (entries, status_label). status_label is one of:
      ok         -- feed parsed with at least one entry
      missing    -- HTTP 404: no such account on nitter (renamed, deleted, or a typo)
      blocked    -- HTTP 403: suspended or protected
      ratelimit  -- HTTP 429 on every attempt (transient; NOT evidence the account is dead)
      empty      -- HTTP 200 but zero entries
      error      -- transport failure or unexpected status
    """
    bases = [NITTER_BASE] + NITTER_FALLBACKS
    last = "error"
    for attempt in range(1, TWEET_FETCH_ATTEMPTS + 1):
        for base in bases:
            feed = feedparser.parse(f"{base}/{handle}/rss?limit=50", agent=BROWSER_UA)
            status = getattr(feed, "status", None)

            if status == 404:
                return [], "missing"      # deterministic -- retrying will not help
            if status == 403:
                return [], "blocked"
            if status == 429:
                last = "ratelimit"
                continue
            if feed.entries:
                return feed.entries, "ok"
            last = "empty" if status == 200 else "error"

        if attempt < TWEET_FETCH_ATTEMPTS:
            time.sleep(TWEET_RETRY_BACKOFF[min(attempt - 1, len(TWEET_RETRY_BACKOFF) - 1)])
    return [], last


def fetch_tweets() -> list[dict]:
    tweets = []
    report: dict[str, list[str]] = {}
    dropped_rt_reply = 0

    for handle in TWITTER_HANDLES:
        entries, status = fetch_one_handle(handle)

        fresh = 0
        for entry in entries:
            pub_dt = entry_published_dt(entry)
            if not is_within_last_24h(pub_dt):
                continue
            # Retweets arrive under the RETWEETER's feed but carry the ORIGINAL
            # tweet's URL, so they both duplicate a tweet we may already have and
            # misattribute it downstream (account diversity caps are keyed on the
            # feed name). Replies are half a conversation. Drop both.
            text = entry.get("title", "")
            if is_retweet_or_reply(text):
                dropped_rt_reply += 1
                continue
            fresh += 1
            raw_link = entry.get("link", "")
            tweets.append({
                "account":  handle,
                "text":     text,
                "link":     nitter_to_twitter(raw_link),
                "pubDate":  format_dt(pub_dt),
            })

        # An account that returned entries but none from the last 24h is healthy and
        # quiet -- a different thing from an account we could not reach at all.
        if status == "ok":
            label = "quiet" if fresh == 0 else "ok"
        else:
            label = status
        report.setdefault(label, []).append(handle)

        note = {
            "ok":        f"{fresh} tweets",
            "quiet":     f"reachable, but nothing in the last 24h ({len(entries)} older)",
            "missing":   "!! HTTP 404 -- account does not exist (renamed/deleted/typo)",
            "blocked":   "!! HTTP 403 -- suspended or protected",
            "ratelimit": f"!! rate-limited after {TWEET_FETCH_ATTEMPTS} attempts -- no data",
            "empty":     "!! reachable but returned no entries at all",
            "error":     "!! fetch failed",
        }[label]
        print(f"  @{handle:<20} {note}")

    # ── Health summary: this must never degrade silently again ──────────────
    ok = report.get("ok", [])
    unreachable = (report.get("ratelimit", []) + report.get("empty", [])
                   + report.get("error", []))
    dead = report.get("missing", []) + report.get("blocked", [])

    print(f"\n  Handle health: {len(ok)}/{len(TWITTER_HANDLES)} produced tweets, "
          f"{len(report.get('quiet', []))} quiet, {len(unreachable)} unreachable, "
          f"{len(dead)} dead")

    if dropped_rt_reply:
        print(f"  -- dropped {dropped_rt_reply} retweet(s)/reply(ies)")

    if dead:
        print(f"  !! DEAD HANDLES (fix TWITTER_HANDLES): {', '.join('@' + h for h in dead)}")
    if unreachable:
        print(f"  !! UNREACHABLE THIS RUN (nitter rate limiting, usually transient): "
              f"{', '.join('@' + h for h in unreachable)}")
    if len(ok) < MIN_HEALTHY_HANDLES:
        print(f"  !! WARNING: only {len(ok)} handle(s) produced tweets "
              f"(expected >= {MIN_HEALTHY_HANDLES}). Tweet supply is degraded; "
              f"story selection will be skewed toward whichever accounts got through.")

    return tweets


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Fetching news headlines...")
    headlines = fetch_news()
    print(f"  -- {len(headlines)} headlines from the last 24 hours\n")

    print("Fetching tweets via Nitter RSS...")
    tweets = fetch_tweets()
    print(f"  -- {len(tweets)} tweets from the last 24 hours\n")

    output = {
        "news_headlines": headlines,
        "tweets":         tweets,
    }

    output_path = "raw_content.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(headlines)} headlines + {len(tweets)} tweets -- {output_path}")


if __name__ == "__main__":
    main()
