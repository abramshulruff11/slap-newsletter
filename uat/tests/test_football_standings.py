"""
Run:  python -X utf8 uat/tests/test_football_standings.py

SLA-130: Pass 3 checks football standings, conference-record, upset and
playoff-win claims.

- Standings (Check 3C): "leads the AFC West", "tied with Kansas City atop",
  "alone in first", "a game back", "last in the division", against each NFL
  team's standing after the game (football_bundle.nfl_standing). A college
  conference LEAD is LOW (nothing we hold confirms one); a conference RECORD
  ("1-1 in SEC play") is checked against ESPN's.
- Upsets and playoff wins (Check 3B): "first win over a top-10 team since
  2013", "first loss to an unranked team since", "first playoff win since",
  against the bundle's facts, with the history check's confirm / HIGH / LOW
  rules. Before this, an upset claim read as a POLL claim and was compared
  with "last ranked this high in ...".

The replay (uat/replay_football_claims.py, 2026-09-05 to 2026-10-04) found 5
claims, all confirmed, 0 HIGH, after the bug it found: "tied with Kansas City
atop the AFC West" (2026-09-28) wasn't read as a share of the lead. The
fixtures are that weekend's real AFC West. No API calls, no network, no
database.
"""
from __future__ import annotations

import sys
from datetime import date
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


def game(gid, home, away, hs, as_, htot, atot, hconf=None, aconf=None, **kw):
    g = {"game_id": gid, "completed": True, "home_team": home, "away_team": away, "home_score": hs,
         "away_score": as_, "home_rank": kw.pop("hrank", None), "away_rank": kw.pop("arank", None),
         "playoffs": False, "season_type": 2,
         "home_records": {"total": htot, **({"vsconf": hconf} if hconf else {})},
         "away_records": {"total": atot, **({"vsconf": aconf} if aconf else {})}}
    g.update(kw)
    return g


def team(name, standing=None, facts=(), location=None, ap=None):
    return {"name": name, "location": location, "ap": ap, "cfp": None, "ranks_known": True, "record_before": None,
            "standing": standing, "facts": list(facts)}


AFC_W = "AFC West"
DAY = "2026-09-27"
GS = {"yesterday_date": DAY, "sports": {
    "nfl": {"yesterday_games": [
        game("n1", "Las Vegas Raiders", "New Orleans Saints", 35, 27, "3-0", "1-2"),
        game("n2", "Denver Broncos", "New York Giants", 20, 23, "1-2", "2-1"),
        game("n3", "Buffalo Bills", "Miami Dolphins", 30, 10, "3-0", "0-3")]},
    "ncaafb": {"yesterday_games": [
        game("c1", "Missouri Tigers", "Florida Gators", 45, 17, "4-1", "4-1", hconf="1-1", aconf="2-1",
             hrank=25, arank=8)]}},
    "football": {"status": "ok", "name_pool": [
        {"league_id": "nfl", "full_name": "New York Giants", "nickname": "Giants", "location": "New York"},
        {"league_id": "mlb", "full_name": "San Francisco Giants", "nickname": "Giants", "location": "San Francisco"}],
        "sports": {
            "nfl": {"games": [
                {"game_id": "n1", "teams": [
                    team("Las Vegas Raiders", {"division": AFC_W, "place": 2, "size": 4, "games_back": 0.0,
                                               "shares_top": True}),
                    team("New Orleans Saints", {"division": "NFC South", "place": 3, "size": 4, "games_back": 1.0,
                                                "shares_top": False})]},
                {"game_id": "n2", "teams": [
                    team("New York Giants", {"division": "NFC East", "place": 2, "size": 4, "games_back": 1.0,
                                             "shares_top": False}),
                    team("Denver Broncos", {"division": AFC_W, "place": 4, "size": 4, "games_back": 2.0,
                                            "shares_top": False})]},
                {"game_id": "n3", "teams": [
                    team("Buffalo Bills", {"division": "AFC East", "place": 1, "size": 4, "games_back": 0.0,
                                           "shares_top": False}),
                    team("Miami Dolphins", {"division": "AFC East", "place": 4, "size": 4, "games_back": 3.0,
                                            "shares_top": False})]}]},
            "ncaafb": {"games": [{"game_id": "c1", "teams": [
                team("Missouri Tigers", location="Missouri", ap=25,
                     facts=[{"kind": "upset_W", "bucket": 10, "last": [2013], "floor": None}]),
                team("Florida Gators", location="Florida", ap=8,
                     facts=[{"kind": "upset_L", "last": [2019], "floor": None}])]}]}}},
}


def level(text, gs=GS):
    out = []
    cv.check_football_claims(text, gs, out)
    return [o[0] for o in out]


def hist(text, gs=GS):
    out = []
    flags = cv.check_history_claims(text, gs, out)
    return [o[0] for o in out], flags


print("The standing, as data (football_bundle.nfl_standing)")


def g(day, home, away, hs, as_):
    return {"date": f"{day}T17:00Z", "home_abbr": home, "away_abbr": away, "home_score": hs, "away_score": as_,
            "completed": True}


log = [g("2026-09-07", "LV", "DEN", 20, 10), g("2026-09-07", "KC", "LAC", 27, 20),
       g("2026-09-14", "LV", "LAC", 24, 21), g("2026-09-14", "KC", "DEN", 30, 17)]
st = fb.nfl_standing(log, "LV", date(2026, 9, 14))
check("division, size, games back", (st["division"], st["size"], st["games_back"]), (AFC_W, 4, 0.0))
check("two 2-0 teams share the top", (st["shares_top"], fb.nfl_standing(log, "DEN", date(2026, 9, 14))["games_back"]),
      (True, 2.0))
