"""
SLAP Newsletter — NFL standings: computation, tiebreakers, HTML rendering.

Consumes the season game log that `fetch_sports_data.fetch_nfl_season_games()`
writes to `game_state.json` under `sports.nfl.season_games` (SLA-42), computes
division standings with the official NFL tiebreaker procedure, and renders a
self-contained, platform-agnostic HTML component (SLA-43).

Nothing here is Substack- or Beehiiv-specific and nothing here fetches: the
module takes a list of game dicts and returns strings. That keeps it testable
offline (uat/tests/test_nfl_standings.py, 0 API calls) and lets the live-render
test (SLA-44) and the real-data wiring (SLA-45) reuse the same code.

Run standalone to write a full 32-team mock table:

    python -X utf8 nfl_standings.py --out nfl_standings_mock.html
"""
from __future__ import annotations

import argparse
import html
import json
import random
from pathlib import Path

# The 32-team conference/division map has exactly ONE copy in this repo, in
# fetch_sports_data.py. Importing it is deliberate: a second hand-kept table is
# the drift failure this project has paid for repeatedly (see CLAUDE.md,
# "Shared logic lives at the repo root, imported by both runners — never
# copied"). fetch_sports_data.py is import-safe; all of its fetching sits
# behind `if __name__ == "__main__"`.
from fetch_sports_data import NFL_DIVISIONS

SCRIPT_DIR = Path(__file__).resolve().parent

# Conference first, division second — the grouping order the ticket specifies.
CONFERENCE_ORDER = ["AFC", "NFC"]
DIVISION_ORDER = ["East", "North", "South", "West"]

# ESPN season.type: 1 = preseason, 2 = regular season, 3 = postseason.
# Only the regular season counts toward standings.
REGULAR_SEASON = 2


# ---------------------------------------------------------------------------
# Column definitions — priority order, most essential first
# ---------------------------------------------------------------------------
# `key`   — field on the computed team record
# `label` — column header
# `css`   — class used by the responsive cut rules below
#
# The order of this list IS the priority order from the ticket. Cuts come off
# the BOTTOM, so PCT (last) is the first column to go.
COLUMNS = [
    ("record", "W-L-T", "nflst-rec"),
    ("streak", "STRK",  "nflst-strk"),
    ("pf",     "PF",    "nflst-pf"),
    ("pa",     "PA",    "nflst-pa"),
    ("diff",   "DIFF",  "nflst-diff"),
    ("div_record",  "DIV",  "nflst-div"),
    ("conf_record", "CONF", "nflst-conf"),
    ("pct",    "PCT",   "nflst-pct"),
]


# ---------------------------------------------------------------------------
# Record computation
# ---------------------------------------------------------------------------

def _blank_record(abbr: str) -> dict:
    conf, div = NFL_DIVISIONS.get(abbr, (None, None))
    return {
        "abbr": abbr,
        "conference": conf,
        "division": div,
        "wins": 0, "losses": 0, "ties": 0,
        "div_wins": 0, "div_losses": 0, "div_ties": 0,
        "conf_wins": 0, "conf_losses": 0, "conf_ties": 0,
        "points_for": 0, "points_against": 0,
        "touchdowns_for": 0, "touchdowns_against": 0,
        # Ordered opponent log, used by every tiebreaker past head-to-head.
        "games": [],
    }


def _counts_toward_standings(game: dict) -> bool:
    """Final regular-season games only — preseason and live games never count."""
    if not game.get("completed"):
        return False
    season_type = game.get("season_type")
    # A feed that omits season_type at all is taken at face value; one that
    # states a season type must state the regular season.
    if season_type is not None and int(season_type) != REGULAR_SEASON:
        return False
    return bool(game.get("home_abbr")) and bool(game.get("away_abbr"))


