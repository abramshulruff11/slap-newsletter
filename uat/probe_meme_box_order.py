"""
SLAP UAT — meme panel-ORDER probe.

WHY THIS EXISTS
    The 2026-08-27 reconciliation fixed every template's box COUNT against
    Imgflip's live get_memes API, which is authoritative. It could not fix
    panel ORDER: knowing a template has 3 caption boxes says nothing about
    which physical panel Imgflip's index 0 actually lands on.

    Those inferred mappings are flagged `role_unverified` in
    prompts/meme_library.DRAFT.json. Guessing wrong is not cosmetic — for
    left-exit-12-off-ramp it decides whether the subject label lands on the
    swerving car or on a road sign, which inverts the joke.

WHAT IT DOES
    Renders each unverified template ONCE with positional marker captions
    ("BOX 0", "BOX 1", ...) and builds an HTML page pairing each render with
    what the library CLAIMS each index means. You look at the image, read the
    claim next to it, and mark agree/disagree. Nothing is auto-applied.

    Also probes the four templates outside Imgflip's top-100 list, whose box
    COUNT is still unverified: it sends more markers than the library claims
    and reports how many actually rendered.

COST
    Imgflip's caption_image is free. URLs are cached in the output JSON, so
    re-running costs nothing and does not re-render.

RUN
    python -X utf8 uat/probe_meme_box_order.py
    then open prompts/meme_box_order_review.html

    --force   re-render even if a cached URL exists
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

UAT_DIR = Path(__file__).resolve().parent
REPO_ROOT = UAT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

import generate_memes as GM  # noqa: E402

LIBRARY_PATH = REPO_ROOT / "prompts" / "meme_library.DRAFT.json"
CACHE_PATH = UAT_DIR / "meme_box_order_probe.json"
REVIEW_PATH = REPO_ROOT / "prompts" / "meme_box_order_review.html"

# Templates absent from Imgflip's get_memes top-100, so box_count could not be
# verified on 2026-08-27. Probe these with extra markers to discover the count.
COUNT_UNKNOWN = {
    "first-time",
    "ight-imma-head-out",
    "spider-man-pointing-at-spider-man",
    "vince-mcmahon-reaction",
}
PROBE_CEILING = 4  # markers to send when the true count is unknown


def load_library() -> dict:
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def targets(lib: dict) -> list:
    """Templates needing a human look, with why."""
    out = []
    for t in lib.get("templates", []):
        unverified = [b for b in t.get("boxes", []) if b.get("role_unverified")]
        unknown_count = t["slug"] in COUNT_UNKNOWN
        if unverified or unknown_count:
            reasons = []
            if unknown_count:
                reasons.append("box_count unverified (not in Imgflip top-100)")
            if unverified:
                reasons.append(f"{len(unverified)} panel role(s) inferred")
            out.append((t, reasons, unknown_count))
    return out


def render(t: dict, unknown_count: bool, user: str, pw: str) -> tuple:
    """Render marker captions. Returns (url, n_sent)."""
    n = PROBE_CEILING if unknown_count else t["box_count"]
    boxes = [f"BOX {i}" for i in range(n)]
    url = GM.generate_meme(t["template_id"], boxes, user, pw, t["slug"])
    return url, n


def build_html(rows: list) -> str:
    head = """<!doctype html><meta charset="utf-8">
