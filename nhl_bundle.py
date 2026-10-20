"""
NHL game flow: one connected fact bundle per game (SLA-148).

fetch_sports_data.py already fetches ESPN's summary for every completed NHL
game and keeps the period line score; since SLA-148 it also keeps every goal
in order with the running score (`box_score.agate.goals`). Until now only the
box score image read any of it, so the writer had the final score and rebuilt
the game from tweets.

This module turns that into game flow, computed in Python through the
sport-neutral `game_flow.py`, never by the model: the score after each
period, who scored first, every lead change and tie, the goal that put the
winner ahead for good, the winner's biggest deficit, wire to wire, shutouts,
overtime and the shootout, and each goal. It reads game_state only (no
database, no network), so it runs at prompt-build time.

- **Checked, or left out.** The goals must add up to the line score period by
  period and to the final score, or the whole game's flow is dropped (the game
  stays in YESTERDAY'S GAME RESULTS as a plain line). A game that isn't final
  has no box score and so no bundle. Never filled in or guessed.
- **Shootouts are not goals.** ESPN restarts the running score for a shootout
  and lists it as a fifth "period"; the fetch drops those attempts. A game
  decided that way reads "level N-N after overtime; X won the shootout" and
  has no game-winning goal.
- **The cap is 3,500 characters** for the whole block: postseason games and
  games a story names in full (score by period, flow, every goal), every other
  game with its flow line as many as fit, close games first; then any room left
  upgrades games to full. A game the cap cut keeps its plain result line; the
  count is printed, never silent.
- **Nothing said twice.** A game the block shows opens with the same result
  (and series) lines YESTERDAY'S GAME RESULTS would print
  (`game_flow.result_lines`) and is left out of that list.
- `claim_facts()` gives claim_validator the same facts as data.
"""
from __future__ import annotations

import re

import game_flow
from history_source import norm, story_text

BUDGET = 3_500

HEADER = [
    "## GROUND TRUTH: NHL GAME FLOW",
    "Source: ESPN goal log and line score, as of {as_of}. Computed from the goals, not written by "
    "anyone: the score after each period, who led, every lead change and tie, and each goal. These "
    "are SOURCED. Any claim about a period, a lead, a comeback, a game-winning goal or who scored "
    "when must match this block; never put a goal in a period it does not show. A shootout is not "
    "a goal: the shootout winner has no game-winning goal.",
    "",
]

_ORD = {1: "1st", 2: "2nd", 3: "3rd"}
_SCORER_RE = re.compile(r"^\s*(.+?)\s+Goal\b")
_ASSIST_RE = re.compile(r"assists?:\s*(.+)$", re.IGNORECASE)


def period_name(p: int, post: bool = False) -> str:
    """"1st" / "2nd" / "3rd" / "OT" / (playoffs) "2OT"."""
    if p <= 3:
        return _ORD[p]
    n = p - 3
    return "OT" if n == 1 else f"{n}OT"


def _abbr(game: dict, side: str) -> str:
    return game.get(f"{side}_abbr") or game.get(f"{side}_team") or side


def _surname(name: str) -> str:
    toks = [t for t in norm(name).split() if t not in ("jr", "sr", "ii", "iii", "iv")]
    return toks[-1] if toks else ""


def scorer(text: str) -> str:
    """The scorer as ESPN wrote the name, from "Danila Yurov Goal (4) Wrist Shot, ...",
    "" when the text has no goal shape."""
    m = _SCORER_RE.match(text or "")
    return m.group(1).strip() if m else ""


def assists(text: str) -> list[str]:
    """Assisting players' names as ESPN wrote them, season totals removed."""
    m = _ASSIST_RE.search(text or "")
    if not m:
        return []
    return [re.sub(r"\s*\(\d+\)\s*$", "", x).strip() for x in m.group(1).split(",") if x.strip()]


def _display_name(name: str) -> str:
    """Everything after the first name ("Nikolaj Ehlers" -> "Ehlers",
    "Pierre-Luc Dubois" -> "Dubois"), as it reads in a goal line."""
    parts = name.split()
    return " ".join(parts[1:]) if len(parts) > 1 else name


# ---------------------------------------------------------------------------
# The facts, from the goal log and the line score
# ---------------------------------------------------------------------------

