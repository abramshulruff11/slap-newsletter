"""
NBA game flow: one connected fact bundle per game, in three tiers (SLA-147).

fetch_sports_data.py already fetches ESPN's summary for every completed NBA
game and keeps the quarter line score; since SLA-147 it also keeps every made
shot and free throw in order, with the shooter, the assister, the clock and a
running score it tallies itself (`box_score.agate.plays`). Until now only the
box score image read the quarter line, so the writer had the final score and
rebuilt the game from tweets.

This module turns that into game flow, computed in Python through the
sport-neutral `game_flow.py`, never by the model. It reads game_state only (no
database, no network), so it runs at prompt-build time.

Three tiers of detail, because storage is cheap and the prompt is not:

1. **Stored, all of it.** Every basket with its shooter, period, clock and
   score stays in game_state.json.
2. **A summary for every game**: the score after each quarter, the biggest
   leads, lead changes and ties, the basket that put the winner ahead for good
   (and who hit it), the winner's biggest deficit, the finish (final minute of
   a close game, overtime), and the top scorers' points by half.
3. **Full sequences for headline games** (a game a selected story names, and
   every playoff game): the same, plus scorers by quarter, every basket in the
   final minute, team runs (14-0 in the 3rd) and individual streaks (12
   straight points).

- **Checked, or left out.** The baskets must add up to the line score quarter
  by quarter and to the final score, or the whole game's flow is dropped (the
  game stays in YESTERDAY'S GAME RESULTS as a plain line). Each shooter's
  points must also match the box score; if any does not, the game keeps its
  quarter/lead flow and loses every name (`shooters_ok`). Never guessed.
- **ESPN's own running score is not used.** It is stale on some plays; the
  point values and the scoring team are right. See fetch_sports_data.
- **The cap is 6,000 characters** for the whole block. Story and postseason
  games come first, in full where it fits; then every other game at the
  summary size, close games first; a game the cap cut keeps its plain result
  line and the count is printed, never silent.
- **Nothing said twice.** A game the block shows opens with the same result
  (and series) lines YESTERDAY'S GAME RESULTS would print
  (`game_flow.result_lines`) and is left out of that list.
- `claim_facts()` gives claim_validator the same facts as data.
"""
from __future__ import annotations

import re

import game_flow
from history_source import norm, story_text
from nhl_bundle import _cost, _names_in          # one copy of the story matcher and the cost count

BUDGET = 6_000
RUN_MIN = 10                  # a team run worth listing: N unanswered points
STREAK_MIN = 10               # an individual streak worth listing: N straight points
CLOSE = 5                     # "close in the final minute": within this many

HEADER = [
    "## GROUND TRUTH: NBA GAME FLOW",
    "Source: ESPN play-by-play and line score, as of {as_of}. Computed from the baskets, not written "
    "by anyone: the score after each quarter, who led, every lead change and tie, who hit the basket "
    "that won it, top scorers by half, and for the big games the runs and streaks. These are SOURCED. "
    "Any claim about a quarter, a lead, a comeback, a go-ahead or game-winning basket, a run or who "
    "scored when must match this block; never put a basket in a quarter it does not show.",
    "",
]

_NAMES_SUFFIX = ("jr", "sr", "ii", "iii", "iv")


def period_name(p: int) -> str:
    """"Q1".."Q4", "OT", "2OT"."""
    if p <= 4:
        return f"Q{p}"
    n = p - 4
    return "OT" if n == 1 else f"{n}OT"


def _abbr(game: dict, side: str) -> str:
    return game.get(f"{side}_abbr") or game.get(f"{side}_team") or side


def surname(name: str) -> str:
    """Normalized last name ("Jaren Jackson Jr." -> "jackson")."""
    toks = [t for t in norm(name).split() if t not in _NAMES_SUFFIX]
    return toks[-1] if toks else ""


