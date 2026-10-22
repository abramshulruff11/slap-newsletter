"""
Game flow from a running score: the sport-neutral core (SLA-148).

The writer used to get a final score and rebuild the game from tweets, which
is how MLB's ALCS Game 5 recap put a run in the wrong inning (SLA-142). Every
sport whose ESPN summary carries a scoring timeline can answer the same
questions in Python instead: who scored first, every lead change and tie, the
goal/basket that put the winner ahead for good, the biggest deficit the winner
overcame, whether the game was wire to wire.

This module is only the arithmetic. It takes a scoring timeline and knows
nothing about hockey, basketball or football; the sport's own file
(`nhl_bundle.py`, later the NBA's and college football's) turns ESPN's payload
into a timeline, labels the periods and renders the lines. Nothing here
fetches anything, calls a model or reads game_state.

A timeline is a list of events in game order:

    {"period": 2, "clock": "11:44", "away": 2, "home": 1, "text": "..."}

`away`/`home` are the score AFTER the event. `build()` returns None, never a
partial answer, when the timeline cannot be trusted: a step that moves both
sides or goes backwards, or a last event that is not the final score. A game
with no flow keeps its plain result line; nothing is guessed.
"""
from __future__ import annotations

from typing import Iterable


def lead_side(away: int, home: int) -> str | None:
    return "away" if away > home else "home" if home > away else None


def result_lines(game: dict) -> list[str]:
    """The lines YESTERDAY'S GAME RESULTS prints for one completed game, any
    sport: the result, then the series state for a playoff game. A game a
    bundle shows opens with these same lines and leaves that list, so the
    two can never disagree."""
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


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def build(events: Iterable[dict], final: tuple[int, int], winner: str | None = None) -> dict | None:
    """The connected facts for one game, or None when the timeline cannot be
    trusted.

    `final` is the score the timeline must end on. `winner` ("away"/"home")
    is only needed when the score alone cannot say (a shootout, where the
    timeline ends tied); otherwise it is the side that finished ahead.

    Returns:
      events        each input event plus kind ("go_ahead" / "tie" / "extend" /
                    "cut"), side (who scored) and first (the opening score)
      winner/loser  "away"/"home"; winner is None only for an undecided tie
      for_good      the last go_ahead by the winner (None with no winner by
                    score, or when the winner never trailed or tied)
      wire_to_wire  the winner scored first and was never behind or level again
      max_deficit   {side: (points, event)}: the most that side ever trailed
                    by (event is the score at that moment)
      max_lead      {side: (points, event)}
      lead_changes  go_ahead events that took the lead from the OTHER side
      ties          times the score became level after the first score
      scoreless     True when no one scored
    """
    evs: list[dict] = []
    a = h = 0
    for e in events:
        na, nh = _int(e.get("away")), _int(e.get("home"))
        if na is None or nh is None:
            return None
        da, dh = na - a, nh - h
        if da < 0 or dh < 0 or (da and dh) or not (da or dh):
            return None                       # backwards, both sides at once, or no change
        evs.append(dict(e, away=na, home=nh, side="away" if da else "home", points=da or dh))
        a, h = na, nh
    if (a, h) != (int(final[0]), int(final[1])):
        return None

    side_by_score = lead_side(a, h)
    win = side_by_score or winner
    if win not in ("away", "home"):
        win = None
    lose = None if win is None else ("home" if win == "away" else "away")

    out: list[dict] = []
    prev = None
    deficit = {"away": (0, None), "home": (0, None)}
    lead = {"away": (0, None), "home": (0, None)}
    for e in evs:
        for side, other in (("away", "home"), ("home", "away")):
            d = e[other] - e[side]
            if d > deficit[side][0]:
                deficit[side] = (d, e)
            if -d > lead[side][0]:
                lead[side] = (-d, e)
        now = lead_side(e["away"], e["home"])
        if now is None:
            kind = "tie"
        elif now != prev:
            kind = "go_ahead"
        else:
            kind = "extend" if now == e["side"] else "cut"
        out.append(dict(e, kind=kind, first=not out))
        prev = now

    changes, last = 0, None                       # a lead change: the lead moved to the OTHER side
    for e in out:
        now = lead_side(e["away"], e["home"])
        if now is not None:
            changes += last is not None and now != last
            last = now
    for_good = None
    if side_by_score:
        for_good = next((e for e in reversed(out)
                         if e["kind"] == "go_ahead" and e["side"] == side_by_score), None)
    return {
        "events": out, "winner": win, "loser": lose,
        "for_good": for_good,
        "wire_to_wire": bool(out and side_by_score and for_good is out[0]),
        "max_deficit": deficit, "max_lead": lead,
        "lead_changes": changes,
        "ties": sum(1 for e in out if e["kind"] == "tie"),
        "scoreless": not out,
    }


def score_after(events: list[dict], period: int) -> tuple[int, int]:
    """The score at the end of `period` (the last event in or before it)."""
    a = h = 0
    for e in events:
        if e["period"] > period:
            break
        a, h = e["away"], e["home"]
    return a, h
