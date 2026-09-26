"""
Run:  python -X utf8 uat/tests/test_defending_champion.py

SLA-65: Pass 3 RESOLVES "defending champion" instead of flagging it.

Until now claim_validator.py could only write a LOW flag asking a human to
check the title holder, because game_state.json knew yesterday's games and
nothing else. It now carries each league's defending champion, read from
slap-sports-db (champions_source.py), and the check acts on it:

  - the team named IS the champion   -> confirmed, the draft is untouched
  - it is a DIFFERENT team           -> FACT FLAG [HIGH] naming the real
                                        champion, which editor Check 9 fixes
  - it can't be matched, or a league's champion isn't known today
                                     -> FACT FLAG [LOW], the title phrase is cut

This locks that decision table, the calendar that decides when a league's
champion is stale, the fail-soft fetch (and that the database password never
reaches its error text), the ground-truth block the writer and editor read,
and the prompt lines that point at it. No API calls, no network, no database.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import champions_source as cs                         # noqa: E402
from claim_validator import check_defending_champion, validate_claims   # noqa: E402
from runner_common import format_game_state_summary   # noqa: E402

TODAY = date(2026, 9, 26)


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


def row(league, season, label, title, team, franchise, nickname, location, disputed=False, note=None):
    return {"league": league, "season": season, "season_label": label, "title": title,
            "team_name": team, "franchise_name": franchise, "nickname": nickname,
            "location": location, "disputed": disputed, "note": note}


# The database's answers on 2026-09-26.
ROWS = [
    row("nfl", 2025, "2025", "Super Bowl LX", "Seattle Seahawks", "Seattle Seahawks", "Seahawks", "Seattle"),
    row("mlb", 2025, "2025", "World Series", "Los Angeles Dodgers", "Los Angeles Dodgers", "Dodgers", "Los Angeles"),
    row("nhl", 2025, "2025-26", "Stanley Cup", "Carolina Hurricanes", "Carolina Hurricanes", "Hurricanes", "Carolina"),
    row("nba", 2025, "2025-26", "NBA Finals", "New York Knicks", "New York Knicks", "Knicks", "New York"),
    row("ncaaf", 2025, "2025", "CFP national title", "Indiana Hoosiers", "Indiana", "Hoosiers", "Indiana"),
    row("ncaamb", 2025, "2025-26", "NCAA championship", "Michigan", "Michigan", None, "Michigan"),
]


def state(rows=ROWS, today=TODAY):
    return {"sports": {}, "champions": cs.build_block(rows, today)}


def flags(text, game_state=None):
    return check_defending_champion(text, game_state if game_state is not None else state())


def severities(text, game_state=None):
    return [re.search(r"FACT FLAG \[(\w+)\]", f).group(1) for f in flags(text, game_state)]


print("=" * 66)
print("WHEN IS A LEAGUE'S CHAMPION KNOWN — the staleness calendar")
print("=" * 66)
check("NFL, September 2026 -> the 2025 season", cs.expected_latest_season("nfl", TODAY), 2025)
check("NFL, the week before Super Bowl LXI -> still 2025", cs.expected_latest_season("nfl", date(2027, 2, 10)), 2025)
check("NFL, after it -> 2026", cs.expected_latest_season("nfl", date(2027, 2, 20)), 2026)
check("MLB, September 2026 -> 2025 (the 2026 Series isn't played yet)", cs.expected_latest_season("mlb", TODAY), 2025)
check("MLB, 10 Nov 2026 -> 2026", cs.expected_latest_season("mlb", date(2026, 11, 10)), 2026)
check("NBA, 1 July 2026 -> the 2025-26 season", cs.expected_latest_season("nba", date(2026, 7, 1)), 2025)
check("NBA, 1 June 2026 -> still 2024-25", cs.expected_latest_season("nba", date(2026, 6, 1)), 2024)
check("NCAAMB, 20 April 2026 -> 2025-26", cs.expected_latest_season("ncaamb", date(2026, 4, 20)), 2025)
check("NCAAF, 1 Feb 2026 -> the 2025 season", cs.expected_latest_season("ncaaf", date(2026, 2, 1)), 2025)

print()
print("=" * 66)
print("THE CHAMPIONS BLOCK")
print("=" * 66)
block = cs.build_block(ROWS, TODAY)
check("every league current on 2026-09-26", {k: v["status"] for k, v in block["leagues"].items()},
      {k: "ok" for k in cs.LEAGUES})
check("NBA champion", block["leagues"]["nba"]["team"], "New York Knicks")
# A missed yearly update must read as unknown, never as last year's champion.
late = cs.build_block(ROWS, date(2026, 11, 10))
check("MLB after the 2026 Series with no new row -> stale", late["leagues"]["mlb"]["status"], "stale")
check("... and it is not offered as a champion", "mlb" in cs.known_champions({"champions": late}), False)
missing = cs.build_block([r for r in ROWS if r["league"] != "nhl"], TODAY)
check("a league with no row at all -> missing", missing["leagues"]["nhl"]["status"], "missing")
check("college aliases include the school", "Michigan" in block["leagues"]["ncaamb"]["aliases"], True)
check("pro aliases do not include a bare city", "New York" in block["leagues"]["nba"]["aliases"], False)

print()
print("=" * 66)
print("FETCHING — fail soft, never leak the password")
print("=" * 66)
check("no SPORTS_DB_URL -> unavailable", cs.fetch_champions(url="")["status"], "unavailable")


class FakeCursor:
    description = [type("C", (), {"name": n}) for n in (
        "league_id", "year", "season_label", "title", "team_name", "franchise_name",
        "nickname", "location", "disputed", "note")]

    def fetchall(self):
        return [(r["league"], r["season"], r["season_label"], r["title"], r["team_name"],
                 r["franchise_name"], r["nickname"], r["location"], r["disputed"], r["note"])
                for r in ROWS]


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        assert "v_league_title" in sql
        return FakeCursor()


got = cs.fetch_champions(url="postgresql://u:p@h/db", today=TODAY, connect=lambda url: FakeConn())
check("a reachable database -> ok", got["status"], "ok")
check("... Super Bowl champion", got["leagues"]["nfl"]["team"], "Seattle Seahawks")

secret_url = "postgresql://postgres.ref:s3cr%25t%40pw@pooler.example.com:5432/postgres"


def refuse(url):
    raise RuntimeError("connection failed: password=s3cr%t@pw, url pw s3cr%25t%40pw")


bad = cs.fetch_champions(url=secret_url, today=TODAY, connect=refuse)
check("a dead database -> unavailable", bad["status"], "unavailable")
check("the password is not in the reason", "s3cr" in bad["reason"], False)

print()
print("=" * 66)
print("PASS 3 — resolved, not just flagged")
print("=" * 66)
check("the right team -> confirmed, no flag", severities("the defending champion Knicks rolled again."), [])
check("the right team, full name", severities("The defending NBA champion New York Knicks lost."), [])
check("the right team, title after the name",
      severities("The Seahawks, the defending champions, opened at home."), [])
check("a college by school name", severities("Defending champion Michigan tipped off."), [])
check("the champion named anywhere in the sentence",
      severities("Say what you like about defending champs, but the Hurricanes looked sharp."), [])
check("a trailing word after the name", severities("The defending champion Dodgers Tuesday were flat."), [])
check("the WRONG team -> HIGH", severities("They eliminated the defending champion Thunder."), ["HIGH"])
wrong = flags("They eliminated the defending champion Thunder.")[0]
check("... the HIGH flag names the real champion", "New York Knicks" in wrong, True)
check("... and quotes the sentence it points at", "defending champion Thunder" in wrong, True)
check("Michigan State is not Michigan -> HIGH",
      severities("Defending champion Michigan State returns four starters."), ["HIGH"])
check("a bare city could be any club -> LOW", severities("The defending champion Carolina looked slow."), ["LOW"])
check("no team at all -> LOW", severities("The defending champs have been awful."), ["LOW"])
check("two claims, two answers",
      severities("The defending champion Knicks beat Boston. Later the defending champion Celtics lost."),
      ["HIGH"])

# Found by replaying the archive (every "defending champ" since April 2026):
check("a capitalised sentence opener is not a team -> not HIGH",
      severities("Against the defending champions. That is the ask."), ["LOW"])
check("the unnamed champion the section is about -> confirmed",
      severities("The Knicks go to Boston. They stole homecourt from the defending champs."), [])
dash = flags("The defending champion Thunder -- again -- lost.")[0]
check("quoted text can't end the HTML comment early", dash.count("--") == 2 and dash.endswith("-->"), True)

stale = state(today=date(2026, 11, 10))   # the 2026 World Series isn't in yet
check("a league unknown today: a mismatch is LOW, not HIGH",
      severities("The defending champion Blue Jays opened camp.", stale), ["LOW"])
check("... and says which champion is unknown",
      "MLB champion is not known" in flags("The defending champion Blue Jays opened camp.", stale)[0], True)
check("no champions block at all -> LOW, as before", severities("the defending champion Knicks", {"sports": {}}), ["LOW"])

html = ("<h1>SLAP</h1><h2>NBA</h2><p>The defending champion Knicks won.</p>"
        "<h2>NHL</h2><p>The defending champion Oilers lost.</p>")
path = REPO / "uat" / "tests" / "_tmp_game_state_sla65.json"
import json  # noqa: E402
path.write_text(json.dumps(state()), encoding="utf-8")
try:
    annotated, n = validate_claims(html, path)
finally:
    path.unlink()
check("end to end: one flag, in the NHL section", n, 1)
check("... the NBA section untouched", "The defending champion Knicks won.</p><h2>NHL" in annotated, True)
check("... the flag lands in the NHL section", annotated.index("FACT FLAG") > annotated.index("<h2>NHL"), True)

# An embedded tweet is someone else's words (and the editor may not touch
# one): 2026-07-04 carried "THE DEFENDING CHAMPIONS SURVIVE..." about
# Argentina, which is not a claim SLAP made.
tweet_html = ('<h2>World Cup</h2><p>Argentina escaped again.</p>'
              '<blockquote class="tweet"><p>THE DEFENDING CHAMPIONS SURVIVE AN EXTRA TIME '
              'THRILLER</p></blockquote>')
path.write_text(json.dumps(state()), encoding="utf-8")
try:
    _, n_tweet = validate_claims(tweet_html, path)
finally:
    path.unlink()
check("a claim inside an embedded tweet is not checked", n_tweet, 0)

print()
print("=" * 66)
print("THE GROUND TRUTH THE WRITER AND EDITOR READ")
print("=" * 66)
summary = format_game_state_summary(state())
check("the block has a DEFENDING CHAMPIONS section", "## GROUND TRUTH: DEFENDING CHAMPIONS" in summary, True)
check("... even on a day with no games", summary.startswith("## GROUND TRUTH: DEFENDING CHAMPIONS"), True)
check("... naming the NBA champion", "NBA: New York Knicks — 2025-26 NBA champion" in summary, True)
stale_summary = format_game_state_summary({"sports": {}, "champions": late})
check("a stale league tells the writer not to use the phrase",
      "MLB: UNKNOWN — do not call any team the defending MLB champion." in stale_summary, True)
check("no champions block -> nothing added", format_game_state_summary({"sports": {}}), "")

print()
print("=" * 66)
print("THE PROMPTS POINT AT IT")
print("=" * 66)
for rel in ("prompts/rolling_feedback.txt", "uat/prompts/rolling_feedback.txt"):
    text = (REPO / rel).read_text(encoding="utf-8")
    check(f"{rel}: RULE 3.4 points at DEFENDING CHAMPIONS", "under DEFENDING CHAMPIONS" in text, True)
    check(f"{rel}: RULE 3.4 no longer sends the writer to game_state.json",
          "Verify against game_state.json before using" in text, False)
for rel in ("prompts/editor_prompt.txt", "uat/prompts/editor_prompt.txt"):
    text = (REPO / rel).read_text(encoding="utf-8")
    check(f"{rel}: a listed defending champion is SOURCED",
          "lists under DEFENDING CHAMPIONS for that league is SOURCED" in text, True)

print("\nall defending-champion checks passed")
