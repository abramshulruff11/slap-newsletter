"""
SLAP Newsletter — Fetch sports news headlines (RSS) and tweets (Nitter RSS).
Filters everything to the last 24 hours and writes raw_content.json.
"""

import json
import random
import socket
import time
from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser

import proxy_session

# feedparser's fetch has no timeout of its own; a single stalled Nitter
# connection can hang the whole run until the CI job's 30-minute cap kills it.
FEED_TIMEOUT_SECONDS = 15
socket.setdefaulttimeout(FEED_TIMEOUT_SECONDS)


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

# Surveyed 2026-08-24 (54 hosts, real feedparser calls against @AdamSchefter/@ESPN).
# Exactly one public mirror is serving real feeds, so exactly one is listed. Order
# matters -- fetch_one_handle() tries these left to right per handle, per attempt.
#
# Re-verify before adding to this list; mirror uptime moves week to week. What the
# 8/24 sweep found, so the next survey doesn't re-walk it:
#   nitter.perennialte.ch  200, 20 fresh entries per handle, 0.4-2.2s  <- the one
#   nitter.net             200 with an EMPTY body (the outage we're routing around)
#   xcancel.com            302 -> rss.xcancel.com, which 400s any browser UA and,
#                          for a feed-reader UA, answers with a WELL-FORMED feed
#                          whose one item is "RSS reader not yet whitelisted!".
#                          Do not add it: it parses clean and would be published as
#                          a tweet. Real access needs a manual email whitelist.
#   poast / lightbrd / catsarch / space / cz / freedit / inbox.lv   403 bot wall
#   tiekoetter / twiiit    200 serving an Anubis "not a bot" challenge page
#   privacydev / privacyredirect / 1d4 / moomoo / fdn / unixfox +14 more  DNS gone
NITTER_FALLBACKS: list[str] = [
    "https://nitter.perennialte.ch",
]

# If fewer than this many handles yield tweets, the run is degraded enough that the
# newsletter's tweet supply is compromised -- say so loudly rather than proceeding quietly.
MIN_HEALTHY_HANDLES = 12

# The per-handle timeout+retry math (up to 4 * 15s + 50s backoff =~ 110s per handle
# worst case) is fine when Nitter is merely flaky, but a FULL outage means every one
# of ~50 handles hits that worst case -- 90+ minutes, well past the CI job's 30-minute
# cap, with nothing to show for it (2026-08-23 incident). This bounds total fetch time
# so a full outage fails fast with whatever partial data it got, instead of eating the
# whole job.
TWEET_FETCH_TIME_BUDGET_SECONDS = 300

# Before committing to the full (expensive) retry ladder for every handle, probe a
# small random sample with a single fast attempt each (~15s worst case). If NONE of
# them are reachable, that's strong evidence Nitter itself is down -- not just a few
# rate-limited accounts, which is normal and worth retrying (see TWEET_FETCH_ATTEMPTS
# above). In that case every remaining handle also gets only one attempt instead of
# the full ladder, so the 300s budget covers ~20 handles instead of ~2-3, and the
# result is a representative sample rather than whatever happened to be first/last
# in the list. If the probe finds Nitter reachable, everything proceeds exactly as
# before -- full retries for handles that need them.
NITTER_OUTAGE_PROBE_SIZE = 5


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

def _blocked_feed(feed) -> bool:
    """A feed answer that is a bot wall rather than a feed.

    ESPN answered every CI request from 2026-07-15 to 2026-09-04 with HTTP 202
    and an empty body — a challenge page, not RSS — so feedparser saw zero
    entries and the run carried on with CBS as the only headline source. 403
    is the same block stated plainly. A real empty feed is HTTP 200 with a
    parseable document, which this does not match.
    """
    status = getattr(feed, "status", None)
    return (not feed.entries) and (status in (202, 403, 429) or feed.bozo)


