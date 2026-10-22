"""
Men's college basketball game flow: one connected fact bundle per game (SLA-146).

fetch_sports_data.py fetches ESPN's summary for yesterday's games (ranked
games first, capped at MAX_FLOW_FETCHES) and keeps the score by half (and
overtime) plus every scoring play with the running score, under
`game["game_flow"]`. Until now nothing read it: college basketball had no box
score at all, so the writer had the final score and rebuilt the game from
tweets.

This module turns that into game flow, computed in Python through the
sport-neutral `game_flow.py` (SLA-148), never by the model: the score at the
half, who scored first, lead changes and ties, the winner's biggest deficit
and biggest lead, when the winner took the lead for good, wire to wire,
overtime, and how close the finish was. It reads game_state only (no
database, no network), so it runs at prompt-build time.

- **Checked, or left out.** The scoring plays must add up to the line score
  half by half and to the final score, or the whole game's flow is dropped
  (the game stays in YESTERDAY'S GAME RESULTS as a plain line). A game past
  the fetch cap, or whose summary had no plays, has no bundle either. Never
  filled in or guessed.
- **The block is lean on purpose.** A game has 60-90 scoring plays; only the
  turning points are shown (the first basket, the lead changes' count, the
  biggest swing, the go-ahead for good, the last basket), never every shot.
- **The cap is 4,000 characters** for the whole block: NCAA tournament games
  and games a story names get the full bundle, then ranked games, then the
  rest as one flow line, close games first. A game the cap cut keeps its
  plain result line; the count is printed, never silent.
- **Nothing said twice.** A game the block shows opens with the same result
  lines YESTERDAY'S GAME RESULTS would print (`game_flow.result_lines`) and
  is left out of that list.
- **No database facts.** slap-sports-db holds NCAAMB champions only (SLA-81
  adds games, records and polls), so records and "first since" facts are
  unavailable all season. Ranks are ESPN's `curatedRank`.
- `claim_facts()` gives claim_validator the same facts as data.
"""
from __future__ import annotations

import re

import game_flow
from history_source import norm, story_text

BUDGET = 4_000
BIG_DEFICIT = 8                 # a deficit worth printing
CLOSE_MARGIN = 3                # "decided by" is only said for a finish this close

HEADER = [
    "## GROUND TRUTH: MEN'S COLLEGE BASKETBALL GAME FLOW",
    "Source: ESPN play-by-play and half scores, as of {as_of}. Computed from the scoring plays, not "
    "written by anyone: the score at the half, who led, how often the lead changed, the biggest "
    "deficit, when the winner took the lead for good, and how it ended. These are SOURCED. Any claim "
    "about the half, a lead, a comeback, a go-ahead basket or a last-second finish must match this "
    "block. Only some games are listed (ranked and tournament games first); a game not here has no "
    "verified flow, so say only what its final score and a tweet support.",
    "",
]


def period_name(p: int) -> str:
    """"1st half" / "2nd half" / "OT" / "2OT"."""
    if p <= 2:
        return {1: "1st half", 2: "2nd half"}[p]
    n = p - 2
    return "OT" if n == 1 else f"{n}OT"


def clock_seconds(clock: str) -> int | None:
    """Seconds left from ESPN's "19:22", "0:03" or "45.2"; None if unreadable."""
    c = (clock or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?:\.\d+)?", c)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})(?:\.\d+)?", c)
    return int(m.group(1)) if m else None


def say_clock(clock: str) -> str:
    """"2:12 left" / "0:03 left" as ESPN wrote it."""
    return f"{clock} left" if clock else ""


def _abbr(game: dict, side: str) -> str:
    return game.get(f"{side}_abbr") or game.get(f"{side}_team") or side


def _rank(game: dict, side: str) -> str:
    r = game.get(f"{side}_rank")
    return f"No. {r} " if r else ""


# ---------------------------------------------------------------------------
# The facts, from the scoring plays and the half scores
# ---------------------------------------------------------------------------

