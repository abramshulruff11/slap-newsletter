"""
SLAP — situation-targeted GIF sourcing (SLA-141).

SLA-138's replay put the situation-match ceiling at 83.3% because ten
situations have 0-2 GIFs. This searches Giphy with hand-written queries, each
one a specific, recognizable reference that punchlines the situation's emotion
(gif_reference.txt: the GIF is the joke, not an illustration of the topic),
and builds a review page grouped by situation.

It never touches the library. Accepted ids go through the usual path
afterwards: add as verified -> uat/gif_tagger.py --ids -> uat/apply_gif_tags.py.

WHAT IS KEPT
    Ids, titles, the query that found each one, and Giphy's own preview URL
    (a link, never a copy; CLAUDE.md Known Issues, Giphy terms).

GIPHY BUDGET
    One search call per query (~60). The per-key cap is ~100/hour, so the
    whole probe fits in one window; it stops at the first 429 and keeps what it
    has, the same rule as gif_tagger.py.

Usage:
    python -X utf8 uat/gif_situation_probe.py              # search + build page
    python -X utf8 uat/gif_situation_probe.py --page-only  # rebuild page, 0 calls
    python -X utf8 uat/gif_situation_probe.py --situation long_drought
Output:
    uat/output/gif_situation_candidates.json
    uat/gif_situation_review.html  (open in a browser; Export -> gif_situation_decisions.json)
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
TAXONOMY_PATH = REPO_ROOT / "prompts" / "gif_taxonomy.DRAFT.json"
ENV_PATH = REPO_ROOT / ".env"
CANDIDATES_PATH = Path(__file__).resolve().parent / "output" / "gif_situation_candidates.json"
PAGE_PATH = Path(__file__).resolve().parent / "gif_situation_review.html"

TARGET = 6
PER_QUERY = 8
SEARCH_URL = "https://api.giphy.com/v1/gifs/search?api_key={key}&q={q}&limit={n}&rating=pg-13"

# Each query names a reference, not a topic. The first ones per situation are
# the ticket's starter concepts. Giphy's search returns nothing for long
# phrases ("kawhi leonard blank stare": 0), so references are kept short.
QUERIES: dict[str, list[str]] = {
    "calm_dominance": [
        "kawhi leonard", "thanos", "john wick", "light work",
        "michael jordan shrug", "too easy", "unbothered sipping",
    ],
    "drought_over_or_star_back": [
        "michael jordan im back", "rocky steps", "finally", "he's back",
        "the king has returned", "about time",
    ],
    "long_drought": [
        "titanic its been 84 years", "still waiting skeleton", "years later",
        "waiting forever", "rip van winkle", "spongebob 2000 years later",
    ],
    "dodged_disaster": [
        "final destination", "indiana jones", "phew close call",
        "wipe sweat relief", "that was close", "matrix dodge bullets",
    ],
    "got_away_with_it": [
        "ferris bueller", "sneaking away", "nobody saw that",
        "walks away whistling", "got away with it", "innocent face",
    ],
    "carrying_the_load": [
        "atlas holding the world", "sam carrying frodo", "carrying the team",
        "one man army", "i have to do everything myself", "carry me",
    ],
    "cruel_twist": [
        "charlie brown football lucy", "darth vader nooo", "so close",
        "why universe", "of course it happened", "rug pulled",
    ],
    "comeback_euphoria": [
        "we're back", "miracle on ice", "ted lasso believe",
        "never give up", "comeback celebration", "from crying to cheering",
    ],
    "historic_company": [
        "legends only", "hall of fame", "mount rushmore", "the goat",
        "welcome to the club", "among the greats",
    ],
    "big_game_buildup": [
        "michael jackson eating popcorn", "lets get ready to rumble", "nervous waiting",
        "this is going to be good", "its game time", "here we go",
    ],
}


def env_value(name: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def taxonomy_by_id(taxonomy: dict) -> dict[str, dict]:
    subs = taxonomy["subcategories"]
    if isinstance(subs, dict):
        return {k: dict(v, id=k) for k, v in subs.items()}
    return {s["id"]: s for s in subs}


def library_ids(library: dict) -> set[str]:
    """Every id in the library, any status: a rejected or retired GIF must not
    come back as a fresh candidate."""
    return {g["id"] for c in library["categories"].values() for g in c.get("gifs", [])}


def current_supply(library: dict) -> dict[str, dict[str, int]]:
    """Verified GIFs per situation, split by fit."""
    out: dict[str, dict[str, int]] = {}
    for c in library["categories"].values():
        for g in c.get("gifs", []):
            if g.get("status") != "verified":
                continue
            for sc in g.get("subcategories", []):
                d = out.setdefault(sc["id"], {"strong": 0, "partial": 0})
                d[sc.get("fit", "partial")] = d.get(sc.get("fit", "partial"), 0) + 1
    return out


def candidate_from_result(r: dict, situation: str, query: str) -> dict | None:
    gid = r.get("id")
    if not gid:
        return None
    images = r.get("images") or {}
    preview = ((images.get("fixed_width") or {}).get("url")
               or (images.get("downsized_medium") or {}).get("url") or "")
    return {
        "id": gid,
        "situation": situation,
        "query": query,
        "title": r.get("title") or "",
        "username": r.get("username") or "",
        "page_url": r.get("url") or f"https://giphy.com/gifs/{gid}",
        "preview_url": preview,
    }


def merge_results(results: list[tuple[str, str, list[dict]]], existing: set[str]) -> list[dict]:
    """(situation, query, giphy results) -> candidates. An id already in the
    library is skipped; an id found by several situations is kept once, under
    the first, with the others listed in also_found_for."""
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for situation, query, items in results:
        for r in items:
            c = candidate_from_result(r, situation, query)
            if not c or c["id"] in existing:
                continue
            if c["id"] in by_id:
                prev = by_id[c["id"]]
                if situation != prev["situation"] and situation not in prev.setdefault("also_found_for", []):
                    prev["also_found_for"].append(situation)
                continue
            by_id[c["id"]] = c
            order.append(c["id"])
    return [by_id[i] for i in order]


def search(query: str, key: str, n: int) -> list[dict]:
    url = SEARCH_URL.format(key=key, q=urllib.parse.quote(query), n=n)
    req = urllib.request.Request(url, headers={"User-Agent": "SLAP-Newsletter-Probe/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("data", [])


def build_page(candidates: list[dict], library: dict, taxonomy: dict) -> str:
    tax = taxonomy_by_id(taxonomy)
    supply = current_supply(library)
    esc = html.escape
    sections = []
    for situation in QUERIES:
        mine = [c for c in candidates if c["situation"] == situation]
        s = tax.get(situation, {})
        have = supply.get(situation, {"strong": 0, "partial": 0})
        need = max(0, TARGET - have["strong"] - have["partial"])
        cards = []
        for c in mine:
            if c["preview_url"]:
                media = f'<img src="{esc(c["preview_url"])}" loading="lazy" alt="">'
            else:
                media = (f'<div class="embed"><iframe loading="lazy" '
                         f'src="https://giphy.com/embed/{esc(c["id"])}"></iframe></div>')
            also = ""
            if c.get("also_found_for"):
                also = f'<div class="also">also found for: {esc(", ".join(c["also_found_for"]))}</div>'
            cards.append(f"""
