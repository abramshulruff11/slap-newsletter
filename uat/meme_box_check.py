"""
SLAP UAT — meme box-count guard.

WHY THIS EXISTS
    generate_memes.generate_meme() sends exactly as many boxes[] entries as it
    is handed. Imgflip happily accepts a short list and returns HTTP 200 with
    the leftover panels rendered BLANK. process_newsletter() counts that as a
    success, so a 4-panel expanding-brain with only 3 captions ships with an
    empty punchline panel and the run reports "0 failed".

    Observed 2026-08-25 UAT run: expanding-brain got 3 captions for 4 panels
    (blank galaxy-brain payoff) and is-this-a-pigeon got 2 for 3 (blank bottom
    line, and the question landed on the butterfly instead). Both reported OK.

WHAT IT DOES
    Scans meme placeholders BEFORE they are rendered, applies the same
    _expand_boxes() mapping the real pipeline uses, and compares the resulting
    caption count against the template's true physical box count.

    Nothing here writes to a production file. This is a UAT-side guard; once
    the behaviour is approved it can be ported into generate_memes.py.

SOURCE OF TRUTH FOR box_count
    1. Imgflip's live get_memes list (authoritative — it is the template's own
       declared box_count). build_template_map() already fetches this.
    2. prompts/meme_library.DRAFT.json as a fallback for templates that are not
       in Imgflip's top-100 live list.

    The library's own box_count is AI-authored and unreviewed; a 2026-08-25
    cross-check found 11 of 26 checkable entries disagreeing with Imgflip, so
    Imgflip wins wherever both are available.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

UAT_DIR = Path(__file__).resolve().parent
REPO_ROOT = UAT_DIR.parent
MEME_LIBRARY_PATH = REPO_ROOT / "prompts" / "meme_library.DRAFT.json"

IMGFLIP_MEMES_URL = "https://api.imgflip.com/get_memes"


def _load_library_box_counts() -> dict:
    """slug -> (template_id, box_count) from the draft meme library, if present."""
    if not MEME_LIBRARY_PATH.exists():
        return {}
    try:
        data = json.loads(MEME_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    for t in data.get("templates", []):
        slug = t.get("slug")
        if slug:
            out[slug] = (str(t.get("template_id", "")), t.get("box_count"))
    return out


def _load_imgflip_box_counts() -> dict:
    """template_id -> box_count from Imgflip's live list. {} on any failure."""
    try:
        req = urllib.request.Request(
            IMGFLIP_MEMES_URL, headers={"User-Agent": "SLAP-Newsletter/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            memes = json.loads(resp.read())["data"]["memes"]
        return {m["id"]: m["box_count"] for m in memes}
    except Exception as e:                                  # noqa: BLE001
        print(f"  [meme-check] could not fetch Imgflip template list: {e}")
        return {}


def build_expected_box_counts(template_map: dict | None = None) -> dict:
    """
    slug -> expected physical box count.

    template_map is generate_memes.build_template_map()'s slug->template_id
    dict. Imgflip's live box_count wins; the draft library fills the gaps.
    """
    lib = _load_library_box_counts()
    live = _load_imgflip_box_counts()

    expected = {}
    for slug, (lib_id, lib_count) in lib.items():
        tid = (template_map or {}).get(slug) or lib_id
        if tid in live:
            expected[slug] = live[tid]
        elif lib_count:
            expected[slug] = lib_count
    return expected


def _parse_boxes(div) -> list:
    """Extract the writer's captions from a placeholder div (data-boxes first)."""
    raw = div.get("data-boxes", "").strip()
    if raw:
        return [p.strip() for p in raw.split("||")]
    legacy = [div.get("data-top", ""), div.get("data-middle", ""),
              div.get("data-bottom", "")]
    return [p.strip() for p in legacy if p and p.strip()]


def check_html(html: str, template_map: dict | None = None) -> tuple[list, dict]:
    """
    Inspect every meme placeholder without rendering anything.

    Returns (findings, expected_map). Each finding:
        {slug, supplied, expanded, expected, ok, captions}
    'expanded' is the count AFTER generate_memes._expand_boxes(), which is what
    actually reaches Imgflip.
    """
    try:
        from generate_memes import _expand_boxes
    except ImportError:                                      # pragma: no cover
        def _expand_boxes(slug, lines):                      # noqa: ANN001
            return lines

    expected_map = build_expected_box_counts(template_map)
    soup = BeautifulSoup(html, "html.parser")
    findings = []

    for div in soup.find_all("div", class_="meme-placeholder"):
        slug = div.get("data-template", "").strip().lower()
        if not slug:
            continue
        captions = _parse_boxes(div)
        expanded = _expand_boxes(slug, captions)
        expected = expected_map.get(slug)
        findings.append({
            "slug": slug,
            "supplied": len(captions),
            "expanded": len(expanded),
            "expected": expected,
            "ok": expected is None or len(expanded) == expected,
            "captions": captions,
        })
    return findings, expected_map


def strip_short_memes(html: str, findings: list) -> tuple[str, int]:
    """
    Remove placeholders that would render with blank panels.

    Rendering a half-captioned meme is worse than rendering none: the joke
    structure breaks and it still occupies a media slot. Dropping it keeps the
    shortfall visible in the media-mix report instead of hiding it behind a
    'success'.
    """
    bad = {f["slug"] for f in findings if not f["ok"]}
    if not bad:
        return html, 0

    soup = BeautifulSoup(html, "html.parser")
    removed = 0
    for div in soup.find_all("div", class_="meme-placeholder"):
        slug = div.get("data-template", "").strip().lower()
        if slug in bad:
            div.decompose()
            removed += 1
    return str(soup), removed


def report(findings: list, *, strict: bool) -> None:
    """Print a human-readable summary of the box-count audit."""
    if not findings:
        print("  [meme-check] no meme placeholders to check")
        return

    bad = [f for f in findings if not f["ok"]]
    unknown = [f for f in findings if f["expected"] is None]

    for f in findings:
        exp = f["expected"] if f["expected"] is not None else "?"
        if f["ok"]:
            status = "ok"
        elif f["expected"] is not None and f["expanded"] < f["expected"]:
            status = "SHORT — panels will render blank"
        else:
            status = "OVER — extra boxes will be dropped"
        detail = ""
        if f["expanded"] != f["supplied"]:
            detail = f" (writer wrote {f['supplied']}, code expanded to {f['expanded']})"
        print(f"  [meme-check] {f['slug']:<34} {f['expanded']}/{exp} boxes  {status}{detail}")

    if unknown:
        print(f"  [meme-check] {len(unknown)} template(s) had no known box_count — not validated")
    if bad:
        verb = "dropped" if strict else "kept (will render with blank panels)"
        print(f"  [meme-check] ⚠ {len(bad)} meme(s) short on captions — {verb}")
    else:
        print("  [meme-check] ✓ all memes have a full caption set")
