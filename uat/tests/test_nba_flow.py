"""
Run:  python -X utf8 uat/tests/test_nba_flow.py

SLA-147: NBA game flow from the play-by-play, in three tiers.

  fetch          _parse_nba_plays keeps every basket with its shooter and tallies
                 the running score itself (ESPN's is stale on some plays)
  nba_bundle.py  baskets + line score -> one bundle per game, under a cap; names
                 only when every shooter's points match the box score; runs and
                 streaks for headline games
  Check 3G       a quarter / lead / tie / shooter / run / streak / points claim is
                 confirmed or flagged
  wiring         both runners attach the story order; the block leaves the
                 results list; the prompts treat the block as sourced

Real games from ESPN (uat/fixtures/nba/games.json, built by
uat/build_nba_fixture.py), parsed by the production functions. No API calls,
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
import nba_bundle as nb  # noqa: E402
from runner_common import format_game_state_summary  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


G = json.loads((REPO / "uat/fixtures/nba/games.json").read_text(encoding="utf-8"))
OT, WIRE, PLAYOFF = G["ot"], G["wire"], G["playoff"]
LAL, MIN, CHI, PHX = G["stale_gs"], G["stale_min"], G["stale_chi"], G["stale_phx"]


def state(*games, **extra):
    return dict({"as_of_date": "2026-05-20",
                 "sports": {"nba": {"label": "NBA", "yesterday_games": list(copy.deepcopy(games))}}}, **extra)


# --------------------------------------------------------------------------
print("The fetch: every basket, with ESPN's stale running score replaced by our own")


def play(text, period, clock, team, pts, away=0, home=0, scoring=True, shooter="1", assist=None):
    parts = [{"athlete": {"id": shooter}}] + ([{"athlete": {"id": assist}}] if assist else [])
    return {"text": text, "type": {"text": "Jump Shot"}, "period": {"number": period},
            "clock": {"displayValue": clock}, "awayScore": away, "homeScore": home,
            "scoringPlay": scoring, "scoreValue": pts, "team": {"id": team}, "participants": parts}


summary = {
    "header": {"competitions": [{"competitors": [{"id": "10", "homeAway": "away"}, {"id": "20", "homeAway": "home"}]}]},
    "plays": [
        play("Away One makes 25-foot three point jumper", 1, "10:30", "10", 3, away=3, home=0, shooter="3"),
        # the home basket is listed AFTER the away one but happened earlier; its
        # running score says 0-2 where ESPN's own tally is stale
        play("Home One makes layup (Home Two assists)", 1, "11:00", "20", 2, away=0, home=2, shooter="1", assist="2"),
        play("Home Two misses jump shot", 1, "10:00", "20", 0, scoring=False),
        play("Home Two makes free throw 1 of 2", 1, "0:45.3", "20", 1, away=3, home=2, shooter="2"),   # stale: should be 3-3
        play("Away One makes jumper", 2, "11:50", "10", 2, away=5, home=3, shooter="3"),
    ],
}
pl = F._parse_nba_plays(summary)
check("sorted by period and clock, not feed order",
      [(p["period"], p["clock"], p["side"]) for p in pl],
      [(1, "11:00", "home"), (1, "10:30", "away"), (1, "0:45.3", "home"), (2, "11:50", "away")])
check("the running score is tallied from the points, not copied",
      [(p["away"], p["home"]) for p in pl], [(0, 2), (3, 2), (3, 3), (5, 3)])
check("misses are dropped", len(pl), 4)
check("shooter and assister come from the text", (pl[0]["shooter"], pl[0]["assist"]), ("Home One", "Home Two"))
check("shooter id is the first participant", (pl[0]["shooter_id"], pl[1]["shooter_id"]), ("1", "3"))
check("seconds left on the clock are kept for the finish", (pl[0]["secs"], pl[2]["secs"]), (660.0, 45.3))
check("a play with no team, or no points, is dropped", len(F._parse_nba_plays({"header": summary["header"], "plays": [
    play("x makes y", 1, "5:00", "99", 2), play("x makes y", 1, "5:00", "10", 0)]})), 0)
check("a game with no plays parses to an empty list", F._parse_nba_plays({}), [])

print("...on the real games ESPN's own score was wrong in")
for k, g in (("MIN @ MIA", MIN), ("CHI @ TOR", CHI), ("GSW @ LAL", LAL), ("PHX @ POR", PHX)):
    plays = g["box_score"]["agate"]["plays"]
    check(f"{k}: our tally ends on the final score", (plays[-1]["away"], plays[-1]["home"]),
          (g["away_score"], g["home_score"]))
    check(f"{k}: and has a bundle", nb.bundle(g) is not None, True)

# --------------------------------------------------------------------------
print("game_flow helpers for basketball: runs, streaks, windows")


def e(side, pts, shooter="A", period=1, secs=500):
    return {"side": side, "pts": pts, "shooter": shooter, "period": period, "secs": secs,
            "away": 0, "home": 0, "clock": "5:00"}


evs = [e("home", 2, "H"), e("home", 3, "H"), e("away", 2, "X"), e("home", 2, "H"), e("home", 2, "J"),
       e("home", 3, "J"), e("away", 3, "X"), e("away", 2, "Y")]
check("unanswered runs", [(r["side"], r["pts"]) for r in nb.runs_of(evs)],
      [("home", 5), ("away", 2), ("home", 7), ("away", 5)])
check("individual streaks ignore the other side's baskets in between",
      [(s["shooter"], s["pts"], s["opp"]) for s in nb.streaks_of(evs) if s["side"] == "home"],
      [("H", 7, 2), ("J", 5, 0)])
check("a 5-0 window exists for home", nb.best_window(evs, "home", 5, 0), True)
check("a 9-2 window exists for home (7 + 2 allowed)", nb.best_window(evs, "home", 9, 2), True)
check("a 12-0 window does not", nb.best_window(evs, "home", 12, 0), False)

# --------------------------------------------------------------------------
print("nba_bundle on real games")
b = nb.bundle(OT)
check("overtime: score by quarter",
      b["by_line"], "By quarter: after Q1 NY 23-16; at the half CLE 48-46; after Q3 CLE 83-69; "
                    "after Q4 tied 101-101; after OT NY 115-104.")
check("overtime: the winner trailed by 22 and won", "NY trailed by as many as 22 (CLE 93-71, Q4 7:52) and won" in b["flow_line"], True)
check("overtime: the basket that won it, with its shooter",
      "NY took the lead for good 102-101 on Anunoby free throw (OT 4:45)" in b["flow_line"], True)
check("overtime: level after Q4", "level 101-101 after Q4" in b["flow_line"], True)
check("overtime: 5 lead changes, 3 ties", "5 lead changes, 3 ties" in b["flow_line"], True)
check("top scorers by half, OT apart",
      b["scorer_line"], ["Top scorers: CLE Mitchell 29 (16 first half, 13 second half); "
                         "NY Brunson 38 (14 first half, 22 second half, 2 OT)."])
check("...and by quarter in the full tier", b["scorer_line_q"][0].count("Brunson 38 (Q1 10, Q2 4, Q3 7, Q4 15, OT 2)"), 1)
check("team runs of 10+ are listed",
      b["seq_lines"][0].startswith("Runs: NY 12-0 (Q1 4:33 to Q1 2:44, CLE 13-8 -> NY 20-13); CLE 10-0"), True)
check("a run can span a quarter boundary, and the clock under a minute reads 0:xx",
      "NY 11-0 (Q4 0:19.3 to OT 1:49, CLE 101-99 -> NY 110-101)" in b["seq_lines"][0], True)
check("an individual streak is listed with what the other side did in between",
      b["seq_lines"][1], "Streaks: Brunson 11 straight NY points (Q4 5:44 to Q4 3:30, the other side scored 1 in between).")
check("the bundle opens with the results lines (and series state)",
      b["result"][0], "Cleveland Cavaliers 104, New York Knicks 115 (OT) — New York Knicks wins")
check("a playoff game is marked", b["post"], True)

bw = nb.bundle(WIRE)
check("wire to wire", "SA led wire to wire (scored first, Q1 11:15)" in bw["flow_line"], True)
check("wire to wire: no 'took the lead for good' line", "for good" in bw["flow_line"], False)
check("wire to wire: the 20-0 run", bw["seq_lines"][0].startswith("Runs: SA 20-0 (Q2 11:22 to Q2 7:09, SA 36-27 -> SA 56-27)"), True)

bp = nb.bundle(PLAYOFF)
check("close finish: the final minute names each shooter",
      bp["fin_full"], ["Final minute (OKC 118-113 going in): Gilgeous-Alexander basket (OKC 120-113, 42.7); "
                       "Caruso basket (OKC 122-113, 19.7)."])

bl = nb.bundle(LAL)
check("a blowout has no final-minute line", bl["fin_full"], [])
check("the go-ahead-for-good shooter is named even in a blowout",
      "LAL took the lead for good 34-33 on LaRavia three (Q1 0:30.4)" in bl["flow_line"], True)
check("a streak of 12 straight points",
      bl["seq_lines"], ["Streaks: Curry 12 straight GS points (Q1 9:01 to Q1 5:46, the other side scored 5 in between)."])
check("a regular-season game is not marked", bl["post"], False)

print("nba_bundle refuses what it can't trust")
bad = copy.deepcopy(OT)
bad["box_score"]["agate"]["plays"].pop(10)
check("a missing basket: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(OT)
bad["box_score"]["agate"]["plays"][0]["period"] = 2
check("baskets that don't match the line score by quarter: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(OT)
bad["box_score"]["agate"]["plays"][5]["pts"] = 3
check("a point value that doesn't add up: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(OT)
del bad["box_score"]["agate"]
check("a game stored before SLA-147 (no play log): no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(OT)
bad["completed"] = False
check("a game that isn't final: no bundle", nb.bundle(bad), None)
bad = copy.deepcopy(OT)
bad["home_score"] = 120
check("a final that isn't the sum of the quarters: no bundle", nb.bundle(bad), None)
check("bundles() survives a junk game", len(nb.bundles(state(OT, {"completed": True, "box_score": 5}))), 1)

print("...and names only what the box score vouches for")
bad = copy.deepcopy(OT)
bad["box_score"]["home"]["players"][0]["stats"]["PTS"] = "99"
nobody = nb.bundle(bad)
check("a shooter total that disagrees with the box score: the game keeps its flow", nobody is not None, True)
check("...but loses every name", ("Brunson" in nobody["flow_line"], nobody["scorer_line"], nobody["seq_lines"]),
      (False, [], ["Runs: " + nobody["seq_lines"][0][6:]]))
check("...and Pass 3 gets no shooters",
      (nb.claim_facts(state(bad))[0]["named"], nb.claim_facts(state(bad))[0]["points"]), (False, {}))
old = copy.deepcopy(OT)
for side in ("away", "home"):
    for pl_ in old["box_score"][side]["players"]:
        del pl_["id"]
check("a game stored without athlete ids: names are not trusted", nb.flow(old)["shooters_ok"], False)
dup = copy.deepcopy(OT)
n_ = 0
for pl_ in dup["box_score"]["agate"]["plays"]:
    if pl_["shooter"] == "Josh Hart" and n_ < 2:
        pl_["shooter"] = "Other Brunson"                    # same id, so the box score still agrees
        n_ += 1
dup_facts = nb.claim_facts(state(dup))[0]
check("two players sharing a surname: neither is attributable", "brunson" in dup_facts["points"], False)

# --------------------------------------------------------------------------
print("The block, under its cap")
gs = state(OT, WIRE, PLAYOFF, LAL, MIN, CHI, PHX)
lines, size = nb.render(gs)
text = "\n".join(lines)
check(f"within the {nb.BUDGET} cap", len(text) <= nb.BUDGET, True)
check("seven games all fit", len(size), 7)
check("postseason games get the sequences", [size[OT["game_id"]], size[PLAYOFF["game_id"]]], ["full", "full"])
check("a regular-season game never gets 'full' without a story", LAL["game_id"] in size and size[LAL["game_id"]] != "full", True)
tiny, tsize = nb.render(state(OT, LAL, MIN), budget=1500)
check("a tight budget shows fewer games and says so", (len(tsize) < 3, "appear only as results above" in "\n".join(tiny)), (True, True))
check("the playoff game is shown first when the budget is tight", list(tsize)[:1], [OT["game_id"]])
check("an empty day renders nothing", nb.render(state()), ([], {}))
check("no play log, nothing rendered", nb.render({"sports": {"nba": {"yesterday_games": [
    {"completed": True, "game_id": "1", "home_team": "A", "away_team": "B"}]}}}), ([], {}))
story_gs = state(LAL, MIN)
_, sz_plain = nb.render(story_gs, story="", budget=2200)
_, sz_story = nb.render(story_gs, story="A big night for the Los Angeles Lakers", budget=2200)
check("without the story the Lakers game is a summary", sz_plain.get(LAL["game_id"]), "summary")
check("a story game gets the sequences", sz_story.get(LAL["game_id"]), "full")
check("sequences are for headline games only: no regular-season 'full' unless a story names it",
      sz_story.get(MIN["game_id"]) != "full", True)
check("the log line says so",
      nb.attach_story(story_gs, {"stories": [{"headline": "Lakers rout Warriors"}]}).startswith("nba flow: block"), True)

print("Nothing said twice")
full = format_game_state_summary(state(OT, LAL))
check("the block is in the ground truth", "## GROUND TRUTH: NBA GAME FLOW" in full, True)
check("a shown game leaves the results list", full.count("Golden State Warriors 105, Los Angeles Lakers 127") == 1, True)
none_shown = format_game_state_summary(state(OT, {**LAL, "box_score": {}}))
check("a game with no bundle keeps its plain result line",
      "Golden State Warriors 105, Los Angeles Lakers 127" in none_shown.split("## GROUND TRUTH: YESTERDAY'S GAME RESULTS")[1], True)

# --------------------------------------------------------------------------
print("Check 3G: the claims")
GS = state(OT)
HEAD = "Knicks and Cavaliers basketball quarter recap.\n"


def flags(sentence, gs=GS, head=HEAD):
    return cv.check_nba_flow_claims(head + sentence, gs)


for label, sentence in (
        ("a score at the half and after three", "The Cavaliers led 48-46 at the half and 83-69 after the third quarter."),
        ("a margin entering the fourth", "Cleveland was up 14 entering the fourth quarter, but the Knicks erased a 22-point deficit."),
        ("a biggest deficit", "Knicks trailed by as many as 22 and still won."),
        ("a player's quarter", "Brunson scored 15 in the fourth quarter."),
        ("a player's half", "Brunson had 14 in the first half for the Knicks."),
        ("the for-good basket", "Anunoby's free throw in overtime put New York ahead for good."),
        ("a run that happened", "The Knicks ripped off a 12-0 run in the first quarter."),
        ("a run answered once", "The Knicks went on a 12-2 run in the first quarter."),
        ("a streak that happened", "Brunson scored 11 straight points for the Knicks down the stretch."),
        ("a tie", "It was tied 101-101 after the fourth quarter."),
        ("a quarter's score", "New York outscored Cleveland 14-3 in overtime."),
        ("a go-ahead three in the right quarter", "Mitchell hit the go-ahead three for the Cavaliers in the second quarter."),
        ("a game-tying three", "Shamet's game-tying three forced overtime for the Knicks."),
        ("a score read as the final", "The Knicks beat the Cavaliers 115-104 after trailing in the fourth quarter."),
):
    check(f"true: {label}", flags(sentence), [])

for label, sentence, said in (
        ("a score at the half", "The Cavaliers led 50-46 at the half.", "CLE 48-46"),
        ("a margin entering the fourth", "Cleveland was up 12 entering the fourth quarter.", "CLE 83-69"),
        ("a comeback bigger than it was", "The Knicks erased a 30-point deficit against Cleveland.", "at most 22"),
        ("a player's quarter", "Brunson scored 20 in the fourth quarter for the Knicks.", "scored 15"),
        ("a player's half", "Brunson had 20 in the first half for the Knicks.", "scored 14"),
        ("the wrong quarter for the for-good basket", "Anunoby's free throw in the third quarter put New York ahead for good.",
         "overtime"),
        ("a game-winner by someone else", "Brunson hit the game-winning three in overtime for the Knicks.", "Anunoby"),
        ("a go-ahead three in the wrong quarter", "Mitchell hit the go-ahead three for the Cavaliers in the fourth quarter.",
         "second quarter"),
        ("a bigger run than there was", "The Knicks ripped off a 20-0 run in the first quarter.", "12-0"),
        ("a longer streak than there was", "Brunson scored 25 straight points for the Knicks.", "was 11"),
        ("never trailed", "Knicks never trailed against the Cavaliers.", "trailed by as many as 22"),
        ("took the lead in the wrong quarter", "The Knicks took the lead for good in the third quarter.", "overtime"),
        ("a quarter's score", "New York outscored Cleveland 20-3 in overtime.", "NY"),
):
    out = flags(sentence)
    check(f"false: {label}", (len(out), said in (out[0] if out else "")), (1, True))
    check(f"  ...is a HIGH FACT FLAG ({label})", "FACT FLAG [HIGH]" in (out[0] if out else ""), True)

check("a team in two games is ambiguous, so left alone (never on a real night)",
      flags("Knicks never trailed against the Cavaliers.", state(OT, copy.deepcopy(OT))), [])
check("a section that doesn't read as basketball is left alone",
      cv.check_nba_flow_claims("Knicks and Cavaliers recap.\nThe Knicks never trailed.", GS), [])
check("a sentence naming two games is not guessed at",
      flags("Knicks and Spurs both took the lead in the third quarter.", state(OT, PLAYOFF)), [])
check("a different year is left alone", flags("In 2019, the Knicks never trailed against the Cavaliers."), [])
check("a series score is not a quarter's score", flags("The Knicks took a 1-0 lead after the third quarter against Cleveland."), [])
check("with no NBA games yesterday nothing is checked",
      flags("Knicks never trailed against the Cavaliers.", state()), [])
check("a player with no matching basket is not named when the names weren't vouched for",
      flags("Brunson hit the game-winning three in overtime for the Knicks.", state(bad)), [])
print("  ...and the false alarms the archive replay found (2026-04-21 .. 05-11)")
check("'outscored them 28-15 over the final 8:51' is a stretch, not a score",
      flags("The Cavaliers led by eight in the fourth quarter but the Knicks outscored them 28-15 over the final 8:51."), [])
check("'outscored' a quarter correctly is confirmed, not read as a score at some point",
      flags("The Knicks outscored the Cavaliers 32-18 in the fourth quarter."), [])
check("...and a wrong quarter is still caught",
      len(flags("The Knicks outscored the Cavaliers 28-15 in the fourth quarter.")), 1)
check("'forced overtime' names where the game went, not where the shot was",
      flags("Shamet's game-tying three forced overtime for the Knicks."), [])
check("'sent it to overtime' likewise",
      flags("Shamet's game-tying three sent the Knicks game against Cleveland to overtime."), [])
check("a margin at halftime that is wrong IS caught (2026-05-02: 'down 24 at halftime', it was 22)",
      len(flags("The Knicks won after being down 20 at halftime against the Cavaliers.")), 1)
check("...and the right margin is confirmed",
      flags("The Cavaliers were up 2 at halftime against the Knicks."), [])

outcomes: list = []
cv.check_nba_flow_claims(HEAD + "Cleveland led 48-46 at the half against the Knicks. "
                         "Cleveland led 50-46 at the half against the Knicks.", GS, outcomes)
check("outcomes carry confirmed and HIGH", [o[0] for o in outcomes], ["confirmed", "HIGH"])

print("Check 3G is part of validation, and of the editor re-check")
html = "<h2>Knicks rally</h2><p>Knicks never trailed against the Cavaliers in a basketball game.</p>"
fixed, found = cv.final_history_check(html, GS, autocut=False)
check("final_history_check reports it", [(r["level"], r["kind"]) for r in found], [("HIGH", "game flow")])
check("...with a note after the heading", "NBA GAME FLOW CLAIM LEFT IN BY THE EDITOR" in fixed, True)
sec, n = cv.validate_section("Knicks rally", html, GS)
check("validate_section flags it", "contradicts the play-by-play" in sec, True)
ok_html = "<h2>Knicks rally</h2><p>The Cavaliers led 48-46 at the half against the Knicks in the quarter-by-quarter box.</p>"
check("a true sentence adds no flag", cv.final_history_check(ok_html, GS, autocut=False)[1], [])
tweet_html = ("<h2>Knicks rally</h2><p>Knicks never trailed against the Cavaliers in basketball.</p>"
              "<blockquote class=\"tweet\">Knicks never trailed</blockquote>")
check("claims inside a tweet are never checked (own text only)",
      cv.check_nba_flow_claims(cv.own_text("<p>Knicks and Cavaliers basketball.</p>"
                                           "<blockquote class=\"tweet\">The Knicks never trailed against the Cavaliers</blockquote>"), GS), [])

# --------------------------------------------------------------------------
print("Wiring")
for path in ("generate_newsletter.py", "uat/run_uat.py"):
    src = (REPO / path).read_text(encoding="utf-8")
    i = src.find("nba_bundle.attach_story(game_state, story_plan)")
    k = src.find("run_pass2(story_plan", i)
    check(f"{path}: attach_story before Pass 2", 0 < i < k, True)
for d in ("prompts", "uat/prompts"):
    rf = (REPO / d / "rolling_feedback.txt").read_text(encoding="utf-8")
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: RULE 3 names the NBA GAME FLOW block", "NBA GAME FLOW part of the GROUND TRUTH block" in rf, True)
    check(f"{d}: editor Check 9 says how to fix an NBA game-flow flag",
          "NBA game-flow flag" in " ".join(ed.split()), True)
rc = (REPO / "runner_common.py").read_text(encoding="utf-8")
check("runner_common builds the NBA block", "nba_bundle.summary_lines(game_state)" in rc, True)
check("the fetch keeps the play log for NBA games only", "_parse_nba_plays(summary)" in
      (REPO / "fetch_sports_data.py").read_text(encoding="utf-8"), True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
