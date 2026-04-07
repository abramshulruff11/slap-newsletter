"""
SLAP Newsletter — Fetch sports news headlines (RSS) and tweets (Nitter RSS).
Filters everything to the last 24 hours and writes raw_content.json.
"""

import json
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
    "HouseOfHighlights",
    "NFLMemes",
    "SleeperHQ",
    "ghetto_gronk",
    "TalkinBaseball_",
    "PatMcAfeeShow",
    "NotBillWalton",
# --- NEW: Insiders with personality ---
    "KevinOConnorNBA",
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
    "CoveringCBB",
    "TrashTalkNFL",
    "ClutchPoints",
    "NBAMemes",
    # --- NEW: Wild cards ---
    "LeBatardShow",
    "CJZero",
    "TomPelissero",
    "BarstoolReags",
    "KenJac",
    # --- NEW: Added batch ---
    "NFL_Memes",
    "OnionSports",
    "FakeSportsCentr",
    "WorldWideWob",
    "RedditCFB",
    "GoodGameCBB",
    "JomboyMedia",
    "CespedesBBQ",
]

NITTER_BASE = "https://nitter.net"


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


# ── Fetch news headlines ─────────────────────────────────────────────────────

def fetch_news() -> list[dict]:
    headlines = []
    for source_name, url in NEWS_FEEDS.items():
        print(f"  Fetching {source_name}...")
        feed = feedparser.parse(url)
        for entry in feed.entries:
            pub_dt = entry_published_dt(entry)
            if not is_within_last_24h(pub_dt):
                continue
            headlines.append({
                "title":       entry.get("title", ""),
                "description": entry.get("summary", ""),
                "source":      source_name,
                "pubDate":     format_dt(pub_dt),
            })
    return headlines


# ── Fetch tweets via Nitter ──────────────────────────────────────────────────

def nitter_to_twitter(url: str) -> str:
    """Convert nitter.net URLs to twitter.com URLs for Substack embedding."""
    return url.replace("https://nitter.net/", "https://twitter.com/").replace("http://nitter.net/", "https://twitter.com/")


def fetch_tweets() -> list[dict]:
    tweets = []
    for handle in TWITTER_HANDLES:
        url = f"{NITTER_BASE}/{handle}/rss"
        print(f"  Fetching @{handle}...")
        feed = feedparser.parse(url)
        for entry in feed.entries:
            pub_dt = entry_published_dt(entry)
            if not is_within_last_24h(pub_dt):
                continue
            raw_link = entry.get("link", "")
            tweets.append({
                "account":  handle,
                "text":     entry.get("title", ""),
                "link":     nitter_to_twitter(raw_link),
                "pubDate":  format_dt(pub_dt),
            })
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
