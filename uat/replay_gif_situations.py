"""
SLA-138 — free replay: does situation-aware selection pick GIFs that fit the moment?

No API calls, no network. Reads only files already in the repo:

  archive/<date>/newsletter_substack.html   every library GIF that shipped
  prompts/gif_taxonomy.DRAFT.json           SLA-133 step 5: each placement's
                                            situation, classified from the
                                            paragraph above it
  prompts/gif_library.DRAFT.json            the SLA-133 tags (SLA-137)

Each placement context ("2026-09-06: ...tail of the paragraph") is found in
that day's issue and tied to the first GIF after it. The writer's category for
that GIF is the library category the shipped GIF lives in. Then every issue
09-02..10-14 is re-picked in order, with its own cooldown history, twice:

  BEFORE  pick_entry(category)                  the mood-only pick, as today
  AFTER   pick_entry(category, subcategory=s)   SLA-138

A placement is a HIT when the picked GIF is tagged with the placement's
situation. Picks are random within a pool, so each arm is run over many seeds
and averaged. The GIF that actually shipped is scored too, as a third column.

WHAT THIS CANNOT SHOW: it hands the AFTER arm the situation the paragraph was
classified into, i.e. it assumes Pass 1 / Pass 2 ask for the right situation
every time. Whether they will is only measurable with a real run.

Usage:
    python -X utf8 uat/replay_gif_situations.py            # report
    python -X utf8 uat/replay_gif_situations.py --seeds 200 --json out.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import gif_library_select as GL  # noqa: E402

GIPHY_ID = re.compile(r"giphy\.com/media/(?:v1\.[^/]+/)?([A-Za-z0-9]+)/")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _linear(issue_html: str) -> tuple[str, list]:
    """The issue as one normalized string, plus (offset, giphy_id) per GIF."""
    soup = BeautifulSoup(issue_html, "html.parser")
    parts, gifs, length = [], [], 0
    for el in soup.descendants:
        if isinstance(el, NavigableString):
            t = _norm(str(el))
            parts.append(t)
            length += len(t)
        elif el.name == "img":
            m = GIPHY_ID.search(el.get("src", ""))
            if m:
                gifs.append((length, m.group(1)))
    return "".join(parts), gifs


def build_placements(library: dict, taxonomy: dict, archive: Path) -> tuple[list, list]:
    """
    Returns (placements, unmatched). placements: one dict per shipped LIBRARY
    GIF in document order per date — {date, gif, category, situation} — with
    situation None where SLA-133 put the paragraph outside every situation.
    """
    id2cat = {g["id"]: c for c, v in library["categories"].items() for g in v["gifs"]}
    contexts = []
    for s in taxonomy["subcategories"]:
        for p in s.get("placement_contexts", []):
            d, text = p.split(": ", 1)
            contexts.append((d, s["id"], _norm(text)))

    dates = sorted({d for d, _, _ in contexts})
    issues = {}
    for d in dates:
        f = archive / d / "newsletter_substack.html"
        issues[d] = _linear(f.read_text(encoding="utf-8"))

    situation_at: dict = {}            # (date, gif index) -> situation
    unmatched = []
    for d, sid, text in contexts:
        txt, gifs = issues[d]
        probe = text[-50:]
        pos = txt.find(probe) if len(probe) >= 10 else -1
        if pos < 0:
            unmatched.append((d, sid))
            continue
        end = pos + len(probe)
        # First GIF after the paragraph that no earlier context claimed.
        for i, (off, _gid) in enumerate(gifs):
            if off >= end and (d, i) not in situation_at:
                situation_at[(d, i)] = sid
                break
        else:
            unmatched.append((d, sid))

    placements = []
    for d in dates:
        for i, (_off, gid) in enumerate(issues[d][1]):
            if gid not in id2cat:
                continue                    # live-search (Tier 3) GIF
            placements.append({"date": d, "gif": gid, "category": id2cat[gid],
                               "situation": situation_at.get((d, i))})
    return placements, unmatched


def tagged_with(entry: dict | None, situation: str) -> str | None:
    """'strong' / 'partial' when the entry carries the situation, else None."""
    for sub in (entry or {}).get("subcategories") or []:
        if sub.get("id") == situation:
            return sub.get("fit")
    return None


def simulate(library: dict, placements: list, situation_aware: bool, seed: int) -> list:
    """Re-pick every placement in date order with a self-built history."""
    random.seed(seed)
    history: list = []
    picks = []
    by_date = defaultdict(list)
    for p in placements:
        by_date[p["date"]].append(p)
    for d in sorted(by_date):
        today = date.fromisoformat(d)
        used: set = set()
        for p in by_date[d]:
            sub = p["situation"] if situation_aware else None
            r = GL.choose_entry(library, p["category"], history, used,
                                subcategory=sub, today=today)
            e = r["entry"]
            picks.append(e)
            if e:
                used.add(e["id"])
                history.append({"date": d, "library_id": e["id"]})
    return picks


def run(seeds: int) -> dict:
    library = GL.load_library()
    taxonomy = GL.load_taxonomy()
    placements, unmatched = build_placements(library, taxonomy, REPO / "archive")
    by_id = {g["id"]: g for c in library["categories"].values() for g in c["gifs"]}

    scored = [i for i, p in enumerate(placements) if p["situation"]]
    sit_supply = Counter()
    for c in library["categories"].values():
        for g in c["gifs"]:
            if g.get("status") == "verified":
                for s in g.get("subcategories") or []:
                    sit_supply[s["id"]] += 1

    shipped_hits = sum(1 for i in scored
                       if tagged_with(by_id.get(placements[i]["gif"]), placements[i]["situation"]))

    def arm(aware: bool):
        hits, strong = Counter(), Counter()
        for k in range(seeds):
            picks = simulate(library, placements, aware, seed=k)
            for i in scored:
                fit = tagged_with(picks[i], placements[i]["situation"])
                if fit:
                    hits[i] += 1
                if fit == "strong":
                    strong[i] += 1
        return ({i: hits[i] / seeds for i in scored},
                {i: strong[i] / seeds for i in scored})

    before, before_strong = arm(False)
    after, after_strong = arm(True)

    per_sit = defaultdict(lambda: {"placements": 0, "before": 0.0, "after": 0.0,
                                   "shipped": 0, "supply": 0})
    for i in scored:
        s = placements[i]["situation"]
        row = per_sit[s]
        row["placements"] += 1
        row["before"] += before[i]
        row["after"] += after[i]
        row["shipped"] += 1 if tagged_with(by_id.get(placements[i]["gif"]), s) else 0
        row["supply"] = sit_supply[s]

    n = len(scored)
    return {
        "library_placements": len(placements),
        "scored_placements": n,
        "unmatched_contexts": unmatched,
        "seeds": seeds,
        "hit_rate": {
            "shipped": shipped_hits / n,
            "before": sum(before.values()) / n,
            "after": sum(after.values()) / n,
            "before_strong": sum(before_strong.values()) / n,
            "after_strong": sum(after_strong.values()) / n,
            # The most ANY selection could reach with today's library: every
            # placement whose situation has at least one tagged GIF.
            "ceiling": sum(1 for i in scored if sit_supply[placements[i]["situation"]]) / n,
        },
        "by_situation": dict(per_sit),
    }


def report(res: dict) -> None:
    hr = res["hit_rate"]
    n = res["scored_placements"]
    print(f"Library GIFs shipped 09-02..10-14: {res['library_placements']}; "
          f"classified into a situation: {n}"
          + (f"; contexts not found: {len(res['unmatched_contexts'])}"
             if res["unmatched_contexts"] else ""))
    print(f"Averaged over {res['seeds']} seeds.\n")
    print("Situation hit rate (picked GIF is tagged with the paragraph's situation):")
    print(f"  shipped (what actually ran)     {hr['shipped']:6.1%}")
    print(f"  BEFORE  mood-only pick          {hr['before']:6.1%}   "
          f"(strong fit only {hr['before_strong']:.1%})")
    print(f"  AFTER   situation-aware pick    {hr['after']:6.1%}   "
          f"(strong fit only {hr['after_strong']:.1%})")
    print(f"  ceiling (situation has any GIF)  {hr['ceiling']:6.1%}   "
          f"(the rest is cooldown on thin situations)\n")

    rows = sorted(res["by_situation"].items(),
                  key=lambda kv: -(kv[1]["placements"] - kv[1]["after"]))
    print(f"{'situation':32s} {'placed':>6s} {'GIFs':>4s} {'before':>7s} {'after':>7s} "
          f"{'missed':>7s}")
    for sid, r in rows:
        missed = r["placements"] - r["after"]
        print(f"{sid:32s} {r['placements']:6d} {r['supply']:4d} "
              f"{r['before'] / r['placements']:7.0%} {r['after'] / r['placements']:7.0%} "
              f"{missed:7.1f}")
    empty = [sid for sid, r in rows if r["supply"] == 0]
    lost = sum(r["placements"] for sid, r in rows if r["supply"] == 0)
    print(f"\nNo GIF tagged at all ({len(empty)}): {', '.join(empty)} "
          f"— {lost} of {n} placements, a miss whatever selection does (SLA-134).")
    print("\nNOT SHOWN: whether Pass 1 / Pass 2 will name the right situation. "
          "AFTER assumes they always do.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--json", help="also write the result here")
    args = ap.parse_args()
    res = run(args.seeds)
    report(res)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
