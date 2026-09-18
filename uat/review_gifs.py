"""
SLAP — GIF Library Visual Review Tool

Reads prompts/gif_library.DRAFT.json, resolves every entry's Giphy ID to a
real thumbnail via the Giphy get-by-ID endpoint, and writes a single static
HTML page (uat/gif_review.html) you can open in a browser to eyeball the
whole library at a glance — grouped by category, with status badge, label,
tags, and any AI-sourcing note shown right under each GIF.

The page lets you click Verify / Retire / Reset on each card. Decisions are
saved in your browser's localStorage as you go (survives closing/reopening
the page), and an "Export Decisions" button downloads a gif_decisions.json
file. Run uat/apply_gif_decisions.py against that file to actually write the
status changes into prompts/gif_library.DRAFT.json.

This generator script itself does NOT modify the library — it only reads it
and produces the review page.

--no-api renders each entry as Giphy's keyless iframe embed
(giphy.com/embed/<id>) instead of resolving a thumbnail URL, so the page can
be generated with no GIPHY_API_KEY and no network at all — the browser loads
the GIFs when you open it. Use it when you only need to eyeball the library
(regenerating the normal way costs one API call per entry, which is what
exhausted the daily quota on 2026-08-26 and blanked a UAT run), or from an
environment that cannot reach api.giphy.com.

Usage:
    python uat/review_gifs.py
    python uat/review_gifs.py --no-api
Output:
    uat/gif_review.html  (open this file in your browser)
"""

import argparse
import json
import time
import urllib.request
import urllib.error
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gif_url_cache as GC  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "prompts" / "gif_library.DRAFT.json"
ENV_PATH = REPO_ROOT / ".env"
OUTPUT_PATH = Path(__file__).resolve().parent / "gif_review.html"

STATUS_COLORS = {
    "verified": "#1e8e3e",
    "candidate": "#e8a33d",
    "retired": "#999999",
}


