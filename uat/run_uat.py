"""
SLAP Media Mix UAT — entry point.

Runs the production chain against a FROZEN fixture so that differences between
runs come from prompt changes, not from a different news day:

    Pass 1  Story Selector      (video tweets already filtered out)
    Pass 1B Highlight Selector  (new — picks 3-5 clips from the video tweets)
            highlight_to_gif    (deterministic clipping + GIF encode)
    Pass 2  Writer              (new media targets + setup/clip/reaction)
    Pass 3  Claim Validator     (deterministic)
    Pass 5  Pre-Edit            (deterministic tweet audit)
    Pass 6  Editor              (taught to preserve highlight-placeholder)
    Pass 7  Media render        (GIF/meme embed + highlight swap)

Pass 4 (Voice Editor) is skipped per spec §0.5.

Nothing here writes to a production file. Output lands in uat/output/ and
uat/media/.

    python uat/run_uat.py
    python uat/run_uat.py --passes 1,1b,2
    python uat/run_uat.py --no-highlights
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UAT_DIR = Path(__file__).resolve().parent
if str(UAT_DIR) not in sys.path:
    sys.path.insert(0, str(UAT_DIR))

import generate_newsletter_uat as G          # noqa: E402
import highlight_to_gif as H                 # noqa: E402
import meme_box_check as MB                  # noqa: E402
import gif_library_select as GL              # noqa: E402

# ---------------------------------------------------------------------------
# CONFIG — §1.2
# ---------------------------------------------------------------------------
MEDIA_MIX_TARGET = 0.50   # (gifs + memes + highlights) / total media blocks
TWEET_TARGET     = (20, 24)
GIF_MEME_TARGET  = (20, 24)
HIGHLIGHT_TARGET = (3, 5)

ALL_PASSES = ["1", "1b", "2", "3", "5", "6", "7"]


# ---------------------------------------------------------------------------
# Highlight rendering
# ---------------------------------------------------------------------------

def render_highlights(html: str, converted: list) -> tuple[str, int, int]:
    """Swap highlight-placeholder divs for local <img> tags.

    Returns (html, rendered, dropped). A placeholder whose clip did not survive
    conversion is REMOVED, never left pointing at a file that does not exist —
    a broken image is worse than a missing one.
    """
    by_id = {h["id"]: h for h in converted}
    rendered = dropped = 0

    soup = BeautifulSoup(html, "html.parser")
    for div in soup.find_all("div", class_="highlight-placeholder"):
        hid = div.get("data-highlight-id", "")
        hl = by_id.get(hid)
        if not hl:
            div.decompose()
            dropped += 1
            continue
        img = soup.new_tag("img")
        # Local relative path — hosting is explicitly out of scope (§3.4).
        img["src"] = f"../media/{hl['gif_file']}"
        img["alt"] = hl.get("description", "highlight")[:180]
        img["class"] = "highlight-gif"
        img["style"] = "max-width:100%; border-radius:4px;"
        div.replace_with(img)
        rendered += 1

    return str(soup), rendered, dropped


# ---------------------------------------------------------------------------
# Media Mix Report — §1.3
# ---------------------------------------------------------------------------

def count_media(html: str) -> tuple[dict, list]:
    """Count media blocks in document order, attributed to the preceding h1/h2.

    Counts BOTH un-rendered placeholders and rendered <img> tags, because the
    GIF and meme pipelines are skippable (missing API key) and the report must
    stay honest either way. Verified against real production output: memes use
    div.meme-placeholder and render to i.imgflip.com; GIFs use
    div.gif-placeholder and render to giphy.
    """
    soup = BeautifulSoup(html, "html.parser")
    totals = {"tweet": 0, "gif": 0, "meme": 0, "highlight": 0}
    per_section: list = []
    current = "(before first header)"
    sections = {current: {"tweet": 0, "gif": 0, "meme": 0, "highlight": 0}}
    order = [current]

    def classify(el) -> str | None:
        name = el.name
        classes = el.get("class") or []
        if name == "blockquote" and "tweet" in classes:
            return "tweet"
        if name == "div":
            if "highlight-placeholder" in classes:
                return "highlight"
            if "meme-placeholder" in classes:
                return "meme"
            if "gif-placeholder" in classes:
                # A gif-placeholder carrying data-boxes is a meme (§1.3).
                return "meme" if el.has_attr("data-boxes") else "gif"
        if name == "img":
            if "highlight-gif" in classes:
                return "highlight"
            src = el.get("src", "")
            if "../media/" in src or src.startswith("media/"):
                return "highlight"
            if "imgflip" in src:
                return "meme"
            if "giphy" in src:
                return "gif"
            # Box-score images and other decoration are not media blocks.
        return None

    for el in soup.find_all(["h1", "h2", "blockquote", "div", "img"]):
        if el.name in ("h1", "h2"):
            current = el.get_text(strip=True)[:48] or "(untitled)"
            if current not in sections:
                sections[current] = {"tweet": 0, "gif": 0, "meme": 0, "highlight": 0}
                order.append(current)
            continue
        kind = classify(el)
        if kind:
            totals[kind] += 1
            sections[current][kind] += 1

    for name in order:
        if any(sections[name].values()):
            per_section.append((name, sections[name]))
    return totals, per_section


def media_mix_report(html: str, *, update_rejected, video_excluded,
                     clip_results, run_cost) -> dict:
    totals, per_section = count_media(html)
    media = totals["gif"] + totals["meme"] + totals["highlight"]
    total = media + totals["tweet"]
    share = (media / total) if total else 0.0

    print("\n" + "=" * 52)
    print("=== MEDIA MIX ===")
    print(f"Tweets:          {totals['tweet']}")
    print(f"GIFs:            {totals['gif']}")
    print(f"Memes:           {totals['meme']}")
    print(f"Highlights:      {totals['highlight']}")
    print(f"Total media:     {total}")
    print(f"GIF/meme share:  {share*100:.1f}%   (target {MEDIA_MIX_TARGET*100:.1f}%)")
    print(f"Tweets:          {totals['tweet']}      "
          f"(target {TWEET_TARGET[0]}-{TWEET_TARGET[1]})")
    print(f"GIFs+memes:      {totals['gif'] + totals['meme']}      "
          f"(target {GIF_MEME_TARGET[0]}-{GIF_MEME_TARGET[1]})")
    print(f"Highlights:      {totals['highlight']}      "
          f"(target {HIGHLIGHT_TARGET[0]}-{HIGHLIGHT_TARGET[1]})")
    print(f"Update tweets rejected by Pass 1: {update_rejected}")
    print(f"Video tweets excluded (§2.1):     {video_excluded}")
    if run_cost is not None:
        print(f"Run cost: ${run_cost:.2f}")

    if per_section:
        print("\nPer-section:")
        for name, c in per_section:
            print(f"  {name:<44} {c['tweet']:>2} tweets, "
                  f"{c['gif'] + c['meme']:>2} gif/meme, {c['highlight']:>2} highlight")

    if clip_results:
        print("\nHighlight clips:")
        for r in clip_results:
            if r.ok:
                print(f"  [{r.highlight_id}] OK   {r.source_dims:>9} "
                      f"src {r.source_duration:>6.2f}s -> window "
                      f"[{r.clip_start:.2f}s +{r.clip_len:.1f}s]  "
                      f"{r.size_mb:.2f}MB  fps{r.final_fps} w{r.final_width} "
                      f"c{r.final_colors}  step-downs={r.step_downs}")
            else:
                print(f"  [{r.highlight_id}] DROP {r.reason}")

    print("=" * 52)
    print("(report only — a miss never fails the run)")

    return {
        "totals": totals,
        "share": round(share, 4),
        "per_section": [{"section": n, **c} for n, c in per_section],
        "update_tweets_rejected": update_rejected,
        "video_tweets_excluded": video_excluded,
    }


# ---------------------------------------------------------------------------
# Highlight history — §3.5
# ---------------------------------------------------------------------------

def append_highlight_history(plan: list, results: list) -> None:
    path = G.HIGHLIGHT_HISTORY_PATH
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []

    by_id = {h["id"]: h for h in plan}
    today = date.today().isoformat()
    for r in results:
        h = by_id.get(r.highlight_id, {})
        history.insert(0, {
            "date":            today,
            "story":           h.get("story_id", ""),
            "description":     h.get("description", ""),
            "source_tweet_url": r.source_url,
            "source_duration": r.source_duration,
            "source_dims":     r.source_dims,
            "clip_start":      r.clip_start,
            "clip_len":        r.clip_len,
            "output_file":     r.output_file,
            "size_mb":         r.size_mb,
            "final_fps":       r.final_fps,
            "final_width":     r.final_width,
            "final_colors":    r.final_colors,
            "step_downs":      r.step_downs,
            "ladder_log":      r.ladder_log,
            "ok":              r.ok,
            "reason":          r.reason,
        })
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ highlight_history.json updated ({len(history)} record(s))")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="SLAP Media Mix UAT")
    ap.add_argument("--passes", default=",".join(ALL_PASSES),
                    help="comma list, e.g. 1,1b,2  (default: full chain)")
    ap.add_argument("--no-highlights", action="store_true",
                    help="skip Pass 1B and clip conversion")
    ap.add_argument("--allow-short-memes", action="store_true",
                    help="render memes even when the writer supplied fewer "
                         "captions than the template has panels (blank panels)")
    ap.add_argument("--max-search-gifs", type=int, default=3,
                    help="Tier 3 budget: max live-search GIFs per issue "
                         "(default 3). Excess placeholders are dropped.")
    args = ap.parse_args()

    passes = {p.strip().lower() for p in args.passes.split(",") if p.strip()}
    unknown = passes - set(ALL_PASSES)
    if unknown:
        raise SystemExit(f"Unknown pass(es): {sorted(unknown)}. Valid: {ALL_PASSES}")

    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (read from repo .env)")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    print("=" * 52)
    print("SLAP MEDIA MIX — UAT")
    print(f"  fixture : {G.RAW_CONTENT_PATH.name}")
    print(f"  passes  : {','.join(p for p in ALL_PASSES if p in passes)}")
    print(f"  output  : {G.OUTPUT_DIR}")
    print("=" * 52)

    ok, msg = H.tools_available()
    print(f"  tooling : {msg}")

    raw = G.load_json(G.RAW_CONTENT_PATH)
    if not raw:
        raise SystemExit(f"Fixture missing: {G.RAW_CONTENT_PATH}\n"
                         "Run: python uat/fetch_fixture.py")
    game_state = G.load_json(G.GAME_STATE_PATH)
    recent_output = G.load_json(G.RECENT_OUTPUT_PATH) or []

    all_tweets = raw.get("tweets", [])
    video_tweets = [t for t in all_tweets if t.get("has_video")]
    print(f"  fixture : {len(raw.get('news_headlines', []))} headlines, "
          f"{len(all_tweets)} tweets ({len(video_tweets)} video-tagged)")

    story_plan = None
    update_rejected = "n/a"
    highlight_plan: list = []
    clip_results: list = []
    converted: list = []

    # ---- Pass 1 ----------------------------------------------------------
    if "1" in passes:
        story_plan = G.run_pass1(raw, recent_output, client, game_state)
        recent_output = G.save_story_log(story_plan, recent_output, G.RECENT_OUTPUT_PATH)
        try:
            update_rejected = (json.loads(story_plan)
                               .get("filter_stats", {})
                               .get("update_tweets_rejected", "not reported"))
        except (json.JSONDecodeError, AttributeError):
            update_rejected = "unparseable"
        print(f"  §2.2 pure-update filter: {update_rejected} tweet(s) rejected")
        (G.OUTPUT_DIR / "story_plan.json").write_text(story_plan, encoding="utf-8")
    else:
        cached = G.OUTPUT_DIR / "story_plan.json"
        if cached.exists():
            story_plan = cached.read_text(encoding="utf-8")
            print("  Pass 1 skipped — reusing cached story_plan.json")
        else:
            raise SystemExit("Pass 1 skipped but no cached story_plan.json exists.")

    # ---- Pass 1B + conversion -------------------------------------------
    if "1b" in passes and not args.no_highlights:
        highlight_plan = G.run_pass1b(story_plan, video_tweets, client)
        G.HIGHLIGHT_PLAN_PATH.write_text(
            json.dumps(highlight_plan, indent=2, ensure_ascii=False), encoding="utf-8")

        if highlight_plan:
            if not ok:
                print(f"\n── HIGHLIGHT CONVERT ───────────────────────────────")
                print(f"  ⚠ {msg} — skipping conversion, no highlights will render")
            else:
                print(f"\n── HIGHLIGHT CONVERT ───────────────────────────────")
                converted, clip_results = H.convert_plan(highlight_plan, G.MEDIA_DIR)
                print(f"\n  {len(converted)}/{len(highlight_plan)} clip(s) survived")
                append_highlight_history(highlight_plan, clip_results)
                # Rewrite the plan to only what actually exists on disk, so the
                # writer is never told about a clip it cannot place.
                G.HIGHLIGHT_PLAN_PATH.write_text(
                    json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- Pass 2 ----------------------------------------------------------
    if "2" in passes:
        html = G.run_pass2(story_plan, client, game_state, highlight_plan=converted)
        (G.OUTPUT_DIR / "pass2_raw.html").write_text(html, encoding="utf-8")
    else:
        cached = G.OUTPUT_DIR / "pass2_raw.html"
        if not cached.exists():
            raise SystemExit("Pass 2 skipped but no cached pass2_raw.html exists.")
        html = cached.read_text(encoding="utf-8")
        print("  Pass 2 skipped — reusing cached pass2_raw.html")

    # ---- Pass 3 ----------------------------------------------------------
    if "3" in passes:
        try:
            from claim_validator import validate_claims
            html, _flags = validate_claims(html, G.GAME_STATE_PATH)
        except ImportError:
            print("\n── PASS 3: Claim Validator ─────────────────────────")
            print("  ⚠ claim_validator.py not found — skipping")
        except Exception as e:
            print(f"\n  ✗ Pass 3 failed on highlight markup: {e}")

    # ---- Pass 5 ----------------------------------------------------------
    if "5" in passes:
        html = G.pre_edit(html, story_plan)

    # ---- Pass 6 ----------------------------------------------------------
    if "6" in passes:
        html = G.run_pass6(html, recent_output, client)

    # ---- Pass 7 — media render ------------------------------------------
    if "7" in passes:
        html, _fake = G.drop_fabricated_tweets(html)
        if _fake:
            print(f"  ⚠ Dropped {_fake} fabricated tweet(s)")

        print("\n── GIF PIPELINE ────────────────────────────────────")
        giphy_key = os.getenv("GIPHY_API_KEY", "")
        if not giphy_key:
            print("  ⚠ GIPHY_API_KEY not set — gif-placeholders left un-rendered")
        else:
            # Curated library first. It consumes only the placeholders carrying
            # data-library-category; anything the writer left as a plain
            # "GIF: <search term>" falls through to the live-search path below,
            # unchanged. Order matters: library placeholders must be resolved
            # before embed_gifs_in_html() so the two never contend.
            _hist = G.load_gif_history(G.OUTPUT_DIR)
            html, _lib_entries, gif_lib_stats = GL.render_library_gifs(
                html, giphy_key, _hist)
            if _lib_entries:
                G.save_gif_history(G.OUTPUT_DIR, _lib_entries, _hist)

            # Tier 3 budget is enforced BEFORE the search runs, so an over-budget
            # GIF costs nothing rather than being fetched and then discarded.
            html, gif_search_stats = GL.enforce_search_budget(
                html, args.max_search_gifs)

            html, _search_entries = G.embed_gifs_in_html(
                html, giphy_key, repo_root=G.OUTPUT_DIR)
            GL.report(gif_lib_stats, gif_search_stats["kept"])
            _planned, _seeds = GL.count_planned_tier3(
                G.OUTPUT_DIR / "story_plan.json")
            GL.report_tiers(gif_lib_stats, gif_search_stats,
                            args.max_search_gifs, _planned, _seeds)

        print("\n── MEME PIPELINE ───────────────────────────────────")
        try:
            from generate_memes import build_template_map, process_newsletter
            u, p = os.getenv("IMGFLIP_USERNAME"), os.getenv("IMGFLIP_PASSWORD")
            if not u or not p:
                print("  ⚠ IMGFLIP creds not set — meme-placeholders left un-rendered")
            else:
                template_map = build_template_map()

                # Box-count guard (§ meme_box_check). Imgflip returns 200 for a
                # short boxes[] list and renders the leftover panels blank, so
                # process_newsletter would otherwise report success on a meme
                # with an empty punchline. Audit before spending the API call.
                findings, _expected = MB.check_html(html, template_map)
                MB.report(findings, strict=not args.allow_short_memes)
                meme_box_findings = findings
                if not args.allow_short_memes:
                    html, _dropped = MB.strip_short_memes(html, findings)
                    if _dropped:
                        print(f"  ⚠ {_dropped} short meme placeholder(s) removed "
                              f"(rerun with --allow-short-memes to render them anyway)")

                html, _ = process_newsletter(html, template_map, u, p,
                                             repo_root=G.OUTPUT_DIR)
                print("  ✓ Memes embedded")
        except Exception as e:
            print(f"  ✗ Meme pipeline failed: {e}")

        print("\n── HIGHLIGHT RENDER ────────────────────────────────")
        html, n_rendered, n_dropped = render_highlights(html, converted)
        print(f"  ✓ {n_rendered} highlight(s) rendered as local <img>")
        if n_dropped:
            print(f"  ⚠ {n_dropped} placeholder(s) removed — no matching clip")

    # ---- Write output ----------------------------------------------------
    # A partial run must not overwrite the full-chain artifact. Without Pass 7
    # the media is still un-rendered placeholders, and silently clobbering a
    # good rendered issue with that is a nasty surprise mid-iteration.
    suffix = "" if "7" in passes else "_partial"
    if suffix:
        print(f"\n  (partial run — media not rendered; writing *{suffix}.html "
              f"so the full-chain output is preserved)")
    out_path = G.OUTPUT_DIR / f"newsletter_uat_{date.today().isoformat()}{suffix}.html"
    out_path.write_text(G.DRAFT_TEMPLATE.format(content=html), encoding="utf-8")
    print(f"\n  ✓ {out_path.relative_to(G.REPO_ROOT)}")

    run_cost = sum(p["cost"] for p in G.PASS_COSTS) if G.PASS_COSTS else None
    if G.PASS_COSTS:
        G.COST_SUMMARY_PATH.write_text(json.dumps({
            "date": date.today().isoformat(),
            "total": round(run_cost, 6),
            "passes": G.PASS_COSTS,
        }, indent=2), encoding="utf-8")

    report = media_mix_report(
        html,
        update_rejected=update_rejected,
        video_excluded=len(video_tweets),
        clip_results=clip_results,
        run_cost=run_cost,
    )
    (G.OUTPUT_DIR / "media_mix_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Open in a browser: {out_path}")
    print("  §4 review checklist starts with: read it on a phone.")


if __name__ == "__main__":
    main()
