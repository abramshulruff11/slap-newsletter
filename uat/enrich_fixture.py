"""
UAT-only: attach a video flag to the frozen fixture.

raw_content.json carries only {account, text, link, pubDate} — fetch_content.py
keeps the Nitter RSS <title> and discards <description>, which is where the
media markup lives. Rather than change production, this re-fetches the same
Nitter feeds and matches by status ID.

Nitter's RSS description for a video tweet contains a literal "Video" marker and
an amplify_video_thumb / ext_tw_video thumbnail URL. That is a direct signal, not
an inference from the account name.

Writes uat/fixtures/raw_content_enriched.json. Commit that file — it is the
frozen control input, and re-running this later will produce different coverage
as tweets age out of the feeds.

    python uat/enrich_fixture.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import feedparser

UAT_DIR      = Path(__file__).resolve().parent
FIXTURE_IN   = UAT_DIR / "fixtures" / "raw_content.json"
FIXTURE_OUT  = UAT_DIR / "fixtures" / "raw_content_enriched.json"

NITTER_BASE  = "https://nitter.net"
RSS_LIMIT    = 200          # ask for as much history as Nitter will give

# Fallback heuristic, used only for tweets the RSS lookup could not resolve.
# Deliberately narrow: a false positive here costs a good tweet.
HEURISTIC_ACCOUNTS = {
    "houseofhighlights", "ihartitz", "bleacherreport", "jomboymedia",
    "talkinbaseball_", "sportscenter", "espn", "clutchpoints", "cespedesbbq",
}
HEURISTIC_TEXT = re.compile(
    r"\b(highlight|watch|full video|must see|check out this|replay)\b", re.I
)

STATUS_RE = re.compile(r"/status/(\d+)")


def status_id(link: str) -> str | None:
    m = STATUS_RE.search(link or "")
    return m.group(1) if m else None


def classify(summary: str) -> str:
    """Map a Nitter RSS description to a media kind."""
    if re.search(r">\s*Video\s*<|amplify_video_thumb|ext_tw_video", summary):
        return "video"
    if "tweet_video_thumb" in summary:          # Twitter's native looping GIF
        return "gif"
    if "<img" in summary:
        return "image"
    return "text"


def build_media_map(handles: list[str]) -> dict[str, str]:
    """status_id -> media kind, harvested from live Nitter RSS."""
    media: dict[str, str] = {}
    for i, handle in enumerate(sorted(handles), 1):
        url = f"{NITTER_BASE}/{handle}/rss?limit={RSS_LIMIT}"
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [{i:>2}/{len(handles)}] @{handle:<20} FETCH FAILED — {e}")
            continue

        entries = getattr(feed, "entries", []) or []
        if not entries:
            print(f"  [{i:>2}/{len(handles)}] @{handle:<20} 0 entries")
            continue

        added = 0
        for e in entries:
            sid = status_id(e.get("link", ""))
            if not sid:
                continue
            # A retweet appears in the retweeter's feed under the ORIGINAL
            # tweet's status ID, so this map keys correctly across accounts.
            media[sid] = classify(str(e.get("summary", "")))
            added += 1
        print(f"  [{i:>2}/{len(handles)}] @{handle:<20} {added} entries")
    return media


def heuristic_has_video(tweet: dict) -> bool:
    acct = str(tweet.get("account", "")).lower()
    author = ""
    m = re.search(r"twitter\.com/([^/]+)/status", tweet.get("link", ""))
    if m:
        author = m.group(1).lower()
    if acct in HEURISTIC_ACCOUNTS or author in HEURISTIC_ACCOUNTS:
        return True
    return bool(HEURISTIC_TEXT.search(tweet.get("text", "")))


def main() -> None:
    if not FIXTURE_IN.exists():
        raise SystemExit(f"Fixture not found: {FIXTURE_IN}")

    raw = json.loads(FIXTURE_IN.read_text(encoding="utf-8"))
    tweets = raw.get("tweets", [])
    if not tweets:
        raise SystemExit("Fixture has no tweets.")

    handles = sorted({t.get("account", "") for t in tweets if t.get("account")})
    print(f"Fixture: {len(tweets)} tweets across {len(handles)} handles")
    print(f"Fetching Nitter RSS (limit={RSS_LIMIT})...")
    media = build_media_map(handles)
    print(f"\nResolved {len(media)} status IDs from live feeds.")

    counts = Counter()
    for t in tweets:
        sid = status_id(t.get("link", ""))
        kind = media.get(sid) if sid else None
        if kind is not None:
            t["media_kind"]    = kind
            t["has_video"]     = kind in ("video", "gif")
            t["detect_source"] = "nitter_rss"
        else:
            t["media_kind"]    = "unknown"
            t["has_video"]     = heuristic_has_video(t)
            t["detect_source"] = "heuristic"
        counts[(t["detect_source"], t["has_video"])] += 1

    resolved = sum(v for (src, _), v in counts.items() if src == "nitter_rss")
    guessed  = len(tweets) - resolved
    videos   = sum(1 for t in tweets if t["has_video"])

    FIXTURE_OUT.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== ENRICHMENT REPORT ===")
    print(f"  Resolved via Nitter RSS : {resolved}/{len(tweets)} "
          f"({resolved/len(tweets)*100:.1f}%)  [high confidence]")
    print(f"  Fell back to heuristic  : {guessed}/{len(tweets)} "
          f"({guessed/len(tweets)*100:.1f}%)  [LOW confidence]")
    print(f"  Tagged has_video        : {videos}/{len(tweets)}")
    kinds = Counter(t["media_kind"] for t in tweets)
    print(f"  Media kinds             : {dict(kinds)}")
    if guessed:
        print("\n  ⚠ Heuristic tweets are guesses — eyeball them in the Media Mix Report.")
    print(f"\n  ✓ Wrote {FIXTURE_OUT.relative_to(UAT_DIR.parent)}")


if __name__ == "__main__":
    sys.exit(main())