def load_giphy_key() -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("GIPHY_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("GIPHY_API_KEY not found in .env")


def resolve_gif(gif_id: str, api_key: str) -> dict | None:
    """Hit Giphy's get-by-ID endpoint, return the parsed 'data' object or None."""
    if gif_id.startswith("PLACEHOLDER"):
        return None
    url = f"https://api.giphy.com/v1/gifs/{gif_id}?api_key={api_key}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("data")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  !! failed to resolve {gif_id}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-api", action="store_true",
                        help="Render keyless giphy.com/embed iframes instead of "
                             "resolving thumbnails (no API key, no network needed)")
    args = parser.parse_args()

    api_key = None if args.no_api else load_giphy_key()
    library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    categories = library.get("categories", {})

    total = sum(len(c.get("gifs", [])) for c in categories.values())
    if args.no_api:
        print(f"Building page for {total} GIF(s) as iframe embeds (no API calls)...")
    else:
        print(f"Resolving {total} GIF(s) via Giphy API...")

    sections_html = []
    resolved_count = 0
    failed_count = 0
    url_cache = {} if args.no_api else GC.load_cache()
    source_counts: dict = {}
    bucket_counts: dict = {}

    for cat_key, cat in categories.items():
        cards = []
        for entry in cat.get("gifs", []):
            gif_id = entry["id"]
            label = entry.get("label", "")
            status = entry.get("status", "candidate")
            tags = ", ".join(entry.get("tags", []))
            note = entry.get("note", "")
            cooldown = entry.get("cooldown_days", library["_meta"].get("cooldown_days_default"))

            # Cache-backed. Regenerating this page used to cost one API call per
            # entry (205 as of 2026-08-26), which is what exhausted the daily
            # Giphy quota and blanked a whole UAT run's library GIFs.
            def _fetch(gid, _key=api_key):
                data = resolve_gif(gid, _key)
                time.sleep(0.05)  # be polite to the API
                if not data:
                    return None
                images = data.get("images", {})
                return (images.get("downsized_medium", {}).get("url")
                        or images.get("original", {}).get("url") or None)

            if args.no_api:
                if gif_id.startswith("PLACEHOLDER"):
                    failed_count += 1
                    img_html = '<div class="broken">⚠ placeholder id — nothing to render</div>'
                else:
                    resolved_count += 1
                    img_html = (f'<div class="embed-wrap"><iframe loading="lazy" '
                                f'src="https://giphy.com/embed/{gif_id}" '
                                f'title="{label}" allowfullscreen></iframe></div>')
            else:
                img_url, source = GC.resolve(gif_id, _fetch, url_cache)
                source_counts[source] = source_counts.get(source, 0) + 1

                if img_url:
                    resolved_count += 1
                    img_html = f'<img src="{img_url}" loading="lazy" alt="{label}">'
                else:
                    failed_count += 1
                    img_html = '<div class="broken">⚠ could not resolve</div>'

            note_html = f'<div class="note">{note}</div>' if note else ""

            # An entry whose own note says it was never looked at, but whose
            # status says verified, is LIVE in selection while unreviewed —
            # the highest-priority review bucket, and invisible in a plain
            # status filter because it sits among the genuinely-checked ones.
            eyeballed = "no" if "not yet eyeballed" in note.lower() else "yes"
            bucket_counts[status] = bucket_counts.get(status, 0) + 1
            if eyeballed == "no" and status == "verified":
                bucket_counts["unreviewed"] = bucket_counts.get("unreviewed", 0) + 1

            cards.append(f"""
            <div class="card" data-id="{gif_id}" data-category="{cat_key}"
                 data-orig-status="{status}" data-status="{status}"
                 data-eyeballed="{eyeballed}">
                {img_html}
                <div class="meta">
                    <span class="badge">{status}</span>
                    <div class="label">{label}</div>
                    <div class="id">id: {gif_id} · cooldown: {cooldown}d</div>
                    <div class="tags">{tags}</div>
                    {note_html}
                    <div class="actions">
                        <button class="act-btn verify-btn" onclick="setStatus(this, 'verified')">✓ Verify</button>
                        <button class="act-btn retire-btn" onclick="setStatus(this, 'retired')">✗ Retire</button>
                        <button class="act-btn reset-btn" onclick="resetStatus(this)">↺ Reset</button>
                    </div>
                </div>
            </div>
            """)

        sections_html.append(f"""
        <section>
            <h2>{cat_key} <span class="count">({len(cat.get('gifs', []))})</span></h2>
            <p class="desc">{cat.get('description', '')}</p>
            <div class="grid">{''.join(cards)}</div>
        </section>
        """)

    if args.no_api:
        stats_line = (f"{resolved_count} embedded · {failed_count} unrenderable · "
                      f"{total} total — iframe mode, GIFs load from Giphy in your browser")
    else:
        stats_line = f"{resolved_count} resolved · {failed_count} failed · {total} total"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>SLAP GIF Library Review</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #111; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ margin-bottom: 4px; }}
  .stats {{ color: #999; margin-bottom: 16px; }}
  .toolbar {{ margin-bottom: 24px; position: sticky; top: 0; background: #111; padding: 12px 0; z-index: 10; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; border-bottom: 1px solid #222; }}
  .filters button {{ background: #222; color: #eee; border: 1px solid #444; padding: 6px 14px; margin-right: 8px; border-radius: 16px; cursor: pointer; }}
  .filters button.active {{ background: #eee; color: #111; }}
  .export-btn {{ background: #2b6cb0; color: #fff; border: none; padding: 8px 16px; border-radius: 16px; cursor: pointer; font-weight: 600; }}
  .clear-btn {{ background: #333; color: #ccc; border: 1px solid #444; padding: 8px 16px; border-radius: 16px; cursor: pointer; }}
  .pending-count {{ color: #7ab8ff; font-size: 13px; }}
  section {{ margin-bottom: 40px; }}
  h2 {{ text-transform: uppercase; font-size: 15px; letter-spacing: 0.05em; color: #aaa; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  .count {{ color: #666; font-weight: normal; }}
  .desc {{ color: #888; font-size: 13px; margin-top: -4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }}
  .card {{ background: #1a1a1a; border-radius: 8px; overflow: hidden; border: 1px solid #2a2a2a; }}
  .card img {{ width: 100%; display: block; background: #000; }}
  .embed-wrap {{ position: relative; width: 100%; aspect-ratio: 4 / 3; background: #000; }}
  .embed-wrap iframe {{ position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }}
  .broken {{ padding: 40px 10px; text-align: center; color: #f66; font-size: 12px; }}
  .meta {{ padding: 10px; }}
  .badge {{ display: inline-block; font-size: 10px; text-transform: uppercase; padding: 2px 8px; border-radius: 10px; color: #111; font-weight: 600; margin-bottom: 6px; }}
  .label {{ font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
  .id {{ font-size: 10px; color: #666; font-family: monospace; margin-bottom: 6px; }}
  .tags {{ font-size: 11px; color: #7ab8ff; margin-bottom: 4px; }}
  .note {{ font-size: 11px; color: #999; line-height: 1.4; margin-top: 4px; }}
  .card[data-status="retired"] {{ opacity: 0.4; }}
  .card.changed {{ border-color: #7ab8ff; }}
  /* live in selection but its own note says nobody ever looked at it */
  .card[data-status="verified"][data-eyeballed="no"] {{ border-color: #7a3b33; }}
  .card[data-status="verified"][data-eyeballed="no"].changed {{ border-color: #7ab8ff; }}
  .filters button.urgent {{ border-color: #c0392b; color: #ff9b8f; }}
  .hidden {{ display: none !important; }}
  .actions {{ margin-top: 8px; display: flex; gap: 6px; }}
  .act-btn {{ flex: 1; font-size: 10px; padding: 5px 4px; border-radius: 4px; border: 1px solid #444; background: #222; color: #ccc; cursor: pointer; }}
  .act-btn:hover {{ background: #333; }}
  .verify-btn:hover {{ border-color: #1e8e3e; color: #4fd17a; }}
  .retire-btn:hover {{ border-color: #c0392b; color: #ff7a6b; }}
</style>
</head>
<body>
<h1>SLAP GIF Library Review</h1>
<div class="stats">{stats_line}</div>
<div class="toolbar">
  <div class="filters">
    <button class="active" onclick="filterStatus('all', this)">All ({total})</button>
    <button class="urgent" onclick="filterStatus('unreviewed', this)">⚠ Never eyeballed &amp; LIVE ({bucket_counts.get('unreviewed', 0)})</button>
    <button onclick="filterStatus('candidate', this)">Candidate ({bucket_counts.get('candidate', 0)})</button>
    <button onclick="filterStatus('verified', this)">Verified ({bucket_counts.get('verified', 0)})</button>
    <button onclick="filterStatus('retired', this)">Retired ({bucket_counts.get('retired', 0)})</button>
  </div>
  <button class="export-btn" onclick="exportDecisions()">⬇ Export Decisions</button>
  <button class="clear-btn" onclick="clearDecisions()">Clear saved decisions</button>
  <span class="pending-count" id="pendingCount"></span>
</div>
{''.join(sections_html)}
<script>
const STORAGE_KEY = 'slap_gif_decisions';

function loadDecisions() {{
  try {{
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');
  }} catch (e) {{
    return {{}};
  }}
}}

function saveDecisions(decisions) {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions));
}}

function applyStoredDecisions() {{
  const decisions = loadDecisions();
  document.querySelectorAll('.card').forEach(card => {{
    const id = card.dataset.id;
    if (decisions[id]) {{
      updateCardUI(card, decisions[id].new_status);
    }}
  }});
  updatePendingCount();
}}

function updateCardUI(card, status) {{
  card.dataset.status = status;
  const badge = card.querySelector('.badge');
  badge.textContent = status;
  const colors = {{ verified: '#1e8e3e', candidate: '#e8a33d', retired: '#999999' }};
  badge.style.background = colors[status] || '#666';
  // Highlight on "has a pending decision", not "status differs" — confirming a
  // never-eyeballed entry is a real decision that leaves the status unchanged.
  card.classList.toggle('changed', !!loadDecisions()[card.dataset.id]);
}}

function setStatus(btn, newStatus) {{
  const card = btn.closest('.card');
  const id = card.dataset.id;
  const category = card.dataset.category;
  const origStatus = card.dataset.origStatus;
  const label = card.querySelector('.label').textContent;

  const decisions = loadDecisions();
  // A no-change click on a never-eyeballed entry is still a decision: it means
  // "I looked at this and it's fine", which is what clears the 'NOT yet
  // eyeballed' marker from its note. Without this, confirming any of the
  // already-verified backlog would be a silent no-op and they'd stay queued
  // for review forever. Use Reset to actually clear a decision.
  const neverEyeballed = card.dataset.eyeballed === 'no';
  if (newStatus === origStatus && !neverEyeballed) {{
    delete decisions[id];
  }} else {{
    decisions[id] = {{ id, category, label, old_status: origStatus,
                      new_status: newStatus, confirmed: newStatus === origStatus }};
  }}
  saveDecisions(decisions);
  updateCardUI(card, newStatus);
  updatePendingCount();
}}

function resetStatus(btn) {{
  const card = btn.closest('.card');
  const decisions = loadDecisions();
  delete decisions[card.dataset.id];
  saveDecisions(decisions);
  updateCardUI(card, card.dataset.origStatus);
  updatePendingCount();
}}

function updatePendingCount() {{
  const decisions = loadDecisions();
  const n = Object.keys(decisions).length;
  document.getElementById('pendingCount').textContent =
    n > 0 ? `${{n}} unsaved-to-file decision(s)` : '';
}}

function exportDecisions() {{
  const decisions = loadDecisions();
  const list = Object.values(decisions);
  if (list.length === 0) {{
    alert('No decisions to export yet — click Verify or Retire on some cards first.');
    return;
  }}
  const payload = {{ generated_at: new Date().toISOString(), decisions: list }};
  const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'gif_decisions.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

function clearDecisions() {{
  if (!confirm('Clear all unsaved decisions? This does not affect the library file.')) return;
  localStorage.removeItem(STORAGE_KEY);
  document.querySelectorAll('.card').forEach(card => updateCardUI(card, card.dataset.origStatus));
  updatePendingCount();
}}

function filterStatus(status, btn) {{
  document.querySelectorAll('.filters button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(card => {{
    // 'unreviewed' is not a status — it's verified-but-never-eyeballed,
    // i.e. live in selection without anyone having confirmed the clip.
    const match = status === 'all'
      ? true
      : status === 'unreviewed'
        ? (card.dataset.status === 'verified' && card.dataset.eyeballed === 'no')
        : card.dataset.status === status;
    card.classList.toggle('hidden', !match);
  }});
  // Empty categories are just noise once a filter is on.
  document.querySelectorAll('section').forEach(sec => {{
    const anyVisible = sec.querySelector('.card:not(.hidden)');
    sec.classList.toggle('hidden', !anyVisible);
  }});
}}

applyStoredDecisions();
</script>
</body>
</html>
"""

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    if not args.no_api:
        GC.save_cache(url_cache)
        print(f"\nDone. {resolved_count}/{total} resolved, {failed_count} failed.")
        print(f"URL source: {GC.summarize(source_counts)}")
    else:
        print(f"\nDone. {resolved_count}/{total} embedded, {failed_count} unrenderable. 0 API calls.")
    print(f"Open: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
