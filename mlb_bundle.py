"""
MLB game flow: one connected fact bundle per game (SLA-142).

The 2026-10-19 ALCS Game 5 recap put Liam Hicks's single in the 5th "to put
the Rays up 2-0". It came in the 3rd, made it 3-0, and scored one run. The
writer had the final score and the series state, nothing else, and rebuilt
the game from tweets. The data to get it right was already on disk:
fetch_sports_data.py fetches ESPN's summary for every completed MLB game and
stores the line score (runs by inning) and every scoring play (half-inning,
running score, ESPN's text) under game["box_score"]. Only the box score
image read it.

This module turns that into game flow, computed in Python, never by the
model: the score after every half-inning in which runs scored, who scored
first, every lead change and tie, the half-inning the winner took the lead
for good, the winner's biggest deficit, wire to wire, walk-offs, extra
innings, and each scoring play. It reads game_state only (no database, no
network), so it runs at prompt-build time and needs no fetch step.

- **Checked, or left out.** A line score that does not add up to the final
  score drops the whole game (it stays in YESTERDAY'S GAME RESULTS as a
  plain line). Scoring plays that do not add up to the line score, half by
  half, drop the play lines and keep the flow. A game that isn't final has
  no box score, so it has no bundle. Never filled in or guessed.
- **The cap is 4,000 characters** for the whole block (Abram, 2026-10-19):
  postseason games and games a story names in full (score by half-inning,
  flow, scoring plays), every other game with its flow line as many as fit,
  close games first; then any room left upgrades games to full. A game the cap cut keeps its plain
  result line; the count is printed, never silent.
- **Nothing said twice.** A game the block shows opens with the same result
  (and series) lines YESTERDAY'S GAME RESULTS would print, and is left out
  of that list. `game_result_lines()` is the one copy of those lines, used
  for every sport.
- `claim_facts()` gives claim_validator Check 3D the same facts as data.
"""
from __future__ import annotations

import re
from collections import defaultdict

from history_source import norm, story_text

BUDGET = 4_000
TWO_WORD_NICKNAMES = ("red sox", "white sox", "blue jays")