def timeline(game: dict) -> tuple[list[dict], dict] | None:
    """(scoring plays as a game_flow timeline, extras) or None when the play
    log is missing or does not add up to the half scores and the final."""
    gf_ = game.get("game_flow") or {}
    plays = gf_.get("plays")
    away_p, home_p = gf_.get("away_periods") or [], gf_.get("home_periods") or []
    if plays is None or not away_p or len(away_p) != len(home_p) or len(away_p) < 2:
        return None
    try:
        per = [(int(a), int(h)) for a, h in zip(away_p, home_p)]
    except (TypeError, ValueError):
        return None
    played = len(per)
    got = [[0, 0] for _ in range(played)]
    pa = ph = 0
    for p in plays:
        n = p.get("period")
        if not isinstance(n, int) or not 1 <= n <= played:
            return None
        da, dh = p["away"] - pa, p["home"] - ph
        if da < 0 or dh < 0 or (da and dh) or not (da or dh):
            return None
        got[n - 1][0] += da
        got[n - 1][1] += dh
        pa, ph = p["away"], p["home"]
    if [tuple(x) for x in got] != per:
        return None
    fa, fh = sum(x[0] for x in per), sum(x[1] for x in per)
    if (game.get("away_score"), game.get("home_score")) != (fa, fh):
        return None
    return list(plays), {"final": (fa, fh), "played": played}


def flow(game: dict) -> dict | None:
    """The connected facts for one completed game, or None when the play log
    can't be trusted."""
    tl = timeline(game)
    if tl is None:
        return None
    plays, ex = tl
    f = game_flow.build(plays, ex["final"])
    if f is None or f["winner"] is None:
        return None
    f["played"] = ex["played"]
    f["ot"] = max(0, ex["played"] - 2)
    f["periods"] = [(p, game_flow.score_after(f["events"], p)) for p in range(1, ex["played"] + 1)]
    return f


# ---------------------------------------------------------------------------
# One bundle per game
# ---------------------------------------------------------------------------

def score_str(game: dict, away: int, home: int) -> str:
    lead = game_flow.lead_side(away, home)
    if lead is None:
        return f"tied {away}-{home}"
    return f"{_abbr(game, lead)} {max(away, home)}-{min(away, home)}"


def bundle(game: dict) -> dict | None:
    """{game_id, result, by_line, flow_line, drama, post, ranked, names} for
    one completed game, or None when there is no trustworthy play log."""
    if not game.get("completed"):
        return None
    f = flow(game)
    if f is None:
        return None
    ab = lambda side: _abbr(game, side)                       # noqa: E731
    W, L = ab(f["winner"]), ab(f["loser"])
    pn = period_name
    ev = f["events"]
    sc = lambda e: f"{max(e['away'], e['home'])}-{min(e['away'], e['home'])}"   # noqa: E731
    at = lambda e: f"{pn(e['period'])}, {say_clock(e['clock'])}" if e.get("clock") else pn(e["period"])  # noqa: E731

    half = f["periods"][0][1]
    by = [f"at halftime {score_str(game, *half)}"]
    by += [f"after {pn(p)} {score_str(game, a, h)}" for p, (a, h) in f["periods"][2:-1]]
    by_line = "By half: " + "; ".join(by) + (f"; final {score_str(game, *f['periods'][-1][1])}"
                                            if f["ot"] else "") + "."

    story: list[str] = []
    first = ev[0]
    story.append(f"{ab(first['side'])} scored first ({say_clock(first['clock'])}, {sc(first)})"
                 if first.get("clock") else f"{ab(first['side'])} scored first")
    if f["wire_to_wire"]:
        story.append(f"{W} led wire to wire (never trailed or tied after the first basket)")
    else:
        story.append(f"{f['lead_changes']} lead change(s), tied {f['ties']} time(s)")
        d, mom = f["max_deficit"][f["winner"]]
        if d >= BIG_DEFICIT:
            story.append(f"{W} trailed by as many as {d} ({score_str(game, mom['away'], mom['home'])}, "
                         f"{at(mom)})")
    ld, lmom = f["max_lead"][f["winner"]]
    if ld >= 15:
        story.append(f"{W} led by as many as {ld} ({at(lmom)})")
    if f["ot"]:
        story.append("went to overtime" if f["ot"] == 1 else f"went to {f['ot']} overtimes")
    fg = f["for_good"]
    if fg is not None and not f["wire_to_wire"]:
        story.append(f"{W} took the lead for good {sc(fg)} ({at(fg)})")
    margin = abs(game["home_score"] - game["away_score"])
    last = ev[-1]
    close = margin <= CLOSE_MARGIN
    if close:
        story.append(f"decided by {margin}; last basket {ab(last['side'])} ({at(last)}, {sc(last)})")
    flow_line = "Flow: " + "; ".join(story) + "."
    swings = f["lead_changes"] + f["ties"]
    drama = (2 * bool(f["ot"]) + swings // 3 + (f["max_deficit"][f["winner"]][0] >= 10)
             + close - f["wire_to_wire"])
    ranked = bool(game.get("home_rank") or game.get("away_rank"))
    return {"game_id": game.get("game_id"), "result": game_flow.result_lines(game),
            "by_line": by_line, "flow_line": flow_line, "drama": drama,
            "post": int(game.get("season_type") or 2) == 3, "ranked": ranked,
            "names": [game.get("home_team") or "", game.get("away_team") or ""],
            "ranks": f"{_rank(game, 'away')}{ab('away')} at {_rank(game, 'home')}{ab('home')}"}