def _parse_feed_via_proxy(url: str):
    """Fetch the feed body through PROXY_URL and parse it. None when unavailable."""
    resp = proxy_session.get_via_proxy(url, timeout=20, headers={"User-Agent": BROWSER_UA})
    if resp is None or resp.status_code != 200:
        return None
    feed = feedparser.parse(resp.content)
    return feed if feed.entries else None


def fetch_news() -> list[dict]:
    headlines = []
    dropped_spam = 0
    for source_name, url in NEWS_FEEDS.items():
        feed = feedparser.parse(url, agent=BROWSER_UA)
        # Surface silent feed failures — ESPN was returning zero entries
        # and nobody noticed because feedparser fails quietly.
        if _blocked_feed(feed):
            via_proxy = _parse_feed_via_proxy(url)
            if via_proxy is not None:
                print(f"  {source_name}: blocked directly (HTTP "
                      f"{getattr(feed, 'status', '?')}), {len(via_proxy.entries)} "
                      f"entries via proxy")
                feed = via_proxy
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
    """Convert a nitter URL -- from ANY configured base -- to twitter.com.

    Tweets pulled from a fallback mirror carry that mirror's host, so rewriting
    only nitter.net would ship links like http://nitter.perennialte.ch/x/status/1
    straight into the newsletter and break every Substack embed. Match the host of
    each base we might have fetched from, over either scheme (mirrors emit http://
    links in their RSS even when served over https).
    """
    for base in [NITTER_BASE] + NITTER_FALLBACKS:
        host = base.split("://", 1)[-1].rstrip("/")
        for scheme in ("https://", "http://"):
            prefix = f"{scheme}{host}/"
            if url.startswith(prefix):
                return "https://twitter.com/" + url[len(prefix):]
    return url


def _feed_belongs_to(feed, handle: str) -> bool:
    """Is this parseable feed actually THIS handle's tweets?

    Entry count alone is not enough. xcancel, for one, answers an un-whitelisted
    reader with a well-formed RSS document -- HTTP 200, bozo False, one <item> --
    whose content is "RSS reader not yet whitelisted!". That sails through an
    `if feed.entries` check and gets published as a tweet. Nitter's channel title
    is always "Display Name / @handle", so require the handle to be in it.
    """
    title = (getattr(feed, "feed", {}) or {}).get("title") or ""
    return f"@{handle}".lower() in title.lower()


def fetch_one_handle(handle: str, max_attempts: int = TWEET_FETCH_ATTEMPTS) -> tuple[list, str]:
    """Fetch one handle's feed, retrying past nitter's per-account rate limiting.

    max_attempts defaults to the full retry ladder; callers pass 1 for a cheap,
    no-backoff probe (see NITTER_OUTAGE_PROBE_SIZE) or when a prior probe already
    found Nitter unreachable and further multi-attempt retries aren't worth the cost.

    Returns (entries, status_label). status_label is one of:
      ok         -- feed parsed with at least one entry
      missing    -- HTTP 404: no such account on nitter (renamed, deleted, or a typo)
      blocked    -- HTTP 403: suspended or protected
      ratelimit  -- HTTP 429 on every attempt (transient; NOT evidence the account is dead)
      empty      -- HTTP 200 but zero entries
      challenge  -- a mirror answered with a bot wall / instance notice, not tweets
      error      -- transport failure or unexpected status
    """
    bases = [NITTER_BASE] + NITTER_FALLBACKS
    last = "error"
    for attempt in range(1, max_attempts + 1):
        for base in bases:
            primary = base == NITTER_BASE
            feed = feedparser.parse(f"{base}/{handle}/rss?limit=50", agent=BROWSER_UA)
            status = getattr(feed, "status", None)

            # 404/403 are facts about the ACCOUNT only from the primary. A mirror
            # serving 403 is describing ITSELF (Cloudflare, an Anubis wall) -- half
            # the surveyed mirrors do exactly that -- so returning "blocked" there
            # would both abandon the remaining fallbacks and libel a live handle as
            # dead in the health summary. Keep walking the list instead.
            if status == 404:
                if primary:
                    return [], "missing"  # deterministic -- retrying will not help
                last = "error"
                continue
            if status == 403:
                if primary:
                    return [], "blocked"
                last = "challenge"
                continue
            if status == 429:
                last = "ratelimit"
                continue
            if feed.entries:
                if _feed_belongs_to(feed, handle):
                    return feed.entries, "ok"
                print(f"     .. {base} answered for @{handle} with a notice feed, "
                      f"not tweets: {(feed.feed.get('title') or '')[:60]!r}")
                last = "challenge"
                continue
            if status == 200 and feed.bozo:
                # Parseable-looking 200 with nothing in it: a Cloudflare/Anubis
                # challenge page, not a real empty feed. Same outcome (no data),
                # but say so -- a mirror stuck here should be dropped, not retried.
                print(f"     .. {base} served a bot-challenge page for @{handle} "
                      f"(HTTP 200, not RSS)")
                last = "challenge"
            else:
                last = "empty" if status == 200 else "error"

        if attempt < max_attempts:
            time.sleep(TWEET_RETRY_BACKOFF[min(attempt - 1, len(TWEET_RETRY_BACKOFF) - 1)])
    return [], last