def compute_records(games: list[dict]) -> dict[str, dict]:
    """
    Fold a season game log into one record per team, keyed by ESPN abbreviation.

    Every team in NFL_DIVISIONS gets a row even with zero games played, so a
    week-1 table is still a full 32-team table.
    """
    records = {abbr: _blank_record(abbr) for abbr in NFL_DIVISIONS}

    for game in games:
        if not _counts_toward_standings(game):
            continue
        home, away = game["home_abbr"], game["away_abbr"]
        if home not in records or away not in records:
            # An unrecognized abbreviation is a mapping bug, not a game to drop
            # silently — but it must not crash the table for the other 31 teams.
            print(f"[nfl_standings] ⚠ unknown team abbreviation in game "
                  f"{game.get('game_id','?')}: {away} @ {home}")
            continue

        hs = int(game.get("home_score", 0) or 0)
        as_ = int(game.get("away_score", 0) or 0)
        h_td = game.get("home_touchdowns")
        a_td = game.get("away_touchdowns")

        for team, opp, pf, pa, td_f, td_a in (
            (home, away, hs, as_, h_td, a_td),
            (away, home, as_, hs, a_td, h_td),
        ):
            rec = records[team]
            opp_rec = records[opp]
            same_div = (rec["conference"] == opp_rec["conference"]
                        and rec["division"] == opp_rec["division"])
            same_conf = rec["conference"] == opp_rec["conference"]

            if pf > pa:
                result = "W"
                rec["wins"] += 1
                if same_div: rec["div_wins"] += 1
                if same_conf: rec["conf_wins"] += 1
            elif pf < pa:
                result = "L"
                rec["losses"] += 1
                if same_div: rec["div_losses"] += 1
                if same_conf: rec["conf_losses"] += 1
            else:
                result = "T"
                rec["ties"] += 1
                if same_div: rec["div_ties"] += 1
                if same_conf: rec["conf_ties"] += 1

            rec["points_for"] += pf
            rec["points_against"] += pa
            if td_f is not None: rec["touchdowns_for"] += int(td_f)
            if td_a is not None: rec["touchdowns_against"] += int(td_a)
            rec["games"].append({
                "opponent": opp, "result": result,
                "points_for": pf, "points_against": pa,
                "week": game.get("week"),
            })

    for rec in records.values():
        _finalize(rec)
    return records


def _pct(w: int, l: int, t: int) -> float:
    """NFL winning percentage: a tie counts as half a win. 0 games played = .000."""
    played = w + l + t
    if played == 0:
        return 0.0
    return (w + 0.5 * t) / played


def _fmt_pct(value: float) -> str:
    """.667 / 1.000 — leading zero dropped, the way standings tables print it."""
    s = f"{value:.3f}"
    return s[1:] if s.startswith("0.") else s


def _fmt_record(w: int, l: int, t: int) -> str:
    """`4-1` normally, `4-1-1` only when there is a tie to show."""
    return f"{w}-{l}-{t}" if t else f"{w}-{l}"


def _streak(games: list[dict]) -> str:
    """W3 / L2 / T1 from the tail of the game log. Empty for a team with no games."""
    if not games:
        return ""
    last = games[-1]["result"]
    n = 0
    for g in reversed(games):
        if g["result"] != last:
            break
        n += 1
    return f"{last}{n}"


def _finalize(rec: dict) -> None:
    """Derived display + sort fields, computed once per team."""
    rec["games_played"] = rec["wins"] + rec["losses"] + rec["ties"]
    rec["win_pct"] = _pct(rec["wins"], rec["losses"], rec["ties"])
    rec["div_pct"] = _pct(rec["div_wins"], rec["div_losses"], rec["div_ties"])
    rec["conf_pct"] = _pct(rec["conf_wins"], rec["conf_losses"], rec["conf_ties"])
    rec["point_diff"] = rec["points_for"] - rec["points_against"]
    rec["record"] = _fmt_record(rec["wins"], rec["losses"], rec["ties"])
    rec["div_record"] = _fmt_record(rec["div_wins"], rec["div_losses"], rec["div_ties"])
    rec["conf_record"] = _fmt_record(rec["conf_wins"], rec["conf_losses"], rec["conf_ties"])
    rec["pct"] = _fmt_pct(rec["win_pct"])
    rec["pf"] = str(rec["points_for"])
    rec["pa"] = str(rec["points_against"])
    rec["diff"] = f"+{rec['point_diff']}" if rec["point_diff"] > 0 else str(rec["point_diff"])
    rec["streak"] = _streak(rec["games"])