def bundles(game_state: dict) -> list[dict]:
    games = ((((game_state or {}).get("sports") or {}).get("ncaamb")) or {}).get("yesterday_games") or []
    out = []
    for g in games:
        try:
            b = bundle(g)
        except Exception:  # noqa: BLE001 - one odd payload costs one bundle, never the prompt
            b = None
        if b:
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# Rendering, under the budget
# ---------------------------------------------------------------------------

def _names_in(text: str, names: list[str]) -> bool:
    """True when the story text names one of the teams: the full name, the
    school (the name without its nickname) or the nickname."""
    t = " " + norm(text) + " "
    for n in names:
        toks = norm(n).split()
        if not toks:
            continue
        for cand in (" ".join(toks), " ".join(toks[:-1]), " ".join(toks[:-2]), " ".join(toks[-2:])):
            if cand and f" {cand} " in t:
                return True
    return False


def _cost(lines: list[str], indent: int) -> int:
    return sum(len(x) + indent + 1 for x in lines)


def render(game_state: dict, story: str | dict | None = None,
           budget: int = BUDGET) -> tuple[list[str], dict[str, str]]:
    """The MEN'S COLLEGE BASKETBALL GAME FLOW part of the GROUND TRUTH block,
    at most `budget` characters. Returns the lines and, per game shown,
    "full" (score by half and the flow) or "flow" (the flow line alone).
    `story` defaults to what attach_story() stored after Pass 1."""
    bs = bundles(game_state)
    if not bs:
        return [], {}
    if story is None:
        story = ((game_state or {}).get("ncaamb_flow") or {}).get("story_text") or ""
    text = story_text(story) if isinstance(story, (dict, list)) else (story or "")
    prio = {b["game_id"]: 1 if b["post"] or (text and _names_in(text, b["names"])) else
            2 if b["ranked"] else 3 for b in bs}
    order = sorted(bs, key=lambda b: (prio[b["game_id"]], -b["drama"]))
    head = [HEADER[0], HEADER[1].format(as_of=(game_state or {}).get("as_of_date", "unknown")), ""]
    used = _cost(head, 0) + 90                                   # room for the "not shown" line
    size: dict[str, str] = {}

    def lines_for(b, full):
        return [b["ranks"] + ".", b["by_line"], b["flow_line"]] if full else [b["flow_line"]]

    def cost(b, full):
        return _cost(b["result"], 2) + _cost(lines_for(b, full), 4)

    for b in order:
        if prio[b["game_id"]] == 1:
            for s in ("full", "flow"):
                if used + cost(b, s == "full") <= budget:
                    size[b["game_id"]], used = s, used + cost(b, s == "full")
                    break
    for b in order:
        if prio[b["game_id"]] > 1 and used + cost(b, False) <= budget:
            size[b["game_id"]], used = "flow", used + cost(b, False)
    for b in order:                                              # room left: upgrade
        if size.get(b["game_id"]) == "flow":
            extra = cost(b, True) - cost(b, False)
            if used + extra <= budget:
                size[b["game_id"]], used = "full", used + extra
    if not size:
        return [], {}
    out = list(head)
    for b in order:
        if b["game_id"] not in size:
            continue
        out.extend("  " + x for x in b["result"])
        out.extend("    " + x for x in lines_for(b, size[b["game_id"]] == "full"))
    if len(size) < len(bs):
        out.append(f"  ... {len(bs) - len(size)} more game(s) with a verified flow appear only as results (budget).")
    out.append("")
    return out, size