def _display(name: str, dup: set[str]) -> str:
    """How a player reads in a line: the surname, or "J. Williams" when two
    players in the game share it. A suffix stays ("Jackson Jr.")."""
    parts = name.split()
    if len(parts) < 2:
        return name
    first = parts[0]
    rest = " ".join(parts[1:])
    return f"{first[0]}. {rest}" if surname(name) in dup else rest


def _clock(e: dict) -> str:
    """"Q3 4:51"; a sub-minute clock ESPN writes as "30.4" reads "0:30.4"."""
    c = e.get("clock") or ""
    if c and ":" not in c:
        c = "0:" + (c if len(c.split(".")[0]) > 1 else "0" + c)
    return f"{period_name(e['period'])} {c}" if c else period_name(e["period"])


def _what(e: dict) -> str:
    return {3: "three", 1: "free throw"}.get(e.get("pts"), "basket")


# ---------------------------------------------------------------------------
# The facts, from the baskets and the line score
# ---------------------------------------------------------------------------

def timeline(game: dict) -> tuple[list[dict], dict] | None:
    """(baskets as a game_flow timeline, extras) or None when the play log is
    missing or does not add up to the line score and the final score."""
    bs = game.get("box_score") or {}
    plays = (bs.get("agate") or {}).get("plays")
    ls = bs.get("linescore") or {}
    away_p, home_p = ls.get("away_periods") or [], ls.get("home_periods") or []
    if not plays or not away_p or len(away_p) != len(home_p) or len(away_p) < 4:
        return None
    try:
        per = [(int(a), int(h)) for a, h in zip(away_p, home_p)]
    except (TypeError, ValueError):
        return None
    got = [[0, 0] for _ in per]
    last_p = 1
    for e in plays:
        p, side, pts = e.get("period"), e.get("side"), e.get("pts")
        if not isinstance(p, int) or not 1 <= p <= len(per) or p < last_p \
                or side not in ("away", "home") or not isinstance(pts, int) or pts <= 0:
            return None
        got[p - 1][0 if side == "away" else 1] += pts
        last_p = p
    if [tuple(x) for x in got] != per:
        return None
    fa, fh = sum(x[0] for x in per), sum(x[1] for x in per)
    if (game.get("away_score"), game.get("home_score")) != (fa, fh) or fa == fh:
        return None
    return list(plays), {"final": (fa, fh), "played": len(per)}


def _shooters_ok(game: dict, plays: list[dict]) -> bool:
    """Every shooter's points from the play log equal the box score's. Without
    athlete ids in the box score (an old game_state) there is nothing to check
    against and the names are not trusted."""
    bs = game.get("box_score") or {}
    box: dict[str, int] = {}
    for side in ("away", "home"):
        for pl in (bs.get(side) or {}).get("players") or []:
            if pl.get("id") is None:
                return False
            try:
                box[str(pl["id"])] = int((pl.get("stats") or {}).get("PTS", 0))
            except (TypeError, ValueError):
                return False
    mine: dict[str, int] = {}
    for e in plays:
        if not e.get("shooter") or e.get("shooter_id") is None:
            return False
        mine[str(e["shooter_id"])] = mine.get(str(e["shooter_id"]), 0) + e["pts"]
    return bool(mine) and all(box.get(i) == n for i, n in mine.items()) \
        and all(n == 0 or i in mine for i, n in box.items())


def runs_of(events: list[dict]) -> list[dict]:
    """Unanswered runs: maximal stretches of one side scoring with the other
    scoreless. {side, pts, start, end, before} (start/end are events, before the
    score going in)."""
    out: list[dict] = []
    before = (0, 0)
    for e in events:
        if out and out[-1]["side"] == e["side"]:
            out[-1]["pts"] += e["pts"]
            out[-1]["end"] = e
        else:
            out.append({"side": e["side"], "pts": e["pts"], "start": e, "end": e, "before": before})
        before = (e["away"], e["home"])
    return out


