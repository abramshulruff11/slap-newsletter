"""
Run:  python -X utf8 uat/tests/test_football_claims.py

SLA-117: Pass 3 checks football rank and record claims (Check 3C).

"No. 4 Ole Miss", "the unranked ...", "improved to 4-1", "came in 4-0",
"Missouri (4-1)", "still unbeaten": each is checked against
football_bundle.claim_facts(), i.e. ESPN's rank and record for yesterday's
games plus the database's AP / CFP ranks. Confirmed claims are left alone;
HIGH only when the one team a claim attaches to plainly disagrees; anything
else about yesterday's teams is LOW. The fixtures are the real 2026-09-26
Florida-Ole Miss game. The archive replay (uat/replay_football_claims.py,
2026-09-05 to 2026-10-04) found 4 claims, all confirmed, 0 HIGH, after two
bugs it found are fixed here: "No. 4 Ole Miss" was split into two sentences
at "No.", and "down 20 to No. 1 Ohio State" read as a forecast.
No API calls, no network, no database.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import claim_validator as cv  # noqa: E402
import football_bundle as fb  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


def game(gid, home, away, hs, as_, hrank=None, arank=None, htot=None, atot=None, **kw):
    g = {"game_id": gid, "completed": True, "home_team": home, "away_team": away, "home_score": hs,
         "away_score": as_, "home_rank": hrank, "away_rank": arank, "playoffs": False, "season_type": 2,
         "home_records": {"total": htot} if htot else {}, "away_records": {"total": atot} if atot else {}}
    g.update(kw)
    return g


DAY = "2026-09-26"
GS = {"yesterday_date": DAY, "sports": {
    "ncaafb": {"yesterday_games": [
        game("c1", "Florida Gators", "Ole Miss Rebels", 52, 28, hrank=21, arank=4, htot="4-0", atot="3-1"),
        game("c2", "Mississippi State Bulldogs", "Missouri Tigers", 31, 24, hrank=24, htot="4-0", atot="3-1"),
        game("c3", "Rutgers Scarlet Knights", "Iowa Hawkeyes", 10, 20, htot="1-3", atot="4-0", arank=14),
        game("c4", "Florida Atlantic Owls", "Rice Owls", 30, 3, htot="2-2", atot="1-3"),
    ]},
    "nfl": {"yesterday_games": [
        game("n1", "Cincinnati Bengals", "Cleveland Browns", 24, 17, htot="2-1", atot="0-3"),
        game("n2", "Kansas City Chiefs", "Buffalo Bills", 27, 24, htot="13-4", atot="12-5", playoffs=True)]}},
    # The database's view of each game, as football_bundle stores it. It
    # disagrees with ESPN once, on purpose: ESPN has Mississippi State at
    # No. 24, the AP poll in the database has it unranked.
    "football": {"status": "ok", "sports": {"ncaafb": {"games": [
        {"game_id": gid, "teams": [{"name": n, "location": loc, "ap": ap, "cfp": None, "ranks_known": True,
                                    "record_before": rb} for n, loc, ap, rb in teams]}
        for gid, teams in (
            ("c1", (("Florida Gators", "Florida", 21, [3, 0, 0]), ("Ole Miss Rebels", "Ole Miss", 4, [3, 0, 0]))),
            ("c2", (("Mississippi State Bulldogs", "Mississippi State", None, [3, 0, 0]),
                    ("Missouri Tigers", "Missouri", None, [3, 0, 0]))),
            ("c3", (("Iowa Hawkeyes", "Iowa", 14, [3, 0, 0]), ("Rutgers Scarlet Knights", "Rutgers", None, [1, 2, 0]))),
            ("c4", (("Florida Atlantic Owls", "Florida Atlantic", None, [1, 2, 0]),
                    ("Rice Owls", "Rice", None, [1, 2, 0]))))]}}},
}


def run(text, gs=GS):
    out = []
    flags = cv.check_football_claims(text, gs, out)
    return [(o[0], o[3]) for o in out], flags


def level(text, gs=GS):
    return [lv for lv, _ in run(text, gs)[0]]


print("What the checker knows (football_bundle.claim_facts)")
facts = {f["team"]: f for f in fb.claim_facts(GS)}
check("ESPN's rank going in, and records: after, and before (= after minus the result)",
      (facts["Florida Gators"]["ranks"], facts["Florida Gators"]["before"], facts["Florida Gators"]["after"]),
      ({21}, (3, 0, 0), (4, 0, 0)))
check("the loser's", (facts["Ole Miss Rebels"]["before"], facts["Ole Miss Rebels"]["after"]), ((3, 0, 0), (3, 1, 0)))
check("ESPN's and the database's ranks together", facts["Iowa Hawkeyes"]["ranks"], {14})
check("the school, as the database spells it", facts["Florida Atlantic Owls"]["school"], "Florida Atlantic")
check("unranked: known when ESPN or the database says so", facts["Rutgers Scarlet Knights"]["unranked"], True)
check("NFL: no rank facts at all", (facts["Cincinnati Bengals"]["ranks"], facts["Cincinnati Bengals"]["unranked"]),
      (set(), None))
check("a playoff game: records not checked", (facts["Kansas City Chiefs"]["before"], facts["Kansas City Chiefs"]["post"]),
      (None, True))
no_espn = {"yesterday_date": DAY, "sports": {"ncaafb": {"yesterday_games": [
    game("c3", "Rutgers Scarlet Knights", "Iowa Hawkeyes", 10, 20, arank=14)]}}, "football": GS["football"]}
f2 = {f["team"]: f for f in fb.claim_facts(no_espn)}
check("no ESPN record: the bundle's record going in, plus the result",
      (f2["Iowa Hawkeyes"]["before"], f2["Iowa Hawkeyes"]["after"]), ((3, 0, 0), (4, 0, 0)))

print("Ranks")
check("THE REPLAY CASE: 'No. 4 Ole Miss' in a headline, confirmed (and not split at 'No.')",
      level("Florida Demolishes No. 4 Ole Miss 52-28"), ["confirmed"])
check("a wrong rank on the team written after it: HIGH", level("Florida Demolishes No. 5 Ole Miss 52-28"), ["HIGH"])
check("'#21 Florida', '21st-ranked Florida', 'No. 21-ranked Florida'",
      [level(s) for s in ("#21 Florida rolled.", "The 21st-ranked Florida Gators rolled.",
                          "The No. 21-ranked Florida rolled.")], [["confirmed"]] * 3)
check("two teams, two ranks: each checked against its own team",
      level("No. 21 Florida beat No. 3 Ole Miss."), ["confirmed", "HIGH"])
check("REPLAY 2026-09-13: 'down 20 to No. 1 Ohio State' is a rank, not a forecast",
      level("Missouri was down 20 to No. 24 Mississippi State."), ["confirmed"])
check("'now No. 5 Florida' is next week's poll: unknown, LOW", level("That makes it now No. 5 Florida."), ["LOW"])
check("'unranked Rutgers': confirmed", level("Iowa handled unranked Rutgers."), ["confirmed"])
check("'unranked Iowa', when the database had Iowa at No. 14: HIGH", level("Unranked Iowa won again."), ["HIGH"])
check("ESPN ranked, database unranked: a claim of either is confirmed",
      (level("No. 24 Mississippi State won."), level("Unranked Mississippi State won.")),
      (["confirmed"], ["confirmed"]))
for text, why in (("No. 3 Texas visits next week.", "a team that didn't play yesterday: not this data's"),
                  ("He was the No. 1 overall pick.", "a pick, a seed, a player: no team after the rank"),
                  ("The No. 2 Bengals defense held.", "NFL: no poll ranks"),
                  ("No. 4 Florida State lost.", "'Florida State' is not Florida")):
    check(f"nothing: {why}", run(text)[0], [])
check("'No. 9 Florida Atlantic' is checked as Florida Atlantic (which played, unranked): HIGH",
      run("No. 9 Florida Atlantic won.")[1][0].count("Florida Atlantic Owls went into the game unranked"), 1)
fau_absent = {"yesterday_date": DAY, "sports": {"ncaafb": {"yesterday_games": GS["sports"]["ncaafb"]["yesterday_games"][:3]}},
              "football": GS["football"]}
check("...and never as the Gators when Florida Atlantic didn't play", run("No. 9 Florida Atlantic won.", fau_absent)[0], [])
fcs = {"yesterday_date": DAY, "sports": {"ncaafb": {"yesterday_games": [
    game("c1", "Florida Gators", "Florida A&M Rattlers", 52, 0, hrank=21, htot="4-0", atot="0-4")]}},
    "football": {"status": "ok", "sports": {"ncaafb": {"games": [{"game_id": "c1", "teams": [
        {"name": "Florida Gators", "location": "Florida", "ap": 21, "cfp": None, "ranks_known": True,
         "record_before": [3, 0, 0]}]}]}}}}
check("a team the database doesn't know never takes a known school's name ('Florida' is still the Gators)",
      level("No. 21 Florida rolled.", fcs), ["confirmed"])

print("Records")
check("REPLAY 2026-09-18 shape: 'X beat Y 27-24 to move to 4-0' (the score is not a record)",
      level("Florida beat Ole Miss 52-28 to move to 4-0."), ["confirmed"])
check("...a wrong one in a two-team sentence: LOW, never HIGH (whose record?)",
      level("Florida beat Ole Miss 52-28 to move to 5-0."), ["LOW"])
check("one team named, wrong record after: HIGH", level("Missouri fell to 3-2 on the road."), ["HIGH"])
check("...right record: confirmed", level("Missouri fell to 3-1 on the road."), ["confirmed"])
check("going in: 'came in 4-0', 'entered at 3-0', '3-0 going in'",
      [level(s) for s in ("Mississippi State came in 3-0.", "Ole Miss entered at 3-0.",
                          "Ole Miss was 3-0 going in.")], [["confirmed"]] * 3)
check("going in, wrong: HIGH", level("Ole Miss came in 4-0."), ["HIGH"])
check("'Missouri (3-1)': attached to the team, so HIGH-capable even with two teams",
      (level("Missouri (3-1) lost to Mississippi State (4-0)."), level("Missouri (4-0) lost to Mississippi State.")),
      (["confirmed", "confirmed"], ["HIGH"]))
check("'Missouri (3-0)' is its record going in: a parenthetical is confirmed by either",
      level("Missouri (3-0) went on the road."), ["confirmed"])
for text, why in (("Smith (4-1) took the loss.", "a pitcher's record, not a team's"),
                  ("Florida won 24-7.", "a score"),
                  ("Florida won a 10-7 game.", "a score with no record cue"),
                  ("Florida moved to a 10-7 win.", "a score after a cue"),
                  ("The count went to 3-2.", "a count, no team")):
    check(f"nothing: {why}", run(text)[0], [])
check("'they improved to 2-1' with no team in the sentence: the section's teams; Bengals confirmed",
      level("The Bengals held on.\nThey improved to 2-1."), ["confirmed"])
check("...and wrong: LOW (the sentence names nobody)", level("The Bengals held on.\nThey improved to 3-0."), ["LOW"])
check("a playoff game's record: unknown, LOW", level("The Chiefs improved to 14-4."), ["LOW"])
check("'still unbeaten' after a loss: HIGH", level("Ole Miss is still unbeaten."), ["HIGH"])
check("'remains unbeaten' after a win: confirmed", level("Florida remains unbeaten."), ["confirmed"])
check("'still winless': confirmed", level("The Browns are still winless."), ["confirmed"])

print("The flag text")
_, flags = run("Missouri fell to 3-2 on the road.")
check("HIGH quotes the data", "Missouri Tigers 3-0 going in, 3-1 after this game" in flags[0], True)
_, flags = run("Florida Demolishes No. 5 Ole Miss 52-28")
check("...for a rank too", "Ole Miss Rebels went into the game ranked No. 4" in flags[0], True)
check("...and is a well-formed comment", (flags[0].strip().startswith("<!-- FACT FLAG [HIGH]"),
                                          flags[0].count("--") == 2), (True, True))
check("no football games yesterday: nothing, no work", cv.check_football_claims("No. 4 Ole Miss won.", {}), [])

print("Wired into Pass 3 and the final-draft re-check")
html = ("<h2>SEC Chaos</h2><p>Missouri fell to 3-2 on the road.</p>"
        "<h2>Gators</h2><p>Florida remains unbeaten.</p>")
annotated, n = cv.validate_section("SEC Chaos", html.split("<h2>Gators")[0], GS)
check("validate_section adds the HIGH flag", (n, "FACT FLAG [HIGH]" in annotated), (1, True))
final, rep = cv.final_history_check(html, GS, autocut=False)
check("the re-check reports what the editor left in, with its kind",
      [(r["level"], r["kind"], r["section"]) for r in rep], [("HIGH", "record", "SEC Chaos")])
check("...and notes it after the heading", "FOOTBALL RECORD CLAIM LEFT IN BY THE EDITOR [HIGH]" in final, True)
sourced = ('<h2>SEC Chaos</h2><p>Missouri fell to 3-2 on the road.</p>'
           '<blockquote class="tweet">Mizzou drops to 3-2</blockquote>')
check("a tweet in the section carrying the same record sources it",
      cv.final_history_check(sourced, GS, autocut=False)[1], [])
cut, rep = cv.final_history_check(html, GS, autocut=True)
check("autocut removes the sentence", ("fell to 3-2" in cut, rep[0]["cut"]), (False, True))

print("The email and the verdict")
sys.path.insert(0, str(REPO))
import email_newsletter  # noqa: E402
import pipeline_status  # noqa: E402
panel = email_newsletter._history_claims_html({"history_claims": [
    {"level": "HIGH", "section": "SEC Chaos", "sentence": "Missouri fell to 3-2", "cut": False, "kind": "record"}]})
check("the email labels it as a record claim", "record CONTRADICTED by the game data" in panel, True)
src = (REPO / "pipeline_status.py").read_text(encoding="utf-8")
check("the PARTIAL headline names rank and record claims", "history, rank or record claim(s)" in src, True)

print("The editor is told how to act on them (both copies)")
for d in ("prompts", "uat/prompts"):
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    # SLA-130 extended this paragraph to standings and removed the SLA-119
    # bundle exception it once had to step around.
    check(f"{d}: Check 9 says how to fix a rank or record flag",
          ("A football rank, record or standings flag" in ed, "improved to 5-0" in ed), (True, True))

print("Sentences")
check("abbreviations don't end a sentence", cv._sentences("No. 4 Ole Miss lost. L.A. won. St. Louis too.\nNext"),
      ["No. 4 Ole Miss lost.", "L.A. won.", "St. Louis too.", "Next"])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
