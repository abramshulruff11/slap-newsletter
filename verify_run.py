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
    rows = [
        ("tweets", c["tweets"], f"floor {MIN_TWEETS}"),
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
