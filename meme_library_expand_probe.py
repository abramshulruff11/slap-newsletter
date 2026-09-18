"""
PROBE SCRIPT — meme_library_expand_probe.py
Run this locally (python meme_library_expand_probe.py), paste the terminal
output back to Claude. This is a throwaway diagnostic, not production code —
same probe-before-building pattern as gif_library_expand_probe.py.

Why this exists: an AI session tried to expand prompts/meme_library.DRAFT.json
with brand-new templates (beyond the 30 in generate_memes.CURATED_TEMPLATES)
and stopped short. Two things block that safely from inside a sandboxed
Claude Code session: no network path to api.imgflip.com (this session's
egress policy blocks it, and WebFetch is blocked on imgflip.com too), and no
way to verify a template_id or box_count from web search alone. Getting
either wrong is exactly the bug class this project already shipped twice
(box_count/panel-order mismatches corrected 2026-08-27 and 2026-09-01,
locked by uat/tests/test_meme_library.py) — so rather than guess from
training-data memory, this script exists for YOU to run somewhere with real
network access, producing real verified data an AI session can then turn
into proper library entries.

What it does:
  Calls Imgflip's public get_memes endpoint (no API key required — it's the
  read-only "100 most captioned templates in the last 30 days" list) and
  prints every template NOT already in generate_memes.CURATED_TEMPLATES:
  slug-ified name, real template_id, and box_count AS REPORTED BY IMGFLIP
  ITSELF — so unlike the GIF probe, box_count here needs no separate
  eyeball pass, only the FIT judgement (does this template's shape/meaning
  suit SLAP's comedic engines) and, per meme_library.DRAFT.json's own
  lesson, a render check of panel ORDER before trusting box_count blindly
  (see uat/probe_meme_box_order.py — box_count from the API has historically
  been reliable; panel ORDER for multi-box templates has not).

What it does NOT do:
  - Does not write to prompts/meme_library.DRAFT.json or generate_memes.py.
    Read-only against Imgflip.
  - Does not judge comedic fit — that's a human (or a follow-up Claude
    session with this output pasted back in) pass.
  - Does not verify panel ORDER, only box_count. Run
    uat/probe_meme_box_order.py against any template you're seriously
    considering before writing box semantics for it.

Usage:
  python meme_library_expand_probe.py
  python meme_library_expand_probe.py --limit 20   (default: all ~100)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen, Request

REPO_ROOT = Path(__file__).resolve().parent
GET_MEMES_URL = "https://api.imgflip.com/get_memes"


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def load_curated_template_ids() -> dict[str, str]:
    sys.path.insert(0, str(REPO_ROOT))
    import generate_memes as gm  # noqa: E402
    return dict(gm.CURATED_TEMPLATES)


def fetch_popular_templates() -> list[dict]:
    req = Request(GET_MEMES_URL, headers={"User-Agent": "SLAP-Newsletter-Probe/1.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR calling {GET_MEMES_URL}: {e}")
        sys.exit(1)
    if not data.get("success"):
        print(f"ERROR: Imgflip reported failure: {data}")
        sys.exit(1)
    return data["data"]["memes"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Only print the top N (by Imgflip's own popularity order)")
    args = parser.parse_args()

    curated = load_curated_template_ids()
    curated_ids = set(curated.values())
    print(f"CURATED_TEMPLATES currently has {len(curated)} entries.\n")

    templates = fetch_popular_templates()
    if args.limit:
        templates = templates[: args.limit]

    print("=" * 70)
    print(f"IMGFLIP get_memes: {len(templates)} templates checked, "
          f"showing ones NOT already in CURATED_TEMPLATES")
    print("=" * 70)

    new_count = 0
    for t in templates:
        tid = str(t["id"])
        if tid in curated_ids:
            continue
        new_count += 1
        slug = slugify(t["name"])
        print(f"\n  name: {t['name']}")
        print(f"    suggested slug: {slug}")
        print(f"    template_id: {tid}")
        print(f"    box_count (per Imgflip): {t['box_count']}")
        print(f"    page: https://imgflip.com/meme/{tid}")

    print(f"\n{'='*70}")
    print(f"Done. {new_count} new candidates out of {len(templates)} checked.")
    print("Nothing was written — this only read from Imgflip's public API.")
    print("Paste this whole output back to Claude for fit judgement + box")
    print("semantics. Still run uat/probe_meme_box_order.py on anything you")
    print("seriously consider before trusting panel ORDER, not just box_count.")


if __name__ == "__main__":
    main()
