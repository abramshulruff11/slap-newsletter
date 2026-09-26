"""
SLAP Newsletter — defending champions from the shared sports database (SLA-65)

game_state.json holds yesterday's games, so on its own it can never say who
won LAST season's title, and a "defending champion" claim could only ever be
flagged for a human. This reads the current champion of every launch league
from slap-sports-db (`v_league_title`, the view whose counts reconcile against
the record books) and fetch_sports_data.py writes it into game_state.json
under "champions". From there it is ground truth like the scores: the writer
and editor see it in the GROUND TRUTH block, and Pass 3 (claim_validator.py)
checks each claim against it and confirms or corrects it.

Two rules keep it from being confidently wrong:

- **Stale means unknown.** Most leagues' champions are curated and added once
  a year (Linear SLA-94/95/96). A league whose newest title is older than the
  calendar says it should be is reported as `stale`, never as the defending
  champion, so a missed yearly update can't turn last year's champion into
  this year's. The dates are the day after the latest a title is normally
  decided; between the title game and that date the previous champion is
  still shown, a window of a few days at most.
- **Fail soft, loudly.** No SPORTS_DB_URL, no driver, or no database: the
  block says `unavailable` and why, Pass 3 falls back to asking for the claim
  to be cut, and the run goes on. The newsletter is the product.

The password never reaches a log: the URL is parsed, the password passed as
a keyword, and any error text is scrubbed of it (same rule as slap-sports-db).
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import date

# league (slap-sports-db id) -> (how the newsletter names it, what its champion is called)
LEAGUES = {
    "nfl":    ("NFL",                      "Super Bowl champion"),
    "mlb":    ("MLB",                      "World Series champion"),
    "nhl":    ("NHL",                      "Stanley Cup champion"),
    "nba":    ("NBA",                      "NBA champion"),
    "ncaaf":  ("college football",         "national champion"),
    "ncaamb": ("men's college basketball", "NCAA tournament champion"),
}

# (month, day) by which season Y's title is decided, and how many years after
# the season's START YEAR that date falls. The Super Bowl is in February of
# Y+1; the World Series ends in early November of Y; the NBA and NHL finish
# in late June of Y+1 (a 2025-26 season is year 2025); the CFP title game is
# in late January of Y+1; the NCAA tournament ends in early April of Y+1.
DECIDED_BY = {
    "nfl":    ((2, 16), 1),
    "mlb":    ((11, 6), 0),
    "nhl":    ((6, 26), 1),
    "nba":    ((6, 24), 1),
    "ncaaf":  ((1, 22), 1),
    "ncaamb": ((4, 10), 1),
}


def expected_latest_season(league: str, today: date) -> int:
    """The newest season whose champion should be known on `today`."""
    (month, day), offset = DECIDED_BY[league]
    decided_this_year = (today.month, today.day) >= (month, day)
    # The season decided this calendar year started `offset` years earlier.
    return today.year - offset - (0 if decided_this_year else 1)


def _secrets(url: str) -> list[str]:
    try:
        pw = urllib.parse.urlsplit(url).password if "://" in url else None
    except ValueError:
        pw = None
    if not pw:
        return []
    raw = urllib.parse.unquote(pw)
    return sorted({pw, raw, urllib.parse.quote(raw, safe="")}, key=len, reverse=True)


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text


def _connect(url: str):
    import psycopg  # only when the database is configured
    params = psycopg.conninfo.conninfo_to_dict(url)
    password = params.pop("password", None)
    return psycopg.connect(**params, password=password, connect_timeout=15)


QUERY = """
    SELECT DISTINCT ON (vl.league_id)
           vl.league_id, vl.year, vl.season_label, vl.title, vl.team_name,
           vl.franchise_name, t.nickname, t.location, vl.disputed, vl.note
    FROM v_league_title vl
    JOIN team t ON t.team_id = vl.team_id
    WHERE vl.league_id = ANY(%s)
    ORDER BY vl.league_id, vl.year DESC
