"""
Run:  python -X utf8 uat/tests/test_football_ground_truth.py

SLA-119: the football bundles (SLA-116) reach every pass.

runner_common.format_game_state_summary() is the GROUND TRUTH block Passes 1,
2 and 6 read. It now carries the FOOTBALL GAMES block, and says nothing twice:
a game the block shows is left out of YESTERDAY'S GAME RESULTS, and a team
whose bundle is shown in full is left out of HISTORICAL CONTEXT. What the
budget cuts keeps its plain line, so the cap never deletes a score or a
verified fact from the prompt. Pass 3 checks against the data, not the text,
so history_source.team_facts() must not change. After Pass 1, both runners
call football_bundle.attach_story() so story games come first.
No API calls, no network, no database.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import football_bundle as fb  # noqa: E402
import history_source  # noqa: E402
from runner_common import format_game_state_summary  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILS.append(label)
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got:  {got!r}\n         want: {want!r}"))


DAY = "2026-10-03"
HIST = {
    ("ncaafb", "Indiana Hoosiers"): ["5-0 after 5 games, their best start since at least 1999 (game data starts 1999)."],
    ("ncaafb", "Ohio State Buckeyes"): ["last college football title: 2024."],
    ("ncaafb", "School0 Owls"): ["3 straight losses this season; last 3+ within a season: 2019."],
    ("nfl", "Buffalo Bills"): ["last Super Bowl appearance: 1993."],
    ("mlb", "New York Yankees"): ["last World Series title: 2009."],
}


def espn(gid, home, away):
    return {"game_id": gid, "home_team": home, "away_team": away, "home_score": 20, "away_score": 17,
            "winner": home, "completed": True}


def bundle(gid, a, b, *, ranked=False, best=99, pad=0):
    return {"game_id": gid, "result": f"{a} 20, {b} 17 (Sat {DAY}, at {a})",
            "lines": [f"{a} were 4-0 going in." + " " * pad, f"Series: {a} lead {b} 10-5."],
            "history": [f"{t} history: " + " ".join(HIST[(sp, t)])
                        for sp, t in (("ncaafb", a), ("ncaafb", b), ("nfl", a), ("nfl", b)) if (sp, t) in HIST],
            "ranked": ranked, "upset": False, "best_rank": best, "vouched": True,
            "teams": [{"name": t, "full_name": t, "nickname": t.split()[-1], "location": t.rsplit(" ", 1)[0]}
                      for t in (a, b)]}


def history_block():
    leagues = {}
    for (sport, team), facts in HIST.items():
        leagues.setdefault(sport, {"label": sport.upper(), "game_data_from": 1999, "season": 2026, "teams": []})
        leagues[sport]["teams"].append({"team": team, "facts": list(facts)})
    return {"status": "ok", "as_of": DAY, "leagues": leagues}


cfb = [("c1", "Indiana Hoosiers", "Ohio State Buckeyes")] + \
      [(f"c{i + 2}", f"School{i} Owls", f"Other{i} Hawks") for i in range(300)]
GS = {
    "as_of_date": DAY, "yesterday_date": DAY,
    "sports": {
        "mlb": {"label": "MLB", "yesterday_games": [espn("m1", "New York Yankees", "Boston Red Sox")]},
        "nfl": {"label": "NFL", "yesterday_games": [espn("n1", "Buffalo Bills", "Miami Dolphins")]},
        "ncaafb": {"label": "College Football", "yesterday_games": [espn(g, h, a) for g, h, a in cfb]},
    },
    "history": history_block(),
    "football": {"status": "ok", "as_of": DAY, "name_pool": [], "sports": {
        "nfl": {"label": "NFL", "games": [bundle("n1", "Buffalo Bills", "Miami Dolphins")]},
        "ncaafb": {"label": "College football", "games":
                   [bundle("c1", "Indiana Hoosiers", "Ohio State Buckeyes", ranked=True, best=1)]
                   + [bundle(g, h, a) for g, h, a in cfb[1:]]}}},
}
FACTS_BEFORE = copy.deepcopy(history_source.team_facts(GS))


def section(text, title):
    m = re.search(r"## GROUND TRUTH: " + re.escape(title) + r"\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1) if m else ""


print("The GROUND TRUTH block carries the football bundles")
out = format_game_state_summary(GS)
foot, results, hist = (section(out, "FOOTBALL GAMES"), section(out, "YESTERDAY'S GAME RESULTS"),
                       section(out, "HISTORICAL CONTEXT"))
check("the FOOTBALL GAMES block is in it", bool(foot), True)
check("...under its cap", len("\n".join(fb.summary_lines(GS))) <= fb.BUDGET, True)
check("the order: results, football, champions/history",
      out.index("YESTERDAY'S GAME RESULTS") < out.index("FOOTBALL GAMES") < out.index("HISTORICAL CONTEXT"), True)

print("Nothing said twice")
check("a game the block shows is out of the results list", "Buffalo Bills" in results, False)
check("...and so is a ranked college game", "Indiana Hoosiers" in results, False)
check("other sports' results are untouched", "Boston Red Sox 17, New York Yankees 20" in results, True)
check("a full bundle's team has no HISTORICAL CONTEXT line", ("Indiana Hoosiers" in hist, "Buffalo Bills" in hist),
      (False, False))
check("...its facts are in the bundle, verbatim",
      all(f.strip() in foot for k in (("ncaafb", "Indiana Hoosiers"), ("nfl", "Buffalo Bills")) for f in HIST[k]), True)
check("other sports' history is untouched", "New York Yankees" in hist, True)

print("Nothing lost to the budget")
games_in_foot = {g for g, _, _ in cfb if f"{dict((x[0], x) for x in cfb)[g][1]} 20," in foot}
dropped = [h for g, h, a in cfb if g not in games_in_foot]
check("the budget dropped some college games (the case under test)", len(dropped) > 0, True)
check("...every one of them keeps its plain result line",
      all(f"{h} 20" in results for h in dropped), True)
check("...and no game the block shows is listed twice",
      all(f"{h} 20" not in results for g, h, a in cfb if g in games_in_foot), True)
cut = "School0 Owls"
shown_size = fb.render(GS)[1].get("c2")
check(f"{cut}: its bundle is not shown in full (got {shown_size})", shown_size != "full", True)
check(f"...so its HISTORICAL CONTEXT line stays", cut in hist, True)
check("Pass 3's facts are unchanged by any of this", history_source.team_facts(GS), FACTS_BEFORE)

print("Story order after Pass 1")
plan = json.dumps({"stories": [{"headline": "School7 Owls shock everyone",
                                "beats": [{"angle": "the School7 Owls defense"}]}]})
gs2 = copy.deepcopy(GS)
before = fb.render(gs2)[1].get("c9")
line = fb.attach_story(gs2, plan)
after = fb.render(gs2)[1].get("c9")
check("before the plan, an unranked game is at most a result line", before in (None, "result"), True)
check("after attach_story, the story's game is in full", after, "full")
check("the log line names the block's size and the story games",
      bool(re.search(r"football: block [\d,]+ -> [\d,]+ chars \(cap 10,000\); 1 story game\(s\), 1 in full", line)),
      True)
out2 = format_game_state_summary(gs2)
check("...and every later pass's ground truth shows it (Pass 2 and Pass 6 use this)",
      "School7 Owls" in section(out2, "FOOTBALL GAMES") and "School7 Owls" not in
      section(out2, "YESTERDAY'S GAME RESULTS"), True)
check("the plan never reaches game_state.json: only the in-memory dict", "story_text" in GS["football"], False)
check("no bundles: says so, does nothing", fb.attach_story({"football": {"status": "unavailable", "reason": "x"}}, plan),
      "football: no bundles today (x)")
check("no football key at all: same", fb.attach_story({}, plan), "football: no bundles today")
bad = {"football": {"status": "ok", "sports": {"nfl": {"games": [{"no": "fields"}]}}}}
check("a malformed block: reported, never raised",
      fb.attach_story(bad, plan).startswith("football: story order skipped: "), True)

print("No football today")
plain = {k: v for k, v in GS.items() if k != "football"}
p_out = format_game_state_summary(plain)
check("without bundles, every football result is in the results list",
      all(f"{h} 20" in p_out for _, h, _ in cfb[:5]) and "Buffalo Bills 20" in p_out, True)
check("...and every team keeps its HISTORICAL CONTEXT line", "Indiana Hoosiers" in section(p_out, "HISTORICAL CONTEXT"),
      True)
check("...and there is no FOOTBALL GAMES block", "FOOTBALL GAMES" in p_out, False)
only = {"as_of_date": DAY, "sports": {"nfl": GS["sports"]["nfl"]},
        "football": {"status": "ok", "as_of": DAY, "name_pool": [],
                     "sports": {"nfl": GS["football"]["sports"]["nfl"]}}}
o_out = format_game_state_summary(only)
check("an NFL-only day: the bundle stands in for the results list, no empty heading",
      ("FOOTBALL GAMES" in o_out, "NFL:\n\n" in o_out), (True, False))

print("Both runners call attach_story after Pass 1, beside extend_for_stories")
for path in ("generate_newsletter.py", "uat/run_uat.py"):
    src = (REPO / path).read_text(encoding="utf-8")
    i, j = src.find("history_source.extend_for_stories(game_state, story_plan)"), \
        src.find("football_bundle.attach_story(game_state, story_plan)")
    k = src.find("run_pass2(story_plan", j)
    check(f"{path}: extend_for_stories, then attach_story, then Pass 2", 0 < i < j < k, True)

print("The prompts treat a bundle fact as sourced")
for d in ("prompts", "uat/prompts"):
    rf = (REPO / d / "rolling_feedback.txt").read_text(encoding="utf-8")
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: RULE 3 names the FOOTBALL GAMES bundles", "FOOTBALL GAMES part of the GROUND TRUTH block" in rf, True)
    check(f"{d}: Check 8 counts them as a source", "and the FOOTBALL GAMES\n     bundles" in ed.replace("\r", ""), True)
    # SLA-119's temporary Check 9 exception for bundle facts was removed by
    # SLA-130, once Pass 3 checked every kind of bundle fact itself.
    check(f"{d}: Check 9 tells the editor Pass 3 checks upset and playoff-win claims against the bundles",
          "Pass 3 checks them against the FOOTBALL GAMES bundles" in " ".join(ed.split()), True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
