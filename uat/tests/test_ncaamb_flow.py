"""
Run:  python -X utf8 uat/tests/test_ncaamb_flow.py

SLA-146: men's college basketball game flow from ESPN's play-by-play.

  fetch          _parse_ncaamb_flow keeps the half scores and scoring plays, and
                 lives under game["game_flow"], never "box_score"
  ncaamb_bundle  half scores + plays -> one bundle per game, under a cap, on the
                 shared game_flow.py core; a play log that does not add up drops
                 the game's flow
  Check 3F       a halftime / lead / tie / comeback / overtime claim is confirmed
                 or flagged HIGH, only when the sentence ties to one game
  wiring         both runners attach the story order; the block leaves the
                 results list; the prompts treat the block as sourced

Real games from ESPN (uat/fixtures/ncaamb/games.json, built by
uat/build_ncaamb_fixture.py), parsed by the production function. No API calls,
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
import ncaamb_bundle as nb  # noqa: E402
from runner_common import format_game_state_summary  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


G = json.loads((REPO / "uat/fixtures/ncaamb/games.json").read_text(encoding="utf-8"))
CLOSE, WIRE, COMEBACK, OT = (G[k] for k in ("close", "wire", "comeback", "ot"))


def state(*games, **extra):
    return dict({"as_of_date": "2026-03-23",
                 "sports": {"ncaamb": {"label": "College Basketball",
                                       "yesterday_games": list(copy.deepcopy(games))}}}, **extra)


# --------------------------------------------------------------------------
print("fetch: _parse_ncaamb_flow")


def summary(plays, away=("36", "34"), home=("38", "36")):
    comp = {"competitors": [
        {"homeAway": "home", "linescores": [{"displayValue": v} for v in home]},
        {"homeAway": "away", "linescores": [{"displayValue": v} for v in away]}]}
    return {"header": {"competitions": [comp]}, "plays": plays}


def play(p, clock, a, h, scoring=True):
    return {"scoringPlay": scoring, "period": {"number": p}, "clock": {"displayValue": clock},
            "awayScore": a, "homeScore": h, "text": "x" * 200}


out = F._parse_ncaamb_flow(summary([play(1, "19:00", 0, 2), play(1, "18:00", 0, 2, scoring=False),
                                    play(1, "17:00", 3, 2)]))
check("only scoring plays are kept", [(p["period"], p["away"], p["home"]) for p in out["plays"]],
      [(1, 0, 2), (1, 3, 2)])
check("the text is truncated", len(out["plays"][0]["text"]), 80)
check("half scores kept", (out["away_periods"], out["home_periods"]), (["36", "34"], ["38", "36"]))
check("one half only is no flow", F._parse_ncaamb_flow(summary([], away=("36",), home=("38",))), None)
check("a play with no running score is skipped",
      len(F._parse_ncaamb_flow(summary([{"scoringPlay": True, "period": {"number": 1}}]))["plays"]), 0)
check("flow is not a box score", "box_score" in CLOSE, False)
check("ncaamb is a flow sport, not a box sport", "ncaamb" in F.FLOW_SPORTS, True)

# --------------------------------------------------------------------------
print("ncaamb_bundle: the facts")
f = nb.flow(CLOSE)
check("halftime score (Iowa at Florida)", f["periods"][0][1], (33, 31))
check("final", f["periods"][-1][1], (73, 72))
check("the winner is the away side", f["winner"], "away")
check("Iowa took the lead for good with 0:04 left", (f["for_good"]["period"], f["for_good"]["clock"]), (2, "0:04"))
check("not wire to wire", f["wire_to_wire"], False)
check("the winner's biggest deficit", f["max_deficit"]["away"][0], 4)
w = nb.flow(WIRE)
check("wire to wire (Arizona)", (w["wire_to_wire"], w["winner"]), (True, "home"))
c = nb.flow(COMEBACK)
check("a 19-point comeback", c["max_deficit"]["away"][0], 19)
o = nb.flow(OT)
check("overtime: periods and OT count", (o["played"], o["ot"]), (3, 1))
check("level after regulation", o["periods"][1][1], (73, 73))

b = nb.bundle(OT)
check("an OT bundle says so", "went to overtime" in b["flow_line"], True)
check("the by-half line carries the final after OT", "final UK 89-84" in b["by_line"], True)
check("close finish is called close", "decided by 1" in nb.bundle(CLOSE)["flow_line"], True)
check("wire to wire says so", "wire to wire" in nb.bundle(WIRE)["flow_line"], True)
check("a big deficit is named", "trailed by as many as 19" in nb.bundle(COMEBACK)["flow_line"], True)
check("ranks are ESPN's", nb.bundle(CLOSE)["ranks"], "No. 9 IOWA at No. 1 FLA")

print("ncaamb_bundle: a play log that does not add up is dropped, not guessed")
bad = copy.deepcopy(CLOSE)
bad["game_flow"]["plays"][10]["home"] += 1
check("a corrupted running score", nb.bundle(bad), None)
bad = copy.deepcopy(CLOSE)
bad["game_flow"]["plays"].pop()
check("a missing last basket", nb.bundle(bad), None)
bad = copy.deepcopy(CLOSE)
bad["home_score"] += 2
check("final score disagrees with the line score", nb.bundle(bad), None)
bad = copy.deepcopy(CLOSE)
del bad["game_flow"]
check("no flow fetched (past the cap)", nb.bundle(bad), None)
bad = copy.deepcopy(CLOSE)
bad["completed"] = False
check("not final", nb.bundle(bad), None)
bad = copy.deepcopy(CLOSE)
bad["game_flow"]["plays"][3]["period"] = 9
check("a play in a period that was not played", nb.bundle(bad), None)
check("an empty game state", nb.bundles({}), [])

print("ncaamb_bundle: the block and its cap")
gs = state(CLOSE, WIRE, COMEBACK, OT)
lines, size = nb.render(gs)
text = "\n".join(lines)
check("all four fit under the cap", len(size), 4)
check("under the cap", len(text) <= nb.BUDGET, True)
check("ranked games come before the unranked", text.index("Santa Clara") < text.index("Saint Joseph"), True)
small, ssize = nb.render(gs, budget=1350)
check("a small budget drops games, counted not silent", ("more game(s)" in "\n".join(small)) and len(ssize) < 4, True)
check("a small budget stays under it", len("\n".join(small)) <= 1350, True)
story_first, _ = nb.render(state(COMEBACK, WIRE), story="Saint Joseph's rallied at Cal", budget=1350)
check("a game the story names is shown ahead of one it does not",
      "Saint Joseph" in "\n".join(story_first), True)
tourney = copy.deepcopy(COMEBACK)
tourney["season_type"] = 3
tb, _ = nb.render(state(tourney, WIRE), budget=1350)
check("an NCAA tournament game gets priority", "Saint Joseph" in "\n".join(tb), True)
check("the log line reports the size", nb.attach_story(gs, {"stories": []}).startswith("ncaamb flow: block"), True)
check("attach_story with no state is quiet", nb.attach_story(None, {}), "ncaamb flow: no game state")
check("attach_story with no games says so",
      nb.attach_story({"sports": {}}, {}), "ncaamb flow: no college basketball games with a play log yesterday")

print("wiring: the results list and the ground truth")
gs = state(CLOSE, WIRE)
gs["sports"]["ncaamb"]["yesterday_games"][1]["game_flow"] = None       # WIRE has no flow: keeps its plain line
summ = format_game_state_summary(gs)
check("the block is in the ground truth", "MEN'S COLLEGE BASKETBALL GAME FLOW" in summ, True)
check("a bundled game is not repeated in the results list",
      summ.count("Iowa Hawkeyes 73, Florida Gators 72"), 1)
check("a game with no flow keeps its plain result line", "Utah State Aggies 66, Arizona Wildcats 78" in summ, True)
check("no college games, no block", "COLLEGE BASKETBALL GAME FLOW" in format_game_state_summary(
    {"as_of_date": "x", "sports": {"nba": {"label": "NBA", "yesterday_games": []}}}), False)

# --------------------------------------------------------------------------
print("Check 3F: claims vs the play log")
GS = state(CLOSE, WIRE, COMEBACK, OT)


def flags(sentence):
    return cv.check_ncaamb_flow_claims(sentence, GS)


def high(sentence):
    return bool(flags(sentence))


check("right halftime score stands", high("Iowa led 33-31 at halftime in a basketball thriller."), False)
check("wrong halftime score is HIGH", high("Iowa led 40-31 at halftime in a basketball thriller."), True)
check("a lead stated for the wrong side is HIGH", high("Florida led 33-31 at halftime of the basketball game."), True)
check("the trailing side's deficit is read", high("Florida trailed 33-31 at the half of the basketball game."), False)
check("...and a wrong trailing claim is HIGH", high("Iowa trailed 33-31 at the half of the basketball game."), True)
check("'entering the second half' is the halftime score",
      high("Iowa entered the basketball game's second half up 33-31, entering the second half."), False)
check("right wire to wire stands", high("Arizona never trailed against Utah State in the basketball game."), False)
check("a wrong 'never trailed' is HIGH", high("Saint Joseph's never trailed at Cal in the basketball game."), True)
check("comeback margin that happened stands",
      high("Saint Joseph's came back from a 19-point deficit at Cal in the basketball game."), False)
check("comeback margin that did not happen is HIGH",
      high("Saint Joseph's came back from a 25-point deficit at Cal in the basketball game."), True)
check("'erased a N-point deficit' is read",
      high("Saint Joseph's erased a 30-point deficit at Cal in the basketball game."), True)
check("OT that happened stands", high("Kentucky and Santa Clara went to overtime in basketball."), False)
check("OT that did not happen is HIGH", high("Iowa and Florida went to overtime in the basketball game."), True)
check("double OT that was a single OT is HIGH", high("Kentucky beat Santa Clara in double overtime in basketball."), True)
check("go-ahead for good in the right half stands",
      high("Iowa took the lead for good in the second half at Florida in basketball."), False)
check("go-ahead for good in the wrong half is HIGH",
      high("Iowa took the lead for good in the first half at Florida in basketball."), True)
check("go-ahead for good in OT stands",
      high("Kentucky took the lead for good in overtime against Santa Clara in basketball."), False)
check("a tie that happened stands", high("Iowa tied it in the second half at Florida in basketball."), False)
check("a different year or game is never checked",
      high("In 2019 Iowa and Florida went to overtime in a basketball game."), False)
check("a game number is never checked",
      high("Game 2 of the basketball series had Iowa and Florida going to overtime."), False)
check("a football section is never checked",
      high("Iowa took the lead for good in the first half against Florida in the Big Ten football opener."), False)
check("a sentence naming no college team is left alone", high("The home team went to overtime in basketball."), False)
check("two games in one sentence are left alone",
      high("Iowa and Arizona both went to overtime in basketball."), False)
check("a final score is not a halftime score",
      high("Iowa beat Florida 73-72 in the second half of a basketball thriller."), False)
check("a record is not a halftime score", high("Iowa (27-6) led at halftime of the basketball game."), False)

out: list = []
cv.check_ncaamb_flow_claims("Iowa led 33-31 at halftime in a basketball thriller.", GS, out)
check("confirmed claims are reported as such for the final re-check", out[0][0], "confirmed")
out = []
cv.check_ncaamb_flow_claims("Iowa led 40-31 at halftime in a basketball thriller.", GS, out)
check("a HIGH carries the kind", (out[0][0], out[0][3]), ("HIGH", "game flow"))
msg = flags("Iowa led 40-31 at halftime in a basketball thriller.")[0]
check("the flag names the real score", "IOWA 33-31" in msg, True)

print("Check 3F: through the section validator and the final re-check")
html = ("<h1>SLAP</h1><h2>Hoops</h2><p>Iowa led 40-31 at halftime in a basketball thriller at Florida.</p>")
annotated, n = cv.validate_section("Hoops", "<h2>Hoops</h2><p>Iowa led 40-31 at halftime in a basketball "
                                   "thriller at Florida.</p>", GS)
check("validate_section flags it", n >= 1 and "FACT FLAG [HIGH]" in annotated, True)
_, rep = cv.final_history_check(html, GS, autocut=False)
check("the final re-check lists it", [(r["level"], r.get("kind")) for r in rep], [("HIGH", "game flow")])
tweeted = html.replace("</p>", "</p><blockquote>Iowa up 40-31 at the half</blockquote>")
_, rep = cv.final_history_check(tweeted, GS, autocut=False)
check("a tweet in the section carrying the score sources it", rep, [])

print("alias index: college basketball names go by school")
idx = cv._alias_index([{"team": "Michigan Wolverines", "sport": "ncaamb", "opponent": ""},
                       {"team": "Michigan State Spartans", "sport": "ncaamb", "opponent": ""}])
check("Michigan is Michigan", cv._named("Michigan won", idx), {"Michigan Wolverines"})

# --------------------------------------------------------------------------
print("runner wiring and prompts")
for rel in ("generate_newsletter.py", "uat/run_uat.py"):
    src = (REPO / rel).read_text(encoding="utf-8")
    check(f"{rel} attaches the story order", "ncaamb_bundle.attach_story(game_state, story_plan)" in src, True)
for d in ("prompts", "uat/prompts"):
    rf = (REPO / d / "rolling_feedback.txt").read_text(encoding="utf-8")
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: RULE 3 treats the block as sourced", "MEN'S COLLEGE BASKETBALL GAME FLOW" in rf, True)
    check(f"{d}: the editor knows how to act on the flags",
          "college basketball game-flow flag" in ed and "BASKETBALL GAME FLOW" in ed, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for x in FAILS:
        print("  -", x)
    sys.exit(1)
print("all ok")
