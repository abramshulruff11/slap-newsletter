"""
meme_library.py — access layer for prompts/meme_library.DRAFT.json

The library documents, per Imgflip template: which comedic engine it belongs to,
who the meme must be ABOUT (subject), the required valence relationship between
its caption boxes, and a worked/anti example pair.

As of 2026-09-04 this file is the ONLY description of meme templates the model
ever sees. meme_reference.txt and pass2_writer.txt each used to carry their own
hand-kept catalogue and caption-count table; they disagreed with this library on
13 of 30 box counts, and a meme built from a wrong count is DROPPED by
meme_box_check rather than shipped — so the writer lost memes for following its
own instructions. Both tables are gone, replaced by projections of this file.

It is consumed in projections rather than injected wholesale — at 30 full
entries the file is ~20K tokens, which would crowd voice_examples.txt out of the
writer's attention:

  Pass 1 (selector)  → load_selector_index(): a compact ~2K-token index, one line
                       per template, grouped by engine. Pass 1 picks a slug.
  Pass 2 (writer)    → the same index, substituted into meme_reference.txt at
                       {{MEME_SELECTOR_INDEX}} (it may place a meme the plan did
                       not seed, and needs the real box counts to do it), PLUS
                       format_meme_specs(slugs): the FULL entry for the
                       templates Pass 1 actually chose.

  Rotation           → recently_used_slugs() / format_cooldown_block() /
                       swap_cooled_templates(): see the Rotation section below.

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
# Rotation
#
# Pass 1 chooses the template, and until 2026-09-04 it chose without ever being
# shown what ran recently — only Pass 2 got the "RECENTLY USED MEDIA" block, and
# by then the slug was already fixed. The result was the log line
# "[memes] ⚠ 'drake' used in last 7 days — consider varying template", printed
# after the meme was already made. Advisory, and ignored.
#
# Two changes: Pass 1 is now told what is cooling down, and anything it still
# picks from that list is swapped here. The swap stays inside the template's own
# ENGINE — the comedic mechanism the selector chose — so the joke it planned
# still works; only the picture changes. Pass 2 is handed the replacement's full
# spec, so it writes captions to the new box count.
#
# A repeat beats no meme: when the engine has no free alternative, the original
# is kept and the report says so.
# ---------------------------------------------------------------------------

def recently_used_slugs(history: list, days: int = 7) -> set:
    """Template slugs used within the last `days`, from meme_history.json rows."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    out = set()
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not slug:
            continue
        try:
            if date.fromisoformat(entry.get("date", "")) >= cutoff:
                out.add(slug)
        except (TypeError, ValueError):
            continue
    return out


def engine_alternatives(slug: str, exclude: set | None = None,
                        path: Path | None = None) -> list:
    """Other slugs driven by the same comedic engine, excluding `exclude`."""
    t = get_template(slug, path)
    if not t:
        return []
    engine = t.get("engine")
    if not engine:
        return []
    exclude = (exclude or set()) | {slug}
    return [o["slug"] for o in load_meme_library(path).get("templates", [])
            if o.get("engine") == engine and o["slug"] not in exclude]


def format_cooldown_block(cooled: set) -> str:
    """The 'do not pick these' block for Pass 1's user message. "" when empty."""
    if not cooled:
        return ""
    return (
        "## MEME TEMPLATES USED IN THE LAST 7 DAYS — CHOOSE DIFFERENT ONES\n"
        + ", ".join(sorted(cooled)) + "\n"
        "Picking one of these does not get you that template: a slug repeated "
        "inside 7 days is swapped automatically for another one in the same "
        "engine, so the choice is made for you. Pick the alternative yourself — "
        "you know which story it is for.\n\n"
    )


def swap_cooled_templates(plan: dict, cooled: set, path: Path | None = None) -> list:
    """
    Replace cooled-down meme_template slugs in place, within the same engine.

    Returns [(headline, old_slug, new_slug_or_None)] — new is None when the
    engine had nothing free and the original was kept.
    """
    if not cooled or not isinstance(plan, dict):
        return []
    stories = [plan.get("lead_story") or {}]
    stories += [s or {} for s in (plan.get("supporting_stories") or [])]

    used_this_issue = {(s.get("meme_template") or "").strip()
                       for s in stories if isinstance(s, dict)}
    used_this_issue.discard("")
    swaps = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        slug = (s.get("meme_template") or "").strip()
        if not slug or slug not in cooled:
            continue
        # Never swap in something already cooling down, and never duplicate a
        # template inside one issue.
        alts = engine_alternatives(slug, cooled | used_this_issue, path)
        headline = str(s.get("headline", "?"))[:44]
        if alts:
            s["meme_template"] = alts[0]
            used_this_issue.add(alts[0])
            swaps.append((headline, slug, alts[0]))
        else:
            swaps.append((headline, slug, None))
    return swaps


# ---------------------------------------------------------------------------
# Selector-index generation
#
# prompts/meme_selector_index.txt says "do not hand-edit" but shipped without a
# generator, so it silently went stale the moment box counts were corrected
# against Imgflip. This rebuilds it from the library — the one source of truth.
#   python -c "import meme_library; meme_library.write_selector_index()"
# ---------------------------------------------------------------------------

INDEX_HEADER = (
    "# MEME TEMPLATE INDEX -- one line per template. The COMPLETE list; there is\n"
    "# no other. Pass 1 picks a slug from here; Pass 2 receives the same index\n"
    "# (substituted into meme_reference.txt) plus the FULL spec for whichever\n"
    "# templates the plan seeded.\n"
    "# Derived from prompts/meme_library.DRAFT.json. Do not hand-edit.\n"
    "# Regenerate: python -c \"import meme_library; meme_library.write_selector_index()\"\n"
    "# The box count printed here is what the pipeline enforces: supply fewer\n"
    "# captions and meme_box_check.py drops the meme rather than ship a blank panel.\n"
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