def timeline(game: dict) -> tuple[list[dict], dict] | None:
    """(goals as a game_flow timeline, extras) or None when the goal log is
    missing or does not add up to the line score and the final score."""
    bs = game.get("box_score") or {}
    agate = bs.get("agate") or {}
    ls = bs.get("linescore") or {}
    goals = agate.get("goals")
    away_p, home_p = ls.get("away_periods") or [], ls.get("home_periods") or []
    if goals is None or not away_p or len(away_p) != len(home_p):
        return None
    shootout = bool(agate.get("shootout"))
    played = len(away_p) - (1 if shootout else 0)       # periods of real play
    if played < 3:
        return None
    try:
        per = [(int(a), int(h)) for a, h in zip(away_p[:played], home_p[:played])]
    except (TypeError, ValueError):
        return None
    # Every goal must sit in a period that was played, and the goals of each
    # period must add up to that period's line score, side by side.
    got = [[0, 0] for _ in range(played)]
    pa = ph = 0
    for g in goals:
        p = g.get("period")
        if not isinstance(p, int) or not 1 <= p <= played:
            return None
        da, dh = g["away"] - pa, g["home"] - ph
        if da < 0 or dh < 0 or (da and dh) or not (da or dh):
            return None
        got[p - 1][0] += da
        got[p - 1][1] += dh
        pa, ph = g["away"], g["home"]
    if [tuple(x) for x in got] != per:
        return None
    fa, fh = sum(x[0] for x in per), sum(x[1] for x in per)
    winner = None
    if shootout:
        if fa != fh:
            return None
        win_name = game.get("winner")
        winner = ("home" if win_name == game.get("home_team") else
                  "away" if win_name == game.get("away_team") else None)
        if winner is None or (game.get("away_score"), game.get("home_score")) != (
                fa + (winner == "away"), fh + (winner == "home")):
            return None
    elif (game.get("away_score"), game.get("home_score")) != (fa, fh):
        return None
    return list(goals), {"final": (fa, fh), "winner": winner, "shootout": shootout, "played": played}


