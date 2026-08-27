"""
meme_library.py — access layer for prompts/meme_library.DRAFT.json

The library documents, per Imgflip template: which comedic engine it belongs to,
who the meme must be ABOUT (subject), the required valence relationship between
its caption boxes, and a worked/anti example pair.

It is consumed in two projections rather than injected wholesale — at 30 full
entries the file is ~20K tokens, which would crowd voice_examples.txt out of the
writer's attention:

  Pass 1 (selector)  → load_selector_index(): a compact ~2K-token index, one line
                       per template, grouped by engine. Pass 1 picks a slug.
  Pass 2 (writer)    → format_meme_specs(slugs): the FULL entry for only the 1-2
                       templates Pass 1 actually chose.

The library is deliberately NOT forked into uat/prompts/. It is structured data
rather than voice-bearing prose, and a fork would reproduce the UAT prompt-drift
problem already tracked in CLAUDE.md. Both runners read this one file.

template_id values are inherited from CURATED_TEMPLATES in generate_memes.py and
must stay in sync with it; nothing here re-derives them.
"""

import json
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "meme_library.DRAFT.json"
INDEX_PATH   = REPO_ROOT / "prompts" / "meme_selector_index.txt"

_cache = {}


def load_meme_library(path: Path | None = None) -> dict:
    """Load and cache the full library. Returns {} if missing, never raises."""
    p = Path(path) if path else LIBRARY_PATH
    key = str(p)
    if key not in _cache:
        try:
            _cache[key] = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[memelib] WARNING: could not load {p}: {e}")
            _cache[key] = {}
    return _cache[key]


def load_selector_index(path: Path | None = None) -> str:
    """The compact per-template index for Pass 1. Empty string if unavailable."""
    p = Path(path) if path else INDEX_PATH
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[memelib] WARNING: could not load {p}: {e}")
        return ""


def get_template(slug: str, path: Path | None = None) -> dict | None:
    """Return one template entry by slug, or None."""
    if not slug:
        return None
    for t in load_meme_library(path).get("templates", []):
        if t.get("slug") == slug:
            return t
    return None


def valid_slugs(path: Path | None = None) -> set:
    return {t["slug"] for t in load_meme_library(path).get("templates", [])}