# ---------------------------------------------------------------------------
# Tiebreakers — the official NFL two-team procedure
# ---------------------------------------------------------------------------
# Each step returns a value for a team where HIGHER IS BETTER, or None when the
# step does not apply (wrong scope, not enough data). A step decides the tie
# only when it applies to both teams and their values differ; otherwise the
# procedure falls through to the next step. That is what "do not approximate"
# means here: a step that cannot be evaluated is skipped, never guessed at.

MIN_COMMON_GAMES = 4  # official minimum for the common-games step

TIEBREAK_STEPS = [
    "head-to-head",
    "division record",
    "common games",
    "conference record",
    "strength of victory",
    "strength of schedule",
    "combined conference ranking (points for / points against)",
    "combined league ranking (points for / points against)",
    "net points in common games",
    "net points in all games",
    "net touchdowns in all games",
    "coin toss",
]


def _head_to_head(rec: dict, other: dict) -> float | None:
    """Win pct in games between exactly these two teams."""
    w = l = t = 0
    for g in rec["games"]:
        if g["opponent"] != other["abbr"]:
            continue
        if g["result"] == "W": w += 1
        elif g["result"] == "L": l += 1
        else: t += 1
    if w + l + t == 0:
        return None
    return _pct(w, l, t)


def _opponents(rec: dict) -> set[str]:
    return {g["opponent"] for g in rec["games"]}


def _common_opponents(a: dict, b: dict) -> set[str]:
    return _opponents(a) & _opponents(b)


def _record_vs(rec: dict, opponents: set[str]) -> tuple[int, int, int, int, int]:
    """(w, l, t, points_for, points_against) restricted to a set of opponents."""
    w = l = t = pf = pa = 0
    for g in rec["games"]:
        if g["opponent"] not in opponents:
            continue
        if g["result"] == "W": w += 1
        elif g["result"] == "L": l += 1
        else: t += 1
        pf += g["points_for"]
        pa += g["points_against"]
    return w, l, t, pf, pa


def _common_games_pct(rec: dict, other: dict) -> float | None:
    """Win pct in games against common opponents. Needs >= 4 common GAMES played."""
    common = _common_opponents(rec, other)
    if not common:
        return None
    w, l, t, _, _ = _record_vs(rec, common)
    if w + l + t < MIN_COMMON_GAMES:
        return None
    return _pct(w, l, t)


def _common_games_net_points(rec: dict, other: dict) -> float | None:
    common = _common_opponents(rec, other)
    if not common:
        return None
    w, l, t, pf, pa = _record_vs(rec, common)
    if w + l + t < MIN_COMMON_GAMES:
        return None
    return float(pf - pa)


def _strength(rec: dict, records: dict[str, dict], beaten_only: bool) -> float | None:
    """
    Strength of victory (beaten_only) / strength of schedule: the combined
    win pct of the opponents concerned, weighted by how often they were played.
    """
    w = l = t = 0
    for g in rec["games"]:
        if beaten_only and g["result"] != "W":
            continue
        opp = records.get(g["opponent"])
        if not opp:
            continue
        w += opp["wins"]; l += opp["losses"]; t += opp["ties"]
    if w + l + t == 0:
        return None
    return _pct(w, l, t)


def _combined_rank(rec: dict, pool: list[dict]) -> float | None:
    """
    Combined ranking in points scored and points allowed within `pool`.
    NFL ranks 1 = best (most points scored / fewest allowed) and the LOWER
    combined rank wins, so this returns the negated sum to keep the
    higher-is-better convention every step here shares.
    """
    if len(pool) < 2:
        return None
    rank_pf = _rank_of(rec, pool, "points_for", higher_is_better=True)
    rank_pa = _rank_of(rec, pool, "points_against", higher_is_better=False)
    return -float(rank_pf + rank_pa)


