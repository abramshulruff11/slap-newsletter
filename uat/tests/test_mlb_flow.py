"""
Run:  python -X utf8 uat/tests/test_mlb_flow.py

SLA-142: MLB game flow, computed from ESPN's line score (mlb_bundle.py), shown
to every pass as the MLB GAME FLOW block, and checked by Pass 3 (Check 3D).

The fixtures are real ESPN data, frozen through fetch_sports_data's own
fetch and parse functions:
  uat/fixtures/mlb/game_state_2026-10-18_alcs_g5.json      ALCS Game 5, TB 3 @ CLE 1:
      the game the 2026-10-19 issue got wrong ("a two-RBI single in the 5th
      to put the Rays up 2-0"; it was the 3rd and made it 3-0)
  uat/fixtures/mlb/game_state_2026-08-21_heavy_regular.json 15 games, four
      extra-inning walk-offs, a ninth-inning walk-off from four down
  ..._2026-10-05_alds_g2_nyy_tb.json, ..._2026-10-06_nlds_g3_lad_atl.json:
      one game each, behind two more shipped errors the archive replay
      (uat/replay_mlb_flow.py) found
No API calls, no network, no database.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import claim_validator as cv  # noqa: E402
import mlb_bundle as mb  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


FIX = REPO / "uat" / "fixtures" / "mlb"
ALCS = json.loads((FIX / "game_state_2026-10-18_alcs_g5.json").read_text(encoding="utf-8"))
HEAVY = json.loads((FIX / "game_state_2026-08-21_heavy_regular.json").read_text(encoding="utf-8"))


def games(gs):
    return gs["sports"]["mlb"]["yesterday_games"]


def by_abbr(gs, away, home):
    return next(g for g in games(gs) if g["away_abbr"] == away and g["home_abbr"] == home)


def flags_for(text, gs, outcomes=None):
    return cv.check_mlb_flow_claims(text, gs, outcomes)


def levels(text, gs):
    out = []
    cv.check_mlb_flow_claims(text, gs, out)
    return [o[0] for o in out]


# ---------------------------------------------------------------------------
print("The game the 2026-10-19 issue got wrong: ALCS Game 5")
g5 = games(ALCS)[0]
f = mb.flow(g5)
check("scored first in the top 1st, and that lead was for good",
      (f["events"][0]["inning"], f["events"][0]["half"], f["for_good"]["inning"]), (1, "top", 1))
check("wire to wire", f["wire_to_wire"], True)
check("score after each scoring half: 1-0, 2-0, 3-0, 3-1",
      [(x["half"], x["inning"], x["away"], x["home"]) for x in f["halves"] if x["runs"]],
      [("top", 1, 1, 0), ("top", 2, 2, 0), ("top", 3, 3, 0), ("bottom", 6, 3, 1)])
check("Hicks's single came in the 3rd and scored one run",
      [(p["inning"], p["runs"], p["batter"][1]) for p in f["plays"]],
      [(1, 1, "deluca"), (2, 1, "aranda"), (3, 1, "hicks"), (6, 1, "delauter")])
check("the replay review is not part of the play text",
      f["plays"][0]["text"], "DeLuca walked, Díaz scored, Simpson to second, Vilade to third")
b = mb.bundle(g5)
check("the bundle opens with the result and series lines", b["result"],
      ["Tampa Bay Rays 3, Cleveland Guardians 1 — Tampa Bay Rays wins",
       "  PLAYOFFS — TB leads series 3-2. Next: Game 6."])
check("by half-inning", b["by_line"],
      "By half-inning (runs scored only): top 1st TB 1-0; top 2nd TB 2-0; top 3rd TB 3-0; bottom 6th TB 3-1.")
check("flow", b["flow_line"],
      "Flow: TB led wire to wire (scored first, top 1st); TB's lead was as big as 3 (TB 3-0 after the top 3rd).")
check("the Hicks play line", "- top 3rd, TB 3-0: Hicks singled to center, Simpson scored" in b["play_lines"], True)
lines, size = mb.render(ALCS)
check("a postseason game is shown in full", size, {"401930515": "full"})

# ---------------------------------------------------------------------------
print("Walk-offs, extras, comebacks (2026-08-21)")
chw = mb.flow(by_abbr(HEAVY, "NYM", "CHW"))
check("CHW: down 4-0, tied in the 6th, walk-off in the 9th",
      (chw["max_deficit"]["home"][0], [e["inning"] for e in chw["events"] if e["kind"] == "tie"],
       chw["walk_off"], chw["for_good"]["inning"]), (4, [6], True, 9))
phi = mb.flow(by_abbr(HEAVY, "STL", "PHI"))
check("PHI: eight lead swings, ahead for good in the bottom 10th, walk-off, 10 innings",
      (sum(e["kind"] in ("go_ahead", "tie") for e in phi["events"]), phi["for_good"]["inning"],
       phi["walk_off"], phi["innings"]), (8, 10, True, 10))
mil = mb.flow(by_abbr(HEAVY, "ATL", "MIL"))
check("a home win with no bottom of the 9th: 17 halves, not a walk-off",
      (len(mil["halves"]), mil["walk_off"], mil["wire_to_wire"]), (17, False, True))
cle = mb.flow(by_abbr(HEAVY, "CLE", "COL"))
check("a road win ends in the top of the 9th's inning; no walk-off", (cle["walk_off"], cle["innings"]), (False, 9))
check("a shutout says so", "shutout" in mb.bundle(by_abbr(HEAVY, "CIN", "ARI"))["flow_line"], True)

# ---------------------------------------------------------------------------
print("Checked, or left out")
bad = copy.deepcopy(g5)
bad["box_score"]["linescore"]["away_innings"][4] = "1"          # 4 runs by inning, final says 3
check("a line score that doesn't add up to the final: no bundle", mb.bundle(bad), None)
bad = copy.deepcopy(g5)
bad["box_score"]["agate"]["scoring_plays"].pop(1)               # plays no longer reconcile
bb = mb.bundle(bad)
check("plays that don't reconcile: flow kept, play lines dropped", (bb is not None, bb["play_lines"]), (True, []))
bad = copy.deepcopy(g5)
bad["completed"] = False
check("a game that isn't final: no bundle", mb.bundle(bad), None)
bad = copy.deepcopy(g5)
bad["box_score"] = None
check("no box score: no bundle, and no exception", mb.bundles({"sports": {"mlb": {"yesterday_games": [bad]}}}), [])

# ---------------------------------------------------------------------------
print("The budget")
lines, size = mb.render(HEAVY)
text = "\n".join(lines)
check("all 15 games of a full regular-season day fit the 4,000 cap", (len(size), len(text) <= mb.BUDGET), (15, True))
check("regular-season games default to their flow line", set(size.values()), {"flow"})
order = [l for l in lines if l.startswith("  ") and not l.startswith("    ")]
check("close games first: the 10-inning walk-off before the wire-to-wire wins",
      order.index("  St. Louis Cardinals 6, Philadelphia Phillies 7 — Philadelphia Phillies wins")
      < order.index("  Toronto Blue Jays 1, New York Yankees 3 — New York Yankees wins"), True)
small, ssize = mb.render(HEAVY, budget=1500)
check("a small budget shows fewer games and says how many it left out",
      (len(ssize) < 15, any("more MLB game(s) appear only as results above" in l for l in small)), (True, True))
check("...and never runs over", len("\n".join(small)) <= 1500, True)
_, story_size = mb.render(HEAVY, story="The Yankees beat the Blue Jays behind a big night from the bullpen.")
nyy = by_abbr(HEAVY, "TOR", "NYY")["game_id"]
check("a story game is shown in full", story_size.get(nyy), "full")
gs = copy.deepcopy(HEAVY)
log = mb.attach_story(gs, {"stories": [{"headline": "Red Sox hold off the Giants"}]})
check("attach_story logs the block's size", log.startswith("mlb flow: block ") and "cap 4,000" in log, True)
check("attach_story stores the story text in memory", "Red Sox" in gs["mlb_flow"]["story_text"], True)
check("attach_story never raises on nothing", mb.attach_story(None, {}), "mlb flow: no game state")

# ---------------------------------------------------------------------------
print("The ground truth every pass reads")
import runner_common  # noqa: E402
gt = runner_common.format_game_state_summary(copy.deepcopy(ALCS))
check("the MLB GAME FLOW block is in it", "## GROUND TRUTH: MLB GAME FLOW" in gt, True)
check("the game's result appears once (moved into the block, not repeated)",
      gt.count("Tampa Bay Rays 3, Cleveland Guardians 1"), 1)
check("the series line travels with it", gt.count("PLAYOFFS — TB leads series 3-2. Next: Game 6."), 1)
check("the Hicks play is in it", "Hicks singled to center, Simpson scored" in gt, True)
gt_small = runner_common.format_game_state_summary(copy.deepcopy(HEAVY))
check("every heavy-day game appears exactly once",
      all(gt_small.count(f"{g['away_team']} {g['away_score']}, {g['home_team']} {g['home_score']}") == 1
          for g in games(HEAVY)), True)
nobox = copy.deepcopy(ALCS)
games(nobox)[0]["box_score"] = None
gt_nobox = runner_common.format_game_state_summary(nobox)
check("no line score: the plain result and series lines, as before SLA-142",
      ("MLB GAME FLOW" in gt_nobox, "  Tampa Bay Rays 3, Cleveland Guardians 1 — Tampa Bay Rays wins" in gt_nobox,
       "    PLAYOFFS — TB leads series 3-2. Next: Game 6." in gt_nobox), (False, True, True))
over = {"home_team": "A", "away_team": "B", "home_score": 2, "away_score": 1, "winner": "A", "overtime": True,
        "series": {"series_over": True, "home_wins": 4, "away_wins": 2}}
check("game_result_lines keeps the old format (OT, series over)", mb.game_result_lines(over),
      ["B 1, A 2 (OT) — A wins", "  SERIES OVER. A advances. Final: 4-2."])

# ---------------------------------------------------------------------------
print("Check 3D: the archived recap's three errors")
WRONG = [
    "Liam Hicks dropped a two-RBI single in the 5th to put the Rays up 2-0.",
    "Chandler Simpson came around on a Ben Williamson single to make it 3-0.",
    "- Hicks: 2-RBI single, 5th inning",
]
for s in WRONG:
    check(f"HIGH: {s[:60]}", levels("The Rays beat Cleveland in the ALCS.\n" + s, ALCS), ["HIGH"])
fl = flags_for(WRONG[0], ALCS)[0]
check("the flag says where 2-0 really came", "the score became TB 2-0 in the 2nd" in fl, True)
check("the flag says where Hicks's run really came", "Hicks's run-scoring play(s) came in the top 3rd (TB 3-0)" in fl, True)
check("the flag is a safe HTML comment", (fl.count("-->"), "--" in fl.replace("<!--", "").replace("-->", "")), (1, False))
fl = flags_for("ALCS Game 5.\n" + WRONG[1], ALCS)[0]
check("the flag names who made it 3-0", "TB 3-0 came on Hicks's play in the top 3rd" in fl, True)

print("Check 3D: the corrected sentences stand")
RIGHT = [
    "Liam Hicks's RBI single in the 3rd made it 3-0.",
    "Jonathan Aranda singled in a run in the 2nd to make it 2-0.",
    "Tampa Bay led 3-0 in the 5th and never let go.",
    "The Rays took the lead for good in the 1st.",
    "Cleveland got one back in the bottom of the 6th to make it 3-1.",
    "The Rays led wire to wire.",
    "The Rays never trailed.",
]
for s in RIGHT:
    check(f"confirmed: {s[:60]}", levels("ALCS Game 5.\n" + s, ALCS), ["confirmed"])

print("Check 3D: who scored (a real Pass 2 run on this game invented the 1st-inning run)")
for s, want in [
    ("The Rays got on the board when Jake Mangum walked, scored on a wild pitch.", "HIGH"),
    ("The Rays went up 1-0 in the 1st when Díaz scored on DeLuca's bases-loaded walk.", "confirmed"),
    ("Cleveland's only run came when Kwan scored in the 6th.", "confirmed"),
    ("For the Rays, Hicks scored in the 3rd.", "HIGH"),
    ("The Rays' Hicks singled and scored Simpson in the 3rd.", None),     # transitive: not read, not flagged
    ("The Rays scored in each of the first three innings.", None),
]:
    check(f"{want}: {s[:60]}", levels("ALCS Game 5.\n" + s, ALCS), [want] if want else [])
fl = flags_for("ALCS Game 5.\nThe Rays got on the board when Jake Mangum walked, scored on a wild pitch.", ALCS)
check("the flag says he isn't in the box score", "Mangum is not in this game's box score" in fl[0], True)

print("Check 3D: two more shipped errors the archive replay found")
ALDS = json.loads((FIX / "game_state_2026-10-05_alds_g2_nyy_tb.json").read_text(encoding="utf-8"))
NLDS = json.loads((FIX / "game_state_2026-10-06_nlds_g3_lad_atl.json").read_text(encoding="utf-8"))
rice = ("The Rays took a 2-0 ALDS lead over the Yankees. Ben Rice did everything a single human being can do. "
        "Two solo homers — one in the third, one in the eighth — and he's now the first Yankees player with "
        "two multi-homer games in a single postseason.")
check("2026-10-06: Rice's homers were in the 4th and 8th, not the 3rd", levels(rice, ALDS), ["HIGH"])
check("...and the corrected sentence draws no flag",
      flags_for(rice.replace("one in the third", "one in the fourth"), ALDS), [])
kike = ("The Dodgers beat Atlanta in the NLDS. Kiké Hernández went full Postseason Kiké with a two-run homer "
        "in the 1st to give Yamamoto a cushion before he even took the mound.")
check("2026-10-07: Hernández's two-run homer came in the 4th, not the 1st", levels(kike, NLDS), ["HIGH"])
check("...and the corrected sentence draws no flag",
      flags_for(kike.replace("in the 1st to give Yamamoto a cushion before he even took the mound",
                             "in the 4th"), NLDS), [])

print("Check 3D: other claim kinds (2026-08-21)")
H = HEAVY
cases = [
    ("The White Sox came back from a four-run deficit to beat the Mets.", "confirmed"),
    ("The White Sox came back from a five-run deficit to beat the Mets.", "HIGH"),
    ("The White Sox walked it off in the 9th: a walk-off homer.", "confirmed"),
    ("The Yankees won it on a walk-off against Toronto.", "HIGH"),
    ("The Phillies went ahead for good in the 10th.", "confirmed"),
    ("The Phillies took the lead for good in the 6th.", "HIGH"),
    ("The Cardinals tied it in the 8th before the Phillies won in extras.", "confirmed"),
    ("The White Sox put up a four-run sixth against the Mets.", "confirmed"),
    ("The White Sox put up a three-run sixth against the Mets.", "HIGH"),
    ("The Mets never trailed until the White Sox rallied.", "HIGH"),
    ("The Red Sox took the lead in the 3rd against the Giants.", "confirmed"),
    ("The Giants took the lead in the 4th at Fenway.", "HIGH"),
    # The named team is not the subject: the Phillies were down 6-5 to St. Louis.
    ("Down 6-5 heading into the bottom of the 10th against the Cardinals.", "confirmed"),
    ("The Mets fell to a walk-off in the 9th against the White Sox.", "confirmed"),
    # Whether there was a walk-off is checked, not whose (that cost five false HIGHs).
    ("The Angels won it on a walk-off in the 10th against Texas.", "confirmed"),
    # The archive replay's real 2026-07-07 error, on this day's road win: no walk-off possible.
    ("Cleveland's 9-1 walk-off win over Colorado.", "HIGH"),
]
for s, want in cases:
    check(f"{want}: {s[:60]}", levels("MLB, Thursday night.\n" + s, H), [want])

print("Check 3D: what it must leave alone")
quiet = [
    # Not baseball: the Cardinals' football game, in a section with no baseball words.
    ("The Cardinals took the lead in the third and never looked back.", H, "Arizona's defense held."),
    # Not innings, not this game's score.
    ("The Rays won for the third straight time in the ALCS.", ALCS, ""),
    ("The Rays lead the series 3-2 heading home.", ALCS, ""),
    ("Tampa Bay's 3-1 win puts them one game from the pennant, and Rasmussen went seven innings.", ALCS, ""),
    # Another game, another year.
    ("In Game 4 the Rays took the lead in the 7th.", ALCS, ""),
    ("The Rays last won a pennant in 2020 after taking the lead in the 6th of Game 6.", ALCS, ""),
    # A runner who scored is not a batter.
    ("Simpson scored on Hicks's single in the 3rd to make it 3-0.", ALCS, ""),
    # Two games named: can't tell which, so nothing.
    ("The Yankees and the Red Sox both took the lead in the 2nd.", H, ""),
]
# The shapes the full archive replay (162 issues) wrongly flagged before
# they were fixed, rebuilt on the 2026-08-21 fixture:
quiet += [
    ("Devers took the Giants' starter deep in the 6th with a 2-2 slider.", H, "Red Sox, bullpen."),   # a count
    ("The White Sox tied it in the 6th and beat New York 6-4 in the 9th.", H, ""),                  # a final score
    ("The White Sox erased a 4-0 deficit in the 6th.", H, ""),                                     # an earlier score
    ("The Mets fell to a walk-off in the 9th against the White Sox.", H, ""),                      # names the loser
    ("Late Tuesday night the Yankees took the lead in the 5th.", H, ""),                           # another day
    ("Wemby went 1-5 in the fourth.", ALCS, "Cleveland. The bullpen held."),                       # a quarter
    ("Hicks had an RBI single before the fifth inning started.", ALCS, "The Rays won."),           # a boundary
]
dh = copy.deepcopy(H)
twin = copy.deepcopy(by_abbr(dh, "NYM", "CHW"))
twin["game_id"] = "twin"
games(dh).append(twin)
dh_s = "The Mets took the lead in the 4th against the White Sox."
check("(without the twin game the same sentence is a HIGH)", levels("Bullpen night.\n" + dh_s, H), ["HIGH"])
quiet.append((dh_s, dh, "Bullpen night."))                                              # a doubleheader
for s, gs_, context in quiet:
    out = []
    fl = cv.check_mlb_flow_claims((context + "\n" + s).strip(), gs_, out)
    check(f"no flag: {s[:60]}", fl, [])

# ---------------------------------------------------------------------------
print("Pass 3 and the final re-check")
draft = (REPO / "archive" / "2026-10-19" / "newsletter_draft.html")
if draft.exists():
    html = draft.read_text(encoding="utf-8")
    _, n = cv.validate_claims(html, game_state=ALCS)
    check("validate_claims flags the archived recap", n >= 3, True)
    _, report = cv.final_history_check(html, ALCS, autocut=False)
    mlb = [r for r in report if r.get("kind") == "game flow"]
    check("the final re-check reports all three, HIGH, labelled game flow",
          (len(mlb), {r["level"] for r in mlb}), (3, {"HIGH"}))
    import pipeline_status  # noqa: E402
    import email_newsletter  # noqa: E402
    panel = email_newsletter._history_claims_html({"history_claims": mlb})
    check("the email labels them", "game flow CONTRADICTED by the game data" in panel, True)
fixed = ("<h1>Rays One Win From the World Series</h1><p>Tampa Bay 3, Cleveland 1. Liam Hicks's RBI single in "
         "the 3rd made it 3-0, and Cleveland got one back in the 6th. Seven innings from Rasmussen.</p>")
_, report = cv.final_history_check(fixed, ALCS, autocut=False)
check("a corrected recap leaves nothing to report", [r for r in report if r.get("kind") == "game flow"], [])
tweet = ("<h1>Rays</h1><p>Hicks's two-run single in the 5th put the Rays up 2-0 in the ALCS.</p>"
         "<blockquote>Hicks with a two-run single in the 5th! Rays up 2-0</blockquote>")
_, report = cv.final_history_check(tweet, ALCS, autocut=False)
check("a tweet in the section carrying the same figure sources it (Check 8 rule 1)",
      [r for r in report if r.get("kind") == "game flow"], [])

# ---------------------------------------------------------------------------
print("Prompts")
for d in ("prompts", "uat/prompts"):
    rf = (REPO / d / "rolling_feedback.txt").read_text(encoding="utf-8")
    check(f"{d}: RULE 3 sources the MLB GAME FLOW block", "The MLB GAME FLOW part of the GROUND TRUTH block" in rf, True)
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: editor Check 8 counts the block as a source and has Category E",
          ("the MLB GAME FLOW bundles" in ed, "CATEGORY E: HOW AN MLB GAME UNFOLDED" in ed), (True, True))
    check(f"{d}: editor Check 9 says how to act on a game-flow flag", "An MLB game-flow flag" in ed, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