def flow(game: dict) -> dict | None:
    """The connected facts for one completed game, or None when the goal log
    can't be trusted."""
    tl = timeline(game)
    if tl is None:
        return None
    goals, ex = tl
    f = game_flow.build(goals, ex["final"], ex["winner"])
    if f is None or f["winner"] is None:
        return None
    f["shootout"], f["played"] = ex["shootout"], ex["played"]
    f["ot"] = max(0, ex["played"] - 3)
    f["periods"] = [(p, game_flow.score_after(f["events"], p)) for p in range(1, ex["played"] + 1)]
    f["final_regulation"] = ex["final"]
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
    """{game_id, result, by_line, flow_line, goal_lines, drama, post, names}
    for one completed game, or None when there is no trustworthy goal log."""
    if not game.get("completed"):
        return None
    f = flow(game)
    if f is None:
        return None
    ab = lambda side: _abbr(game, side)                       # noqa: E731
    W = ab(f["winner"])
    pn = lambda p: period_name(p)                             # noqa: E731
    by = "; ".join(f"after {pn(p)} {score_str(game, a, h)}" for p, (a, h) in f["periods"])
    by_line = f"By period: {by}."
    story: list[str] = []
    ev = f["events"]
    at = lambda e: f"{pn(e['period'])}, {e['clock']}" if e.get("clock") else pn(e["period"])  # noqa: E731
    if not ev:
        story.append("scoreless through regulation and overtime")
    elif f["shootout"]:
        e0 = ev[0] if ev else None
        if e0 is not None:
            story.append(f"{ab(e0['side'])} scored first ({at(e0)}, {max(e0['away'], e0['home'])}-{min(e0['away'], e0['home'])})")
        story.append(f"level {f['final_regulation'][0]}-{f['final_regulation'][1]} after "
                     f"{'overtime' if f['ot'] else 'regulation'}; {W} won the shootout (no game-winning goal)")
    else:
        if f["wire_to_wire"]:
            story.append(f"{W} led wire to wire (scored first, {at(ev[0])})")
        for e in ev:
            if f["wire_to_wire"]:
                break
            who, sc = ab(e["side"]), f"{max(e['away'], e['home'])}-{min(e['away'], e['home'])}"
            if e["kind"] == "go_ahead" and e["first"]:
                story.append(f"{who} scored first ({at(e)}, {sc})")
            elif e["kind"] == "go_ahead":
                good = " for good" if e is f["for_good"] else ""
                story.append(f"{who} went ahead{good} {sc} ({at(e)})")
            elif e["kind"] == "tie":
                story.append(f"{who} tied it {sc} ({at(e)})")
        d, mom = f["max_deficit"][f["winner"]]
        if d >= 2:
            story.append(f"{W} trailed by as many as {d} ({score_str(game, mom['away'], mom['home'])} "
                         f"in the {pn(mom['period'])})")
        gw = f["for_good"]
        if gw is not None and f["ot"] and gw["period"] > 3:
            story.append(f"{W} won it in {'overtime' if f['ot'] == 1 else pn(gw['period'])} ({at(gw)})")
    if not f["shootout"] and f["ot"] and not (f["for_good"] and f["for_good"]["period"] > 3):
        story.append(f"overtime ({f['ot']})")
    lo = f["loser"]
    if ev and f["final_regulation"][0 if lo == "away" else 1] == 0 and not f["shootout"]:
        story.append("shutout")
    flow_line = "Flow: " + "; ".join(story) + "."
    goal_lines = []
    for e in ev:
        who = ab(e["side"])
        sc = f"{max(e['away'], e['home'])}-{min(e['away'], e['home'])}"
        what = {"tie": f"{who} tied it {sc}", "cut": f"{who} cut it to {sc}"}.get(e["kind"], f"{who} {sc}")
        name = scorer(e.get("text"))
        asst = [_display_name(x) for x in assists(e.get("text"))]
        tail = (_display_name(name) if name else "goal") + (f" (from {', '.join(asst)})" if asst else "")
        goal_lines.append(f"- {pn(e['period'])} {e['clock']}, {what}: {tail}")
    if goal_lines:
        goal_lines.insert(0, "Goals:")
    swings = f["lead_changes"] + f["ties"]
    drama = (2 * bool(f["ot"]) + 2 * f["shootout"] + swings
             + (f["max_deficit"][f["winner"]][0] >= 2) - f["wire_to_wire"])
    return {"game_id": game.get("game_id"), "result": game_flow.result_lines(game),
            "by_line": by_line, "flow_line": flow_line, "goal_lines": goal_lines,
            "drama": drama,
            "post": bool(game.get("playoffs")) or int(game.get("season_type") or 2) == 3,
            "names": [game.get("home_team") or "", game.get("away_team") or ""]}


def bundles(game_state: dict) -> list[dict]:
    games = ((((game_state or {}).get("sports") or {}).get("nhl")) or {}).get("yesterday_games") or []
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
    """True when the story text names one of the teams: the full name, the last
    word ("Rangers") or the last two ("Blue Jackets", "Golden Knights")."""
    t = " " + norm(text) + " "
    for n in names:
        toks = norm(n).split()
        if not toks:
            continue
        for cand in (" ".join(toks), " ".join(toks[-2:]), toks[-1]):
            if f" {cand} " in t:
                return True
    return False


def _cost(lines: list[str], indent: int) -> int:
    return sum(len(x) + indent + 1 for x in lines)