def collect_meme_slugs(story_plan: dict) -> list:
    """
    Pull the meme_template slugs Pass 1 chose, in plan order, deduped.
    Tolerates a plan that predates the field entirely.
    """
    slugs, seen = [], set()
    stories = []
    if isinstance(story_plan, dict):
        lead = story_plan.get("lead_story")
        if lead:
            stories.append(lead)
        stories.extend(story_plan.get("supporting_stories") or [])
    for s in stories:
        if not isinstance(s, dict):
            continue
        slug = (s.get("meme_template") or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def _fmt_example(ex: dict, kind: str) -> str:
    if not ex:
        return ""
    lines = [f"  {kind}:"]
    if ex.get("subject"):
        lines.append(f"    subject: {ex['subject']}")
    for i, b in enumerate(ex.get("boxes", [])):
        lines.append(f"    box {i}: {b}")
    why = ex.get("why_it_lands") or ex.get("why_it_fails")
    if why:
        lines.append(f"    why: {why}")
    if ex.get("timing"):
        lines.append(f"    timing: {ex['timing']}")
    return "\n".join(lines)


def format_meme_specs(slugs, path: Path | None = None) -> str:
    """
    Render full specs for the selected templates, for injection into Pass 2.
    Returns "" when nothing was selected, so callers can concatenate blindly.
    """
    lib = load_meme_library(path)
    if not lib or not slugs:
        return ""
    engines = lib.get("_meta", {}).get("engines", {})

    out = ["## SELECTED MEME TEMPLATES — WRITE CAPTIONS TO THIS SPEC",
           "",
           "The story selector chose these templates. Write each meme's captions to the",
           "spec below. The VALENCE rule and the SUBJECT rule are hard requirements: a",
           "caption set that violates either produces a meme with no joke in it.",
           ""]
    for slug in slugs:
        t = get_template(slug, path)
        if not t:
            out.append(f"### {slug} — NOT IN LIBRARY; do not use this slug.\n")
            continue
        eng = engines.get(t.get("engine"), {})
        out.append(f"### {t['slug']}  ({t['box_count']} caption boxes)")
        if eng:
            out.append(f"  engine: {t['engine']} — {eng.get('summary','')}")
            if eng.get("failure_mode"):
                out.append(f"  this engine fails when: {eng['failure_mode']}")
        out.append(f"  the laugh comes from: {t.get('comedic_engine','')}")
        out.append(f"  VALENCE RULE: {t.get('valence','')}")

        sub = t.get("subject") or {}
        if sub.get("required"):
            out.append(f"  SUBJECT RULE ({sub.get('placement','copy')}): {sub.get('who','')}")
            out.append(f"    {sub.get('rule','')}")
            if sub.get("note"):
                out.append(f"    {sub['note']}")
        for b in t.get("boxes", []):
            v = f"  [{b['valence']}]" if b.get("valence") else ""
            out.append(f"  box {b['index']}: {b['purpose']}{v}")
        if t.get("special_handling"):
            out.append(f"  PIPELINE NOTE: {t['special_handling']}")
        out.append(f"  use when: {t.get('use_when','')}")
        out.append(f"  do NOT use when: {t.get('do_not_use_when','')}")
        we = _fmt_example(t.get("worked_example"), "WORKED EXAMPLE (shape only — never reuse as fact)")
        ae = _fmt_example(t.get("anti_example"), "ANTI-EXAMPLE (do not write this)")
        if we:
            out.append(we)
        if ae:
            out.append(ae)
        out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Selector-index generation
#
# prompts/meme_selector_index.txt says "do not hand-edit" but shipped without a
# generator, so it silently went stale the moment box counts were corrected
# against Imgflip. This rebuilds it from the library — the one source of truth.
#   python -c "import meme_library; meme_library.write_selector_index()"
# ---------------------------------------------------------------------------

INDEX_HEADER = (
    "# MEME SELECTOR INDEX -- one line per template, for Pass 1 slug selection.\n"
    "# Derived from prompts/meme_library.DRAFT.json. Do not hand-edit.\n"
    "# Regenerate: python -c \"import meme_library; meme_library.write_selector_index()\"\n"
    "# Pass 1 picks a slug; Pass 2 receives that template's full entry for caption writing.\n"
)


def build_selector_index(path: Path | None = None) -> str:
    """Render the Pass 1 index from the library, grouped by engine."""
    data = load_meme_library(path)
    engines = data.get("_meta", {}).get("engines", {})
    by_engine: dict = {}
    for t in data.get("templates", []):
        by_engine.setdefault(t.get("engine", "_ungrouped"), []).append(t)

    out = [INDEX_HEADER]
    for name, meta in engines.items():
        members = by_engine.get(name, [])
        if not members:
            continue
        out.append(f"## {name}")
        out.append(f"   {meta.get('summary', '').strip()}")
        if meta.get("key_test"):
            out.append(f"   TEST: {meta['key_test'].strip()}")
        for t in members:
            placement = t.get("subject", {}).get("placement", "copy")
            line = (t.get("selector_line") or t.get("use_when", "")).strip()
            out.append(
                f"   - {t['slug']} ({t['box_count']} boxes, subject: {placement}) -- {line}"
            )
        out.append("")

    leftovers = [t for k, v in by_engine.items() if k not in engines for t in v]
    if leftovers:
        out.append("## _ungrouped")
        for t in leftovers:
            out.append(f"   - {t['slug']} ({t['box_count']} boxes)")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_selector_index(path: Path | None = None) -> Path:
    dest = INDEX_PATH if path is None else Path(path)
    dest.write_text(build_selector_index(), encoding="utf-8")
    return dest
