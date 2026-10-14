"""
SLAP — review page for the GIF vision tagger's proposals (SLA-133).

    python -X utf8 uat/review_gif_tags.py
        reads  uat/output/gif_tags.json (written by uat/gif_tagger.py)
        writes uat/gif_tag_review.html  (open in a browser)

On the page every proposed tag (mood, situation, entity, and the reject call)
gets a right / wrong click, and each card can add a situation the tagger
MISSED. Verdicts are saved in the browser as you go; "Export" downloads
gif_tag_decisions.json.

    python -X utf8 uat/review_gif_tags.py --score path/to/gif_tag_decisions.json

prints the agreement rate per tag kind and per situation, and whether the
situation tags clear the bar SLA-133 set before the run (ACCURACY_BAR). Only
tags that were judged count; an unjudged tag is neither right nor wrong.

GIFs render through Giphy's keyless iframe embed (giphy.com/embed/<id>), the
same as review_gifs.py --no-api, so building the page needs no API key and no
network. Nothing here writes to the library.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAGS_PATH = HERE / "output" / "gif_tags.json"
OUTPUT_PATH = HERE / "gif_tag_review.html"
TAXONOMY_PATH = HERE.parent / "prompts" / "gif_taxonomy.DRAFT.json"

ACCURACY_BAR = 0.85   # share of judged situation tags marked right; set before the run
STORAGE_KEY = "slap_gif_tag_review_v1"


def tag_keys(gif_id: str, t: dict) -> list[tuple[str, str]]:
    """(key, display) for every judgeable claim on one GIF. Keys are stable."""
    out = []
    if not t.get("usable", True):
        out.append(("reject", f"reject: {t.get('reject_reason', '')}"))
    for m in t.get("moods", []):
        out.append((f"mood:{m}", m))
    for s in t.get("subcategories", []):
        out.append((f"sit:{s['id']}", f"{s['id']} ({s.get('fit', '')})"))
    ent = t.get("entities") or {}
    for kind in ("players", "teams", "figures"):
        for name in ent.get(kind, []):
            out.append((f"ent:{kind}:{name}", name))
    return out


def build_page(tags: dict, situation_ids: list[str]) -> str:
    cards = []
    items = sorted(tags.items(), key=lambda kv: ({"low": 0, "medium": 1, "high": 2}
                                                 .get(kv[1].get("confidence"), 3), kv[0]))
    for gid, t in items:
        chips = []
        for key, label in tag_keys(gid, t):
            kind = key.split(":")[0]
            chips.append(
                f'<span class="chip {kind}" data-key="{html.escape(key)}">{html.escape(label)}'
                f'<button data-v="1" title="right">&#10003;</button>'
                f'<button data-v="0" title="wrong">&#10007;</button></span>')
        ent = t.get("entities") or {}
        evidence = ent.get("evidence", "")
        options = "".join(f'<option value="{html.escape(s)}">{html.escape(s)}</option>'
                          for s in situation_ids)
        cards.append(f"""
<div class="card" data-id="{html.escape(gid)}">
  <div class="embed"><iframe loading="lazy" src="https://giphy.com/embed/{html.escape(gid)}"
       title="{html.escape(t.get('label', ''))}" allowfullscreen></iframe></div>
  <div class="body">
    <div class="label">{html.escape(t.get('label', ''))}</div>
    <div class="meta">{html.escape(t.get('category', ''))} &middot; confidence
      <b>{html.escape(t.get('confidence', ''))}</b> &middot; {html.escape(t.get('reaction_readiness', ''))}
      &middot; intensity {html.escape(t.get('intensity', ''))}</div>
    <div class="what">{html.escape(t.get('what_happens', ''))}</div>
    {f'<div class="caption">Caption: “{html.escape(t["caption_text"])}”</div>' if t.get('caption_text') else ''}
    <div class="chips">{''.join(chips) or '<i>no tags</i>'}</div>
    {f'<div class="evidence">Entity evidence: {html.escape(evidence)}</div>' if evidence else ''}
    <div class="missed">Missed a situation?
      <select><option value="">add…</option>{options}</select>
      <span class="missed-list"></span></div>
  </div>
