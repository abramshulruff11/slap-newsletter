"""
Hydrate tweet metadata for Substack "twitter2" embed nodes.

Substack stores a tweet embed as a `twitter2` node and renders the card from
the *stored attrs* (author, text, photos, date) -- it does NOT re-fetch from
the URL. A node with only a `url` shows an empty "Invalid Date" card. So we
fetch each tweet's content ourselves from Twitter's public syndication endpoint
(the same one the official embed widget uses) and bake it into the node.

No Twitter login/API key required. This is unofficial and can break if Twitter
changes the endpoint; failures degrade gracefully to a url-only node.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import requests

_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

_ID_RE = re.compile(r"status/(\d+)")
_TCO_TAIL_RE = re.compile(r"\s*https://t\.co/\S+\s*$")


def tweet_id(url: str) -> Optional[str]:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _token(tid: str) -> str:
    """Replicate the embed widget's token derivation (value not strictly checked)."""
    n = (int(tid) / 1e15) * math.pi
    intp, frac = int(n), n - int(n)
    s = ""
    if intp == 0:
        s = "0"
    while intp > 0:
        s = _DIGITS[intp % 36] + s
        intp //= 36
    f = ""
    for _ in range(12):
        frac *= 36
        d = int(frac)
        f += _DIGITS[d]
        frac -= d
    return re.sub(r"(0+|\.)", "", f"{s}.{f}")


def _fmt_date(iso: str) -> str:
    """'2026-06-04T03:14:56.000Z' -> 'Wed Jun 04 03:14:56 +0000 2026' (Twitter style)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%a %b %d %H:%M:%S +0000 %Y")
    except Exception:
        return ""


def _best_mp4(media: Dict) -> Optional[str]:
    """Highest-bitrate playable mp4 from a video/gif media's variants.

    Twitter's syndication payload lists an HLS (.m3u8) variant plus several
    progressive `video/mp4` renditions; Substack's player needs an mp4, so we
    pick the highest-bitrate one (matching what the editor stores on paste)."""
    variants = (media.get("video_info") or {}).get("variants") or []
    mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4s:
        return None
    return max(mp4s, key=lambda v: v.get("bitrate", 0) or 0)["url"]


def _media(data: Dict) -> tuple[list, Optional[str]]:
    """Return (photos, video_url) for a tweet.

    Photos are still images; a video/animated_gif tweet additionally contributes
    its poster thumbnail (so the embed shows a preview frame) and yields the
    `video_url` Substack's twitter2 card needs to render a player instead of a
    bare link. The first video wins -- Substack shows a single inline player."""
    photos: list = []
    video_url: Optional[str] = None
    for m in data.get("mediaDetails", []) or []:
        mtype = m.get("type")
        if mtype not in ("photo", "video", "animated_gif"):
            continue
        info = m.get("original_info", {}) or {}
        photos.append(
            {
                "url": m.get("media_url_https", ""),
                "width": info.get("width", 0),
                "height": info.get("height", 0),
            }
        )
        if mtype in ("video", "animated_gif") and video_url is None:
            video_url = _best_mp4(m)
    return photos, video_url


def _card_image(bv: Dict) -> str:
    """Best link-preview image URL from a card's binding_values (largest first)."""
    for key in (
        "photo_image_full_size_large", "summary_photo_image_large",
        "thumbnail_image_large", "photo_image_full_size", "thumbnail_image",
    ):
        url = (bv.get(key, {}) or {}).get("image_value", {}).get("url")
        if url:
            return url
    return ""


def _card(data: Dict) -> Optional[Dict]:
    """Link-preview attrs for a tweet that links out (Substack's `expanded_url`).

    A tweet with no media but an attached link renders as a "summary" card on
    Twitter; Substack stores it as expanded_url={url,title,description,domain,
    image}. Returns None (NOT {}) when there's no card -- Substack renders an
    empty grey link box for an empty dict but nothing for null."""
    card = data.get("card") or {}
    bv = card.get("binding_values") or {}
    if not bv:
        return None
    # The card's t.co points at the outbound link; resolve it to the real URL.
    tco = card.get("url")
    resolved = tco
    for u in data.get("entities", {}).get("urls", []) or []:
        if u.get("url") == tco:
            resolved = u.get("expanded_url") or tco
            break
    domain = (bv.get("domain", {}) or {}).get("string_value", "")
    return {
        "url": resolved or "",
        "title": (bv.get("title", {}) or {}).get("string_value", ""),
        "description": (bv.get("description", {}) or {}).get("string_value", ""),
        "domain": re.sub(r"^www\.", "", domain),
        "image": _card_image(bv),
    }


def _quoted(data: Dict) -> Dict:
    """Quoted-tweet attrs (Substack's `quoted_tweet`): text + author only.

    Substack's twitter2 card renders a quote as a nested text+author block and
    does NOT inline the quoted tweet's own media (matching what pasting the URL
    produces). Without this a quote-tweet shows an empty grey link box. Returns
    {} when the tweet quotes nothing."""
    q = data.get("quoted_tweet") or {}
    if not q:
        return {}
    user = q.get("user", {}) or {}
    return {
        "full_text": _TCO_TAIL_RE.sub("", q.get("text", "") or ""),
        "username": user.get("screen_name", ""),
        "name": user.get("name", ""),
        "profile_image_url": user.get("profile_image_url_https", ""),
    }


def _bare_attrs(url: str) -> Dict:
    return {
        "url": url, "full_text": "", "username": "", "name": "",
        "profile_image_url": "", "date": "", "photos": [], "quoted_tweet": {},
        "reply_count": 0, "retweet_count": 0, "like_count": 0,
        "impression_count": 0, "expanded_url": None, "video_url": None,
        "belowTheFold": False,
    }


def fetch_tweet_attrs(url: str, session: Optional[requests.Session] = None) -> Dict:
    """Return hydrated `twitter2` attrs for `url`; degrade to url-only on failure."""
    tid = tweet_id(url)
    if not tid:
        return _bare_attrs(url)
    sess = session or requests
    try:
        r = sess.get(
            _SYNDICATION,
            params={"id": tid, "lang": "en", "token": _token(tid)},
            headers={"User-Agent": _UA},
            timeout=20,
        )
        if r.status_code != 200:
            return _bare_attrs(url)
        d = r.json()
    except Exception:
        return _bare_attrs(url)

    user = d.get("user", {}) or {}
    text = _TCO_TAIL_RE.sub("", d.get("text", "") or "")
    photos, video_url = _media(d)
    return {
        "url": url,
        "full_text": text,
        "username": user.get("screen_name", ""),
        "name": user.get("name", ""),
        "profile_image_url": user.get("profile_image_url_https", ""),
        "date": _fmt_date(d.get("created_at", "")),
        "photos": photos,
        "quoted_tweet": _quoted(d),
        "reply_count": d.get("conversation_count", 0) or 0,
        "retweet_count": d.get("retweet_count", 0) or 0,
        "like_count": d.get("favorite_count", 0) or 0,
        "impression_count": 0,
        "expanded_url": _card(d),
        "video_url": video_url,
        "belowTheFold": False,
    }