def streaks_of(events: list[dict]) -> list[dict]:
    """Individual streaks: a side's consecutive baskets all by one player,
    with the other side's baskets allowed in between. {side, shooter, pts,
    start, end, opp} where opp is the other side's points inside the stretch
    (0 means it was also an unanswered run)."""
    out: list[dict] = []
    for side in ("away", "home"):
        mine = [(i, e) for i, e in enumerate(events) if e["side"] == side]
        cur = None
        for i, e in mine:
            if cur and cur["shooter"] == e["shooter"]:
                cur["pts"] += e["pts"]
                cur["end"], cur["last"] = e, i
            else:
                if cur:
                    out.append(cur)
                cur = {"side": side, "shooter": e["shooter"], "pts": e["pts"],
                       "start": e, "end": e, "first": i, "last": i}
        if cur:
            out.append(cur)
    for s in out:
        s["opp"] = sum(e["pts"] for e in events[s["first"]:s["last"] + 1] if e["side"] != s["side"])
    return out


def best_window(events: list[dict], side: str, want: int, allow: int) -> bool:
    """Is there a stretch where `side` scored at least `want` while the other
    side scored at most `allow`? ("a 10-2 run")"""
    n = len(events)
    for i in range(n):
        mine = theirs = 0
        for j in range(i, n):
            if events[j]["side"] == side:
                mine += events[j]["pts"]
            else:
                theirs += events[j]["pts"]
            if theirs > allow:
                break
            if mine >= want:
                return True
    return False


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
    ev = f["events"]
    f["played"], f["ot"] = ex["played"], ex["played"] - 4
    f["periods"] = [(p, game_flow.score_after(ev, p)) for p in range(1, ex["played"] + 1)]
    f["shooters_ok"] = _shooters_ok(game, plays)
    # Per player: points by period, from the play log.
    pl: dict[str, dict] = {}
    for e in ev:
        who = e.get("shooter") or ""
        d = pl.setdefault(who, {"name": who, "side": e["side"], "pts": 0, "by_period": {}})
        d["pts"] += e["pts"]
        d["by_period"][e["period"]] = d["by_period"].get(e["period"], 0) + e["pts"]
    f["players"] = pl if f["shooters_ok"] else {}
    f["runs"] = runs_of(ev)
    f["streaks"] = streaks_of(ev) if f["shooters_ok"] else []
    # The finish: baskets in the last minute of the last period.
    last = ex["played"]
    fin = [e for e in ev if e["period"] == last and e.get("secs", 999) <= 60]
    f["final_minute"] = fin
    if fin:
        i = ev.index(fin[0])
        before = (ev[i - 1]["away"], ev[i - 1]["home"]) if i else (0, 0)
        f["minute_margin"] = abs(before[0] - before[1])
        f["minute_score"] = before
    else:
        f["minute_margin"], f["minute_score"] = None, None
    f["close_finish"] = bool(fin) and f["minute_margin"] <= CLOSE
    return f


# ---------------------------------------------------------------------------
# One bundle per game
# ---------------------------------------------------------------------------

def score_str(game: dict, away: int, home: int) -> str:
    lead = game_flow.lead_side(away, home)
    if lead is None:
        return f"tied {away}-{home}"
    return f"{_abbr(game, lead)} {max(away, home)}-{min(away, home)}"


def _dup_surnames(f: dict) -> set[str]:
    seen: dict[str, set] = {}
    for e in f["events"]:
        if e.get("shooter"):
            seen.setdefault(surname(e["shooter"]), set()).add(e["shooter"])
    return {k for k, v in seen.items() if len(v) > 1}


