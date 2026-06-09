"""
MLB & NHL game highlights — find the official YouTube recap for a covered game
and inject it inline with that game's story in the newsletter body.

Why YouTube: Substack embeds YouTube as a native inline player (a `youtube2`
node), and MLB/NHL post official "Game Highlights" recaps to their own channels
within hours. ESPN's clips were the obvious source but ESPN gates the clean MP4s
(403/500) and Substack won't embed them; YouTube sidesteps both.

Flow:
  inject_highlights(body_html, game_state)
    -> for each completed MLB/NHL game in game_state["...yesterday_games"]
       whose BOTH teams appear in a story section, search the official channel
       for that game's highlight, and splice a marker <p class="yt-highlight">
       into that section. Downstream:
         * Substack: convert.py turns the marker into a youtube2 node (inline player)
         * Email:    the marker is already a linked thumbnail (<a><img>), so it
                     renders as a click-to-watch poster as-is.

Matching is deliberately conservative: official channel + both team nicknames in
the title + recent upload. No confident match -> no embed (never the wrong clip).

Auth: YOUTUBE_API_KEY env var (YouTube Data API v3). No key -> no-op (returns the
body unchanged), so the pipeline never breaks if the key is missing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# Leagues we source highlights for, mapped to their official YouTube channel
# title (as it appears in search results). Team channels (channelTitle == the
# team's full name) are also accepted.
_LEAGUES = {"mlb": "MLB", "nhl": "NHL"}

# Two-word team nicknames whose last token alone ("Sox", "Jays", "Leafs") would
# be ambiguous or wrong. Everything else uses the final word of the full name.
_MULTIWORD_NICK = {
    "red sox", "white sox", "blue jays", "maple leafs",
    "golden knights", "blue jackets",
}


def _nickname(full_name: str) -> str:
    """'Toronto Blue Jays' -> 'Blue Jays'; 'Detroit Tigers' -> 'Tigers'."""
    parts = (full_name or "").split()
    if len(parts) >= 2 and " ".join(parts[-2:]).lower() in _MULTIWORD_NICK:
        return " ".join(parts[-2:])
    return parts[-1] if parts else ""


def _youtube_search(query: str, api_key: str, max_results: int = 6) -> List[Dict]:
    params = {
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "order": "relevance",
        "videoEmbeddable": "true",  # only clips that can be embedded
    }
    url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("items", [])


def _recent(published_at: str, max_age_days: int = 3) -> bool:
    """True if an ISO publish time is within max_age_days of now (rejects
    nostalgia re-uploads of old games that share team names)."""
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days <= max_age_days
    except Exception:
        return False


def find_highlight(league: str, away_full: str, home_full: str,
                   api_key: str) -> Optional[Dict]:
    """Official-channel YouTube highlight for one game, or None.

    Confident match only: channel is the league's official channel or one of the
    two team channels, the title names BOTH teams and says "highlight", and the
    upload is recent. Returns {video_id, title, channel, watch_url, thumb_url}.
    """
    official = _LEAGUES.get(league)
    if not official:
        return None
    away_nick, home_nick = _nickname(away_full), _nickname(home_full)
    if not away_nick or not home_nick:
        return None

    query = f"{away_nick} {home_nick} game highlights"
    try:
        items = _youtube_search(query, api_key)
    except Exception as e:  # noqa: BLE001 -- never let a YouTube hiccup break the run
        print(f"      youtube search failed ({league} {away_nick}/{home_nick}): "
              f"{type(e).__name__}: {str(e)[:60]}")
        return None

    allowed_channels = {official.lower(), away_full.lower(), home_full.lower()}
    for it in items:
        sn = it.get("snippet", {})
        vid = (it.get("id") or {}).get("videoId")
        title = sn.get("title", "")
        channel = sn.get("channelTitle", "")
        tl = title.lower()
        if not vid:
            continue
        if channel.lower() not in allowed_channels:
            continue
        if "highlight" not in tl:
            continue
        if away_nick.lower() not in tl or home_nick.lower() not in tl:
            continue
        if not _recent(sn.get("publishedAt", "")):
            continue
        return {
            "video_id": vid,
            "title": title,
            "channel": channel,
            "watch_url": f"https://www.youtube.com/watch?v={vid}",
            "thumb_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        }
    return None


# ---- newsletter injection --------------------------------------------------

# Email-safe poster: a <p>-wrapped linked thumbnail. convert.py reads
# data-video-id off this <p> for Substack; email renders the <a><img> as-is.
def _marker(hl: Dict) -> str:
    return (
        f'<p class="yt-highlight" data-video-id="{hl["video_id"]}">'
        f'<a href="{hl["watch_url"]}">'
        f'<img src="{hl["thumb_url"]}" alt="{_esc(hl["title"])}" width="100%" '
        f'style="max-width:100%;display:block;margin:16px auto;height:auto;" border="0">'
        f'</a></p>'
    )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _completed_games(game_state: Dict) -> List[Tuple[str, str, str]]:
    """[(league, away_full, home_full)] for completed MLB/NHL games yesterday."""
    out: List[Tuple[str, str, str]] = []
    sports = (game_state or {}).get("sports", {})
    for league in _LEAGUES:
        for g in sports.get(league, {}).get("yesterday_games", []) or []:
            if g.get("completed") and g.get("away_team") and g.get("home_team"):
                out.append((league, g["away_team"], g["home_team"]))
    return out


def inject_highlights(body_html: str, game_state: Dict,
                      api_key: Optional[str] = None) -> Tuple[str, int]:
    """Splice official highlight embeds into the story sections they belong to.

    A section is the HTML from one <h2> up to the next. A game is matched to a
    section when BOTH its team nicknames appear in that section's text. The first
    matching section wins (and each game/section is used at most once). Returns
    (new_body, count_injected). No key or no matches -> body unchanged.
    """
    api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("  No YOUTUBE_API_KEY set — skipping highlight embeds.")
        return body_html, 0

    games = _completed_games(game_state)
    if not games:
        return body_html, 0

    # Section boundaries from <h2> positions.
    heads = list(_H2_RE.finditer(body_html))
    if not heads:
        return body_html, 0
    bounds = [(m.start(), m.end(), heads[i + 1].start() if i + 1 < len(heads)
               else len(body_html)) for i, m in enumerate(heads)]

    # Markers are appended at each section's end; collect then splice once so
    # positions stay valid.
    inserts: Dict[int, str] = {}   # insert_position -> marker html
    used_sections: set = set()
    print(f"  Matching {len(games)} completed MLB/NHL game(s) to story sections...")
    for league, away, home in games:
        an, hn = _nickname(away).lower(), _nickname(home).lower()
        for si, (h_start, h_end, sec_end) in enumerate(bounds):
            if si in used_sections:
                continue
            section_text = _TAG_RE.sub(" ", body_html[h_start:sec_end]).lower()
            if an and hn and an in section_text and hn in section_text:
                hl = find_highlight(league, away, home, api_key)
                if hl:
                    inserts[sec_end] = inserts.get(sec_end, "") + "\n" + _marker(hl)
                    used_sections.add(si)
                    print(f"    ✓ {league.upper()} {away} @ {home} -> "
                          f"[{hl['channel']}] {hl['title'][:50]}")
                else:
                    print(f"    – {league.upper()} {away} @ {home}: "
                          f"no clean official highlight found — skipped")
                break

    if not inserts:
        return body_html, 0
    # Splice from the end so earlier offsets remain valid.
    new_body = body_html
    for pos in sorted(inserts, reverse=True):
        new_body = new_body[:pos] + inserts[pos] + new_body[pos:]
    return new_body, len(inserts)
