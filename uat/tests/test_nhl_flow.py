"""
Run:  python -X utf8 uat/tests/test_nhl_flow.py

SLA-148: NHL game flow from the goal log.

  game_flow.py   the sport-neutral arithmetic: lead changes, ties, the go-ahead
                 for good, deficits, wire to wire; refuses a timeline that does
                 not add up
  nhl_bundle.py  goals + line score -> one bundle per game, under a cap, with a
                 shootout that is not a goal
  fetch          _parse_nhl_goals keeps the goals and drops the shootout
  Check 3E       a period / lead / tie / scorer claim is confirmed or flagged
  wiring         both runners attach the story order; the block leaves the
                 results list; the prompts treat the block as sourced

Real games from ESPN (uat/fixtures/nhl/games.json, built by
uat/build_nhl_fixture.py), parsed by the production functions. No API calls,
no network, no database.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import claim_validator as cv  # noqa: E402
import fetch_sports_data as F  # noqa: E402
import game_flow as gf  # noqa: E402
import nhl_bundle as nb  # noqa: E402
from runner_common import format_game_state_summary  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


G = json.loads((REPO / "uat/fixtures/nhl/games.json").read_text(encoding="utf-8"))
COMEBACK, OT, SO, SHUTOUT, PLAYOFF = (G[k] for k in ("comeback", "ot", "so", "shutout", "playoff"))


def state(*games, **extra):
    return dict({"as_of_date": "2026-03-10",
                 "sports": {"nhl": {"label": "NHL", "yesterday_games": list(copy.deepcopy(games))}}}, **extra)


# --------------------------------------------------------------------------
print("game_flow.build: the arithmetic")


def ev(p, a, h, clock="1:00"):
    return {"period": p, "clock": clock, "away": a, "home": h, "text": ""}


f = gf.build([ev(1, 1, 0), ev(1, 1, 1), ev(2, 1, 2), ev(3, 2, 2), ev(3, 3, 2)], (3, 2))
check("kinds in order", [e["kind"] for e in f["events"]], ["go_ahead", "tie", "go_ahead", "tie", "go_ahead"])
check("winner", f["winner"], "away")
check("for good is the last go-ahead by the winner", (f["for_good"]["period"], f["for_good"]["away"]), (3, 3))
check("not wire to wire", f["wire_to_wire"], False)
check("the winner's biggest deficit", f["max_deficit"]["away"][0], 1)
check("two lead changes: A led, B went ahead, A went ahead again", f["lead_changes"], 2)
check("ties", f["ties"], 2)

w = gf.build([ev(1, 1, 0), ev(2, 2, 0), ev(2, 2, 1)], (2, 1))
check("wire to wire", w["wire_to_wire"], True)
check("a cut is a cut, not a tie", [e["kind"] for e in w["events"]], ["go_ahead", "extend", "cut"])

c = gf.build([ev(1, 0, 1), ev(1, 0, 2), ev(2, 1, 2), ev(3, 2, 2), ev(3, 3, 2)], (3, 2))
check("a comeback: the winner trailed by 2", c["max_deficit"]["away"][0], 2)
check("a comeback is not wire to wire", c["wire_to_wire"], False)

check("scoreless, level (shootout) is allowed with a winner given",
      gf.build([], (0, 0), "home")["winner"], "home")
check("scoreless with no winner has none", gf.build([], (0, 0))["winner"], None)
check("both sides at once is refused", gf.build([ev(1, 1, 1)], (1, 1)), None)
check("a score going backwards is refused", gf.build([ev(1, 2, 0), ev(1, 1, 0)], (1, 0)), None)
check("a repeated score is refused", gf.build([ev(1, 1, 0), ev(1, 1, 0)], (1, 0)), None)
check("a timeline that ends off the final is refused", gf.build([ev(1, 1, 0)], (2, 0)), None)
check("score_after", (gf.score_after(f["events"], 1), gf.score_after(f["events"], 2), gf.score_after(f["events"], 3)),
      ((1, 1), (1, 2), (3, 2)))

# --------------------------------------------------------------------------
print("The fetch keeps goals and drops the shootout")


def play(text, period, away, home, scoring, ptype="Goal", clock="5:00"):
    return {"text": text, "type": {"text": ptype}, "period": {"number": period},
            "clock": {"displayValue": clock}, "awayScore": away, "homeScore": home, "scoringPlay": scoring}


goals, so = F._parse_nhl_goals({"plays": [
    play("Start of 1st Period", 1, 0, 0, False, "Period Start"),
    play("A B Goal (1) Wrist Shot, assists: C D (2)", 1, 1, 0, True),
    play("Shot on goal", 1, 1, 0, False, "Shot"),
    play("Start of Shootout", 5, 1, 0, False, "Period Start"),
    play("E F Goal Snap Shot", 5, 1, 0, True),
]})
check("the goal is kept", [(g["period"], g["away"], g["home"]) for g in goals], [(1, 1, 0)])
check("the shootout is noticed", so, True)
goals, so = F._parse_nhl_goals({"plays": [play("A B Goal (1) Wrist Shot", 1, 0, 1, True)]})
check("no shootout without one", (len(goals), so), (1, False))
check("the box score labels a shootout 'SO', not a second overtime",
      SO["box_score"]["linescore"]["period_labels"], ["P1", "P2", "P3", "OT1", "SO"])
check("an overtime game is labelled OT1", OT["box_score"]["linescore"]["period_labels"], ["P1", "P2", "P3", "OT1"])

# --------------------------------------------------------------------------
print("nhl_bundle on real games")
bc = nb.bundle(COMEBACK)
check("comeback: score by period", bc["by_line"], "By period: after 1st SEA 2-0; after 2nd NSH 3-2; after 3rd NSH 4-2.")
check("comeback: trailed by 2", "NSH trailed by as many as 2" in bc["flow_line"], True)
check("comeback: the go-ahead for good is in the 2nd", "NSH went ahead for good 3-2 (2nd, 14:25)" in bc["flow_line"], True)
check("comeback: six goals listed", len(bc["goal_lines"]) - 1, 6)
check("comeback: first goal line names scorer and assist",
      bc["goal_lines"][1], "- 1st 2:14, SEA 1-0: Kakko (from Wright)")

bo = nb.bundle(OT)
check("overtime: CAR won it in OT", "CAR won it in overtime (OT, 5:31)" in bo["flow_line"], True)
check("overtime: by period includes OT", bo["by_line"].endswith("after OT CAR 3-2."), True)

bs = nb.bundle(SO)
check("shootout: no game-winning goal", "won the shootout (no game-winning goal)" in bs["flow_line"], True)
check("shootout: level 4-4 after overtime", "level 4-4 after overtime" in bs["flow_line"], True)
check("shootout: no shootout attempts among the goals", len(bs["goal_lines"]) - 1, 8)
check("shootout: never says 'went ahead for good'", "for good" in bs["flow_line"], False)

bw = nb.bundle(SHUTOUT)
check("shutout + wire to wire", "CAR led wire to wire" in bw["flow_line"] and "shutout" in bw["flow_line"], True)
check("playoff games are marked", nb.bundle(PLAYOFF)["post"], True)
check("a regular-season game is not", bc["post"], False)
check("the bundle opens with the results lines", bc["result"][0], "Nashville Predators 4, Seattle Kraken 2 — Nashville Predators wins")

print("nhl_bundle refuses what it can't trust")
bad = copy.deepcopy(COMEBACK)
bad["box_score"]["agate"]["goals"][2]["clock"] = "x"      # a clock isn't checked; scores are
check("an odd clock is not a reason to drop the game", nb.bundle(bad) is not None, True)
bad = copy.deepcopy(COMEBACK)
bad["box_score"]["agate"]["goals"].pop()                   # a missing goal
check("goals that don't add up to the final: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(COMEBACK)
bad["box_score"]["agate"]["goals"][0]["period"] = 2        # right scores, wrong period
check("goals that don't match the line score by period: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(COMEBACK)
del bad["box_score"]["agate"]
check("a game stored before SLA-148 (no goal log): no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(COMEBACK)
bad["completed"] = False
check("a game that isn't final: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(SO)
bad["winner"] = "Nobody"
check("a shootout with an unknown winner: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(SO)
bad["home_score"] = 9
check("a shootout whose final isn't regulation+1: no bundle", nb.bundle(bad), None)
check("bundles() survives a junk game", len(nb.bundles(state(COMEBACK, {"completed": True, "box_score": 5}))), 1)

# --------------------------------------------------------------------------
print("The block, under its cap")
gs = state(COMEBACK, OT, SO, SHUTOUT, PLAYOFF)
lines, size = nb.render(gs)
text = "\n".join(lines)
check(f"within the {nb.BUDGET} cap", len(text) <= nb.BUDGET, True)
check("every game fits at this size", len(size), 5)
mixed = state(COMEBACK, SO, PLAYOFF)
tiny, tsize = nb.render(mixed, budget=1000)
check("a tight budget shows fewer games and says so", (len(tsize) < 3, "appear only as results above" in "\n".join(tiny)), (True, True))
check("the playoff game is shown first when the budget is tight", list(tsize)[:1], [PLAYOFF["game_id"]])
check("an empty day renders nothing", nb.render(state()), ([], {}))
check("no goal log, nothing rendered", nb.render({"sports": {"nhl": {"yesterday_games": [
    {"completed": True, "game_id": "1", "home_team": "A", "away_team": "B"}]}}}), ([], {}))
story_gs = state(COMEBACK, SO)
tiny_plain, sz_plain = nb.render(story_gs, story="", budget=1250)
tiny_story, sz_story = nb.render(story_gs, story="A big night for the Nashville Predators", budget=1250)
check("without the story the same game is flow-only", sz_plain.get(COMEBACK["game_id"]), "flow")
check("a story game is shown in full when the budget is tight",
      sz_story.get(COMEBACK["game_id"]), "full")
check("..." + " and the log line says so", nb.attach_story(story_gs, {"stories": [{"headline": "Predators rally"}]}).startswith("nhl flow: block"), True)

print("Nothing said twice")
gs = state(COMEBACK, OT)
full = format_game_state_summary(gs)
check("the block is in the ground truth", "## GROUND TRUTH: NHL GAME FLOW" in full, True)
check("a shown game leaves the results list",
      full.count("Nashville Predators 4, Seattle Kraken 2") == 1, True)
none_shown = format_game_state_summary(state(COMEBACK, {**OT, "box_score": {}}))
check("a game with no bundle keeps its plain result line",
      "Carolina Hurricanes 3, Philadelphia Flyers 2" in none_shown.split("## GROUND TRUTH: YESTERDAY'S GAME RESULTS")[1], True)

# --------------------------------------------------------------------------
print("Check 3E: the claims")
GS = state(COMEBACK, OT, PLAYOFF)      # one game per team, as on a real night


def flags(sentence, gs=GS):
    return cv.check_nhl_flow_claims(sentence, gs)


check("true: the Predators went ahead for good in the second",
      flags("The Predators went ahead for good in the second period on a Stamkos-era Nashville power play goal."), [])
check("false: the Predators took the lead in the third",
      len(flags("The Predators took the lead in the third period against the Kraken.")), 1)
check("the flag is a HIGH FACT FLAG naming the real period",
      all(x in (flags("The Predators took the lead in the third period against the Kraken.") or [""])[0]
          for x in ("FACT FLAG [HIGH]", "2nd")), True)
check("true: the Predators tied it in the second",
      flags("Nashville tied it in the second period against Seattle."), [])
check("false: the Predators tied it in the third",
      len(flags("Nashville tied it in the third period against Seattle.")), 1)
check("true: Seattle led 2-0 after the first period",
      flags("Seattle led 2-0 after the first period against Nashville."), [])
check("false: Seattle led 3-0 after the first period",
      len(flags("Seattle led 3-0 after the first period against Nashville.")), 1)
check("true: Schaefer scored in the second period",
      flags("Schaefer scored in the second period for Nashville."), [])
check("false: Schaefer scored in the third period",
      len(flags("Schaefer scored in the third period for Nashville against the Kraken.")), 1)
check("false: Nashville never trailed",
      len(flags("Nashville never trailed against the Kraken, and the goalie stopped everything late.")), 1)
check("true: Carolina led wire to wire (the shutout)",
      flags("Carolina led wire to wire against Philadelphia and never gave up a goal in the first period.",
            state(SHUTOUT)), [])
check("a team in two games is ambiguous, so left alone (never on a real night)",
      flags("Carolina took the lead in the third period against Philadelphia.", state(OT, SHUTOUT)), [])
check("true: came back from a two-goal deficit",
      flags("Nashville came back from a two-goal deficit against Seattle."), [])
check("false: came back from a three-goal deficit",
      len(flags("Nashville came back from a three-goal deficit against Seattle.")), 1)
check("an overtime winner in OT: confirmed",
      flags("Carolina went ahead for good in overtime against Philadelphia."), [])
check("an overtime winner in the third: contradicted",
      len(flags("Carolina went ahead for good in the third period against Philadelphia.")), 1)
check("a claim about no NHL team in a hockey-looking section is left alone",
      flags("The Cowboys took the lead in the third period."), [])
check("a section that doesn't read as hockey is left alone",
      flags("Nashville took the lead in the third quarter."), [])
check("a sentence naming two games is not guessed at",
      flags("Seattle and Carolina both took the lead in the third period."), [])
check("a different year is left alone",
      flags("In 2019, Nashville took the lead in the third period against Seattle."), [])
check("with no NHL games yesterday nothing is checked",
      flags("Nashville took the lead in the third period against Seattle.", state()), [])
print("  ...and the false alarms the archive replay found (2026-05-14 .. 05-26)")
check("'midway through the first period' is during it, not after it",
      flags("Down 2-0 midway through the first period, Nashville rallied against Seattle to win 4-2 on a late goal."), [])
check("a final score after 'beat' is not a period score",
      flags("Nashville rallied to beat Seattle 4-2 and the goal in the first period stood out."), [])
check("a series score is not a period score",
      flags("Montreal took a 2-1 series lead after the second period against Buffalo."), [])
check("'after trailing 3-1 in the first' names the deficit, not where the goals came",
      flags("Caufield scored twice and Montreal scored four straight goals after trailing 1-0 in the first period against Buffalo."), [])
check("...but a scorer's goal in the wrong period is still caught",
      len(flags("Caufield scored in the third period for Montreal against Buffalo.")), 1)
check("a bare record before 'in overtime' is not a score claim",
      flags("Philadelphia is 1-3 in overtime this series against Carolina, with a goal each time."), [])
outcomes: list = []
cv.check_nhl_flow_claims("Nashville tied it in the second period against Seattle. "
                         "Nashville tied it in the third period against Seattle.", GS, outcomes)
check("outcomes carry confirmed and HIGH", [o[0] for o in outcomes], ["confirmed", "HIGH"])

print("Check 3E is part of validation, and of the editor re-check")
html = ("<h2>Predators rally</h2><p>The Predators took the lead in the third period against the Kraken.</p>")
fixed, found = cv.final_history_check(html, GS, autocut=False)
check("final_history_check reports it", [(r["level"], r["kind"]) for r in found], [("HIGH", "game flow")])
check("...with a note after the heading", "NHL GAME FLOW CLAIM LEFT IN BY THE EDITOR" in fixed, True)
sec, n = cv.validate_section("Predators rally", html, GS)
check("validate_section flags it", "contradicts the goal log" in sec, True)
ok_html = "<h2>Predators rally</h2><p>Nashville tied it in the second period against Seattle.</p>"
check("a true sentence adds no flag", cv.final_history_check(ok_html, GS, autocut=False)[1], [])

# --------------------------------------------------------------------------
print("Wiring")
for path in ("generate_newsletter.py", "uat/run_uat.py"):
    src = (REPO / path).read_text(encoding="utf-8")
    i = src.find("nhl_bundle.attach_story(game_state, story_plan)")
    k = src.find("run_pass2(story_plan", i)
    check(f"{path}: attach_story before Pass 2", 0 < i < k, True)
for d in ("prompts", "uat/prompts"):
    rf = (REPO / d / "rolling_feedback.txt").read_text(encoding="utf-8")
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: RULE 3 names the NHL GAME FLOW block", "NHL GAME FLOW part of the GROUND TRUTH block" in rf, True)
    check(f"{d}: editor Check 9 says how to fix an NHL game-flow flag",
          "NHL game-flow flag" in " ".join(ed.split()), True)
rc = (REPO / "runner_common.py").read_text(encoding="utf-8")
check("runner_common builds the NHL block", "nhl_bundle.summary_lines(game_state)" in rc, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