def _scorer_lines(game: dict, f: dict, dup: set[str], by_quarter: bool) -> list[str]:
    """Top scorers per side (two in the full tier, one in the summary) with
    points by half, or by quarter in the full tier."""
    if not f["players"]:
        return []
    parts = []
    for side in ("away", "home"):
        mine = sorted((p for p in f["players"].values() if p["side"] == side), key=lambda p: -p["pts"])[:2 if by_quarter else 1]
        for p in mine:
            bp = p["by_period"]
            if by_quarter:
                cut = ", ".join(f"{period_name(k)} {bp[k]}" for k in sorted(bp))
            else:
                first, second = bp.get(1, 0) + bp.get(2, 0), bp.get(3, 0) + bp.get(4, 0)
                ot = sum(v for k, v in bp.items() if k > 4)
                cut = f"{first} first half, {second} second half" + (f", {ot} OT" if ot else "")
            parts.append(f"{_abbr(game, side)} {_display(p['name'], dup)} {p['pts']} ({cut})")
    return ["Top scorers: " + "; ".join(parts) + "."] if parts else []


def bundle(game: dict) -> dict | None:
    """{game_id, result, by_line, flow_line, scorer_line, scorer_line_q,
    seq_lines, drama, post, names} for one completed game, or None when there
    is no trustworthy play log."""
    if not game.get("completed"):
        return None
    f = flow(game)
    if f is None:
        return None
    ab = lambda side: _abbr(game, side)                       # noqa: E731
    W, L = ab(f["winner"]), ab(f["loser"])
    ev = f["events"]
    dup = _dup_surnames(f)
    named = f["shooters_ok"]
    who = lambda e: (_display(e["shooter"], dup) + " ") if named and e.get("shooter") else ""  # noqa: E731

    parts = []
    for p, (a, h) in f["periods"]:
        if p == f["played"] and f["ot"] == 0:
            continue                                          # the last quarter is the final score
        label = "the half" if p == 2 else period_name(p)
        parts.append(f"{'at' if p == 2 else 'after'} {label} {score_str(game, a, h)}")
    by_line = "By quarter: " + "; ".join(parts) + "."

    story: list[str] = []
    first = ev[0]
    if f["wire_to_wire"]:
        story.append(f"{W} led wire to wire (scored first, {_clock(first)})")
    else:
        story.append(f"{ab(first['side'])} scored first ({_clock(first)})")
        lc, ti = f["lead_changes"], f["ties"]
        story.append(f"{lc} lead change{'s' * (lc != 1)}, {ti} tie{'s' * (ti != 1)}")
    ml_w, ml_l = f["max_lead"][f["winner"]], f["max_lead"][f["loser"]]
    leads = [f"{W} {ml_w[0]} ({score_str(game, ml_w[1]['away'], ml_w[1]['home'])}, {_clock(ml_w[1])})"]
    if ml_l[0]:
        leads.append(f"{L} {ml_l[0]} ({score_str(game, ml_l[1]['away'], ml_l[1]['home'])}, {_clock(ml_l[1])})")
    story.append("biggest leads: " + ", ".join(leads))
    d, mom = f["max_deficit"][f["winner"]]
    if d >= 8:
        story.append(f"{W} trailed by as many as {d} ({score_str(game, mom['away'], mom['home'])}, "
                     f"{_clock(mom)}) and won")
    gw = f["for_good"]
    if gw is not None and not f["wire_to_wire"]:
        story.append(f"{W} took the lead for good {max(gw['away'], gw['home'])}-{min(gw['away'], gw['home'])} "
                     f"on {who(gw)}{_what(gw)} ({_clock(gw)})")
    if f["ot"]:
        tied = f["periods"][3][1]
        story.append(f"overtime{'' if f['ot'] == 1 else f' ({f['ot']} extra periods)'}: level {tied[0]}-{tied[1]} after Q4")
    flow_line = "Flow: " + "; ".join(story) + "."

    fin_lines: list[str] = []
    if f["close_finish"]:
        ms = f["minute_score"]
        shown = f["final_minute"]
        fin_lines.append(f"Final minute ({score_str(game, *ms)} going in): " + "; ".join(
            f"{who(e)}{_what(e)} ({ab(e['side'])} {max(e['away'], e['home'])}-{min(e['away'], e['home'])}, {e['clock']})"
            if e["kind"] != "tie" else f"{who(e)}{_what(e)} tied it {e['away']}-{e['home']} ({e['clock']})"
            for e in shown) + ".")
    seq: list[str] = []
    runs = [r for r in f["runs"] if r["pts"] >= RUN_MIN]
    if runs:
        seq.append("Runs: " + "; ".join(
            f"{ab(r['side'])} {r['pts']}-0 ({_clock(r['start'])} to {_clock(r['end'])}, "
            f"{score_str(game, *r['before'])} -> {score_str(game, r['end']['away'], r['end']['home'])})"
            for r in runs) + ".")
    streaks = [s for s in f["streaks"] if s["pts"] >= STREAK_MIN]
    if streaks:
        seq.append("Streaks: " + "; ".join(
            f"{_display(s['shooter'], dup)} {s['pts']} straight {ab(s['side'])} points "
            f"({_clock(s['start'])} to {_clock(s['end'])}" + (f", the other side scored {s['opp']} in between" if s["opp"] else "") + ")"
            for s in streaks) + ".")

    swings = f["lead_changes"] + f["ties"]
    drama = (3 * bool(f["ot"]) + swings // 3 + (d >= 10) * 2 + 2 * f["close_finish"] - 2 * f["wire_to_wire"])
    short_fin = fin_lines[0] if fin_lines else ""
    if short_fin and len(f["final_minute"]) > 4:               # tier 2 keeps the last four baskets
        k = f["final_minute"][-4:]
        short_fin = f"Final minute, last baskets: " + "; ".join(
            f"{who(e)}{_what(e)} ({ab(e['side'])} {max(e['away'], e['home'])}-{min(e['away'], e['home'])}, {e['clock']})" for e in k) + "."
    return {"game_id": game.get("game_id"), "result": game_flow.result_lines(game),
            "by_line": by_line, "flow_line": flow_line,
            "scorer_line": _scorer_lines(game, f, dup, False),
            "scorer_line_q": _scorer_lines(game, f, dup, True),
            "fin_short": [short_fin] if short_fin else [], "fin_full": fin_lines,
            "seq_lines": seq, "drama": drama, "named": named,
            "post": bool(game.get("playoffs")) or int(game.get("season_type") or 2) == 3,
            "names": [game.get("home_team") or "", game.get("away_team") or ""]}


def bundles(game_state: dict) -> list[dict]:
    games = ((((game_state or {}).get("sports") or {}).get("nba")) or {}).get("yesterday_games") or []
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

def _lines_for(b: dict, size: str) -> list[str]:
    if size == "full":
        return [b["by_line"], b["flow_line"]] + b["scorer_line_q"] + b["fin_full"] + b["seq_lines"]
    if size == "summary":
        return [b["by_line"], b["flow_line"]] + b["scorer_line"] + b["fin_short"]
    return [b["flow_line"]]


def render(game_state: dict, story: str | dict | None = None,
           budget: int = BUDGET) -> tuple[list[str], dict[str, str]]:
    """The NBA GAME FLOW part of the GROUND TRUTH block, at most `budget`
    characters. Returns the lines and, per game shown, "full" (everything,
    sequences included), "summary" (quarters, flow, scorers by half, the
    finish) or "flow" (the flow line alone). `story` defaults to what
    attach_story() stored after Pass 1."""
    bs = bundles(game_state)
    if not bs:
        return [], {}
    if story is None:
        story = ((game_state or {}).get("nba_flow") or {}).get("story_text") or ""
    text = story_text(story) if isinstance(story, (dict, list)) else (story or "")
    prio = {b["game_id"]: 1 if b["post"] or (text and _names_in(text, b["names"])) else 2 for b in bs}
    order = sorted(bs, key=lambda b: (prio[b["game_id"]], -b["drama"]))
    head = [HEADER[0], HEADER[1].format(as_of=(game_state or {}).get("as_of_date", "unknown")), ""]
    used = _cost(head, 0) + 80                                   # room for the "not shown" line
    size: dict[str, str] = {}
    ladder = ("full", "summary", "flow")

    def cost(b, s):
        return _cost(b["result"], 2) + _cost(_lines_for(b, s), 4)

    for b in order:                                              # headline games: the richest that fits
        if prio[b["game_id"]] == 1:
            for s in ladder:
                if used + cost(b, s) <= budget:
                    size[b["game_id"]], used = s, used + cost(b, s)
                    break
    for b in order:                                              # every other game: a summary, then a flow line
        if prio[b["game_id"]] == 2:
            for s in ladder[1:]:
                if used + cost(b, s) <= budget:
                    size[b["game_id"]], used = s, used + cost(b, s)
                    break
    for b in order:                                              # room left: upgrade, richest first
        cur = size.get(b["game_id"])
        for s in ladder:
            if cur is None or ladder.index(s) >= ladder.index(cur):
                break
            if prio[b["game_id"]] == 2 and s == "full":
                continue                                         # sequences are for headline games only
            extra = cost(b, s) - cost(b, cur)
            if used + extra <= budget:
                size[b["game_id"]], used = s, used + extra
                break
    if not size:
        return [], {}
    out = list(head)
    for b in order:
        if b["game_id"] not in size:
            continue
        out.extend("  " + x for x in b["result"])
        out.extend("    " + x for x in _lines_for(b, size[b["game_id"]]))
    if len(size) < len(bs):
        out.append(f"  ... {len(bs) - len(size)} more NBA game(s) appear only as results above (budget).")
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
    game_state["nba_flow"], in memory only, so the games the stories cover
    get their sequences from Pass 2 on. Returns one line for the log (the
    block's size, which the live run is checked by). Never raises."""
    try:
        if game_state is None:
            return "nba flow: no game state"
        before = len("\n".join(summary_lines(game_state)))
        game_state.setdefault("nba_flow", {})["story_text"] = story_text(story_plan)
        lines, size = render(game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        (game_state or {}).get("nba_flow", {}).pop("story_text", None)
        return f"nba flow: story order skipped: {type(e).__name__}: {e}"
    total = len(bundles(game_state))
    if not total:
        return "nba flow: no NBA games with a play log yesterday"
    full = sum(1 for s in size.values() if s == "full")
    after = len("\n".join(lines))
    return (f"nba flow: block {before:,} -> {after:,} chars (cap {BUDGET:,}); "
            f"{len(size)} of {total} game(s) shown, {full} in full")


# ---------------------------------------------------------------------------
# The same facts, as data, for Pass 3
# ---------------------------------------------------------------------------

def _series_pairs(game: dict) -> set[tuple[int, int]]:
    """The series scores a "3-2" in the prose could be (before and after this
    game, smaller number first): a recap's "take a 2-1 lead" is the series,
    not the game, and the checker must not read it as a quarter score."""
    s = game.get("series") or {}
    try:
        hw, aw = int(s.get("home_wins", 0)), int(s.get("away_wins", 0))
    except (TypeError, ValueError):
        return set()
    out = {tuple(sorted((hw, aw)))}
    if game.get("winner") == game.get("home_team") and hw:
        out.add(tuple(sorted((hw - 1, aw))))
    elif game.get("winner") == game.get("away_team") and aw:
        out.add(tuple(sorted((hw, aw - 1))))
    return out


def claim_facts(game_state: dict) -> list[dict]:
    """One entry per completed NBA game yesterday whose play log adds up.

      teams       {"away"/"home": {"name", "abbr"}}
      winner      "away" / "home"
      periods     periods played (4 + overtimes)
      scores      per period, every score the game stood at during it: the score
                  entering it, then after each basket (a set of (away, home))
      end_score   {period: (away, home)}, the score at the end of each
      baskets     [{period, side, away, home, pts, shooter, secs}], shooter a
                  normalized surname ("" when the names are not trusted)
      go_ahead    [{side, period, shooter, pts}], ties [(side, period)],
                  tie_baskets [{side, period, shooter, pts}]
      for_good    {side, period, shooter, pts} or None
      max_deficit / max_lead {side: points}, wire_to_wire
      points      {surname: {period: points}} (empty without trusted names)
      sides       {surname: side}
      runs        [{side, pts, period_start, period_end}], every unanswered run
      streaks     [{shooter, side, pts, opp}], every individual streak
      events      the baskets, for "an N-M run" windows
      named       the shooter names matched the box score
      final       the final score, smaller number first"""
    out = []
    games = ((((game_state or {}).get("sports") or {}).get("nba")) or {}).get("yesterday_games") or []
    for g in games:
        if not g.get("completed"):
            continue
        try:
            f = flow(g)
        except Exception:  # noqa: BLE001
            f = None
        if f is None:
            continue
        named = f["shooters_ok"]
        dups = _dup_surnames(f)                       # two "Williams" in one game: neither is attributable
        sn = (lambda e: (surname(e.get("shooter") or "") if surname(e.get("shooter") or "") not in dups else "")) \
            if named else (lambda e: "")              # noqa: E731
        ev = f["events"]
        scores: dict[int, set] = {}
        cur = (0, 0)
        end_score = {}
        for p in range(1, f["played"] + 1):
            scores[p] = {cur}
            for e in ev:
                if e["period"] == p:
                    cur = (e["away"], e["home"])
                    scores[p].add(cur)
            end_score[p] = cur
        points: dict[str, dict] = {}
        sides: dict[str, str] = {}
        if named:
            for e in ev:
                k = sn(e)
                if k:
                    points.setdefault(k, {})
                    points[k][e["period"]] = points[k].get(e["period"], 0) + e["pts"]
                    sides[k] = e["side"]
        gf_ = f["for_good"]
        pick = lambda e: {"side": e["side"], "period": e["period"], "shooter": sn(e), "pts": e["pts"]}  # noqa: E731
        out.append({
            "game_id": g.get("game_id"),
            "teams": {s: {"name": g.get(f"{s}_team") or "", "abbr": g.get(f"{s}_abbr") or ""}
                      for s in ("away", "home")},
            "winner": f["winner"], "periods": f["played"],
            "scores": scores, "end_score": end_score,
            "baskets": [{"period": e["period"], "side": e["side"], "away": e["away"], "home": e["home"],
                         "pts": e["pts"], "shooter": sn(e), "secs": e.get("secs")} for e in ev],
            "go_ahead": [pick(e) for e in ev if e["kind"] == "go_ahead"],
            "ties": [(e["side"], e["period"]) for e in ev if e["kind"] == "tie"],
            "tie_baskets": [pick(e) for e in ev if e["kind"] == "tie"],
            "for_good": pick(gf_) if gf_ else None,
            "max_deficit": {s: f["max_deficit"][s][0] for s in ("away", "home")},
            "max_lead": {s: f["max_lead"][s][0] for s in ("away", "home")},
            "wire_to_wire": f["wire_to_wire"],
            "points": points, "sides": sides, "named": named,
            "runs": [{"side": r["side"], "pts": r["pts"], "period_start": r["start"]["period"],
                      "period_end": r["end"]["period"]} for r in f["runs"]],
            "streaks": [{"shooter": surname(s["shooter"]), "side": s["side"], "pts": s["pts"], "opp": s["opp"]}
                        for s in f["streaks"] if surname(s["shooter"]) not in dups],
            "events": [{"side": e["side"], "pts": e["pts"]} for e in ev],
            "final": tuple(sorted((int(g.get("away_score") or 0), int(g.get("home_score") or 0)))),
            "series_pairs": _series_pairs(g),
        })
    return out