def fetch_tweets() -> list[dict]:
    tweets = []
    report: dict[str, list[str]] = {}
    dropped_rt_reply = 0

    # Probe first: a handful of single-attempt fetches decide whether the FULL
    # per-handle retry ladder (up to 110s worst case) is worth running at all this
    # run. A 429/error on one or two accounts is normal (see TWEET_FETCH_ATTEMPTS);
    # the probe sample being entirely unreachable is a much stronger signal that
    # Nitter itself is down.
    probe_sample = random.sample(TWITTER_HANDLES, min(NITTER_OUTAGE_PROBE_SIZE, len(TWITTER_HANDLES)))
    probe_reachable = False
    for h in probe_sample:
        _, probe_status = fetch_one_handle(h, max_attempts=1)
        if probe_status in ("ok", "missing", "blocked", "empty"):
            probe_reachable = True
            break
    max_attempts = TWEET_FETCH_ATTEMPTS if probe_reachable else 1
    if not probe_reachable:
        print(f"  !! PROBE: {len(probe_sample)} sample handle(s) all unreachable -- "
              f"Nitter looks down. Using single-attempt fetches for every handle this "
              f"run (faster failure; won't retry past transient rate limits).")

    # Shuffled so a budget/probe-driven cutoff doesn't always sacrifice whichever
    # handles happen to sit last in TWITTER_HANDLES -- coverage loss should be a
    # random sample, not a standing bias against accounts later in the file.
    order = list(TWITTER_HANDLES)
    random.shuffle(order)

    start = time.monotonic()
    budget_hit = False

    for i, handle in enumerate(order):
        if time.monotonic() - start > TWEET_FETCH_TIME_BUDGET_SECONDS:
            budget_hit = True
            skipped = order[i:]
            report.setdefault("skipped", []).extend(skipped)
            print(f"  !! TIME BUDGET EXCEEDED ({TWEET_FETCH_TIME_BUDGET_SECONDS}s) -- "
                  f"stopping with {len(skipped)} handle(s) not attempted "
                  f"(Nitter may be fully down)")
            break

        entries, status = fetch_one_handle(handle, max_attempts=max_attempts)

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
            "ratelimit": f"!! rate-limited after {max_attempts} attempt(s) -- no data",
            "empty":     "!! reachable but returned no entries at all",
            "challenge": "!! every mirror answered with a bot wall, not RSS",
            "error":     "!! fetch failed",
        }[label]
        print(f"  @{handle:<20} {note}")

    # ── Health summary: this must never degrade silently again ──────────────
    ok = report.get("ok", [])
    unreachable = (report.get("ratelimit", []) + report.get("empty", [])
                   + report.get("challenge", []) + report.get("error", [])
                   + report.get("skipped", []))
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
    if len(ok) == 0:
        print(f"  !! Nitter appears to be FULLY DOWN this run -- 0 tweets. "
              f"generate_newsletter.py will run in degraded (headline-only) mode.")

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