<div class="card" data-id="{esc(c['id'])}" data-situation="{esc(situation)}" data-title="{esc(c['title'])}">
  {media}
  <div class="meta">
    <div class="title">{esc(c['title']) or '(untitled)'}</div>
    <div class="q">query: “{esc(c['query'])}”{' · ' + esc(c['username']) if c['username'] else ''}</div>
    {also}
    <a href="{esc(c['page_url'])}" target="_blank" rel="noopener">open on Giphy</a>
    <div class="actions">
      <button class="yes" onclick="decide(this,'accept')">✓ Accept</button>
      <button class="no" onclick="decide(this,'reject')">✗ Reject</button>
    </div>
  </div>
</div>""")
        sections.append(f"""
<section data-situation="{esc(situation)}" data-need="{need}">
  <h2>{esc(situation)} <span class="tally"></span></h2>
  <p class="desc"><b>{esc(s.get('description', ''))}</b> Use when: {esc(s.get('use_when', ''))}</p>
  <p class="desc">In the library now: {have['strong']} strong + {have['partial']} partial.
     Need <b>{need}</b> more to reach {TARGET}. {len(mine)} candidates below.</p>
  <div class="grid">{''.join(cards) or '<p class="desc">No new candidates.</p>'}</div>
</section>""")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SLAP GIF Situation Review</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#111; color:#eee; margin:0; padding:24px; }}
 .bar {{ position:sticky; top:0; background:#111; padding:12px 0; border-bottom:1px solid #222; z-index:5; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }}
 .bar button {{ background:#2b6cb0; color:#fff; border:0; padding:8px 16px; border-radius:16px; cursor:pointer; font-weight:600; }}
 .bar button.plain {{ background:#333; color:#ccc; }}
 h2 {{ text-transform:uppercase; font-size:15px; letter-spacing:.05em; color:#aaa; border-bottom:1px solid #333; padding-bottom:8px; margin-top:36px; }}
 .tally {{ color:#7ab8ff; text-transform:none; font-weight:normal; }}
 .desc {{ color:#999; font-size:13px; margin:4px 0; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:14px; margin-top:10px; }}
 .card {{ background:#1a1a1a; border:2px solid #2a2a2a; border-radius:8px; overflow:hidden; }}
 .card img {{ width:100%; display:block; background:#000; }}
 .embed {{ position:relative; aspect-ratio:4/3; background:#000; }}
 .embed iframe {{ position:absolute; inset:0; width:100%; height:100%; border:0; }}
 .meta {{ padding:8px 10px; font-size:12px; }}
 .title {{ font-weight:600; margin-bottom:4px; }}
 .q, .also {{ color:#888; font-size:11px; margin-bottom:4px; }}
 a {{ color:#7ab8ff; font-size:11px; }}
 .actions {{ display:flex; gap:6px; margin-top:8px; }}
 .actions button {{ flex:1; padding:6px; border-radius:4px; border:1px solid #444; background:#222; color:#ccc; cursor:pointer; }}
 .card.accept {{ border-color:#1e8e3e; }} .card.accept .yes {{ background:#1e8e3e; color:#fff; }}
 .card.reject {{ opacity:.35; }} .card.reject .no {{ background:#7a3b33; color:#fff; }}
</style></head><body>
<h1>SLAP GIF Situation Review (SLA-141)</h1>
<p class="desc">Accept a GIF only if it is a specific, recognizable reference that lands the situation's emotion on its own.
Target: {TARGET} per situation. Decisions are kept in this browser until you export them.</p>
<div class="bar">
  <button onclick="exportDecisions()">⬇ Export Decisions</button>
  <button class="plain" onclick="clearAll()">Clear decisions</button>
  <span id="total" class="tally"></span>
</div>
{''.join(sections)}
<script>
const KEY = 'slap_gif_situation_decisions';
// Memory is the truth; localStorage only carries it across a reload, and may
// be unavailable (private window, blocked site data).
let MEM = null;
function load() {{
  if (MEM === null) {{ try {{ MEM = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{ MEM = {{}}; }} }}
  return MEM;
}}
function save(d) {{ MEM = d; try {{ localStorage.setItem(KEY, JSON.stringify(d)); }} catch (e) {{}} }}
function paint() {{
  const d = load();
  document.querySelectorAll('.card').forEach(c => {{
    c.classList.remove('accept', 'reject');
    const v = d[c.dataset.id]; if (v) c.classList.add(v.decision);
  }});
  let acc = 0;
  document.querySelectorAll('section').forEach(s => {{
    const n = s.querySelectorAll('.card.accept').length; acc += n;
    s.querySelector('.tally').textContent = `— ${{n}} accepted / ${{s.dataset.need}} needed`;
  }});
  document.getElementById('total').textContent = `${{acc}} accepted, ${{Object.keys(d).length}} decided`;
}}
function decide(btn, v) {{
  const c = btn.closest('.card'); const d = load();
  if (d[c.dataset.id] && d[c.dataset.id].decision === v) delete d[c.dataset.id];
  else d[c.dataset.id] = {{ id: c.dataset.id, situation: c.dataset.situation, title: c.dataset.title, decision: v }};
  save(d); paint();
}}
function exportDecisions() {{
  const list = Object.values(load());
  if (!list.length) {{ alert('Nothing decided yet.'); return; }}
  const blob = new Blob([JSON.stringify({{ generated_at: new Date().toISOString(), decisions: list }}, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'gif_situation_decisions.json';
  document.body.appendChild(a); a.click(); a.remove();
}}
function clearAll() {{ if (confirm('Clear all decisions on this page?')) {{ MEM = {{}}; try {{ localStorage.removeItem(KEY); }} catch (e) {{}} paint(); }} }}
paint();
</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-only", action="store_true", help="rebuild the page from the saved candidates; no network")
    ap.add_argument("--situation", default="", help="only search this situation")
    ap.add_argument("--per-query", type=int, default=PER_QUERY)
    args = ap.parse_args(argv)

    library = load_json(LIBRARY_PATH)
    taxonomy = load_json(TAXONOMY_PATH)
    unknown = [s for s in QUERIES if s not in taxonomy_by_id(taxonomy)]
    if unknown:
        print(f"ERROR: not in the taxonomy: {unknown}")
        return 1

    if args.page_only:
        candidates = load_json(CANDIDATES_PATH)["candidates"]
    else:
        key = env_value("GIPHY_API_KEY")
        if not key:
            print("ERROR: GIPHY_API_KEY not found in .env")
            return 1
        situations = [args.situation] if args.situation else list(QUERIES)
        results, calls = [], 0
        stopped = False
        for situation in situations:
            for q in QUERIES[situation]:
                try:
                    items = search(q, key, args.per_query)
                    calls += 1
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        print("  !! Giphy rate limit (429): stopping. Keeping what was found; wait an hour and re-run.")
                        stopped = True
                        break
                    print(f"  !! '{q}': HTTP {e.code}")
                    continue
                except Exception as e:  # network blip: keep going
                    print(f"  !! '{q}': {e}")
                    continue
                results.append((situation, q, items))
                print(f"  {situation:28s} “{q}”: {len(items)} results")
                time.sleep(0.3)
            if stopped:
                break
        candidates = merge_results(results, library_ids(library))
        if args.situation and CANDIDATES_PATH.exists():
            others = [c for c in load_json(CANDIDATES_PATH)["candidates"] if c["situation"] != args.situation]
            candidates = others + candidates
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATES_PATH.write_text(json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        print(f"\n{calls} Giphy call(s). {len(candidates)} new candidate(s) -> {CANDIDATES_PATH}")

    PAGE_PATH.write_text(build_page(candidates, library, taxonomy), encoding="utf-8")
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c["situation"]] = counts.get(c["situation"], 0) + 1
    for s in QUERIES:
        print(f"  {s:28s} {counts.get(s, 0):3d} candidate(s)")
    print(f"Open: {PAGE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
