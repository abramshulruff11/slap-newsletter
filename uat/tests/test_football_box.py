"""
Run:  python -X utf8 uat/tests/test_football_box.py

Locks the NFL / college-football box score path added 2026-09-15.

Before this, MLB was the ONLY sport that produced box scores at all. Three
separate gaps stacked up, and all three had to be closed for football:

  1. fetch_sports_data.box_sports was {nba, mlb, nhl, wnba}, so no football
     game summary was ever requested and game["box_score"] never existed.
  2. parse_box_score() had no football branch — it returned {} for nfl/ncaafb
     even when handed a real summary.
  3. build_box_score._render_sport_inline()'s regular-season branch emitted
     standings plus a one-line-per-game scores strip and never called a
     per-game renderer. MLB escaped it only because _render_mlb_sections() is
     a separate function.

Measured against the 8 issues shipped 09-08..09-15: MLB rendered 10-15 box
score tables a day, NFL and CFB rendered 0 every single day. The best football
day (09-14, the 13-game Sunday slate) shipped "Buccaneers 27, Bengals 33" and
nothing else, and 09-13 crammed 80 CFB games into one unchunked image.

CFB ships box scores for RANKED matchups only — 80 full box scores is a
different product from MLB's 15 and would blow the email size guard weekly.

No API calls, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "box_score"))

import fetch_sports_data as fsd          # noqa: E402
import build_box_score as bbs            # noqa: E402

FAILURES: list[str] = []


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAILURES.append(msg)


# ---------------------------------------------------------------------------
# Fixtures — shaped exactly like the ESPN payloads the parsers consume.
# ---------------------------------------------------------------------------

def football_summary():
    def group(name, labels, athletes):
        return {"name": name, "labels": labels, "athletes": athletes}

    def ath(short, pos, stats):
        return {"athlete": {"shortName": short, "position": {"abbreviation": pos}},
                "stats": stats}

    return {
        "boxscore": {
            "players": [
                {"homeAway": "away", "team": {"abbreviation": "DEN"},
                 "statistics": [
                     group("passing", ["C/ATT", "YDS", "AVG", "TD", "INT", "QBR"],
                           [ath("B. Nix", "QB", ["22/38", "251", "6.6", "1", "2", "41.3"])]),
                     group("rushing", ["CAR", "YDS", "AVG", "TD", "LONG"],
                           [ath("J. McLaughlin", "RB", ["11", "44", "4.0", "0", "12"]),
                            ath("B. Nix", "QB", ["0", "0", "0.0", "0", "0"])]),
                     group("receiving", ["REC", "YDS", "AVG", "TD", "LONG"],
                           [ath("C. Sutton", "WR", ["6", "81", "13.5", "1", "24"])]),
                     # A group we deliberately do not render.
                     group("defensive", ["TOT", "SOLO", "SACKS"],
                           [ath("P. Surtain", "CB", ["7", "5", "0"])]),
                 ]},
                {"homeAway": "home", "team": {"abbreviation": "KC"},
                 "statistics": [
                     group("passing", ["C/ATT", "YDS", "AVG", "TD", "INT", "QBR"],
                           [ath("P. Mahomes", "QB", ["27/37", "306", "8.3", "3", "0", "88.1"])]),
                     group("rushing", ["CAR", "YDS", "AVG", "TD", "LONG"],
                           [ath("I. Pacheco", "RB", ["18", "92", "5.1", "1", "21"])]),
                     group("receiving", ["REC", "YDS", "AVG", "TD", "LONG"],
                           [ath("T. Kelce", "TE", ["9", "104", "11.6", "2", "27"])]),
                 ]},
            ],
        },
        "header": {"competitions": [{"competitors": [
            {"homeAway": "away", "linescores": [{"displayValue": "3"}, {"displayValue": "0"},
                                                {"displayValue": "7"}, {"displayValue": "0"}]},
            {"homeAway": "home", "linescores": [{"displayValue": "7"}, {"displayValue": "10"},
                                                {"displayValue": "7"}, {"displayValue": "7"}]},
        ]}]},
        "scoringPlays": [
            {"period": {"number": 1}, "clock": {"displayValue": "8:12"},
             "team": {"abbreviation": "KC"}, "awayScore": 0, "homeScore": 7,
             "text": "P. Mahomes 12 Yd pass to T. Kelce (H. Butker Kick)"},
            {"period": {"number": 3}, "clock": {"displayValue": "2:04"},
             "team": {"abbreviation": "DEN"}, "awayScore": 10, "homeScore": 24,
             "text": "B. Nix 24 Yd pass to C. Sutton (W. Lutz Kick)"},
        ],
    }


def nfl_game(gid, away, home, a_score, h_score, boxed=True, **extra):
    g = {
        "game_id": gid, "completed": True,
        "away_team": away, "home_team": home,
        "away_abbr": away.split()[-1][:3].upper(), "home_abbr": home.split()[-1][:3].upper(),
        "away_score": a_score, "home_score": h_score,
        "winner": away if a_score > h_score else home,
        "away_rank": None, "home_rank": None,
    }
    g.update(extra)
    if boxed:
        g["box_score"] = fsd.parse_box_score(football_summary(), "nfl")
    return g


# ---------------------------------------------------------------------------
# 1. The parser
# ---------------------------------------------------------------------------
print("\n[1] _parse_football_box")

box = fsd.parse_box_score(football_summary(), "nfl")
check(box != {}, "parse_box_score dispatches nfl (was {} — no football branch)")
check(fsd.parse_box_score(football_summary(), "ncaafb") != {},
      "parse_box_score dispatches ncaafb")

away, home = box.get("away", {}), box.get("home", {})
check(away.get("team") == "DEN" and home.get("team") == "KC", "home/away sides resolved")
check(len(away["passing"]) == 1 and away["passing"][0]["name"] == "B. Nix", "passing parsed")
check(away["passing"][0]["stats"] == {"C/ATT": "22/38", "YDS": "251", "TD": "1", "INT": "2"},
      "passing keeps only the rendered columns, by label not position")
check(len(home["receiving"]) == 1 and home["receiving"][0]["stats"]["TD"] == "2",
      "receiving parsed")
check([p["name"] for p in away["rushing"]] == ["J. McLaughlin"],
      "all-zero stat lines dropped (B. Nix 0 carries)")
check("defensive" not in away, "unrendered groups are not carried")

ls = box["linescore"]
check(ls["period_labels"] == ["Q1", "Q2", "Q3", "Q4"], "quarter labels")
check(ls["away_periods"] == ["3", "0", "7", "0"], "quarter scores")

ot = football_summary()
for c in ot["header"]["competitions"][0]["competitors"]:
    c["linescores"].append({"displayValue": "6"})
check(fsd.parse_box_score(ot, "nfl")["linescore"]["period_labels"][-1] == "OT1",
      "overtime period appends OT1")

plays = box["agate"]["scoring_plays"]
check(len(plays) == 2 and plays[0]["team"] == "KC" and plays[0]["quarter"] == 1,
      "scoring summary parsed from scoringPlays[]")

check(fsd.parse_box_score({}, "nfl")["linescore"]["period_labels"] == [],
      "empty summary degrades instead of raising")


# ---------------------------------------------------------------------------
# 2. Ranked-matchup filtering (CFB)
# ---------------------------------------------------------------------------
print("\n[2] ranked-matchup filter")

check(fsd._competitor_rank({"curatedRank": {"current": 7}}) == 7, "curatedRank read")
check(fsd._competitor_rank({"curatedRank": {"current": 99}}) is None,
      "ESPN's 99 sentinel is unranked")
check(fsd._competitor_rank({"rank": 3}) == 3, "bare rank field read")
check(fsd._competitor_rank({}) is None, "missing rank is unranked")
check(fsd._competitor_rank({"curatedRank": {"current": 26}}) is None, "26 is outside the poll")

rankings = [{"rank": 1, "team": "Longhorns", "abbr": "TEX", "record": "2-0"},
            {"rank": 3, "team": "Buckeyes", "abbr": "OSU", "record": "2-0"}]
games = [
    {"game_id": "ranked-both", "home_abbr": "TEX", "away_abbr": "OSU",
     "home_team": "Texas Longhorns", "away_team": "Ohio State Buckeyes"},
    {"game_id": "ranked-one", "home_abbr": "TEX", "away_abbr": "RICE",
     "home_team": "Texas Longhorns", "away_team": "Rice Owls"},
    {"game_id": "unranked", "home_abbr": "RICE", "away_abbr": "TULN",
     "home_team": "Rice Owls", "away_team": "Tulane Green Wave"},
    {"game_id": "by-curated", "home_abbr": "XXX", "away_abbr": "YYY",
     "home_team": "Somewhere State", "away_team": "Elsewhere Tech", "home_rank": 12},
]
keep = fsd._ranked_game_ids(games, rankings)
check("ranked-both" in keep, "both-ranked matchup kept")
check("ranked-one" in keep, "one-ranked matchup kept (the upset case)")
check("unranked" not in keep, "unranked matchup dropped")
check("by-curated" in keep, "scoreboard curatedRank works when the poll misses the team")

no_poll = fsd._ranked_game_ids(games, [])
check(no_poll == {"by-curated"},
      "with no poll, only curatedRank decides — the filter never silently keeps everything")

# A big Saturday can put 20+ ranked teams on the field, so the fetch cap has to
# keep the best games rather than whichever ESPN happened to list first.
check(fsd._best_rank({"home_rank": 3, "away_rank": 1}) == 1, "best rank is the lower of the two")
check(fsd._best_rank({"home_rank": None, "away_rank": 7}) == 7, "one ranked side still sorts")
check(fsd._best_rank({}) > fsd.POLL_SIZE, "unranked sorts last")
ordered = sorted([{"home_rank": None, "away_rank": None}, {"home_rank": 12, "away_rank": None},
                  {"home_rank": 3, "away_rank": 1}, {"home_rank": None, "away_rank": 7}],
                 key=fsd._best_rank)
check([fsd._best_rank(g) for g in ordered] == [1, 7, 12, 26],
      "capping keeps the marquee matchups, not the first N")
check(fsd.MAX_FOOTBALL_BOX_FETCHES > 0 and fsd.CFB_REQUIRE_BOTH_RANKED is False,
      "ranked-only policy constants are set as documented")


# ---------------------------------------------------------------------------
# 3. The renderer
# ---------------------------------------------------------------------------
print("\n[3] renderers")

g = nfl_game("1", "Denver Broncos", "Kansas City Chiefs", 10, 31)
html = bbs._mi_football_game(g)
check("Chiefs 31" in html and "Broncos 10" in html, "headline leads with the winner")
check("Passing" in html and "Rushing" in html and "Receiving" in html,
      "all three unit tables render")
check("Passer" in html and "Receiver" in html,
      "stat tables label their own unit (were hardcoded Batter/Pitcher)")
check("P. Mahomes" in html and "306" in html, "player stats reach the page")
check("Q1" in html and "Q4" in html, "quarter linescore renders")
check("Scoring Summary" in html and "H. Butker Kick" in html, "scoring summary renders")

ranked = nfl_game("2", "Ohio State Buckeyes", "Texas Longhorns", 23, 24,
                  away_rank=3, home_rank=1)
check("#3 Buckeyes" in bbs._mi_football_game(ranked)
      and "#1 Longhorns" in bbs._mi_football_game(ranked),
      "poll ranks prefix the matchup line")

check("Broncos" in bbs._mi_football_game(nfl_game("3", "Denver Broncos",
                                                  "Kansas City Chiefs", 10, 31, boxed=False)),
      "a game with no box score still renders its headline")

poll_html = bbs._mi_rankings(rankings)
check("Longhorns" in poll_html and "2-0" in poll_html, "AP poll table renders")
check(bbs._mi_rankings([]) == "", "no poll renders nothing")

dirty = [{"team": "South Florida Bulls", "wins": "0", "losses": "?", "win_pct": "?",
          "games_behind": "-", "streak": "-"}]
std = bbs._mi_simple_standings(dirty)
check("?" not in std.replace('team="?"', ""),
      "ESPN's '?' sentinel never reaches a reader (shipped on every CFB page)")


# ---------------------------------------------------------------------------
# 4. Sections and chunking
# ---------------------------------------------------------------------------
print("\n[4] sections + chunking")

def state(sport_key, games, **extra):
    data = {"label": "NFL" if sport_key == "nfl" else "College Football",
            "yesterday_games": games, "today_games": [], "standings": [],
            "rankings": [], "leaders": {}, "bracket": []}
    data.update(extra)
    return {"yesterday_date": "2026-09-14", "sports": {sport_key: data}}

nfl_13 = [nfl_game(str(i), f"Away {i}", f"Home {i}", 20, 24) for i in range(13)]
gs = state("nfl", nfl_13)

sections = bbs._render_football_sections("nfl", gs["sports"]["nfl"])
check("Box Scores" in sections, "football section emits a Box Scores rule (MLB-only before)")
check(sections.count("Passing") == 26, "every completed game gets a box score (2 sides each)")

blocks = bbs.build_chunk_blocks(gs, "nfl", bare=True)
check(len(blocks) == 1 + 5, f"13 games at 3/chunk → summary + 5 chunks (got {len(blocks)})")
check("NFL" in blocks[0], "sport band on the first image only")
check(all("box-score-band" not in b for b in blocks[1:]), "continuation images carry no repeat band")

per_sport = bbs.build_box_score_block_for_sport(gs, "nfl", bare=True)
check("Passing" in per_sport, "per-sport block carries box scores")

# CFB: only the ranked games carry box_score, so only those render — but the
# results strip still lists the whole slate.
cfb_games = ([nfl_game(f"r{i}", f"Ranked {i}", f"Home {i}", 30, 24) for i in range(4)]
             + [nfl_game(f"u{i}", f"Unranked{i}", f"Nobody{i}", 10, 7, boxed=False) for i in range(60)])
cfb = state("ncaafb", cfb_games, rankings=rankings)
cfb_blocks = bbs.build_chunk_blocks(cfb, "ncaafb", bare=True)
check(len(cfb_blocks) == 1 + 2, f"4 ranked games at 3/chunk → summary + 2 (got {len(cfb_blocks)})")
check("AP Top 25" in cfb_blocks[0], "CFB summary leads with the poll, not a random conference")
check("Ranked Matchups" in "".join(cfb_blocks[1:]),
      "the label says the other 60 games were left out on purpose")
body = "".join(cfb_blocks)
check(body.count("Passing") == 8, "unranked games contribute no box score (4 games x 2 sides)")
check("Unranked0" in cfb_blocks[0] and "Unranked59" in cfb_blocks[0],
      "the results strip still carries the full 64-game slate")

check(bbs.build_chunk_blocks(state("nfl", []), "nfl", bare=True) == [],
      "a sport with nothing to show produces no images")
check(bbs.build_chunk_blocks(gs, "nba", bare=True) == [],
      "build_chunk_blocks is a no-op for unchunked sports")
check(bbs.build_mlb_chunk_blocks({"sports": {}}, bare=True) == [],
      "the old MLB entry point still works")

check(bbs.CHUNK_SIZES["nfl"] == 3 and bbs.CHUNK_SIZES["mlb"] == 4,
      "football chunks smaller than baseball (3 tables per side, not 2)")
check("nfl" in bbs.PLAYOFF_WINDOWS, "NFL has a playoff window (was missing → no bracket in Jan)")


# ---------------------------------------------------------------------------
# 5. End to end through main()'s per-sport path
# ---------------------------------------------------------------------------
print("\n[5] end to end")

full = {"yesterday_date": "2026-09-14", "sports": {
    "nfl": state("nfl", nfl_13)["sports"]["nfl"],
    "ncaafb": cfb["sports"]["ncaafb"],
}}
check(bbs._ordered_sport_keys(full) == ["nfl", "ncaafb"], "both football keys have data")
pages = [bbs.build_standalone(b)
         for k in bbs._ordered_sport_keys(full)
         for b in bbs.build_chunk_blocks(full, k, bare=True)]
check(len(pages) == 6 + 3, f"NFL 6 + CFB 3 images (got {len(pages)})")
check(all(p.startswith("<!DOCTYPE html>") and "</html>" in p for p in pages),
      "every page is a complete renderable document")
check(all(len(p) < 400_000 for p in pages),
      "no single page is enormous (the 80-game strip was the failure mode)")

combined = bbs.build_box_score_block(full)
check("Passing" in combined and "AP Top 25" in combined,
      "the combined --append block carries football box scores too")


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("All football box score checks passed.")
