"""
SLAP — Meme Library Semantic Review Tool

The meme counterpart to uat/review_gifs.py. Reads
prompts/meme_library.DRAFT.json and writes a single static HTML page
(uat/meme_review.html) with one card per template: the comedic engine, the
valence rule, every caption box and what it is for, the worked and anti
examples, and any special handling. Click Verify / Retire / Reset on each,
decisions persist in localStorage, and "Export Decisions" downloads
meme_decisions.json for uat/apply_meme_decisions.py to write back.

WHAT IS ACTUALLY UNDER REVIEW HERE
    Not the template_id and not the box_count — both were verified against
    Imgflip's live API on 2026-08-27, and panel ORDER by render probe on
    2026-09-01 (uat/probe_meme_box_order.py). Zero role_unverified flags
    remain. What has never been reviewed is the SEMANTICS: whether the box
    purposes, the valence rule and the examples describe a joke that lands.
    That is exactly what the library's own _meta says `status` tracks, and
    all 30 templates still sit at "candidate".

    Retiring here is real: meme_library.active_templates() drops retired
    templates from the Pass 1 menu, from valid_slugs() and from rotation
    swaps.

--with-renders captions each template with its own worked example via
Imgflip and shows the result, so you can see the joke rendered rather than
inferring it from the caption list. That needs IMGFLIP_USERNAME /
IMGFLIP_PASSWORD in .env and network to Imgflip; caption_image is free and
URLs are cached in uat/meme_review_cache.json, so re-running costs nothing.
Without the flag the page is text-only and builds anywhere — which is enough
to review the writing, and is the only mode available from a sandbox that
cannot reach Imgflip.

Usage:
    python -X utf8 uat/review_memes.py                 (text only)
    python -X utf8 uat/review_memes.py --with-renders  (render worked examples)
    python -X utf8 uat/review_memes.py --with-renders --force  (ignore cache)
Output:
    uat/meme_review.html  (open this file in your browser)
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

LIBRARY_PATH = REPO_ROOT / "prompts" / "meme_library.DRAFT.json"
CACHE_PATH = UAT_DIR / "meme_review_cache.json"
OUTPUT_PATH = UAT_DIR / "meme_review.html"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_worked_example(t: dict, user: str, pw: str) -> str | None:
    """Caption the template with its own worked example. Returns a URL or None."""
    import generate_memes as GM

    boxes = (t.get("worked_example") or {}).get("boxes")
    if not boxes:
        return None
    try:
        return GM.generate_meme(t["template_id"], list(boxes), user, pw, t["slug"])
    except Exception as e:  # a single bad template must not kill the page
        print(f"  !! {t['slug']}: {e}")
        return None


def box_rows(t: dict) -> str:
    rows = []
    for b in t.get("boxes", []):
        valence = b.get("valence")
        rows.append(
            f'<tr><td class="bi">{b["index"]}</td><td>{esc(b.get("purpose"))}'
            + (f'<div class="bv">{esc(valence)}</div>' if valence else "")
            + "</td></tr>"
        )
    return "".join(rows)


def example_block(ex: dict | None, kind: str) -> str:
    if not ex:
        return ""
    caps = "".join(f'<li>{esc(c)}</li>' for c in ex.get("boxes", []))
    why = ex.get("why_it_lands") or ex.get("why_it_fails") or ""
    subject = ex.get("subject")
    subj = f'<div class="subj">subject: {esc(subject)}</div>' if subject else ""
    return (f'<div class="ex {kind}"><div class="exh">{kind.upper()} EXAMPLE</div>'
            f'{subj}<ol class="caps">{caps}</ol><div class="why">{esc(why)}</div></div>')


def build_cards(templates: list, renders: dict) -> str:
    cards = []
    for t in templates:
        slug = t["slug"]
        status = t.get("status", "candidate")
        img = ""
        if renders.get(slug):
            img = (f'<div class="shot"><img loading="lazy" src="{esc(renders[slug])}" '
                   f'alt="{esc(slug)} worked example"></div>')
        special = t.get("special_handling")
        special_html = (f'<div class="special"><b>special handling:</b> {esc(special)}</div>'
                        if special else "")
        placement = (t.get("subject") or {}).get("placement", "copy")
        cards.append(f"""
        <div class="card" data-slug="{esc(slug)}" data-orig-status="{esc(status)}"
             data-status="{esc(status)}">
          <div class="head">
            <span class="badge">{esc(status)}</span>
            <span class="slug">{esc(slug)}</span>
            <span class="meta">{t.get('box_count')} boxes &middot; subject: {esc(placement)}</span>
          </div>
          <div class="body">
            <div class="left">
              {img}
              <div class="engine"><b>engine:</b> {esc(t.get('engine'))}</div>
              <div class="ce">{esc(t.get('comedic_engine'))}</div>
              <div class="valence"><b>VALENCE RULE:</b> {esc(t.get('valence'))}</div>
              {special_html}
            </div>
            <div class="right">
              <table class="boxes">{box_rows(t)}</table>
              <div class="uw"><b>use when:</b> {esc(t.get('use_when'))}</div>
              <div class="dw"><b>do NOT use when:</b> {esc(t.get('do_not_use_when'))}</div>
              {example_block(t.get('worked_example'), 'worked')}
              {example_block(t.get('anti_example'), 'anti')}
              <div class="tags">{esc(', '.join(t.get('tags', [])))}</div>
            </div>
          </div>
          <div class="actions">
            <button class="act verify" onclick="setStatus(this,'verified')">&#10003; Verify semantics</button>
            <button class="act retire" onclick="setStatus(this,'retired')">&#10007; Retire template</button>
            <button class="act reset" onclick="resetStatus(this)">&#8634; Reset</button>
          </div>
        </div>""")
    return "".join(cards)


def build_html(templates: list, renders: dict, mode_note: str) -> str:
    from collections import Counter
    counts = Counter(t.get("status", "candidate") for t in templates)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SLAP Meme Library Review</title>
<style>
 body {{ font: 14px/1.55 -apple-system, Segoe UI, sans-serif; background:#111; color:#eee;
        margin:0; padding:24px; }}
 h1 {{ margin:0 0 4px; }}
 .stats {{ color:#999; margin-bottom:16px; }}
 .toolbar {{ position:sticky; top:0; background:#111; padding:12px 0; z-index:10; display:flex;
            gap:14px; flex-wrap:wrap; align-items:center; border-bottom:1px solid #222;
            margin-bottom:24px; }}
 .filters button {{ background:#222; color:#eee; border:1px solid #444; padding:6px 14px;
                   margin-right:8px; border-radius:16px; cursor:pointer; }}
 .filters button.active {{ background:#eee; color:#111; }}
 .export {{ background:#2b6cb0; color:#fff; border:0; padding:8px 16px; border-radius:16px;
           cursor:pointer; font-weight:600; }}
 .clear {{ background:#333; color:#ccc; border:1px solid #444; padding:8px 16px;
          border-radius:16px; cursor:pointer; }}
 .pending {{ color:#7ab8ff; font-size:13px; }}
 .card {{ background:#1a1a1a; border:1px solid #2a2a2a; border-radius:8px; margin-bottom:18px;
         overflow:hidden; }}
 .card.changed {{ border-color:#7ab8ff; }}
 .card[data-status="retired"] {{ opacity:.45; }}
 .head {{ display:flex; gap:12px; align-items:center; padding:10px 14px;
         border-bottom:1px solid #262626; background:#161616; }}
 .badge {{ font-size:10px; text-transform:uppercase; padding:2px 8px; border-radius:10px;
          color:#111; font-weight:700; background:#e8a33d; }}
 .slug {{ font-weight:700; font-size:15px; }}
 .head .meta {{ color:#777; font-size:12px; }}
 .body {{ display:grid; grid-template-columns:minmax(260px,1fr) 1.4fr; gap:18px; padding:14px; }}
 .shot img {{ max-width:100%; border-radius:6px; border:1px solid #333; background:#000; }}
 .engine {{ margin-top:10px; color:#7ab8ff; font-size:12px; }}
 .ce {{ color:#bbb; font-size:12px; margin:4px 0 8px; }}
 .valence {{ background:#2a2214; border:1px solid #5a4520; border-radius:5px; padding:8px;
            font-size:12px; color:#f0d9a8; }}
 .special {{ margin-top:8px; background:#1c2436; border:1px solid #2f4468; border-radius:5px;
            padding:8px; font-size:12px; color:#aac6f0; }}
 table.boxes {{ border-collapse:collapse; width:100%; margin-bottom:10px; }}
 table.boxes td {{ border-bottom:1px solid #262626; padding:5px 6px; vertical-align:top;
                  font-size:12.5px; }}
 td.bi {{ width:2.2rem; color:#4fd17a; font-weight:700; }}
 .bv {{ color:#888; font-size:11px; margin-top:2px; }}
 .uw, .dw {{ font-size:12.5px; margin-bottom:6px; }}
 .dw {{ color:#ff9b8f; }}
 .ex {{ border-radius:5px; padding:8px; margin-top:8px; font-size:12px; }}
 .ex.worked {{ background:#16241a; border:1px solid #2c4a33; }}
 .ex.anti {{ background:#241616; border:1px solid #4a2c2c; }}
 .exh {{ font-size:10px; letter-spacing:.06em; color:#888; margin-bottom:4px; }}
 .subj {{ color:#7ab8ff; font-size:11px; margin-bottom:4px; }}
 ol.caps {{ margin:4px 0; padding-left:20px; }}
 ol.caps li {{ font-family:ui-monospace, monospace; font-size:11.5px; }}
 .why {{ color:#999; margin-top:4px; }}
 .tags {{ color:#7ab8ff; font-size:11px; margin-top:8px; }}
 .actions {{ display:flex; gap:8px; padding:0 14px 12px; }}
 .act {{ font-size:11px; padding:6px 12px; border-radius:4px; border:1px solid #444;
        background:#222; color:#ccc; cursor:pointer; }}
 .act:hover {{ background:#333; }}
 .verify:hover {{ border-color:#1e8e3e; color:#4fd17a; }}
 .retire:hover {{ border-color:#c0392b; color:#ff7a6b; }}
 .hidden {{ display:none !important; }}
</style></head><body>
<h1>SLAP Meme Library Review</h1>
<div class="stats">{len(templates)} templates &middot; {mode_note} &middot;
  reviewing SEMANTICS (box purposes, valence, examples) &mdash; ids, box counts and
  panel order are already verified</div>
<div class="toolbar">
  <div class="filters">
    <button class="active" onclick="filt('all',this)">All ({len(templates)})</button>
    <button onclick="filt('candidate',this)">Candidate ({counts.get('candidate',0)})</button>
    <button onclick="filt('verified',this)">Verified ({counts.get('verified',0)})</button>
    <button onclick="filt('retired',this)">Retired ({counts.get('retired',0)})</button>
  </div>
  <button class="export" onclick="exportDecisions()">&#8595; Export Decisions</button>
  <button class="clear" onclick="clearDecisions()">Clear saved decisions</button>
  <span class="pending" id="pending"></span>
</div>
{build_cards(templates, renders)}
<script>
const KEY='slap_meme_decisions';
function load(){{ try {{ return JSON.parse(localStorage.getItem(KEY)||'{{}}'); }} catch(e) {{ return {{}}; }} }}
function save(d){{ localStorage.setItem(KEY, JSON.stringify(d)); }}
function ui(card,status){{
  card.dataset.status=status;
  const b=card.querySelector('.badge');
  b.textContent=status;
  b.style.background={{verified:'#1e8e3e',candidate:'#e8a33d',retired:'#999'}}[status]||'#666';
  card.classList.toggle('changed', !!load()[card.dataset.slug]);
}}
function setStatus(btn,status){{
  const card=btn.closest('.card'), slug=card.dataset.slug, orig=card.dataset.origStatus;
  const d=load();
  // Every template ships as "candidate" and none has ever been reviewed, so a
  // no-change click still has to record something — otherwise confirming the
  // semantics of a candidate is a silent no-op. Reset is the way to clear.
  d[slug]={{ slug, old_status: orig, new_status: status, confirmed: status===orig }};
  save(d); ui(card,status); pending();
}}
function resetStatus(btn){{
  const card=btn.closest('.card'); const d=load();
  delete d[card.dataset.slug]; save(d); ui(card,card.dataset.origStatus); pending();
}}
function pending(){{
  const n=Object.keys(load()).length;
  document.getElementById('pending').textContent = n? n+' decision(s) not yet exported':'';
}}
function exportDecisions(){{
  const list=Object.values(load());
  if(!list.length){{ alert('No decisions yet — click Verify or Retire on some cards first.'); return; }}
  const blob=new Blob([JSON.stringify({{generated_at:new Date().toISOString(),decisions:list}},null,2)],
                      {{type:'application/json'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='meme_decisions.json';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}}
function clearDecisions(){{
  if(!confirm('Clear all unexported decisions? The library file is not affected.')) return;
  localStorage.removeItem(KEY);
  document.querySelectorAll('.card').forEach(c=>ui(c,c.dataset.origStatus));
  pending();
}}
function filt(status,btn){{
  document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(c=>
    c.classList.toggle('hidden', !(status==='all'||c.dataset.status===status)));
}}
document.querySelectorAll('.card').forEach(c=>{{
  const d=load()[c.dataset.slug];
  if(d) ui(c,d.new_status);
}});
pending();
</script>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-renders", action="store_true",
                        help="Caption each template with its worked example via Imgflip")
    parser.add_argument("--force", action="store_true",
                        help="Re-render even when a cached URL exists")
    args = parser.parse_args()

    library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    templates = library.get("templates", [])

    renders: dict = {}
    if args.with_renders:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
        user, pw = os.getenv("IMGFLIP_USERNAME"), os.getenv("IMGFLIP_PASSWORD")
        if not user or not pw:
            print("ERROR: IMGFLIP_USERNAME / IMGFLIP_PASSWORD missing from .env")
            return 1
        if CACHE_PATH.exists() and not args.force:
            try:
                renders = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                renders = {}
        todo = [t for t in templates if t["slug"] not in renders]
        print(f"Rendering {len(todo)} worked example(s) via Imgflip "
              f"({len(renders)} cached)...")
        for t in todo:
            url = render_worked_example(t, user, pw)
            if url:
                renders[t["slug"]] = url
        CACHE_PATH.write_text(json.dumps(renders, indent=1), encoding="utf-8")
        mode_note = f"{len(renders)}/{len(templates)} worked examples rendered"
    else:
        mode_note = "text only (pass --with-renders to see each joke rendered)"
        print(f"Building text-only page for {len(templates)} templates (0 API calls)...")

    OUTPUT_PATH.write_text(build_html(templates, renders, mode_note), encoding="utf-8")
    print(f"Open: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