<title>Meme panel-order review</title>
<style>
 body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;
      background:#fbfbfa;color:#1a1a19}
 h1{font-size:1.5rem;margin-bottom:.2rem}
 .sub{color:#666;margin-bottom:2rem}
 .card{display:grid;grid-template-columns:320px 1fr;gap:1.5rem;padding:1.25rem;margin:1rem 0;
       background:#fff;border:1px solid #e3e3e0;border-radius:8px}
 .card img{max-width:300px;border-radius:4px;border:1px solid #ddd}
 .slug{font-weight:600;font-size:1.1rem;margin:0 0 .1rem}
 .why{color:#a15c00;font-size:.85rem;margin-bottom:.75rem}
 table{border-collapse:collapse;width:100%;font-size:.9rem}
 td{padding:.35rem .5rem;border-bottom:1px solid #eee;vertical-align:top}
 td.i{width:3.5rem;font-weight:600;color:#0a6}
 .inferred td.i{color:#c40}
 .ask{margin-top:.75rem;padding:.5rem .7rem;background:#f6f6f4;border-radius:5px;font-size:.85rem}
 .fail{color:#b00}
 code{background:#f0f0ee;padding:1px 4px;border-radius:3px}
</style>
<h1>Meme panel-order review</h1>
<div class="sub">Each meme was rendered with marker captions. Compare where
<code>BOX n</code> physically landed against what the library claims index
<code>n</code> means. Counts are already verified against Imgflip; only the
<strong>order</strong> is in question here.</div>
"""
    parts = [head]
    for t, reasons, url, n_sent, err in rows:
        parts.append('<div class="card"><div>')
        if url:
            parts.append(f'<img src="{html.escape(url)}" alt="{html.escape(t["slug"])}">')
        else:
            parts.append(f'<p class="fail">render failed: {html.escape(str(err))}</p>')
        parts.append("</div><div>")
        parts.append(f'<p class="slug">{html.escape(t["slug"])}</p>')
        parts.append(f'<p class="why">{html.escape("; ".join(reasons))} '
                     f'&mdash; sent {n_sent} markers</p>')
        parts.append("<table>")
        for b in t.get("boxes", []):
            cls = ' class="inferred"' if b.get("role_unverified") else ""
            parts.append(
                f'<tr{cls}><td class="i">BOX {b["index"]}</td>'
                f'<td>{html.escape(b.get("purpose", ""))}</td></tr>'
            )
        parts.append("</table>")
        subj = t.get("subject", {})
        if str(subj.get("placement", "")).startswith("box:"):
            parts.append(
                f'<div class="ask"><strong>Check first:</strong> the library puts the '
                f'SUBJECT in <code>{html.escape(subj["placement"])}</code>. Did that marker '
                f'land on the figure making the decision? If not, permute the indices.</div>'
            )
        parts.append("</div></div>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-render even when a cached URL exists")
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    user = os.getenv("IMGFLIP_USERNAME")
    pw = os.getenv("IMGFLIP_PASSWORD")
    if not (user and pw):
        print("ERROR: IMGFLIP_USERNAME / IMGFLIP_PASSWORD missing from .env")
        return 1

    lib = load_library()
    todo = targets(lib)
    if not todo:
        print("Nothing flagged role_unverified — everything is confirmed.")
        return 0

    cache = {}
    if CACHE_PATH.exists() and not args.force:
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}

    print(f"{len(todo)} template(s) need a look\n")
    rows = []
    for t, reasons, unknown in todo:
        slug = t["slug"]
        hit = cache.get(slug)
        if hit and not args.force:
            print(f"  [cached] {slug}")
            rows.append((t, reasons, hit["url"], hit["n_sent"], None))
            continue
        try:
            url, n = render(t, unknown, user, pw)
        except Exception as e:                                  # noqa: BLE001
            print(f"  [FAIL]   {slug}: {e}")
            rows.append((t, reasons, None, 0, e))
            continue
        if url:
            cache[slug] = {"url": url, "n_sent": n}
            rows.append((t, reasons, url, n, None))
        else:
            print(f"  [FAIL]   {slug}: no URL returned")
            rows.append((t, reasons, None, n, "no URL returned"))

    CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    REVIEW_PATH.write_text(build_html(rows), encoding="utf-8")

    ok = sum(1 for r in rows if r[2])
    print(f"\nrendered {ok}/{len(rows)}")
    print(f"cache:  {CACHE_PATH}")
    print(f"review: {REVIEW_PATH}")
    print("\nOpen the review page. For each meme, check where BOX n landed against")
    print("the claimed role. To confirm one, drop its boxes[].role_unverified flag;")
    print("to fix one, permute the boxes[] entries so index matches physical panel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