def summary_lines(game_state: dict, story: str | dict | None = None, budget: int = BUDGET) -> list[str]:
    return render(game_state, story, budget)[0]


def shown(game_state: dict) -> set[str]:
    """The game ids the block, as rendered right now, carries: they are left
    out of YESTERDAY'S GAME RESULTS (the block opens with the same lines)."""
    return set(render(game_state)[1])


def attach_story(game_state: dict, story_plan) -> str:
    """Called by both runners after Pass 1. Stores the plan's story text in
    game_state["ncaamb_flow"], in memory only, so the games the stories cover
    are shown in full from Pass 2 on. Returns one line for the log. Never
    raises."""
    try:
        if game_state is None:
            return "ncaamb flow: no game state"
        before = len("\n".join(summary_lines(game_state)))
        game_state.setdefault("ncaamb_flow", {})["story_text"] = story_text(story_plan)
        lines, size = render(game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        (game_state or {}).get("ncaamb_flow", {}).pop("story_text", None)
        return f"ncaamb flow: story order skipped: {type(e).__name__}: {e}"
    total = len(bundles(game_state))
    if not total:
        return "ncaamb flow: no college basketball games with a play log yesterday"
    full = sum(1 for s in size.values() if s == "full")
    after = len("\n".join(lines))
    return (f"ncaamb flow: block {before:,} -> {after:,} chars (cap {BUDGET:,}); "
            f"{len(size)} of {total} game(s) shown, {full} in full")


# ---------------------------------------------------------------------------
# The same facts, as data, for Pass 3
# ---------------------------------------------------------------------------

def claim_facts(game_state: dict) -> list[dict]:
    """One entry per completed game yesterday whose play log adds up.

      teams     {"away"/"home": {"name", "abbr"}}
      winner    "away" / "home"
      periods   periods played (2 halves + overtimes)
      scores    per period, every score the game stood at during it: the
                score entering it, then after each basket (a set of (away, home))
      half      the score at halftime (away, home)
      go_ahead  [(side, period)], ties [(side, period)], for_good (side, period)
      max_deficit {side: points}, wire_to_wire, lead_changes
      final     (low, high) of the final score, sorted"""
    out = []
    games = ((((game_state or {}).get("sports") or {}).get("ncaamb")) or {}).get("yesterday_games") or []
    for g in games:
        if not g.get("completed"):
            continue
        try:
            f = flow(g)
        except Exception:  # noqa: BLE001
            f = None
        if f is None:
            continue
        scores: dict[int, set] = {}
        cur = (0, 0)
        for p in range(1, f["played"] + 1):
            scores[p] = {cur}
            for e in f["events"]:
                if e["period"] == p:
                    cur = (e["away"], e["home"])
                    scores[p].add(cur)
        out.append({
            "game_id": g.get("game_id"),
            "teams": {s: {"name": g.get(f"{s}_team") or "", "abbr": g.get(f"{s}_abbr") or ""}
                      for s in ("away", "home")},
            "winner": f["winner"], "periods": f["played"], "scores": scores,
            "half": f["periods"][0][1],
            "go_ahead": [(e["side"], e["period"]) for e in f["events"] if e["kind"] == "go_ahead"],
            "ties": [(e["side"], e["period"]) for e in f["events"] if e["kind"] == "tie"],
            "for_good": (f["for_good"]["side"], f["for_good"]["period"]) if f["for_good"] else None,
            "max_deficit": {s: f["max_deficit"][s][0] for s in ("away", "home")},
            "wire_to_wire": f["wire_to_wire"], "lead_changes": f["lead_changes"],
            "final": tuple(sorted((int(g.get("away_score") or 0), int(g.get("home_score") or 0)))),
        })
    return out