HEADER = [
    "## GROUND TRUTH: MLB GAME FLOW",
    "Source: ESPN line score and scoring plays, as of {as_of}. Computed from the line score, not "
    "written by anyone: the score after every half-inning in which runs scored, who led, every lead "
    "change and tie, and each scoring play. These are SOURCED. Any claim about an inning, a lead, a "
    "comeback or a run-scoring play must match this block; never put a run in an inning it does not show.",
    "",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def half_name(inning: int, half: str) -> str:
    """"top of the 3rd" / "bottom of the 10th"."""
    return f"{half} of the {ordinal(inning)}"


def _int(v) -> int | None:
    s = str(v).strip() if v is not None else ""
    return int(s) if s.isdigit() else None


def _side_lead(away: int, home: int) -> str | None:
    return "away" if away > home else "home" if home > away else None


def nickname(full_name: str) -> str:
    n = norm(full_name)
    for two in TWO_WORD_NICKNAMES:
        if n.endswith(" " + two) or n == two:
            return two
    return n.split()[-1] if n else ""


def game_result_lines(game: dict) -> list[str]:
    """The lines YESTERDAY'S GAME RESULTS prints for one completed game, any
    sport: the result, then the series state for a playoff game. The one copy:
    format_game_state_summary uses it for the list, and render() opens each
    MLB bundle with it, so a game moved into the block says the same thing."""
    home, away = game.get("home_team", "?"), game.get("away_team", "?")
    hs, as_ = game.get("home_score", 0), game.get("away_score", 0)
    ot = " (OT)" if game.get("overtime") else ""
    winner = game.get("winner", "")
    lines = [f"{away} {as_}, {home} {hs}{ot}" + (f" — {winner} wins" if winner else "")]
    series = game.get("series")
    if series:
        if series.get("series_over"):
            lines.append(f"  SERIES OVER. {winner} advances. Final: "
                         f"{series.get('home_wins', 0)}-{series.get('away_wins', 0)}.")
        else:
            elim = [n for n, k in ((home, "elimination_game_for_home"), (away, "elimination_game_for_away"))
                    if series.get(k)]
            elim_str = f" {', '.join(elim)} eliminated." if elim else ""
            lines.append(f"  PLAYOFFS — {series.get('summary', '')}. "
                         f"Next: Game {series.get('next_game_number', '?')}.{elim_str}")
    return lines


# ---------------------------------------------------------------------------
# The facts, from the line score and the scoring plays
# ---------------------------------------------------------------------------

def halves(game: dict) -> list[dict] | None:
    """Every half-inning played, in order, with its runs and the score after
    it. None when the line score is missing or does not add up to the final."""
    ls = ((game.get("box_score") or {}).get("linescore")) or {}
    away, home = ls.get("away_innings") or [], ls.get("home_innings") or []
    out: list[dict] = []
    a = h = 0
    for i in range(max(len(away), len(home))):
        ra = _int(away[i]) if i < len(away) else None
        if ra is None:
            break
        a += ra
        out.append({"inning": i + 1, "half": "top", "runs": ra, "away": a, "home": h})
        rh = _int(home[i]) if i < len(home) else None
        if rh is None:
            break                                   # bottom not played: the home team was ahead
        h += rh
        out.append({"inning": i + 1, "half": "bottom", "runs": rh, "away": a, "home": h})
    # Anything after an unplayed half is a line score we don't understand.
    played = len(out)
    rest = [x for x in away[(played + 1) // 2:] + home[played // 2:] if _int(x) is not None]
    if not out or rest:
        return None
    if a != _int(game.get("away_score")) or h != _int(game.get("home_score")):
        return None
    return out


_SCORE_RE = re.compile(r"^\s*(\S+)\s+(\d+),\s*(\S+)\s+(\d+)\s*$")
# A sentence ends at a period that doesn't close an initial ("B. Montgomery")
# or a suffix ("Acuña Jr. scored").
_SENT_END_RE = re.compile(r"(?<!\b[A-Z])(?<!\bJr)(?<!\bSr)(?<!\bSt)\.(?:\s+|$)")
_VERB_RE = re.compile(r"\s+(walked|singled|doubled|tripled|homered|hit|grounded|flied|lined|popped|reached|"
                      r"struck|was|bunted|sacrificed|fouled|safe|out|scored|stole|advanced|grounds|singles|"
                      r"doubles|triples|homers|walks)\b")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def short_text(text: str) -> str:
    """ESPN's play text up to the end of its first sentence: the play, not the
    replay review that follows it."""
    return _SENT_END_RE.split((text or "").strip(), maxsplit=1)[0].strip()


def batter(text: str) -> tuple[str, str] | None:
    """(name as ESPN wrote it, normalized surname) of the player who made a
    scoring play, from the words before the verb. None when the play starts
    with a runner ("Smith scored on a wild pitch"), not a batter."""
    m = _VERB_RE.search(text or "")
    if not m or m.group(1) == "scored":
        return None
    name = text[:m.start()].strip()
    toks = [t for t in norm(name).split() if t not in _SUFFIXES and len(t) > 1]
    return (name, toks[-1]) if toks else None


def scorers(text: str) -> set[str]:
    """Normalized surnames of everyone who crossed the plate on a play:
    "DeLuca walked, Díaz scored, ..." -> {"diaz"}; a home run's batter too."""
    out = set()
    for seg in re.split(r",\s*|\s+and\s+", text or ""):
        m = re.match(r"\s*(.+?)\s+scored\b", seg)
        if m:
            toks = [t for t in norm(m.group(1)).split() if t not in _SUFFIXES and len(t) > 1]
            if toks:
                out.add(toks[-1])
    b = batter(text)
    if b and re.search(r"\bhomered\b|\bhome run\b|\bgrand slam\b", text or "", re.IGNORECASE):
        out.add(b[1])
    return out


def scoring_plays(game: dict, hv: list[dict]) -> list[dict] | None:
    """Every scoring play with the runs it scored and the score after it.
    None unless they add up to the line score, half-inning by half-inning."""
    sps = (((game.get("box_score") or {}).get("agate")) or {}).get("scoring_plays") or []
    if not sps:
        return None
    out: list[dict] = []
    pa = ph = 0
    for sp in sps:
        m = _SCORE_RE.match(sp.get("score") or "")
        inning, half = sp.get("inning"), sp.get("half")
        if not m or not isinstance(inning, int) or half not in ("top", "bottom"):
            return None
        a, h = int(m.group(2)), int(m.group(4))
        side = "away" if half == "top" else "home"
        runs, still = (a - pa, h == ph) if side == "away" else (h - ph, a == pa)
        if runs <= 0 or not still:
            return None
        pa, ph = a, h
        text = short_text(sp.get("text") or "")
        shown = text if len(text) <= 140 else text[:140].rsplit(" ", 1)[0] + " …"
        out.append({"inning": inning, "half": half, "side": side, "runs": runs, "away": a, "home": h,
                    "text": shown, "batter": batter(text), "scorers": scorers(text)})
    per = {(x["inning"], x["half"]): x["runs"] for x in hv}
    got: dict[tuple, int] = defaultdict(int)
    for p in out:
        got[(p["inning"], p["half"])] += p["runs"]
    if dict(got) != {k: v for k, v in per.items() if v}:
        return None
    return out


def flow(game: dict) -> dict | None:
    """The connected facts for one completed game, or None when the line
    score can't be trusted."""
    hv = halves(game)
    if hv is None:
        return None
    last = hv[-1]
    winner = _side_lead(last["away"], last["home"])
    if winner is None:
        return None                                     # a final can't be tied
    loser = "home" if winner == "away" else "away"
    events, prev = [], None
    max_def = {"away": (0, None), "home": (0, None)}    # (runs, the half it was)
    max_lead = {"away": (0, None), "home": (0, None)}
    for x in hv:
        for side, other in (("away", "home"), ("home", "away")):
            d = x[other] - x[side]
            if d > max_def[side][0]:
                max_def[side] = (d, x)
            if -d > max_lead[side][0]:
                max_lead[side] = (-d, x)
        if not x["runs"]:
            continue
        now = _side_lead(x["away"], x["home"])
        bat = "away" if x["half"] == "top" else "home"
        if now is None:
            kind = "tie"
        elif now != prev:
            kind = "go_ahead"
        else:
            kind = "extend" if now == bat else "cut"
        events.append(dict(x, kind=kind, side=bat, first=not events))
        prev = now
    for_good = next((e for e in reversed(events) if e["kind"] == "go_ahead" and e["side"] == winner), None)
    last_top = next((x for x in reversed(hv) if x["half"] == "top"), None)
    walk_off = (winner == "home" and last["half"] == "bottom"
                and last_top is not None and _side_lead(last_top["away"], last_top["home"]) != "home")
    plays = scoring_plays(game, hv)
    return {
        "halves": hv, "events": events, "winner": winner, "loser": loser,
        "for_good": for_good, "wire_to_wire": bool(events and for_good is events[0]),
        "walk_off": walk_off, "innings": last["inning"],
        "max_deficit": max_def, "max_lead": max_lead, "plays": plays,
    }


# ---------------------------------------------------------------------------
# One bundle per game
# ---------------------------------------------------------------------------

def _abbr(game: dict, side: str) -> str:
    return game.get(f"{side}_abbr") or game.get(f"{side}_team") or side


def score_str(game: dict, away: int, home: int) -> str:
    lead = _side_lead(away, home)
    if lead is None:
        return f"tied {away}-{home}"
    hi, lo = max(away, home), min(away, home)
    return f"{_abbr(game, lead)} {hi}-{lo}"


def bundle(game: dict) -> dict | None:
    """{game_id, result, by_line, flow_line, play_lines, drama, post, names}
    for one completed game, or None when there is no trustworthy line score."""
    if not game.get("completed"):
        return None
    f = flow(game)
    if f is None:
        return None
    ab = lambda side: _abbr(game, side)              # noqa: E731
    W = ab(f["winner"])
    by = "; ".join(f"{x['half']} {ordinal(x['inning'])} {score_str(game, x['away'], x['home'])}"
                   for x in f["halves"] if x["runs"])
    flow_lines = [f"By half-inning (runs scored only): {by or 'no runs'}."]
    # Short on purpose: on a full regular-season day this line is all most
    # games get, and every character comes out of the 4,000.
    story: list[str] = []
    hn = lambda x: f"{x['half']} {ordinal(x['inning'])}"   # noqa: E731
    if f["wire_to_wire"]:
        story.append(f"{W} led wire to wire (scored first, {hn(f['events'][0])})")
    else:
        for e in f["events"]:
            who, sc = ab(e["side"]), f"{max(e['away'], e['home'])}-{min(e['away'], e['home'])}"
            if e["kind"] == "go_ahead" and e["first"]:
                story.append(f"{who} scored first ({hn(e)}, {sc})")
            elif e["kind"] == "go_ahead":
                good = " for good" if e is f["for_good"] else ""
                story.append(f"{who} went ahead{good} {sc} ({hn(e)})")
            elif e["kind"] == "tie":
                story.append(f"{who} tied it {sc} ({hn(e)})")
        d, at = f["max_deficit"][f["winner"]]
        if d >= 2:
            story.append(f"{W} trailed by as many as {d} ({score_str(game, at['away'], at['home'])} "
                         f"after the {hn(at)})")
    if f["walk_off"]:
        story.append(f"walk-off in the bottom {ordinal(f['innings'])}")
    if f["innings"] > 9:
        story.append(f"{f['innings']} innings")
    elif f["innings"] < 9:
        story.append(f"shortened to {f['innings']} innings")
    lead, at = f["max_lead"][f["winner"]]
    margin = abs(f["halves"][-1]["away"] - f["halves"][-1]["home"])
    if lead > margin:
        story.append(f"{W}'s lead was as big as {lead} ({score_str(game, at['away'], at['home'])} "
                     f"after the {hn(at)})")
    if f["halves"][-1][f["loser"]] == 0:
        story.append("shutout")
    flow_lines.append("Flow: " + "; ".join(story) + ".")
    play_lines = []
    for p in f["plays"] or []:
        runs = f" ({p['runs']} runs)" if p["runs"] > 1 else ""
        play_lines.append(f"- {p['half']} {ordinal(p['inning'])}, {score_str(game, p['away'], p['home'])}: "
                          f"{p['text']}{runs}")
    if play_lines:
        play_lines.insert(0, "Scoring plays:")
    swings = sum(1 for e in f["events"] if e["kind"] in ("go_ahead", "tie")) - 1
    drama = (2 * f["walk_off"] + (f["innings"] > 9) + max(0, swings)
             + (f["max_deficit"][f["winner"]][0] >= 3) - f["wire_to_wire"])
    return {"game_id": game.get("game_id"), "result": game_result_lines(game),
            "by_line": flow_lines[0], "flow_line": flow_lines[1], "play_lines": play_lines,
            "drama": drama,
            "post": bool(game.get("playoffs")) or int(game.get("season_type") or 2) == 3,
            "names": [game.get("home_team") or "", game.get("away_team") or ""]}


def bundles(game_state: dict) -> list[dict]:
    games = ((((game_state or {}).get("sports") or {}).get("mlb")) or {}).get("yesterday_games") or []
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
    t = " " + norm(text) + " "
    return any(n and (f" {norm(n)} " in t or f" {nickname(n)} " in t) for n in names)


def _cost(lines: list[str], indent: int) -> int:
    return sum(len(x) + indent + 1 for x in lines)


def render(game_state: dict, story: str | dict | None = None,
           budget: int = BUDGET) -> tuple[list[str], dict[str, str]]:
    """The MLB GAME FLOW part of the GROUND TRUTH block, at most `budget`
    characters. Returns the lines and, per game shown, "full" (score by
    half-inning, flow and scoring plays) or "flow" (the flow line alone). `story` defaults to what attach_story() stored
    after Pass 1."""
    bs = bundles(game_state)
    if not bs:
        return [], {}
    if story is None:
        story = ((game_state or {}).get("mlb_flow") or {}).get("story_text") or ""
    text = story_text(story) if isinstance(story, (dict, list)) else (story or "")
    prio = {b["game_id"]: 1 if b["post"] or (text and _names_in(text, b["names"])) else 2 for b in bs}
    # Within a tier the close games go first: a walk-off, extras, lead changes
    # or a comeback is where an inning claim goes wrong. Then the feed's order.
    order = sorted(bs, key=lambda b: (prio[b["game_id"]], -b["drama"]))
    head = [HEADER[0], HEADER[1].format(as_of=(game_state or {}).get("as_of_date", "unknown")), ""]
    used = _cost(head, 0) + 80                                   # room for the "not shown" line
    size: dict[str, str] = {}

    def lines_for(b, full):
        return [b["by_line"], b["flow_line"]] + b["play_lines"] if full else [b["flow_line"]]

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
        out.append(f"  ... {len(bs) - len(size)} more MLB game(s) appear only as results above (budget).")
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
    game_state["mlb_flow"], in memory only, so the games the stories cover
    are shown in full from Pass 2 on. Returns one line for the log (the
    block's size, which the live run is checked by). Never raises."""
    try:
        if game_state is None:
            return "mlb flow: no game state"
        before = len("\n".join(summary_lines(game_state)))
        game_state.setdefault("mlb_flow", {})["story_text"] = story_text(story_plan)
        lines, size = render(game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        (game_state or {}).get("mlb_flow", {}).pop("story_text", None)
        return f"mlb flow: story order skipped: {type(e).__name__}: {e}"
    total = len(bundles(game_state))
    if not total:
        return "mlb flow: no MLB games with a line score yesterday"
    full = sum(1 for s in size.values() if s == "full")
    after = len("\n".join(lines))
    return (f"mlb flow: block {before:,} -> {after:,} chars (cap {BUDGET:,}); "
            f"{len(size)} of {total} game(s) shown, {full} in full")


# ---------------------------------------------------------------------------
# The same facts, as data, for Pass 3 Check 3D
# ---------------------------------------------------------------------------

def roster(game: dict) -> set[str]:
    """Normalized surnames of everyone in the game's box score."""
    out = set()
    for side in ("away", "home"):
        t = ((game.get("box_score") or {}).get(side)) or {}
        for p in (t.get("batting") or []) + (t.get("pitching") or []):
            toks = [x for x in norm(p.get("name") or "").split() if x not in _SUFFIXES and len(x) > 1]
            if toks:
                out.add(toks[-1])
    return out


def claim_facts(game_state: dict) -> list[dict]:
    """One entry per completed MLB game yesterday whose line score adds up.

      teams      {"away"/"home": {"name", "abbr", "nick"}}
      winner     "away" / "home"
      innings    innings played
      moments    per inning: every score the game stood at during it (the
                 score entering it, then after each scoring play, or each
                 half when the plays don't reconcile), as (away, home)
      made       per inning: the scores a play in that inning PRODUCED
      go_ahead   [(side, inning)], ties [(side, inning)], for_good (side, inning)
      runs       {(side, inning): runs}
      max_deficit {side: runs}, walk_off, wire_to_wire
      plays      [{inning, half, side, runs, away, home, surname}], the score
                 after each (empty when they don't reconcile)
      roster     every surname in the box score (batters and pitchers)
      scorers    every surname that crossed the plate (None without plays)"""
    out = []
    games = ((((game_state or {}).get("sports") or {}).get("mlb")) or {}).get("yesterday_games") or []
    for g in games:
        if not g.get("completed"):
            continue
        try:
            f = flow(g)
        except Exception:  # noqa: BLE001
            f = None
        if f is None:
            continue
        hv, plays = f["halves"], f["plays"]
        moments: dict[int, list[tuple]] = defaultdict(list)
        made: dict[int, set[tuple]] = defaultdict(set)
        cur = (0, 0)
        steps = ([(p["inning"], (p["away"], p["home"])) for p in plays] if plays
                 else [(x["inning"], (x["away"], x["home"])) for x in hv if x["runs"]])
        innings = f["innings"]
        si = 0
        for inn in range(1, innings + 1):
            moments[inn].append(cur)
            while si < len(steps) and steps[si][0] == inn:
                cur = steps[si][1]
                moments[inn].append(cur)
                made[inn].add(cur)
                si += 1
        runs: dict[tuple, int] = {}
        for x in hv:
            side = "away" if x["half"] == "top" else "home"
            runs[(side, x["inning"])] = x["runs"]
        out.append({
            "game_id": g.get("game_id"),
            "teams": {s: {"name": g.get(f"{s}_team") or "", "abbr": g.get(f"{s}_abbr") or "",
                          "nick": nickname(g.get(f"{s}_team") or "")} for s in ("away", "home")},
            "winner": f["winner"], "innings": innings,
            "moments": dict(moments), "made": dict(made), "runs": runs,
            "go_ahead": [(e["side"], e["inning"]) for e in f["events"] if e["kind"] == "go_ahead"],
            "ties": [(e["side"], e["inning"]) for e in f["events"] if e["kind"] == "tie"],
            "for_good": (f["for_good"]["side"], f["for_good"]["inning"]) if f["for_good"] else None,
            "max_deficit": {s: f["max_deficit"][s][0] for s in ("away", "home")},
            "walk_off": f["walk_off"], "wire_to_wire": f["wire_to_wire"],
            "plays": [{"inning": p["inning"], "half": p["half"], "side": p["side"], "runs": p["runs"],
                       "away": p["away"], "home": p["home"], "scorers": sorted(p["scorers"]),
                       "surname": p["batter"][1] if p["batter"] else None} for p in plays or []],
            "roster": roster(g),
            "scorers": set().union(*(p["scorers"] for p in plays)) if plays else None,
        })
    return out