</div>""")
    return PAGE.format(count=len(tags), cards="".join(cards), key=STORAGE_KEY)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GIF tag review</title>
<style>
  :root {{ --bg:#f6f6f4; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --ok:#1f7a3a; --bad:#b3261e; --line:#ddd; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --card:#222220; --ink:#ecebe6; --muted:#a3a29b; --line:#3a3a37; }} }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }}
  header {{ position:sticky; top:0; background:var(--bg); padding:12px 16px; border-bottom:1px solid var(--line); z-index:2;
            display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  main {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; padding:16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  .embed {{ position:relative; padding-top:62%; background:#000; }}
  .embed iframe {{ position:absolute; inset:0; width:100%; height:100%; border:0; }}
  .body {{ padding:10px 12px; }}
  .label {{ font-weight:600; }} .meta, .evidence, .caption {{ color:var(--muted); font-size:12px; }}
  .what {{ margin:6px 0; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }}
  .chip {{ border:1px solid var(--line); border-radius:999px; padding:2px 4px 2px 10px; font-size:12px; }}
  .chip.reject {{ border-style:dashed; }} .chip.ent {{ font-style:italic; }}
  .chip button {{ border:0; background:none; cursor:pointer; color:var(--muted); font-size:13px; padding:0 4px; }}
  .chip.v1 {{ border-color:var(--ok); color:var(--ok); }} .chip.v0 {{ border-color:var(--bad); color:var(--bad); text-decoration:line-through; }}
  .missed {{ font-size:12px; color:var(--muted); }} .missed-list b {{ color:var(--bad); margin-left:6px; }}
  button.top {{ padding:6px 12px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--ink); cursor:pointer; }}
</style></head><body>
<header><b>GIF tag review</b> <span id="progress"></span>
  <button class="top" id="export">Export decisions</button>
  <span style="color:var(--muted)">{count} GIFs, least confident first. Judge each tag; unjudged tags don't count.</span></header>
<main>{cards}</main>
<script>
const KEY = "{key}";
let D = {{}};
try {{ D = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ D = {{}}; }}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(D)); }} catch (e) {{}} render(); }}
function render() {{
  let judged = 0, total = 0;
  document.querySelectorAll(".card").forEach(card => {{
    const d = D[card.dataset.id] || {{}};
    card.querySelectorAll(".chip").forEach(ch => {{
      total++; const v = (d.tags || {{}})[ch.dataset.key];
      ch.classList.toggle("v1", v === true); ch.classList.toggle("v0", v === false);
      if (v !== undefined) judged++;
    }});
    card.querySelector(".missed-list").innerHTML = (d.missed || []).map(m => "<b>+" + m + "</b>").join("");
  }});
  document.getElementById("progress").textContent = judged + " / " + total + " tags judged";
}}
document.querySelectorAll(".chip button").forEach(b => b.addEventListener("click", () => {{
  const id = b.closest(".card").dataset.id, key = b.closest(".chip").dataset.key;
  D[id] = D[id] || {{}}; D[id].tags = D[id].tags || {{}};
  const v = b.dataset.v === "1";
  if (D[id].tags[key] === v) delete D[id].tags[key]; else D[id].tags[key] = v;
  save();
}}));
document.querySelectorAll(".missed select").forEach(s => s.addEventListener("change", () => {{
  if (!s.value) return;
  const id = s.closest(".card").dataset.id; D[id] = D[id] || {{}};
  const m = new Set(D[id].missed || []); m.has(s.value) ? m.delete(s.value) : m.add(s.value);
  D[id].missed = [...m]; s.value = ""; save();
}}));
document.getElementById("export").addEventListener("click", () => {{
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(D, null, 2)], {{type: "application/json"}}));
  a.download = "gif_tag_decisions.json"; a.click();
}});
render();
</script></body></html>
"""


def score(tags: dict, decisions: dict) -> dict:
    """
    Agreement per tag kind and per situation. Decisions are matched against the
    CURRENT tags file: a verdict on a tag the tagger no longer proposes (after a
    --retag) is ignored rather than counted.
    """
    by_kind = defaultdict(Counter)
    by_sit = defaultdict(Counter)
    missed = Counter()
    for gid, t in tags.items():
        d = decisions.get(gid) or {}
        verdicts = d.get("tags") or {}
        for key, _label in tag_keys(gid, t):
            if key not in verdicts:
                continue
            kind = key.split(":")[0]
            outcome = "right" if verdicts[key] else "wrong"
            by_kind[kind][outcome] += 1
            if kind == "sit":
                by_sit[key.split(":", 1)[1]][outcome] += 1
        for m in d.get("missed") or []:
            missed[m] += 1

    def rate(c: Counter) -> float | None:
        n = c["right"] + c["wrong"]
        return round(c["right"] / n, 3) if n else None

    def summary(c: Counter) -> dict:
        return {"right": c["right"], "wrong": c["wrong"], "rate": rate(c)}

    sit = by_kind["sit"]
    return {
        "kinds": {k: summary(c) for k, c in by_kind.items()},
        "situations": {k: summary(c) for k, c in sorted(by_sit.items())},
        "missed": dict(missed.most_common()),
        "situation_rate": rate(sit),
        "passes": rate(sit) is not None and rate(sit) >= ACCURACY_BAR,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the GIF tag review page, or score its export.")
    ap.add_argument("--score", metavar="DECISIONS_JSON", help="score an exported decisions file")
    args = ap.parse_args(argv)
    if not TAGS_PATH.exists():
        raise SystemExit(f"{TAGS_PATH} not found; run uat/gif_tagger.py first")
    tags = json.loads(TAGS_PATH.read_text(encoding="utf-8"))["tags"]

    if args.score:
        result = score(tags, json.loads(Path(args.score).read_text(encoding="utf-8")))
        names = {"sit": "situations", "mood": "moods", "ent": "entities", "reject": "reject calls"}
        for kind, c in result["kinds"].items():
            print(f"{names.get(kind, kind):<13} {c['right']:>4} right / {c['wrong']:>4} wrong   {c['rate']}")
        print("\nPer situation (wrong first):")
        for sid, c in sorted(result["situations"].items(), key=lambda kv: -kv[1]["wrong"]):
            print(f"  {sid:<32} {c['right']:>3} / {c['right'] + c['wrong']:<3} {c['rate']}")
        if result["missed"]:
            print("\nMissed (situations you added):", ", ".join(f"{k} x{v}" for k, v in result["missed"].items()))
        rate = result["situation_rate"]
        print(f"\nSituation accuracy {rate} vs bar {ACCURACY_BAR}: "
              f"{'PASS' if result['passes'] else 'NOT YET' if rate is None else 'FAIL'}")
        return 0 if result["passes"] else 1

    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    OUTPUT_PATH.write_text(build_page(tags, [s["id"] for s in taxonomy["subcategories"]]), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(tags)} GIFs). Open it in a browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