def render(game_state: dict, story: str | dict | None = None,
           budget: int = BUDGET) -> tuple[list[str], dict[str, str]]:
    """The NHL GAME FLOW part of the GROUND TRUTH block, at most `budget`
    characters. Returns the lines and, per game shown, "full" (score by period,
    flow and every goal) or "flow" (the flow line alone). `story` defaults to
    what attach_story() stored after Pass 1."""
    bs = bundles(game_state)
    if not bs:
        return [], {}
    if story is None:
        story = ((game_state or {}).get("nhl_flow") or {}).get("story_text") or ""
    text = story_text(story) if isinstance(story, (dict, list)) else (story or "")
    prio = {b["game_id"]: 1 if b["post"] or (text and _names_in(text, b["names"])) else 2 for b in bs}
    order = sorted(bs, key=lambda b: (prio[b["game_id"]], -b["drama"]))
    head = [HEADER[0], HEADER[1].format(as_of=(game_state or {}).get("as_of_date", "unknown")), ""]
    used = _cost(head, 0) + 80                                   # room for the "not shown" line
    size: dict[str, str] = {}

    def lines_for(b, full):
        return [b["by_line"], b["flow_line"]] + b["goal_lines"] if full else [b["flow_line"]]

    def cost(b, full):
        return _cost(b["result"], 2) + _cost(lines_for(b, full), 4)

    for b in order:
        if prio[b["game_id"]] == 1:
            for s in ("full", "flow"):
                if used + cost(b, s == "full") <= budget:
                    size[b["game_id"]], used = s, used + cost(b, s == "full")
                    break
    for b in order:
        if prio[b["game_id"]] == 2 and used + cost(b, False) <= budget:
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
        out.append(f"  ... {len(bs) - len(size)} more NHL game(s) appear only as results above (budget).")
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
    game_state["nhl_flow"], in memory only, so the games the stories cover
    are shown in full from Pass 2 on. Returns one line for the log (the
    block's size, which the live run is checked by). Never raises."""
    try:
        if game_state is None:
            return "nhl flow: no game state"
        before = len("\n".join(summary_lines(game_state)))
        game_state.setdefault("nhl_flow", {})["story_text"] = story_text(story_plan)
        lines, size = render(game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        (game_state or {}).get("nhl_flow", {}).pop("story_text", None)
        return f"nhl flow: story order skipped: {type(e).__name__}: {e}"
    total = len(bundles(game_state))
    if not total:
        return "nhl flow: no NHL games with a goal log yesterday"
    full = sum(1 for s in size.values() if s == "full")
    after = len("\n".join(lines))
    return (f"nhl flow: block {before:,} -> {after:,} chars (cap {BUDGET:,}); "
            f"{len(size)} of {total} game(s) shown, {full} in full")


# ---------------------------------------------------------------------------
# The same facts, as data, for Pass 3
# ---------------------------------------------------------------------------

def _series_pairs(game: dict) -> set[tuple[int, int]]:
    """The series scores a "3-2" in the prose could be (before and after this
    game, smaller number first): a recap's "take a 2-1 lead" is the series,
    not the game, and the checker must not read it as a period score."""
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
    """One entry per completed NHL game yesterday whose goal log adds up.

      teams     {"away"/"home": {"name", "abbr"}}
      winner    "away" / "home"
      shootout  decided in a shootout (no game-winning goal)
      periods   periods played, shootout excluded
      scores    per period, every score the game stood at during it: the score
                entering it, then after each goal (a set of (away, home))
      goals     [{period, side, away, home, scorer, assists}], scorer/assists
                as normalized surnames
      go_ahead  [(side, period)], ties [(side, period)], for_good (side, period)
      max_deficit {side: goals}, wire_to_wire
      roster    every surname that scored or assisted"""
    out = []
    games = ((((game_state or {}).get("sports") or {}).get("nhl")) or {}).get("yesterday_games") or []
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
        goals = [{"period": e["period"], "side": e["side"], "away": e["away"], "home": e["home"],
                  "scorer": _surname(scorer(e.get("text"))),
                  "assists": [_surname(x) for x in assists(e.get("text"))]} for e in f["events"]]
        roster = {x["scorer"] for x in goals if x["scorer"]}
        for x in goals:
            roster.update(a for a in x["assists"] if a)
        out.append({
            "game_id": g.get("game_id"),
            "teams": {s: {"name": g.get(f"{s}_team") or "", "abbr": g.get(f"{s}_abbr") or ""}
                      for s in ("away", "home")},
            "winner": f["winner"], "shootout": f["shootout"], "periods": f["played"],
            "scores": scores, "goals": goals,
            "go_ahead": [(e["side"], e["period"]) for e in f["events"] if e["kind"] == "go_ahead"],
            "ties": [(e["side"], e["period"]) for e in f["events"] if e["kind"] == "tie"],
            "for_good": (f["for_good"]["side"], f["for_good"]["period"]) if f["for_good"] else None,
            "max_deficit": {s: f["max_deficit"][s][0] for s in ("away", "home")},
            "wire_to_wire": f["wire_to_wire"], "roster": roster,
            "final": tuple(sorted((int(g.get("away_score") or 0), int(g.get("home_score") or 0)))),
            "series_pairs": _series_pairs(g),
        })
    return out