check("as of the day: a week earlier, a week less", fb.nfl_standing(log, "DEN", date(2026, 9, 7))["games_back"], 1.0)
line = fb.nfl_standings_after(log, "DEN", date(2026, 9, 14))
check("the text line the writer sees says the same", (line.startswith("0-2, "), "AFC West" in line,
                                                      line.endswith("2 games back")), (True, True, True))

print("NFL standings claims")
check("REPLAY 2026-09-28: 'tied with Kansas City atop the AFC West' is a share: confirmed",
      level("Raiders demolished the Saints 35-27 and are now tied with Kansas City atop the AFC West."),
      ["confirmed"])
check("'the Raiders lead the AFC West', tied and second on the tiebreaker: still confirmed",
      level("The Raiders lead the AFC West."), ["confirmed"])
check("'alone in first' when tied: HIGH", level("The Raiders are alone in first."), ["HIGH"])
check("'the Bills lead the AFC East': confirmed; 'alone in first': confirmed",
      (level("The Bills lead the AFC East."), level("The Bills are alone in first place.")), (["confirmed"], ["confirmed"]))
check("the wrong division: HIGH", level("The Bills lead the AFC North."), ["HIGH"])
check("not first: HIGH", level("The Broncos lead the AFC West."), ["HIGH"])
check("'a game back': confirmed / 'two games back': HIGH",
      (level("The Saints are a game back."), level("The Saints are two games back.")), (["confirmed"], ["HIGH"]))
check("'1.5 games back', 'half a game back' parse",
      (level("The Saints sit 1.5 games back."), level("The Saints sit half a game back.")), (["HIGH"], ["HIGH"]))
check("'last in the AFC West' / 'in the cellar'",
      (level("The Broncos are last in the AFC West."), level("The Dolphins are in the cellar.")),
      (["confirmed"], ["confirmed"]))
check("'tied for first' when not: HIGH", level("The Bills are tied for first."), ["HIGH"])
check("two teams named, both wrong: LOW, never HIGH", level("The Bills and Dolphins lead the NFC West."), ["LOW"])
for text, why in (("The Raiders led 21-7 at halftime.", "'led 21-7' is a score, not a division"),
                  ("Kansas City leads the series 30-28.", "a series"),
                  ("The Giants are two games back of the Dodgers.", "'Giants' alone is never the NFL team"),
                  ("They are a game back in the NL West.", "no team named: never the section's NFL team")):
    check(f"nothing: {why}", level(text), [])
check("...though 'the New York Giants' is", level("The New York Giants are a game back."), ["confirmed"])

print("College conference claims")
check("a conference lead can't be confirmed: LOW", level("Missouri leads the SEC."), ["LOW"])
check("a conference record: ESPN's", (level("Missouri is 1-1 in SEC play."), level("Missouri is 2-0 in SEC play.")),
      (["confirmed"], ["HIGH"]))
check("...and never mistaken for the overall record ('moved to 1-1 in SEC play')",
      level("Missouri moved to 1-1 in SEC play."), ["confirmed"])

print("Upsets and playoff wins (through the history check)")
lv, flags = hist("Missouri's first win over a top-10 team since 2013.")
check("confirmed against the bundle's upset fact", lv, ["confirmed"])
lv, flags = hist("Missouri's first win over a top-10 team since 2017.")
check("contradicted, one team named: HIGH, quoting the fact", (lv, "last regular-season win over an AP top-10 team: 2013"
                                                               in flags[0]), (["HIGH"], True))
check("'in 13 years' counts too", hist("Missouri beat a top-10 team for the first time in 13 years.")[0], ["confirmed"])
check("a different bucket ('a ranked team') is not this fact: LOW, never HIGH",
      hist("Missouri's first win over a ranked team since 2017.")[0], ["LOW"])
check("Florida's 'first loss to an unranked team since 2019': confirmed",
      hist("Florida suffered its first loss to an unranked team since 2019.")[0], ["confirmed"])
check("...'since 2021': HIGH", hist("Florida suffered its first loss to an unranked team since 2021.")[0], ["HIGH"])
check("THE BUG THIS FIXES: an upset claim is not a poll claim",
      cv._claim_kinds("first win over a top-10 team since"), {"upset_W"})
check("'first playoff win since' is its own kind (no longer thrown out as 'other postseason')",
      cv._claim_kinds("their first playoff win since"), {"playoff_win"})
post = {"yesterday_date": "2027-01-10", "sports": {"nfl": {"yesterday_games": [
    game("p1", "Buffalo Bills", "Houston Texans", 31, 17, "13-4", "10-7", playoffs=True, season_type=3)]}},
    "football": {"status": "ok", "sports": {"nfl": {"games": [{"game_id": "p1", "teams": [
        team("Buffalo Bills", facts=[{"kind": "playoff_win", "last": [2024, 2025], "floor": None}])]}]}}}}
check("a playoff win: 'first since the 2024 season' confirmed, 'since 2019' HIGH",
      (hist("The Bills got their first playoff win since 2024.", post)[0],
       hist("The Bills got their first playoff win since 2019.", post)[0]), (["confirmed"], ["HIGH"]))

print("The editor no longer carries the football exception (both copies)")
for d in ("prompts", "uat/prompts"):
    ed = (REPO / d / "editor_prompt.txt").read_text(encoding="utf-8")
    check(f"{d}: the SLA-119 carve-out is gone", "the bundle sources it: LEAVE IT" in ed, False)
    check(f"{d}: Check 9 covers standings flags", "A football rank, record or standings flag" in ed, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all passed")
