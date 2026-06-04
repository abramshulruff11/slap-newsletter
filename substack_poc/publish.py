"""
SLAP -> Substack publisher (proof of concept).

Pipeline:  newsletter_substack.html  ->  convert.html_to_blocks  ->
           python-substack Post  ->  (dry-run | draft | publish)

By DEFAULT this runs in --dry-run mode: it builds the *exact* post body
that would be sent to Substack and prints it, WITHOUT logging in or
touching the network. That lets you verify the whole pipeline with zero
credentials and zero risk of accidentally publishing.

Modes
-----
  (default)    --dry-run   Build + print the payload. No network.
  --draft                  Log in and create a DRAFT (not published).
  --publish                Log in, create a draft, then PUBLISH it live.

Auth (only needed for --draft / --publish) is read from a .env file in
this folder. Prefer cookies; see .env.example. Order of preference:
  1. SUBSTACK_COOKIES_PATH      (path to a cookies json file)
  2. SUBSTACK_COOKIES_STRING    (cookie header copied from your browser)
  3. SUBSTACK_EMAIL + SUBSTACK_PASSWORD
SUBSTACK_PUBLICATION_URL (e.g. https://yourpub.substack.com) is recommended.

Examples
--------
  python publish.py "../Archive/2026-06-04/newsletter_substack.html"
  python publish.py "../Archive/2026-06-04/newsletter_substack.html" --draft
  python publish.py "../Archive/2026-06-04/newsletter_substack.html" --publish --title "SLAP - June 4"
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


def next_noon_et(now: Optional[datetime] = None) -> datetime:
    """The next upcoming 12:00 PM America/New_York (today if still ahead, else tomorrow)."""
    now = now or datetime.now(ET)
    target = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def build_post(blocks: List[Dict], title: str, subtitle: str, user_id: int,
               tweet_attrs: Optional[Dict[str, Dict]] = None):
    """Turn our intermediate blocks into a python-substack Post object.

    tweet_attrs: optional {url: hydrated twitter2 attrs}. When absent, tweet
    nodes get url-only attrs (fine for an offline dry-run; renders an empty
    card if actually published -- so hydrate before draft/schedule/publish).
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
            # captioned_image() appends into the *last* node, so open a fresh
            # captionedImage container first, then fill it (mirrors Post.add).
            post.draft_body["content"].append({"type": "captionedImage"})
            post.captioned_image(src=b["src"], alt=b.get("alt") or None)
        elif b["type"] == "hr":
            post.horizontal_rule()
        elif b["type"] == "tweet":
            attrs = tweet_attrs.get(b["url"]) or _bare_attrs(b["url"])
            post.draft_body["content"].append({"type": "twitter2", "attrs": attrs})
    return post


def hydrate_tweets(blocks: List[Dict]) -> Dict[str, Dict]:
    """Fetch metadata for every tweet block so embeds render fully."""
    from tweets import fetch_tweet_attrs

    urls = [b["url"] for b in blocks if b["type"] == "tweet"]
    out: Dict[str, Dict] = {}
    print(f"Hydrating {len(urls)} tweet embeds...")
    sess = requests.Session()
    for i, url in enumerate(urls, 1):
        out[url] = fetch_tweet_attrs(url, session=sess)
        print(f"  [{i}/{len(urls)}] {'ok   ' if out[url]['full_text'] else 'EMPTY'} {url}")
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


def make_api():
    """Authenticate against Substack using whatever .env / env vars provide."""
    try:  # .env is for local use; in CI the vars come from the environment.
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    _install_proxy_session()
    from substack import Api
    pub = os.getenv("SUBSTACK_PUBLICATION_URL") or None
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish a SLAP newsletter to Substack.")
    ap.add_argument("input", help="path to newsletter_substack.html")
    ap.add_argument("--title", help="post title (default: derived from date)")
    ap.add_argument("--subtitle", default="", help="post subtitle")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) build + print, no network")
    mode.add_argument("--draft", action="store_true", help="create a Substack draft")
    mode.add_argument("--schedule", action="store_true", help="create a draft and schedule it (12pm ET by default)")
    mode.add_argument("--publish", action="store_true", help="create a draft AND publish it live now")
    ap.add_argument(
        "--at",
        help="schedule time, ISO 8601 (e.g. 2026-06-05T12:00). Interpreted as ET if no offset. "
        "Only used with --schedule; defaults to the next upcoming 12:00 PM ET.",
    )
    ap.add_argument("--out", help="dry-run: also write the post body JSON here")
    args = ap.parse_args()

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
            sched_dt = next_noon_et()
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

    tweet_attrs = hydrate_tweets(blocks)
    post = build_post(blocks, title, args.subtitle, user_id=user_id, tweet_attrs=tweet_attrs)
    draft = api.post_draft(post.get_draft())
    draft_id = draft.get("id")
    print(f"Created draft id={draft_id}")

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