def _rank_of(rec: dict, pool: list[dict], field: str, higher_is_better: bool) -> int:
    """
    1-based competition rank, ties sharing the best rank (1, 2, 2, 4) — the way
    the NFL publishes its points-scored / points-allowed rankings.
    """
    value = rec[field]
    better = 0
    for other in pool:
        if other["abbr"] == rec["abbr"]:
            continue
        if (other[field] > value) if higher_is_better else (other[field] < value):
            better += 1
    return better + 1


def _net_points(rec: dict) -> float:
    return float(rec["point_diff"])


def _net_touchdowns(rec: dict) -> float | None:
    """
    Net TDs in all games. The season game log carries scores, not touchdown
    counts, so this returns None unless a feed supplies home_touchdowns /
    away_touchdowns. Returning None (skip to the coin toss) is correct;
    deriving TDs from the final score would be an approximation, and the
    ticket is explicit that this procedure must not be approximated.
    """
    if rec["touchdowns_for"] == 0 and rec["touchdowns_against"] == 0:
        return None
    return float(rec["touchdowns_for"] - rec["touchdowns_against"])


def break_two_way_tie(a: dict, b: dict, records: dict[str, dict]) -> tuple[int, str]:
    """
    Official NFL two-team tiebreaker.

    Returns (order, step) where order is -1 if `a` ranks ahead of `b`, 1 if `b`
    ranks ahead, and 0 if every step including the coin toss left it unresolved.
    `step` names the step that decided it, or "coin toss" when unresolved.
    """
    same_div = (a["conference"] == b["conference"] and a["division"] == b["division"])
    same_conf = a["conference"] == b["conference"]
    conf_pool = [r for r in records.values() if r["conference"] == a["conference"]]
    league_pool = list(records.values())

    # Zipped against TIEBREAK_STEPS rather than repeating the names, so the
    # published step list and the code that runs them cannot drift apart.
    # `records` here is the WHOLE league, because steps 5-8 are league-wide.
    evaluators = [
        lambda r, o: _head_to_head(r, o),                                        # 1
        (lambda r, o: r["div_pct"]) if same_div else (lambda r, o: None),        # 2
        lambda r, o: _common_games_pct(r, o),                                    # 3
        (lambda r, o: r["conf_pct"]) if same_conf else (lambda r, o: None),      # 4
        lambda r, o: _strength(r, records, beaten_only=True),                    # 5
        lambda r, o: _strength(r, records, beaten_only=False),                   # 6
        (lambda r, o: _combined_rank(r, conf_pool)) if same_conf
        else (lambda r, o: None),                                                # 7
        lambda r, o: _combined_rank(r, league_pool),                             # 8
        lambda r, o: _common_games_net_points(r, o),                             # 9
        lambda r, o: _net_points(r),                                             # 10
        lambda r, o: _net_touchdowns(r),                                         # 11
    ]
    # TIEBREAK_STEPS' last entry is the coin toss, which has no evaluator.
    assert len(evaluators) == len(TIEBREAK_STEPS) - 1, "tiebreaker steps out of sync"

    for step, fn in zip(TIEBREAK_STEPS, evaluators):
        va, vb = fn(a, b), fn(b, a)
        if va is None or vb is None:
            continue  # step does not apply — fall through, never guess
        if va > vb:
            return -1, step
        if vb > va:
            return 1, step

    # Step 12 (TIEBREAK_STEPS[-1]). The real rule is a coin toss; a coin toss in code would make the
    # table non-deterministic and hide a genuinely unresolved tie, so it is
    # reported as unresolved and the caller orders alphabetically for stability.
    return 0, TIEBREAK_STEPS[-1]


