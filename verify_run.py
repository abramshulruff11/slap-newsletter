"""
Run-quality gate. The last step of the daily pipeline.

WHY THIS EXISTS
    Nothing could fail for a bad newsletter. The only gates were "raw content
    is not empty" and "the two output files exist", so a run could have zero
    tweets, zero GIFs, zero memes, an email that never sent and a hollow
    game_state, and still finish green. Every real failure this repo has hit
    was visible in the log and invisible to CI:

      email delivery broken since at least 2026-08-14   (caught, printed, exit 0)
      ESPN scoreboard 403 on every run since 8/14        (empty file, stamped fresh)
      7 GIF placeholders shipped as empty divs on 9/01    ("No GIF placeholders found")

    So this reads what the run actually produced and says so out loud, in the
    job log and in the GitHub step summary, and fails the job on the things
    that mean the issue is broken rather than merely thin.

WHAT FAILS vs WHAT WARNS
    Failing is for "this issue is not fit to send". Warning is for "this issue
    is thinner than we want" — a quiet sports day is not a bug, and a run that
    goes red every time it is a little light trains you to ignore red.

    python verify_run.py            # exit 1 if the issue is broken
    python verify_run.py --strict   # also exit 1 on warnings
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import run_status

ROOT = Path(__file__).resolve().parent
DRAFT = ROOT / "newsletter_draft.html"
SUBSTACK = ROOT / "newsletter_substack.html"
BOX_SCORE_DIR = ROOT / "box_score"

# Calibrated against the last eight archived issues, which ran 15-22 tweets,
# 6-10 GIFs and 1-2 memes. These are floors for "something is wrong", not
# targets — the targets live in the prompts.
MIN_TWEETS = 6           # below this the issue is not a newsletter
MIN_MEDIA = 3            # GIFs + memes combined
WANT_MEMES = 3           # the §2.4 seed floor, measured here on RENDERED memes
WANT_GIFS = 5

# Tweets in the lead + supporting stories, i.e. everywhere a reader meets one
# inside prose. An issue-wide total cannot see this: on 2026-09-20 eight tweets
# shipped and SEVEN were in Around the League, so the lead and three of four
# supporting stories ran with none — and 8 > MIN_TWEETS, so the run went green
# with a "thin for a normal day" warning. Calibrated by running _placement over
# the 20 archived issues 09-01 -> 09-20, whose headliner counts ran 1-13 with a
# median of 7: a floor of 3 fires on 09-16 (2) and 09-20 (1) and stays quiet on
# the other eighteen. Nothing in that window trips the hard failure below, which
# is the intent — it is a floor for "not a newsletter", not for "a light day".
WANT_HEADLINER_TWEETS = 3


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def _count(html: str) -> dict:
    return {
        "tweets": len(re.findall(r'<blockquote[^>]*class="tweet"', html))
                  or len(re.findall(r'class="tweet-url"', html)),
        "gifs": len(re.findall(r'<img[^>]+giphy\.com', html)),
        "memes": len(re.findall(r'<img[^>]+imgflip\.com', html)),
        "highlights": len(re.findall(r'yt-highlight|youtube\.com/watch', html)),
        # Anything still a placeholder never rendered: an invisible hole where
        # media was meant to be. This is the 2026-09-01 failure exactly.
        "unrendered": len(re.findall(
            r'class="(?:gif|meme|highlight)-placeholder"', html)),
        "failed_memes": len(re.findall(r'\[MEME FAILED', html)),
        "words": len(re.findall(r'\w+', re.sub(r'<[^>]+>', ' ', html))),
    }


def _placement(html: str) -> dict:
    """Tweet counts per section: the lead, each supporting story, and ATL.

    Sections are split on h1/h2 exactly as the writer emits them. Box Scores is
    skipped (it holds images, never tweets); Around the League is counted apart
    from the headliners because it is the one section a video tweet may live in,
    and so the one place tweets pile up when the headliner pool fails.
    """
    parts = re.split(r'<h([12])>(.*?)</h\1>', html, flags=re.S)
    lead, supporting, atl = 0, [], 0
    lead_seen = False
    i = 1
    while i + 2 < len(parts):
        level, title, body = parts[i], parts[i + 1], parts[i + 2]
        n = (len(re.findall(r'<blockquote[^>]*class="tweet"', body))
             or len(re.findall(r'class="tweet-url"', body)))
        low = re.sub(r'<[^>]+>', '', title).strip().lower()
        if "box score" in low:
            pass
        elif "around the league" in low:
            atl += n
        elif level == "1":
            lead += n
            lead_seen = True
        else:
            supporting.append(n)
        i += 3
    # A draft with no h1/h2 at all splits into a single chunk and every count
    # above stays 0 — which would read as "all the tweets are in ATL" and fire
    # the hard failure for the wrong reason. `sections` lets the caller tell
    # "the body has no tweets" from "this file has no sections to look in".
    return {"lead": lead, "supporting": supporting, "atl": atl,
            "headliner": lead + sum(supporting),
            "sections": (1 if lead_seen else 0) + len(supporting)}


def main() -> int:
    ap = argparse.ArgumentParser(description="SLAP run-quality gate")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures too")
    args = ap.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    if not SUBSTACK.exists() or not DRAFT.exists():
        print("::error::newsletter output missing — nothing was produced")
        _summary("> ❌ newsletter output missing")
        return 1

    draft = DRAFT.read_text(encoding="utf-8")
    published = SUBSTACK.read_text(encoding="utf-8")
    c = _count(draft)
    pub = _count(published)
    status = run_status.load()
    box_images = sorted(BOX_SCORE_DIR.glob("box_score_sport_*.png"))

    # ---- the report, which is the point even when everything passes --------
    place = _placement(draft)
    rows = [
        ("tweets", c["tweets"], f"floor {MIN_TWEETS}"),
        ("  in stories", place["headliner"], f"want {WANT_HEADLINER_TWEETS}+"),
        ("  in ATL", place["atl"], ""),
        ("GIFs", c["gifs"], f"want {WANT_GIFS}+"),
        ("memes", c["memes"], f"want {WANT_MEMES}+"),
        ("highlight clips", c["highlights"], ""),
        ("box score images", len(box_images), ""),
        ("words", c["words"], ""),
    ]
    print("\n── RUN QUALITY ─────────────────────────────────────")
    _summary("### Newsletter produced")
    _summary("| item | count | target |")
    _summary("|---|---:|---|")
    for label, n, target in rows:
        print(f"  {label:<18} {n:>4}   {target}")
        _summary(f"| {label} | {n} | {target} |")

    media = c["gifs"] + c["memes"]
    share = media / (media + c["tweets"]) * 100 if (media + c["tweets"]) else 0
    print(f"  {'media share':<18} {share:>3.0f}%   (GIFs+memes vs tweets)")
    _summary(f"| media share | {share:.0f}% | 40% |")

    # ---- hard failures: the issue is not fit to send -----------------------
    if c["tweets"] < MIN_TWEETS and c["gifs"] + c["memes"] == 0:
        errors.append(f"only {c['tweets']} tweet(s) and no media — this is not an issue")
    if media == 0:
        errors.append("zero GIFs AND zero memes — every media slot came out empty")
    if pub["unrendered"]:
        errors.append(f"{pub['unrendered']} placeholder(s) reached the PUBLISHED file "
                      f"un-rendered — invisible holes where media should be")
    if c["failed_memes"]:
        errors.append(f"{c['failed_memes']} '[MEME FAILED' marker(s) in the draft")
    if status.get("incomplete_passes"):
        errors.append("a pass returned incomplete output: "
                      + ", ".join(status["incomplete_passes"]))
    # Every tweet in the issue landed in Around the League. The stories a reader
    # actually reads carry none, which no issue-wide total can show.
    if c["tweets"] >= MIN_TWEETS and place["sections"] and place["headliner"] == 0:
        errors.append(
            f"all {c['tweets']} tweet(s) are in Around the League — the lead and "
            f"every supporting story shipped without one")
    if status.get("email_sent") is False:
        errors.append(f"the email did not send — {status.get('email_error', 'no reason recorded')}")
    elif "email_sent" not in status:
        warnings.append("no email status recorded — did email_newsletter.py run?")

    # ---- warnings: thinner than we want, but shippable ---------------------
    if media and media < MIN_MEDIA:
        warnings.append(f"only {media} GIF(s)+meme(s) in the whole issue")
    if c["memes"] < WANT_MEMES:
        warnings.append(f"{c['memes']} meme(s) rendered against a floor of {WANT_MEMES} "
                        f"(the §2.4 floor counts SEEDS; this counts what actually rendered)")
    if c["gifs"] < WANT_GIFS:
        warnings.append(f"{c['gifs']} GIF(s) rendered, want {WANT_GIFS}+")
    if MIN_TWEETS <= c["tweets"] < 12:
        warnings.append(f"{c['tweets']} tweets — thin for a normal day")
    if 0 < place["headliner"] < WANT_HEADLINER_TWEETS:
        warnings.append(
            f"only {place['headliner']} tweet(s) across the lead and supporting "
            f"stories ({place['atl']} in Around the League) — the body is "
            f"running on GIFs and prose")
    if place["lead"] == 0 and place["headliner"]:
        warnings.append("the lead story shipped without a tweet")
    if not box_images:
        warnings.append("no box score images were rendered")
    if share < 30 and media:
        warnings.append(f"media share {share:.0f}% — target is 40%")

    for w in warnings:
        print(f"  ⚠ {w}")
        print(f"::warning::{w}")
        _summary(f"> ⚠️ {w}")
    for e in errors:
        print(f"  ✗ {e}")
        print(f"::error::{e}")
        _summary(f"> ❌ {e}")

    if errors:
        print(f"\n  {len(errors)} problem(s) make this issue unfit to send.")
        return 1
    if warnings and args.strict:
        print(f"\n  {len(warnings)} warning(s), failing because --strict.")
        return 1
    print(f"\n  ✓ Issue looks shippable"
          + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
