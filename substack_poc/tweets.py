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
import time
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


def _clean_text(obj: Dict) -> str:
    """Tweet body with t.co shortlinks resolved the way Twitter/Substack show them.

    A tweet's raw `text` ends in opaque t.co links -- one per attached photo/video
    plus any links the author included. Using the tweet's `entities` we drop the
    media t.co's entirely (they're just the pic/video) and swap link t.co's for
    their human-readable `display_url` (e.g. 'spr.ly/abc'). Without this the embed
    shows a bare 'https://t.co/xxxx' in the body. A trailing-junk sweep catches any
    t.co not represented in entities."""
    text = obj.get("text", "") or ""
    ents = obj.get("entities", {}) or {}
    for u in ents.get("urls", []) or []:
        tco, disp = u.get("url"), (u.get("display_url") or u.get("expanded_url") or "")
        if tco:
            text = text.replace(tco, disp)
    for m in ents.get("media", []) or []:
        tco = m.get("url")
        if tco:
            text = text.replace(tco, "")
    return _TCO_TAIL_RE.sub("", text).strip()


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


_PBS_TWIMG = "https://pbs.twimg.com/"
_PBS_SUBSTACK = "https://pbs.substack.com/"


def _substack_img(url: str) -> str:
    """Rewrite a pbs.twimg.com image url to Substack's mirror host.

    A twitter2 `photos` entry renders ONLY when its `img_url` points at Substack's
    own pbs.substack.com proxy (same path, just a different host) -- a raw
    pbs.twimg.com url renders blank in the embed. This is exactly the rewrite the
    editor does when you paste a tweet; the proxy fetches the image from Twitter
    on demand, so no upload is needed."""
    return url.replace(_PBS_TWIMG, _PBS_SUBSTACK, 1) if url else url


def _media(data: Dict) -> tuple[list, Optional[str]]:
    """Return (photos, video_url) for a tweet.

    A twitter2 `photos` entry is {img_url, link_url} (NOT url/width/height -- those
    are ignored by Substack's renderer): img_url is the still image on Substack's
    mirror host (see _substack_img), link_url is the media's t.co short link. Photos
    cover still images plus a video/animated_gif's poster thumbnail; the first video
    also yields the `video_url` Substack needs to render an inline player."""
    photos: list = []
    video_url: Optional[str] = None
    for m in data.get("mediaDetails", []) or []:
        mtype = m.get("type")
        if mtype not in ("photo", "video", "animated_gif"):
            continue
        photos.append(
            {
                "img_url": _substack_img(m.get("media_url_https", "")),
                "link_url": m.get("url", ""),
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
        "full_text": _clean_text(q),
        "username": user.get("screen_name", ""),
        "name": user.get("name", ""),
        "profile_image_url": user.get("profile_image_url_https", ""),
    }


def _fetch_json(tid: str, sess, attempts: int = 4) -> Optional[Dict]:
    """GET a tweet's syndication JSON, retrying transient failures.

    Twitter rate-limits / intermittently blocks datacenter IPs (e.g. GitHub
    Actions), so a single miss would otherwise bake an empty "Invalid Date"
    card into the post. Retry non-200s and exceptions with backoff; a 404 that
    persists is treated as a genuine miss (deleted/protected tweet)."""
    for attempt in range(1, attempts + 1):
        try:
            r = sess.get(
                _SYNDICATION,
                params={"id": tid, "lang": "en", "token": _token(tid)},
                headers={"User-Agent": _UA},
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        if attempt < attempts:
            time.sleep(1.5 * attempt)  # 1.5s, 3s, 4.5s backoff
    return None


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
    d = _fetch_json(tid, sess)
    if not d:
        return _bare_attrs(url)

    user = d.get("user", {}) or {}
    text = _clean_text(d)
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
