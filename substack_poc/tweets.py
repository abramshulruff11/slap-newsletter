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


def _photos(data: Dict) -> list:
    out = []
    for m in data.get("mediaDetails", []) or []:
        if m.get("type") != "photo":
            continue
        info = m.get("original_info", {}) or {}
        out.append(
            {
                "url": m.get("media_url_https", ""),
                "width": info.get("width", 0),
                "height": info.get("height", 0),
            }
        )
    return out


def _bare_attrs(url: str) -> Dict:
    return {
        "url": url, "full_text": "", "username": "", "name": "",
        "profile_image_url": "", "date": "", "photos": [], "quoted_tweet": {},
        "reply_count": 0, "retweet_count": 0, "like_count": 0,
        "impression_count": 0, "expanded_url": {}, "video_url": None,
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
    return {
        "url": url,
        "full_text": text,
        "username": user.get("screen_name", ""),
        "name": user.get("name", ""),
        "profile_image_url": user.get("profile_image_url_https", ""),
        "date": _fmt_date(d.get("created_at", "")),
        "photos": _photos(d),
        "quoted_tweet": {},
        "reply_count": d.get("conversation_count", 0) or 0,
        "retweet_count": d.get("retweet_count", 0) or 0,
        "like_count": d.get("favorite_count", 0) or 0,
        "impression_count": 0,
        "expanded_url": {},
        "video_url": None,
        "belowTheFold": False,
    }