"""


def aliases(row: dict) -> list[str]:
    """Every name a sentence might use for the champion. Colleges go by the
    school ("Michigan"), pros by the nickname or the full name; a bare city
    is not enough for a pro team ("New York" is three NBA/NHL/NFL clubs)."""
    names = {row["team_name"], row["franchise_name"]}
    if row.get("nickname"):
        names.add(row["nickname"])
    if row["league"] in ("ncaaf", "ncaamb"):
        names.add(row["location"])
    return sorted(n for n in names if n)


def build_block(rows: list[dict], today: date) -> dict:
    """Shape query rows into the game_state "champions" block."""
    by_league = {r["league"]: r for r in rows}
    leagues = {}
    for league, (label, title_word) in LEAGUES.items():
        want = expected_latest_season(league, today)
        r = by_league.get(league)
        if r is None:
            leagues[league] = {"label": label, "status": "missing",
                               "reason": "no champion in the database"}
            continue
        entry = {
            "label": label,
            "title_word": title_word,
            "season": r["season"],
            "season_label": r["season_label"],
            "title": r["title"],
            "team": r["team_name"],
            "franchise": r["franchise_name"],
            "aliases": aliases(r),
            "city": r["location"] if league not in ("ncaaf", "ncaamb") else None,
            "disputed": bool(r.get("disputed")),
            "note": r.get("note"),
        }
        if r["season"] < want:
            entry["status"] = "stale"
            entry["reason"] = (f"newest title is {r['season_label']}, but the "
                               f"{season_word(league, want)} champion should be decided by now")
        else:
            entry["status"] = "ok"
        leagues[league] = entry
    return {"status": "ok", "as_of": today.isoformat(), "source": "slap-sports-db",
            "leagues": leagues}


def season_word(league: str, year: int) -> str:
    return str(year) if league in ("nfl", "mlb", "ncaaf") else f"{year}-{(year + 1) % 100:02d}"


def fetch_champions(url: str | None = None, today: date | None = None, connect=_connect) -> dict:
    """The game_state "champions" block. Never raises."""
    url = url if url is not None else os.environ.get("SPORTS_DB_URL", "")
    today = today or date.today()
    if not url:
        return {"status": "unavailable", "reason": "SPORTS_DB_URL is not set"}
    secrets = _secrets(url)
    try:
        with connect(url) as conn:
            cur = conn.execute(QUERY, (list(LEAGUES),))
            cols = [c.name for c in cur.description]
            rows = []
            for rec in cur.fetchall():
                d = dict(zip(cols, rec))
                rows.append({"league": d["league_id"], "season": d["year"],
                             "season_label": d["season_label"], "title": d["title"],
                             "team_name": d["team_name"], "franchise_name": d["franchise_name"],
                             "nickname": d["nickname"], "location": d["location"],
                             "disputed": d["disputed"], "note": d["note"]})
        return build_block(rows, today)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        return {"status": "unavailable", "reason": _scrub(f"{type(e).__name__}: {e}", secrets)}


def known_champions(game_state: dict) -> dict[str, dict]:
    """League -> entry, for the leagues whose champion is current. Empty when
    the block is missing or unavailable."""
    block = (game_state or {}).get("champions") or {}
    if block.get("status") != "ok":
        return {}
    return {k: v for k, v in block.get("leagues", {}).items() if v.get("status") == "ok"}


def unknown_leagues(game_state: dict) -> list[str]:
    block = (game_state or {}).get("champions") or {}
    if block.get("status") != "ok":
        return list(LEAGUES)
    return [k for k, v in block.get("leagues", {}).items() if v.get("status") != "ok"]


def summary_lines(game_state: dict) -> list[str]:
    """The DEFENDING CHAMPIONS part of the GROUND TRUTH block."""
    block = (game_state or {}).get("champions") or {}
    if block.get("status") != "ok":
        return []
    lines = ["## GROUND TRUTH: DEFENDING CHAMPIONS",
             f"Source: SLAP sports database as of {block.get('as_of')} (the most recent completed "
             "championship in each league). A team may be called the defending champion ONLY "
             "if it is listed here for that league. Anyone else is not.",
             ""]
    for league, entry in block.get("leagues", {}).items():
        if entry.get("status") == "ok":
            line = (f"  {entry['label']}: {entry['team']} — {entry['season_label']} "
                    f"{entry['title_word']} ({entry['title']})")
            if entry.get("disputed") and entry.get("note"):
                line += f". Disputed: {entry['note']}"
            lines.append(line)
        else:
            lines.append(f"  {entry['label']}: UNKNOWN — do not call any team the defending "
                         f"{entry.get('label')} champion.")
    lines.append("")
    return lines
