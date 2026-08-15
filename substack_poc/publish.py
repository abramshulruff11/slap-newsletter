"""
SLAP -> Substack publisher (proof of concept).

Pipeline:  newsletter_substack.html  ->  convert.html_to_blocks  ->  hydrate
           tweets + upload box scores  ->  python-substack Post  ->
           (dry-run | draft | schedule | publish)

By DEFAULT this runs in --dry-run mode: it builds the *exact* post body
that would be sent to Substack and prints it, WITHOUT logging in or
touching the network. That lets you verify the whole pipeline with zero
credentials and zero risk of accidentally publishing.

Modes (mutually exclusive)
--------------------------
  (default)         --dry-run   Build + print the payload. No network.
  --draft                       Log in and create a DRAFT (not published).
  --publish                     Create a draft, then PUBLISH it live now.
  --publish-existing            Publish a draft created by an earlier --draft run
                                (via --state-in/--draft-id) IF it still exists and
                                is not already published/scheduled. The midday half
                                of the two-job auto-post -- see daily-newsletter.yml
                                (morning --draft) + publish-substack.yml (noon).
  --schedule [--at ISO]         BROKEN: Substack removed the /schedule API endpoint
                                (returns 404). Use the --draft + --publish-existing
                                two-job flow instead. Kept only for reference.

Other flags: --title, --subtitle, --out (dry-run JSON dump),
  --box-score-dir DIR, --no-box-scores, --state-out PATH (with --draft),
  --state-in PATH / --draft-id ID (with --publish-existing),
  --send / --no-send (email subscribers vs web-only; default web-only).

Auth (needed for draft/schedule/publish) comes from a .env file locally, or
environment variables in CI. Prefer cookies; see .env.example. Order:
  1. SUBSTACK_COOKIES_PATH      (path to a cookies json file)
  2. SUBSTACK_COOKIES_STRING    (cookie header copied from your browser)
  3. SUBSTACK_EMAIL + SUBSTACK_PASSWORD
SUBSTACK_PUBLICATION_URL (e.g. https://yourpub.substack.com) is recommended.
Set PROXY_URL (http://user:pass@host:port) to route via a residential proxy
(needed from datacenter IPs like GitHub Actions; see _install_proxy_session).

Examples
--------
  python publish.py "../Archive/2026-06-04/newsletter_substack.html"
  python publish.py newsletter_substack.html --draft
  python publish.py newsletter_substack.html --schedule
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

import convert

try:  # stdout may be a cp1252 console on Windows; force UTF-8 so dashes/accents print.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


ET = ZoneInfo("America/New_York")


def _fmt_title(d: date) -> str:
    # House style: "SLAP - 6/4/2026" (no leading zeros, matching prior issues).
    return f"SLAP - {d.month}/{d.day}/{d.year}"


def derive_title(input_path: str, override: Optional[str]) -> str:
    """Title from --title, else from the YYYY-MM-DD archive folder name."""
    if override:
        return override
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", input_path.replace("\\", "/"))
    if m:
        return _fmt_title(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return _fmt_title(datetime.now(ET).date())


# Daily issues publish at 12:30 PM ET. The CI run fires ~2 AM ET, so the "next
# upcoming 12:30 PM ET" below resolves to the same day's slot.
PUBLISH_HOUR, PUBLISH_MINUTE = 12, 30


def next_publish_et(now: Optional[datetime] = None) -> datetime:
    """The next upcoming 12:30 PM America/New_York (today if still ahead, else tomorrow)."""
    now = now or datetime.now(ET)
    target = now.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def _append_image(post, src: str, alt: Optional[str],
                  width: Optional[int] = None, height: Optional[int] = None) -> None:
    """Append a standalone captionedImage node (captioned_image() otherwise
    fills the *last* node instead of creating its own). When real width/height
    are known, set them so tall box-score images keep their aspect ratio."""
    post.draft_body["content"].append({"type": "captionedImage"})
    if width and height:
        post.captioned_image(src=src, alt=alt or None, width=width, height=height,
                             resizeWidth=min(width, 728))
    else:
        post.captioned_image(src=src, alt=alt or None)


# Substack stores uploaded image dimensions in the URL, e.g. ".._800x7700.jpeg".
_DIM_RE = re.compile(r"_(\d+)x(\d+)\.[a-z]+$", re.IGNORECASE)


def build_post(blocks: List[Dict], title: str, subtitle: str, user_id: int,
               tweet_attrs: Optional[Dict[str, Dict]] = None,
               box_score_images: Optional[List[Dict]] = None):
    """Turn our intermediate blocks into a python-substack Post object.

    tweet_attrs: optional {url: hydrated twitter2 attrs}. When absent, tweet
    nodes get url-only attrs (fine for an offline dry-run; renders an empty
    card if actually published -- so hydrate before draft/schedule/publish).
    box_score_images: optional [{"url":.., "alt":..}] of already-uploaded box
    score images, appended after the trailing "Box Scores" heading.
    """
    from substack.post import Post
    from tweets import _bare_attrs

    tweet_attrs = tweet_attrs or {}
    post = Post(title=title, subtitle=subtitle, user_id=user_id)
    for b in blocks:
        if b["type"] == "heading":
            post.heading(content=[{"content": b["text"]}], level=b["level"])
        elif b["type"] == "paragraph":
            post.paragraph(content=b["tokens"])
        elif b["type"] == "image":
            _append_image(post, b["src"], b.get("alt"))
        elif b["type"] == "hr":
            post.horizontal_rule()
        elif b["type"] == "tweet":
            attrs = tweet_attrs.get(b["url"]) or _bare_attrs(b["url"])
            post.draft_body["content"].append({"type": "twitter2", "attrs": attrs})
        elif b["type"] == "youtube":
            # Substack renders an inline player from a youtube2 node whose
            # videoId is the bare 11-char id (a full watch URL fails to load).
            post.draft_body["content"].append(
                {"type": "youtube2", "attrs": {"videoId": b["video_id"]}}
            )

    # Box score images go under the trailing "Box Scores" heading.
    for img in box_score_images or []:
        _append_image(post, img["url"], img.get("alt"), img.get("width"), img.get("height"))
    return post


# Box score PNGs are named like "box_score_sport_01_nba.png" -- the zero-padded
# number gives display order; the trailing token is the sport (for alt text).
_BOX_RE = re.compile(r"box_score_sport_(\d+)_([a-z]+)", re.IGNORECASE)


def glob_box(box_dir: str) -> List[str]:
    """Sorted list of box_score_sport_*.png paths in box_dir (display order)."""
    import glob

    return sorted(glob.glob(os.path.join(box_dir, "box_score_sport_*.png")))


def upload_box_scores(api, box_dir: str) -> List[Dict]:
    """Upload box_score_sport_*.png from box_dir to Substack, in order.

    Returns [{"url": substack_cdn_url, "alt": "NBA box score"}] for each. Missing
    dir or zero images -> empty list (auto-draft simply omits the section)."""
    paths = glob_box(box_dir)
    if not paths:
        print(f"No box score PNGs found in {box_dir!r} -- skipping box score upload.")
        return []
    out: List[Dict] = []
    print(f"Uploading {len(paths)} box score image(s) from {box_dir!r}...")
    for i, p in enumerate(paths, 1):
        m = _BOX_RE.search(os.path.basename(p))
        sport = m.group(2).upper() if m else ""
        url = _upload_one_image(api, p)  # retries transient resets internally
        if not url:
            print(f"  [{i}/{len(paths)}] FAILED (giving up) {os.path.basename(p)}")
            continue
        dm = _DIM_RE.search(url)
        item = {"url": url, "alt": f"{sport} box score".strip()}
        if dm:
            item["width"], item["height"] = int(dm.group(1)), int(dm.group(2))
        out.append(item)
        print(f"  [{i}/{len(paths)}] ok   {os.path.basename(p)} -> {url}")
    return out


def _upload_one_image(api, path: str, attempts: int = 4) -> Optional[str]:
    """Upload one image with retries. Big box-score PNGs occasionally get the
    connection reset mid-upload (ConnectionResetError 10054), so retry with
    backoff before giving up."""
    import time

    for attempt in range(1, attempts + 1):
        try:
            res = api.get_image(path)
            url = res.get("url")
            if url:
                return url
            print(f"      attempt {attempt}: no url returned")
        except Exception as e:  # noqa: BLE001
            print(f"      attempt {attempt}/{attempts} failed: {type(e).__name__}: {str(e)[:80]}")
        if attempt < attempts:
            time.sleep(2 * attempt)  # 2s, 4s, 6s backoff
    return None


def _syndication_session():
    """Session for Twitter's syndication endpoint.

    Routes through the residential proxy with Chrome impersonation when PROXY_URL
    is set: GitHub Actions' datacenter IP gets rate-limited/blocked by Twitter,
    which otherwise bakes empty "Invalid Date" cards (retries from the same blocked
    IP can't recover). Falls back to a plain requests session locally (residential
    IP needs no proxy). Tweet JSON is tiny (~1-2 KB each), so the proxy bandwidth
    cost is negligible."""
    proxy = os.getenv("PROXY_URL")
    if proxy:
        from curl_cffi import requests as creq

        print("Routing tweet syndication through residential proxy + curl_cffi(chrome).")
        return creq.Session(impersonate="chrome", proxies={"http": proxy, "https": proxy})
    return requests.Session()


def _rehost_image(api, url: str) -> str:
    """Mirror a Twitter-hosted image onto Substack's CDN, returning the new url.

    Substack renders raw pbs.twimg.com URLs unreliably inside a twitter2 embed --
    a programmatically-built node shows the image blank/broken even though the same
    URL loads fine in a browser. Pasting a tweet in the editor works because Substack
    re-uploads the image to its own CDN; we mirror that here. Falls back to the raw
    url on any failure (better a hotlinked image than none)."""
    if not api or not url:
        return url
    try:
        res = api.get_image(url)
        return res.get("url") or url
    except Exception as e:  # noqa: BLE001 -- keep raw url on failure
        print(f"      image rehost failed: {type(e).__name__}: {str(e)[:60]}")
        return url


def hydrate_tweets(blocks: List[Dict], api=None) -> Dict[str, Dict]:
    """Fetch metadata for every tweet block so embeds render fully.

    When `api` is given, the link-card thumbnail is rehosted onto Substack's CDN
    (see _rehost_image): a raw pbs.twimg.com/card_img url renders blank inside the
    embed, so we mirror what Substack does on paste and upload it. Tweet PHOTOS need
    no upload -- they render from a pbs.substack.com mirror url set in tweets._media.
    A rehost failure falls back to the raw url."""
    from tweets import fetch_tweet_attrs

    urls = [b["url"] for b in blocks if b["type"] == "tweet"]
    out: Dict[str, Dict] = {}
    print(f"Hydrating {len(urls)} tweet embeds...")
    sess = _syndication_session()
    for i, url in enumerate(urls, 1):
        attrs = fetch_tweet_attrs(url, session=sess)
        if api:
            card = attrs.get("expanded_url")
            if card and card.get("image"):
                card["image"] = _rehost_image(api, card["image"])
        out[url] = attrs
        print(f"  [{i}/{len(urls)}] {'ok   ' if attrs['full_text'] else 'EMPTY'} {url}")
    return out


def _install_proxy_session() -> None:
    """If PROXY_URL is set, route python-substack through a residential proxy
    using a curl_cffi Chrome-impersonating session (to clear Cloudflare from a
    datacenter IP like GitHub Actions). No-op locally when PROXY_URL is unset."""
    proxy = os.getenv("PROXY_URL")
    if not proxy:
        return
    import substack.api as sapi
    from curl_cffi import requests as creq

    proxies = {"http": proxy, "https": proxy}
    sapi.requests.Session = lambda: creq.Session(impersonate="chrome", proxies=proxies)
    print("Using residential proxy + curl_cffi(chrome) for Substack requests.")


def _looks_like_transient_network_error(exc: Exception) -> bool:
    """True for proxy/connection-level failures worth retrying (timeouts, CONNECT
    tunnel errors, resets) -- NOT real API responses (bad cookies, 404s, etc.),
    which a retry can't fix. The residential proxy that routes CI's datacenter IP
    around Cloudflare occasionally drops the connect handshake; see the Jul/Aug
    2026 CI failure streak, which was ~90% this exact error."""
    s = str(exc).lower()
    return any(t in s for t in (
        "timed out", "timeout", "tunnel failed", "connection reset",
        "connection refused", "failed to perform", "remote end closed",
    ))


def make_api(attempts: int = 3, base_delay: int = 15):
    """Authenticate against Substack using whatever .env / env vars provide.

    Retries on transient proxy/network failures with backoff (15s, 30s, ...)
    since the Api() constructor itself makes the first network call (signin).
    Real API/credential errors raise immediately without retrying."""
    import time

    try:  # .env is for local use; in CI the vars come from the environment.
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    _install_proxy_session()
    from substack import Api
    pub = os.getenv("SUBSTACK_PUBLICATION_URL") or None

    def _connect():
        if os.getenv("SUBSTACK_COOKIES_PATH"):
            return Api(cookies_path=os.environ["SUBSTACK_COOKIES_PATH"], publication_url=pub)
        if os.getenv("SUBSTACK_COOKIES_STRING"):
            return Api(cookies_string=os.environ["SUBSTACK_COOKIES_STRING"], publication_url=pub)
        if os.getenv("SUBSTACK_EMAIL") and os.getenv("SUBSTACK_PASSWORD"):
            return Api(
                email=os.environ["SUBSTACK_EMAIL"],
                password=os.environ["SUBSTACK_PASSWORD"],
                publication_url=pub,
            )
        raise SystemExit(
            "No Substack credentials found. Copy .env.example to .env and fill it in "
            "(cookies recommended). See README.md."
        )

    for attempt in range(1, attempts + 1):
        try:
            return _connect()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            if not _looks_like_transient_network_error(e) or attempt == attempts:
                raise
            delay = base_delay * attempt
            print(f"  Substack auth attempt {attempt}/{attempts} failed "
                  f"({type(e).__name__}: {str(e)[:100]}); retrying in {delay}s...")
            time.sleep(delay)


def today_et() -> str:
    """Today's date (ISO) in America/New_York -- the handoff key between the
    morning 'create draft' job and the midday 'publish' job."""
    return datetime.now(ET).date().isoformat()


def write_state(path: str, draft_id, title: str) -> None:
    """Record which draft the midday job should publish. Keyed by ET date so a
    stale file (a morning run that failed days ago) is ignored, never published."""
    payload = {"date": today_et(), "draft_id": draft_id, "title": title}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote draft handoff -> {path}: {payload}")


def _resolve_send_email(flag: Optional[bool]) -> bool:
    """Whether to email subscribers on auto-publish. --send/--no-send wins; else
    env SUBSTACK_SEND_EMAIL (truthy); else False (web-only) -- the safe default so
    a first live run can never blast an unintended email."""
    if flag is not None:
        return flag
    return (os.getenv("SUBSTACK_SEND_EMAIL") or "").strip().lower() in ("1", "true", "yes", "on")


def _looks_like_not_found(exc: Exception) -> bool:
    s = str(exc).lower()
    return "404" in s or "not found" in s


def publish_existing(args) -> None:
    """Publish a draft created by an earlier --draft run -- the midday half of the
    two-job auto-post. Every 'skip' below is a clean exit 0 (a no-op, not a failure):
      * no / unreadable handoff file          -> skip (nothing was queued)
      * handoff is for a different ET date     -> skip (stale; morning likely failed)
      * draft 404s                             -> skip (you deleted it)
      * draft already published                -> skip (you published it, or a retry)
      * draft already has a Substack schedule  -> skip (you scheduled it yourself)
    Otherwise: prepublish + publish the draft *as it stands now*, so any edits made
    during the morning review go out automatically. Only unexpected errors raise."""
    draft_id = args.draft_id
    recorded_date = None
    if args.state_in:
        if not os.path.exists(args.state_in):
            print(f"[skip] No handoff file at {args.state_in!r}; nothing to auto-publish.")
            return
        with open(args.state_in, encoding="utf-8") as f:
            state = json.load(f)
        draft_id = draft_id or state.get("draft_id")
        recorded_date = state.get("date")
    if not draft_id:
        print("[skip] No draft id (empty handoff and no --draft-id); nothing to do.")
        return
    if recorded_date is not None and recorded_date != today_et():
        print(f"[skip] Handoff draft is for {recorded_date}, today is {today_et()} -- stale, not publishing.")
        return

    api = make_api()
    try:
        draft = api.get_draft(draft_id)
    except Exception as e:  # noqa: BLE001 -- 404 means you deleted it; anything else is real
        if _looks_like_not_found(e):
            print(f"[skip] Draft {draft_id} not found (deleted) -- nothing to publish.")
            return
        raise

    if draft.get("is_published"):
        print(f"[skip] Draft {draft_id} is already published -- nothing to do.")
        return
    if draft.get("postSchedules"):
        print(f"[skip] Draft {draft_id} already has a Substack schedule -- leaving it alone.")
        return

    send = _resolve_send_email(args.send_email)
    title = draft.get("draft_title") or draft.get("title") or "(untitled)"
    print(f"Publishing draft {draft_id} ({title!r}) now -- email_subscribers={send}.")
    api.prepublish_draft(draft_id)
    published = api.publish_draft(draft_id, send=send)
    print(f"PUBLISHED. is_published={published.get('is_published')} slug={published.get('slug', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish a SLAP newsletter to Substack.")
    ap.add_argument("input", nargs="?", help="path to newsletter_substack.html (omit for --publish-existing)")
    ap.add_argument("--title", help="post title (default: derived from date)")
    ap.add_argument("--subtitle", default="", help="post subtitle")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) build + print, no network")
    mode.add_argument("--draft", action="store_true", help="create a Substack draft")
    mode.add_argument("--schedule", action="store_true", help="create a draft and schedule it (12:30pm ET by default)")
    mode.add_argument("--publish", action="store_true", help="create a draft AND publish it live now")
    mode.add_argument("--publish-existing", action="store_true",
                      help="publish a draft made by an earlier --draft run (via --state-in/--draft-id), "
                           "only if it still exists and is not already published/scheduled")
    ap.add_argument(
        "--at",
        help="schedule time, ISO 8601 (e.g. 2026-06-05T12:30). Interpreted as ET if no offset. "
        "Only used with --schedule; defaults to the next upcoming 12:30 PM ET.",
    )
    ap.add_argument("--out", help="dry-run: also write the post body JSON here")
    ap.add_argument(
        "--box-score-dir",
        help="dir holding box_score_sport_*.png (default: input file's dir, else ./box_score)",
    )
    ap.add_argument("--no-box-scores", action="store_true", help="skip box score image upload")
    ap.add_argument("--state-out", metavar="PATH",
                    help="with --draft: write a {date,draft_id,title} handoff file for a later --publish-existing run")
    ap.add_argument("--state-in", metavar="PATH",
                    help="with --publish-existing: read the handoff file written by an earlier --draft run")
    ap.add_argument("--draft-id", help="with --publish-existing: target this draft id directly (instead of --state-in)")
    send = ap.add_mutually_exclusive_group()
    send.add_argument("--send", dest="send_email", action="store_true", default=None,
                      help="email subscribers when publishing (default: web-only, or env SUBSTACK_SEND_EMAIL)")
    send.add_argument("--no-send", dest="send_email", action="store_false", default=None,
                      help="publish web-only, no email")
    ap.set_defaults(send_email=None)
    args = ap.parse_args()

    # --publish-existing is the lightweight midday job: no HTML build, just auth + publish.
    if args.publish_existing:
        publish_existing(args)
        return
    if not args.input:
        ap.error("input HTML path is required (omit only with --publish-existing)")

    with open(args.input, encoding="utf-8") as f:
        blocks = convert.html_to_blocks(f.read())
    title = derive_title(args.input, args.title)
    print(f"Parsed {len(blocks)} blocks from {args.input}")
    print(f"Title: {title}\n")

    # Resolve schedule time up front so dry-run can show it too.
    sched_dt: Optional[datetime] = None
    if args.schedule:
        if args.at:
            sched_dt = datetime.fromisoformat(args.at)
            if sched_dt.tzinfo is None:
                sched_dt = sched_dt.replace(tzinfo=ET)
        else:
            sched_dt = next_publish_et()
        print(f"Scheduling for: {sched_dt.astimezone(ET):%Y-%m-%d %I:%M %p %Z}\n")

    # ---- dry run (default): no creds, no network -------------------------
    if not args.draft and not args.publish and not args.schedule:
        post = build_post(blocks, title, args.subtitle, user_id=0)
        draft = post.get_draft()
        body = json.loads(draft["draft_body"])
        print(convert.block_summary(blocks))
        print(f"\nProseMirror doc: {len(body['content'])} top-level nodes.")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=2, ensure_ascii=False)
            print(f"Wrote post body JSON -> {args.out}")
        print("\n[dry-run] Nothing was sent to Substack. Use --draft to create a draft.")
        return

    # ---- draft / publish: needs auth -------------------------------------
    api = make_api()
    user_id = api.get_user_id()
    print(f"Authenticated as user_id={user_id}")

    tweet_attrs = hydrate_tweets(blocks, api=api)

    box_images: List[Dict] = []
    if not args.no_box_scores:
        box_dir = args.box_score_dir
        if not box_dir:
            input_dir = os.path.dirname(os.path.abspath(args.input))
            box_dir = input_dir if glob_box(input_dir) else "box_score"
        box_images = upload_box_scores(api, box_dir)

    post = build_post(blocks, title, args.subtitle, user_id=user_id,
                      tweet_attrs=tweet_attrs, box_score_images=box_images)
    draft = api.post_draft(post.get_draft())
    draft_id = draft.get("id")
    print(f"Created draft id={draft_id}")

    if args.state_out:
        write_state(args.state_out, draft_id, title)

    if args.publish:
        api.prepublish_draft(draft_id)
        published = api.publish_draft(draft_id)
        print(f"PUBLISHED LIVE. slug={published.get('slug', '')}")
    elif args.schedule:
        api.prepublish_draft(draft_id)
        api.schedule_draft(draft_id, sched_dt)
        print(f"SCHEDULED for {sched_dt.astimezone(ET):%Y-%m-%d %I:%M %p %Z}.")
    else:
        print("Draft created (not published). Open Substack to review it.")


if __name__ == "__main__":
    main()