def _sort_division(teams: list[dict], records: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """
    Order one division. Returns (ordered_teams, notes).

    Teams separate first on win percentage. Any group still level is resolved by
    the official procedure when it is exactly two teams; a group of three or
    more is ordered provisionally and reported (see `notes`).
    """
    notes: list[dict] = []
    groups: dict[float, list[dict]] = {}
    for t in teams:
        groups.setdefault(round(t["win_pct"], 6), []).append(t)

    ordered: list[dict] = []
    for pct in sorted(groups, reverse=True):
        group = groups[pct]
        if len(group) == 1:
            group[0]["tiebreak"] = ""
            ordered.extend(group)
            continue

        if len(group) == 2:
            a, b = group
            direction, step = break_two_way_tie(a, b, records)
            if direction == 0:
                pair = sorted(group, key=lambda r: r["abbr"])
                for t in pair:
                    t["tiebreak"] = "unresolved (coin toss)"
                notes.append({
                    "teams": [t["abbr"] for t in pair],
                    "step": step,
                    "resolved": False,
                    "detail": "every tiebreaker through net touchdowns was level; "
                              "the NFL resolves this by coin toss, so the order "
                              "shown is alphabetical and not official",
                })
                ordered.extend(pair)
            else:
                pair = [a, b] if direction < 0 else [b, a]
                pair[0]["tiebreak"] = f"won {step}"
                pair[1]["tiebreak"] = f"lost {step}"
                notes.append({
                    "teams": [t["abbr"] for t in pair],
                    "step": step,
                    "resolved": True,
                    "detail": f"{pair[0]['abbr']} ahead of {pair[1]['abbr']} on {step}",
                })
                ordered.extend(pair)
            continue

        # Three or more teams level. The NFL's multi-team procedure is NOT the
        # two-team one applied pairwise: it eliminates one club at a time and
        # restarts from step 1 with the survivors, and a club that loses a step
        # drops out rather than simply sorting below. Implementing that faithfully
        # is its own piece of work (tracked as a follow-up), so this orders the
        # group provisionally and says so rather than shipping a wrong order
        # quietly.
        provisional = _provisional_multi_team_order(group, records)
        for t in provisional:
            t["tiebreak"] = "provisional (3+ team tie)"
        notes.append({
            "teams": [t["abbr"] for t in provisional],
            "step": "multi-team",
            "resolved": False,
            "detail": f"{', '.join(t['abbr'] for t in provisional)} are level; the NFL's "
                      f"multi-team procedure (eliminate one club, restart from step 1) "
                      f"is not implemented — this order is provisional",
        })
        ordered.extend(provisional)

    return ordered, notes


def _provisional_multi_team_order(group: list[dict], records: dict[str, dict]) -> list[dict]:
    """
    Best-effort ordering for a 3+ team tie, using only the steps that are
    well-defined for a group (no pairwise-only steps). Explicitly provisional.
    """
    members = {t["abbr"] for t in group}

    def key(rec):
        w, l, t, _, _ = _record_vs(rec, members - {rec["abbr"]})
        return (
            -_pct(w, l, t),                                    # record within the group
            -rec["div_pct"],
            -rec["conf_pct"],
            -(_strength(rec, records, beaten_only=True) or 0.0),
            -(_strength(rec, records, beaten_only=False) or 0.0),
            -rec["point_diff"],
            rec["abbr"],                                        # stable, deterministic
        )

    return sorted(group, key=key)


# ---------------------------------------------------------------------------
# Table assembly
# ---------------------------------------------------------------------------

def build_standings(games: list[dict]) -> dict:
    """
    Full standings structure from a season game log.

    Returns:
        {
          "conferences": [
            {"name": "AFC", "divisions": [
                {"name": "East", "label": "AFC East", "teams": [record, ...]},
                ...]},
            ...],
          "notes": [tiebreak note, ...],
        }
    """
    records = compute_records(games)

    by_div: dict[tuple[str, str], list[dict]] = {}
    for rec in records.values():
        by_div.setdefault((rec["conference"], rec["division"]), []).append(rec)

    conferences = []
    all_notes: list[dict] = []
    for conf in CONFERENCE_ORDER:
        divisions = []
        for div in DIVISION_ORDER:
            teams = by_div.get((conf, div), [])
            if not teams:
                continue
            ordered, notes = _sort_division(teams, records)
            all_notes.extend(notes)
            divisions.append({"name": div, "label": f"{conf} {div}", "teams": ordered})
        conferences.append({"name": conf, "divisions": divisions})

    return {"conferences": conferences, "notes": all_notes}


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
# Mobile-first, and deliberately NOT a copy of the MLB pattern. What the repo
# already does (box_score/build_box_score.py):
#
#   * PAGE_CSS's `@media(max-width:680px)` stacks two-column LAYOUT regions —
#     it never cuts a table column.
#   * `_mi_std_table()` / `_mi_simple_standings()` handle narrow screens by
#     pre-trimming the column set at BUILD time to six short columns
#     (Team/W/L/Pct/GB/Strk) and rendering with inline styles only.
#
# The second pattern is the closest precedent and it does not stretch to NFL:
# the ticket's column list is nine wide, two of them five-character records
# (DIV, CONF), so a single build-time trim would have to drop DIV and CONF on
# every screen — losing the two columns that make an NFL table an NFL table.
# So the columns are cut by width instead, and the 680px value from PAGE_CSS is
# reused as the top breakpoint so the component agrees with the rest of the
# repo where they overlap. Below it, each breakpoint removes exactly the next
# column off the BOTTOM of the ticket's priority list (PCT first, then CONF,
# then DIV, then DIFF).
#
# Two things keep the "no horizontal scroll, ever" promise even where the
# <style> block is stripped (Substack, Gmail, and most email clients):
#   * every cell also carries inline styles, so the table degrades to a plain,
#     readable, full-width table rather than an unstyled one; and
#   * `table-layout:fixed` + `width:100%` + `word-break` means the table can
#     never exceed its container no matter how many columns survive.
# A caller that knows its destination strips <style> can pre-trim instead by
# passing `columns=` — the same build-time trim MLB does.

_INK = "#1a1a1a"; _MUTED = "#5f5f5f"; _HAIR = "#e4e4e4"
_SANS = "Arial,Helvetica,sans-serif"
_MONO = "'Courier New',Courier,monospace"

# Breakpoints cut from the bottom of the priority list. 680px matches
# PAGE_CSS in box_score/build_box_score.py; the rest are derived from the
# width each remaining column needs at 11px monospace.
_CUT_RULES = [
    (680, ["nflst-pct"]),
    (560, ["nflst-pct", "nflst-conf"]),
    (460, ["nflst-pct", "nflst-conf", "nflst-div"]),
    (380, ["nflst-pct", "nflst-conf", "nflst-div", "nflst-diff"]),
]

RESPONSIVE_CSS = "\n".join(
    f"@media only screen and (max-width:{px}px){{"
    + "".join(f".nflst .{c}{{display:none;}}" for c in classes)
    + "}"
    for px, classes in _CUT_RULES
)


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def render_standings_html(standings: dict, columns: list[str] | None = None,
                          include_style: bool = True,
                          show_notes: bool = True) -> str:
    """
    Render the standings component.

    columns       — optional build-time trim: a list of COLUMNS keys to keep,
                    for destinations known to strip <style>. Default: all.
    include_style — emit the <style> block carrying the responsive cut rules.
    show_notes    — append the tiebreaker footnotes (which ties were decided by
                    what, and which are unresolved).
    """
    active = [c for c in COLUMNS if columns is None or c[0] in columns]

    parts = []
    if include_style:
        parts.append(f"<style>\n{RESPONSIVE_CSS}\n</style>")
    parts.append(f'<div class="nflst" style="font-family:{_SANS};color:{_INK};'
                 f'max-width:680px;margin:0 auto;">')

    for conf in standings["conferences"]:
        parts.append(
            f'<div style="border-top:2px solid {_INK};border-bottom:1px solid {_INK};'
            f'margin:22px 0 10px;padding:3px 0;font-size:12px;font-weight:bold;'
            f'letter-spacing:.09em;text-transform:uppercase;color:{_MUTED};">'
            f'{_e(conf["name"])}</div>'
        )
        for div in conf["divisions"]:
            parts.append(_render_division(div, active))

    if show_notes and standings.get("notes"):
        parts.append(_render_notes(standings["notes"]))

    parts.append("</div>")
    return "\n".join(parts)


def _render_division(div: dict, active: list[tuple[str, str, str]]) -> str:
    # overflow-wrap:anywhere is what holds the no-horizontal-scroll promise in
    # the worst case — a destination that strips the <style> block, so all nine
    # columns survive down to a 320px phone. Without it a header like "W-L-T"
    # sets a min-content floor the fixed layout cannot go under, and the table
    # pushes a few pixels past the viewport.
    wrap = "overflow-wrap:anywhere;word-break:break-word;"
    th = (f'padding:2px 2px;border-bottom:1px solid {_INK};font-family:{_SANS};'
          f'font-size:10px;font-weight:bold;color:{_MUTED};text-align:right;{wrap}')
    th0 = th + "text-align:left;"
    td = (f'padding:2px 2px;border-bottom:1px solid {_HAIR};font-family:{_MONO};'
          f'font-size:11px;text-align:right;color:{_INK};{wrap}')
    td0 = (f'padding:2px 2px;border-bottom:1px solid {_HAIR};font-family:{_SANS};'
           f'font-size:12px;font-weight:bold;text-align:left;color:{_INK};{wrap}')

    # table-layout:fixed splits width evenly unless told otherwise, and the
    # team/division column needs more than a stat column does. Giving only the
    # first column an explicit width leaves the rest sharing the remainder,
    # which keeps working as breakpoints hide columns.
    head = f'<th style="{th0}width:22%;">{_e(div["label"])}</th>' + "".join(
        f'<th class="{css}" style="{th}">{_e(label)}</th>' for _, label, css in active
    )
    rows = ""
    for rec in div["teams"]:
        cells = "".join(
            f'<td class="{css}" style="{td}">{_e(rec.get(key, ""))}</td>'
            for key, _, css in active
        )
        rows += f'<tr><td style="{td0}">{_e(rec["abbr"])}</td>{cells}</tr>'

    # table-layout:fixed is what makes "no horizontal scroll, ever" true even
    # with the <style> block stripped: the table is bound to its container.
    return (f'<table class="nflst-table" style="border-collapse:collapse;width:100%;'
            f'table-layout:fixed;margin:0 0 14px;">'
            f'<tr>{head}</tr>{rows}</table>')


def _render_notes(notes: list[dict]) -> str:
    lines = []
    for n in notes:
        marker = "" if n["resolved"] else "⚠ "
        lines.append(f'<div style="margin:2px 0;">{marker}{_e(n["detail"])}</div>')
    return (f'<div style="font-family:{_SANS};font-size:10px;color:{_MUTED};'
            f'border-top:1px solid {_HAIR};margin-top:10px;padding-top:6px;'
            f'line-height:1.5;">'
            f'<div style="font-weight:bold;margin-bottom:3px;">Tiebreakers</div>'
            + "".join(lines) + "</div>")


def render_standalone_page(standings: dict, title: str = "NFL Standings") -> str:
    """The component wrapped in a minimal page, for opening in a browser."""
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title>"
        "<style>body{margin:0;padding:16px;background:#fff;}</style></head><body>"
        + render_standings_html(standings) +
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Mock season
# ---------------------------------------------------------------------------

def mock_season(weeks: int = 6, seed: int = 43) -> list[dict]:
    """
    A deterministic mock regular season in the exact shape
    `fetch_sports_data.fetch_nfl_season_games()` produces.

    Built to EXERCISE the standings code, not to look like a real schedule:
    every team plays each of its three division rivals twice (so head-to-head
    and division record are always decidable) and the remaining weeks are
    cross-matchups, which gives common opponents and conference records.
    Scores are drawn from a seeded RNG, so the same table renders every run.
    """
    rng = random.Random(seed)
    teams = list(NFL_DIVISIONS)
    by_div: dict[tuple[str, str], list[str]] = {}
    for abbr, (conf, div) in NFL_DIVISIONS.items():
        by_div.setdefault((conf, div), []).append(abbr)

    # A 4-team division round robin is three rounds of two games. Six weeks
    # runs it twice with home field flipped, so every pair meets home and away
    # — which is what makes head-to-head, division record and the four-game
    # common-games minimum all decidable in the mock.
    rounds = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
    schedule: list[list[tuple[str, str]]] = []
    for week_index in range(6):
        pair_a, pair_b = rounds[week_index % 3]
        second_leg = week_index >= 3
        pairings = []
        for members in by_div.values():
            for i, j in (pair_a, pair_b):
                a, b = members[i], members[j]
                pairings.append((b, a) if second_leg else (a, b))
        schedule.append(pairings)

    games: list[dict] = []
    gid = 40000
    for week in range(1, weeks + 1):
        pairings = schedule[week - 1] if week <= len(schedule) else []
        if not pairings:
            # Past the division round robin, pair teams across divisions so the
            # log carries common opponents and inter-division conference games.
            shuffled = teams[:]
            rng.shuffle(shuffled)
            pairings = [(shuffled[k], shuffled[k + 1]) for k in range(0, 32, 2)]
        for home, away in pairings:
            gid += 1
            hs = rng.choice([10, 13, 14, 17, 20, 20, 21, 23, 24, 27, 27, 30, 31, 34, 38])
            as_ = rng.choice([10, 13, 14, 17, 20, 20, 21, 23, 24, 27, 27, 30, 31, 34, 38])
            h_conf, h_div = NFL_DIVISIONS[home]
            a_conf, a_div = NFL_DIVISIONS[away]
            games.append({
                "game_id": str(gid),
                "season_year": 2026,
                "season_type": REGULAR_SEASON,
                "week": week,
                "home_abbr": home, "away_abbr": away,
                "home_team": home, "away_team": away,
                "home_score": hs, "away_score": as_,
                "completed": True,
                "status": "final",
                "home_conference": h_conf, "home_division": h_div,
                "away_conference": a_conf, "away_division": a_div,
            })
    return games


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_games(path: Path) -> list[dict]:
    """Read `sports.nfl.season_games` out of a game_state.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("sports", {}).get("nfl", {}).get("season_games", []) or []


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the NFL standings HTML component.")
    ap.add_argument("--game-state", type=Path,
                    help="read the season game log from this game_state.json "
                         "instead of using mock data")
    ap.add_argument("--weeks", type=int, default=6,
                    help="mock season length in weeks (default 6)")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "nfl_standings_mock.html",
                    help="output HTML path")
    ap.add_argument("--fragment", action="store_true",
                    help="write the bare component instead of a standalone page")
    args = ap.parse_args()

    if args.game_state:
        games = load_games(args.game_state)
        source = f"{args.game_state} ({len(games)} games)"
    else:
        games = mock_season(weeks=args.weeks)
        source = f"mock season, {args.weeks} week(s), {len(games)} games"

    standings = build_standings(games)
    html_out = (render_standings_html(standings) if args.fragment
                else render_standalone_page(standings))
    args.out.write_text(html_out, encoding="utf-8")

    resolved = sum(1 for n in standings["notes"] if n["resolved"])
    open_ties = sum(1 for n in standings["notes"] if not n["resolved"])
    print(f"[nfl_standings] source: {source}")
    print(f"[nfl_standings] ties decided: {resolved}, unresolved/provisional: {open_ties}")
    print(f"[nfl_standings] wrote {args.out} ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
