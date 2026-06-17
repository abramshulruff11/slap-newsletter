"""
MLB & NHL highlights — embed official YouTube clips in the newsletter.

Two kinds of clip, both rendered as inline YouTube players on Substack (a
youtube2 node) and as linked thumbnails in email:

  1. FULL-GAME RECAP — the official "{Away} vs. {Home} Game Highlights" reel,
     appended at the end of the story section that covers that game.
  2. COOL PLAYS — the marquee individual plays the league posts as standalone
     clips ("STAAL scores FROM THE ICE!", "...WALK-OFF HOME RUN..."). Tied to a
     covered game when a player from that game (per ESPN's notable-play list)
     appears in the clip title; leftover marquee plays go in a "Top Plays"
     cluster appended before Box Scores.

Everything is sourced from each league's OFFICIAL YouTube channel uploads (one
cheap playlistItems call per league), so we never embed fan re-uploads. Clips
are matched by team nicknames (recaps) or ESPN player surnames (plays), which
filters out clickbait/rants/features that don't name a real player. Conservative
throughout: no confident match -> no embed.

Auth: YOUTUBE_API_KEY (YouTube Data API v3). Missing key -> no-op (returns the
body unchanged) so the pipeline never breaks. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_ESPN = "https://site.api.espn.com/apis/site/v2/sports"

_LEAGUES = {"mlb": "MLB", "nhl": "NHL"}        # league -> official channel handle
_ESPN_SPORT = {"mlb": "baseball", "nhl": "hockey"}

_MAX_CLUSTER = 4          # cap on the "Top Plays" cluster
_RECENT_HOURS = 40        # uploads/games older than this are ignored
_UA = "Mozilla/5.0"

_MULTIWORD_NICK = {
    "red sox", "white sox", "blue jays", "maple leafs",
    "golden knights", "blue jackets",
}

# Section headings that are NOT a single game's story (never anchor a recap here).
_NON_STORY_HEADINGS = {"around the league", "box scores", "top plays"}

_UPLOADS_CACHE: Dict[str, List[Dict]] = {}


# ---- small helpers ---------------------------------------------------------

def _get(url: str) -> Optional[Dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except Exception as e:  # noqa: BLE001
        print(f"      fetch failed: {type(e).__name__}: {str(e)[:60]}")
        return None


def _nickname(full_name: str) -> str:
    parts = (full_name or "").split()
    if len(parts) >= 2 and " ".join(parts[-2:]).lower() in _MULTIWORD_NICK:
        return " ".join(parts[-2:])
    return parts[-1] if parts else ""


def _recent(published_at: str, hours: int = _RECENT_HOURS) -> bool:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() <= hours * 3600
    except Exception:
        return False


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


# ---- YouTube: official channel uploads -------------------------------------

def _channel_uploads(league: str, api_key: str, max_items: int = 45) -> List[Dict]:
    """Recent uploads from a league's official channel: [{title, video_id,
    published_at}], newest first. Cached per league for the run."""
    if league in _UPLOADS_CACHE:
        return _UPLOADS_CACHE[league]
    handle = _LEAGUES[league]
    ch = _get(f"{_CHANNELS_URL}?" + urllib.parse.urlencode(
        {"key": api_key, "part": "contentDetails", "forHandle": handle}))
    items = (ch or {}).get("items") or []
    if not items:
        _UPLOADS_CACHE[league] = []
        return []
    uploads_pl = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = _get(f"{_PLAYLIST_URL}?" + urllib.parse.urlencode(
        {"key": api_key, "part": "snippet", "playlistId": uploads_pl,
         "maxResults": max_items}))
    out: List[Dict] = []
    for it in (pl or {}).get("items", []):
        sn = it.get("snippet", {})
        vid = (sn.get("resourceId") or {}).get("videoId")
        if vid:
            out.append({"title": sn.get("title", ""), "video_id": vid,
                        "published_at": sn.get("publishedAt", "")})
    _UPLOADS_CACHE[league] = out
    return out


# ---- ESPN: notable-play player surnames per game ---------------------------

_CAP_PHRASE = re.compile(r"[A-Z][a-z]+(?:['’]s)?(?:\s[A-Z][a-z]+(?:['’]s)?){0,2}")


def _surnames_from_text(text: str, exclude: Set[str], min_len: int = 4) -> Set[str]:
    """Last-name tokens of capitalized name phrases in `text`, dropping team
    tokens (so 'Golden Knights' / 'White Sox' aren't read as players)."""
    out: Set[str] = set()
    for phrase in _CAP_PHRASE.findall(text):
        toks = [re.sub(r"['’]s$", "", t) for t in phrase.split()]
        surname = toks[-1]
        if len(surname) >= min_len and surname.lower() not in exclude:
            out.add(surname)
    return out


def _game_players(league: str, game_id: str, exclude: Set[str]) -> Tuple[Set[str], Set[str]]:
    """One ESPN call -> (notable_play_surnames, boxscore_surnames).

    notable = players named in the game's individual-play video headlines (ESPN's
    curated clips — can miss the marquee play). boxscore = EVERY player who
    appeared, used to confirm a name we pulled from our own story prose is really
    a player in this game before we go hunting its clip."""
    d = _get(f"{_ESPN}/{_ESPN_SPORT[league]}/{league}/summary?event={game_id}")
    if not d:
        return set(), set()
    notable: Set[str] = set()
    for v in d.get("videos", []) or []:
        head = v.get("headline", "") or ""
        if "game highlights" not in head.lower():
            notable |= _surnames_from_text(head, exclude)
    box: Set[str] = set()
    for team in d.get("boxscore", {}).get("players", []) or []:
        for stat in team.get("statistics", []) or []:
            for ath in stat.get("athletes", []) or []:
                name = ((ath.get("athlete") or {}).get("displayName") or "").split()
                if name and len(name[-1]) >= 4 and name[-1].lower() not in exclude:
                    box.add(name[-1])
    return notable, box


# ---- marker (email-safe linked thumbnail; convert.py -> youtube2) ----------

def _marker(video_id: str, title: str) -> str:
    return (
        f'<p class="yt-highlight" data-video-id="{video_id}">'
        f'<a href="https://www.youtube.com/watch?v={video_id}">'
        f'<img src="https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" '
        f'alt="{_esc(title)}" width="100%" '
        f'style="max-width:100%;display:block;margin:16px auto;height:auto;" border="0">'
        f'</a></p>'
    )


# ---- matching --------------------------------------------------------------

def _find_recap(uploads: List[Dict], away_nick: str, home_nick: str,
                used: Set[str]) -> Optional[Dict]:
    an, hn = away_nick.lower(), home_nick.lower()
    for u in uploads:
        t = u["title"].lower()
        if (u["video_id"] not in used and "highlight" in t
                and an in t and hn in t and _recent(u["published_at"])):
            return u
    return None


def _is_recap(title: str) -> bool:
    t = title.lower()
    return "game highlights" in t or "playoff highlights" in t


# Features / compilations / non-play content that may name a player but aren't a
# single cool play — kept out of the play slots and the Top Plays cluster.
_FEATURE_RE = re.compile(
    r"all games|every pitch|top \d|best of|full game|mic'?d up|post-?game|"
    r"press conf|reaction|breakdown|preview|in \d+ minutes|condensed|"
    r"broadcast|retro|turn back time|behind the scenes|bloopers?|interview",
    re.IGNORECASE,
)
# Signals a title really is a single highlight play (used to rank candidates).
_PLAY_RE = re.compile(
    r"walk-?off|home run|homer|grand slam|\bhr\b|\bgoal\b|scores?|buzzer|"
    r"hat ?trick|dinger|snipe|game-?winner|overtime|\bot\b|save|slam|blast",
    re.IGNORECASE,
)


def _is_play_clip(title: str) -> bool:
    return not _is_recap(title) and not _FEATURE_RE.search(title)


def _find_play(uploads: List[Dict], players: Set[str], used: Set[str]) -> Optional[Dict]:
    """Best play clip naming one of `players`: among recent, non-feature uploads,
    prefer titles that read like an actual play (walk-off, home run, goal, ...).
    Newest wins on ties (uploads are newest-first)."""
    pl = {p.lower() for p in players}
    best, best_score = None, -1
    for u in uploads:
        if u["video_id"] in used or not _is_play_clip(u["title"]) or not _recent(u["published_at"]):
            continue
        tl = u["title"].lower()
        if not any(re.search(rf"\b{re.escape(p)}\b", tl) for p in pl):
            continue
        score = 1 if _PLAY_RE.search(u["title"]) else 0
        if score > best_score:
            best, best_score = u, score
    return best


# ---- newsletter injection --------------------------------------------------

_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _completed_games(game_state: Dict) -> List[Dict]:
    out: List[Dict] = []
    sports = (game_state or {}).get("sports", {})
    for league in _LEAGUES:
        for g in sports.get(league, {}).get("yesterday_games", []) or []:
            if g.get("completed") and g.get("away_team") and g.get("home_team"):
                out.append({"league": league, "away": g["away_team"],
                            "home": g["home_team"], "game_id": str(g.get("game_id", "")),
                            "playoffs": bool(g.get("playoffs"))})
    return out


def inject_highlights(body_html: str, game_state: Dict,
                      api_key: Optional[str] = None) -> Tuple[str, int]:
    """Embed recaps + cool plays. Returns (new_body, count_embedded)."""
    api_key = api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("  No YOUTUBE_API_KEY set — skipping highlight embeds.")
        return body_html, 0
    games = _completed_games(game_state)
    if not games:
        return body_html, 0

    # Per-league team-token exclude sets + per-game player surnames (ESPN).
    league_excludes: Dict[str, Set[str]] = {}
    for lg in _LEAGUES:
        toks: Set[str] = set()
        for g in games:
            if g["league"] == lg:
                for nm in (g["away"], g["home"]):
                    toks.update(t.lower() for t in nm.split())
        league_excludes[lg] = toks
    for g in games:
        if g["game_id"]:
            g["notable"], g["box"] = _game_players(g["league"], g["game_id"],
                                                   league_excludes[g["league"]])
        else:
            g["notable"], g["box"] = set(), set()
    league_players: Dict[str, Set[str]] = {lg: set() for lg in _LEAGUES}
    for g in games:
        league_players[g["league"]] |= g["notable"]

    # Story sections (exclude non-story headings).
    heads = list(_H2_RE.finditer(body_html))
    sections = []  # (heading_lower, section_text_lower, start_pos, end_pos, idx)
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body_html)
        htext = _TAG_RE.sub(" ", m.group(1)).strip().lower()
        sec_text = _TAG_RE.sub(" ", body_html[m.start():end]).lower()
        sections.append((htext, sec_text, m.start(), end, i))

    used_videos: Set[str] = set()
    used_sections: Set[int] = set()
    inline_inserts: Dict[int, str] = {}
    cluster_markers: List[str] = []
    n = 0
    print(f"  {len(games)} completed MLB/NHL game(s); matching to stories...")

    for g in games:
        lg, away, home = g["league"], g["away"], g["home"]
        an, hn = _nickname(away), _nickname(home)
        uploads = _channel_uploads(lg, api_key)
        recap = _find_recap(uploads, an, hn, used_videos)

        # Find the story section that covers this game (either team named).
        target = None
        for htext, sec_text, start, end, idx in sections:
            if idx in used_sections or htext in _NON_STORY_HEADINGS:
                continue
            if (an.lower() in sec_text or hn.lower() in sec_text
                    or away.lower() in sec_text or home.lower() in sec_text):
                target = (start, end, idx)
                break

        if target:
            start, end, idx = target
            used_sections.add(idx)
            # Cool play tied to OUR WRITING: the players our story actually names,
            # confirmed as players in this game (boxscore). This catches the clip
            # the story is about (e.g. a rookie's walk-off) even when ESPN's
            # curated clip list misses it. Fall back to ESPN's notable plays.
            story_names = _surnames_from_text(_TAG_RE.sub(" ", body_html[start:end]),
                                              league_excludes[lg])
            play = (_find_play(uploads, story_names & g["box"], used_videos)
                    or _find_play(uploads, g["notable"], used_videos))
            chunk = ""
            if recap:
                chunk += "\n" + _marker(recap["video_id"], recap["title"]); used_videos.add(recap["video_id"]); n += 1
            if play:
                chunk += "\n" + _marker(play["video_id"], play["title"]); used_videos.add(play["video_id"]); n += 1
            if chunk:
                inline_inserts[end] = inline_inserts.get(end, "") + chunk
                print(f"    ✓ inline: {lg.upper()} {away} @ {home}"
                      f"{' +recap' if recap else ''}{' +play' if play else ''}")
        elif g["playoffs"] and recap:
            # Marquee game with no story section -> recap goes in the cluster.
            cluster_markers.append(_marker(recap["video_id"], recap["title"]))
            used_videos.add(recap["video_id"]); n += 1
            print(f"    ✓ cluster recap (marquee): {lg.upper()} {away} @ {home}")

    # Top Plays cluster: leftover marquee play clips that name a real player
    # from one of the day's games (filters out rants/features/clickbait).
    for lg in _LEAGUES:
        for u in _channel_uploads(lg, api_key):
            if len(cluster_markers) >= _MAX_CLUSTER:
                break
            if u["video_id"] in used_videos or not _is_play_clip(u["title"]) or not _recent(u["published_at"]):
                continue
            tl = u["title"].lower()
            if any(re.search(rf"\b{re.escape(p.lower())}\b", tl) for p in league_players[lg]):
                cluster_markers.append(_marker(u["video_id"], u["title"]))
                used_videos.add(u["video_id"]); n += 1

    if not inline_inserts and not cluster_markers:
        return body_html, 0

    new_body = body_html
    for pos in sorted(inline_inserts, reverse=True):
        new_body = new_body[:pos] + inline_inserts[pos] + new_body[pos:]
    if cluster_markers:
        new_body = new_body.rstrip() + "\n<h2>Top Plays</h2>\n" + "\n".join(cluster_markers) + "\n"
        print(f"    ✓ Top Plays cluster: {len(cluster_markers)} clip(s)")
    return new_body, n
