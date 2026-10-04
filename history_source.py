"""
SLAP Newsletter — verified team history from the shared sports database (SLA-108)

RULE 3 cuts or blurs every specific historical claim ("first since 2015",
"a 27-year drought") because nothing could source one; the model fills them
from memory and gets them wrong (the Knicks' "53-year Finals drought" was a
27-year one). This computes the history for the teams in yesterday's games
from slap-sports-db, and fetch_sports_data.py writes it into game_state.json
under "history". It reaches the writer and editor as the HISTORICAL CONTEXT
part of the GROUND TRUTH block, where RULE 3 treats it as sourced.

Every fact is computed in SQL from stored games, standings, titles and polls.
No model writes or checks one. Four rules keep them honest:

- **Depth is stated, never implied.** NFL games go back to 1999 here,
  baseball's to 1876. A streak with no equal in the data is "the longest since
  at least 1999", never "since 1999", which would claim a fact about 1998.
  Titles are the exception: the title record is complete, so "never won a
  World Series" is a fact.
- **As of the issue's date.** Games after `yesterday_date` are ignored, so a
  replay of an old issue sees what that morning's run saw.
- **Only what is notable is listed.** A streak of 3+ or a start that hasn't
  happened in 5+ seasons, a head-to-head drought of 3+ seasons, plus one line
  of title and playoff history per team. The block is in every pass's prompt;
  a fact nobody would write is cost and noise.
- **Droughts are kept apart.** Last title, last title-game appearance, last
  playoff appearance and last winning season are four facts, because conflating
  two of them is exactly the Knicks error.
- **Fail soft.** No SPORTS_DB_URL, no driver, no database: the block says
  `unavailable` and the run goes on with RULE 3 as before. The newsletter is
  the product. The password is scrubbed from any error (champions_source).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from champions_source import _connect, _scrub, _secrets, expected_latest_season

# game_state sport key -> (slap-sports-db league, how the newsletter names it,
# what its title is called, what its title game is called)
LEAGUES = {
    "nfl":    ("nfl",   "NFL",              "league title",     "championship game"),
    "mlb":    ("mlb",   "MLB",              "World Series title", "World Series"),
    "nhl":    ("nhl",   "NHL",              "Stanley Cup",        "Stanley Cup Final"),
    "nba":    ("nba",   "NBA",              "NBA title",          "NBA Finals"),
    "ncaafb": ("ncaaf", "college football", "national title",   None),
}
# Leagues whose playoff games are stored, so "last playoff appearance" is a
# fact rather than a guess (the others only have their champions; SLA-77).
PLAYOFF_GAMES = {"nfl"}
# What the streak, start and head-to-head facts count, per league.
SCOPE = {"ncaafb": "regular season, conference title games included, bowls not"}

STREAK_MIN = 3          # a streak shorter than this isn't a story
START_MIN_GAMES = 3     # nor is a start
NOTABLE_SEASONS = 5     # "best start since" is only worth saying if it's been a while
H2H_NOTABLE = 3         # "first win over X since" likewise
NOTABLE_STREAK = 3      # and a streak matched within the last 3 seasons isn't one


# Leagues whose seasons straddle two years. Their facts use the season's own
# label ("2025-26"), never its start year alone: the Knicks' 2025-26 title
# was won in 2026, and "last title: 2025" is the ambiguity RULE 3 exists for.
TWO_YEAR = {"nba", "nhl"}
# Leagues with no "winning season" in the usual sense (overtime losses).
NO_WINNING_SEASON = {"nhl"}


def season(league: str, year: int | None) -> str | None:
    if year is None:
        return None
    return f"{year}-{(year + 1) % 100:02d}" if league in TWO_YEAR else str(year)


def norm(name: str) -> str:
    """Accents, apostrophes and case evened out: ESPN's "Montreal Canadiens"
    is the database's "Montréal Canadiens"."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


@dataclass
class TeamFacts:
    sport: str
    name: str                   # as the newsletter knows it (ESPN's)
    opponent: str | None
    lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Which teams: yesterday's games, every league the database covers. College
# football only for games with a ranked team, as the box scores do: a
# Saturday has 80 FBS games and the newsletter covers the ranked ones.
# ---------------------------------------------------------------------------

def teams_in_play(game_state: dict) -> dict[str, list[dict]]:
    """Each team in yesterday's completed games. A team that played a
    postseason game is marked `postseason` (SLA-110): until then playoff teams
    got no facts at all, which is how the Knicks' "53-year Finals drought"
    could never have been caught in June."""
    out: dict[str, list[dict]] = {}
    for sport, spec in LEAGUES.items():
        games = ((game_state.get("sports") or {}).get(sport) or {}).get("yesterday_games") or []
        rows = []
        for g in games:
            if not g.get("completed"):
                continue
            if sport == "ncaafb" and g.get("home_rank") is None and g.get("away_rank") is None:
                continue
            for side, other in (("home", "away"), ("away", "home")):
                rows.append({"name": g[f"{side}_team"], "espn_id": str(g.get(f"{side}_id") or ""),
                             "opponent": g[f"{other}_team"], "opponent_espn_id": str(g.get(f"{other}_id") or ""),
                             "home": side == "home", "postseason": bool(g.get("playoffs"))})
        if rows:
            out[sport] = rows
    return out


# ---------------------------------------------------------------------------
# SQL. One set of queries per league, all teams at once.
# ---------------------------------------------------------------------------

CURRENT_TEAMS = """
    SELECT t.team_id, t.franchise_id, t.full_name, t.nickname, t.location
    FROM team t WHERE t.league_id = %(league)s AND t.last_season IS NULL"""

CFBD_TEAMS = """
    SELECT x.external_id, t.team_id, t.franchise_id, t.full_name
    FROM team_xref x JOIN team t USING (team_id)
    WHERE x.source_id = 'cfbd' AND x.external_id = ANY(%(ids)s)"""

# The season of the league's most recent games up to the day. Not "the newest
# season that has started": a live lane can store a season without dates
# (MLB's 2026 had none), which made 2025 look current.
SEASON = """
    SELECT s.year, (SELECT min(s2.year) FROM season s2 JOIN game g2 ON g2.season_id = s2.season_id
                    WHERE s2.league_id = %(league)s AND g2.season_type = 'regular') AS first_year
    FROM game g JOIN season s ON s.season_id = g.season_id
    WHERE g.league_id = %(league)s AND g.season_type = 'regular'
      AND g.game_date BETWEEN %(day)s::date - %(days)s AND %(day)s::date
    ORDER BY g.game_date DESC LIMIT 1"""

# Every regular-season result of the franchises, one row per team-game,
# numbered within each season, built ONCE per league into a temp table that
# the streak, start and head-to-head queries all read. Two index-friendly
# halves (the game's home side, its away side) rather than one join on
# "team IN (home, away)": that scanned every game and timed out on MLB.
BUILD_SIDES = """
    CREATE TEMP TABLE hist_sides ON COMMIT DROP AS
    WITH ft AS (SELECT team_id, franchise_id FROM team WHERE franchise_id = ANY(%(franchises)s)),
    sides AS (
        SELECT ft.franchise_id, g.season_id, g.game_date, g.game_id, g.away_team_id AS opp_team, true AS home,
               CASE WHEN g.winner_team_id = g.home_team_id THEN 'W' WHEN g.is_tie THEN 'T' ELSE 'L' END AS r
        FROM ft JOIN game g ON g.home_team_id = ft.team_id
        WHERE g.league_id = %(league)s AND g.season_type = 'regular' AND g.status IN ('final', 'forfeit')
          AND g.game_date <= %(day)s
        UNION ALL
        SELECT ft.franchise_id, g.season_id, g.game_date, g.game_id, g.home_team_id, false,
               CASE WHEN g.winner_team_id = g.away_team_id THEN 'W' WHEN g.is_tie THEN 'T' ELSE 'L' END
        FROM ft JOIN game g ON g.away_team_id = ft.team_id
        WHERE g.league_id = %(league)s AND g.season_type = 'regular' AND g.status IN ('final', 'forfeit')
          AND g.game_date <= %(day)s)
    SELECT si.franchise_id, s.year, si.game_date, si.r, ot.franchise_id AS opp, si.home,
           row_number() OVER (PARTITION BY si.franchise_id, s.year ORDER BY si.game_date, si.game_id) AS n,
           row_number() OVER (PARTITION BY si.franchise_id, s.year, si.r ORDER BY si.game_date, si.game_id) AS nr
    FROM sides si JOIN season s ON s.season_id = si.season_id JOIN team ot ON ot.team_id = si.opp_team"""

STREAKS = """
    WITH islands AS (
        SELECT franchise_id, year, r, count(*) AS len, max(n) AS ends_at
        FROM hist_sides GROUP BY franchise_id, year, r, n - nr),
    last_game AS (SELECT franchise_id, year, max(n) AS n FROM hist_sides GROUP BY 1, 2)
    SELECT i.franchise_id, i.year, i.r, i.len, (i.ends_at = l.n) AS is_last
    FROM islands i JOIN last_game l USING (franchise_id, year) WHERE i.r IN ('W', 'L')"""

STARTS = """
    WITH cur AS (SELECT franchise_id, count(*) AS g FROM hist_sides WHERE year = %(year)s GROUP BY 1)
    SELECT o.franchise_id, o.year, count(*) FILTER (WHERE o.r = 'W') AS w,
           count(*) FILTER (WHERE o.r = 'L') AS l, count(*) FILTER (WHERE o.r = 'T') AS t, c.g
    FROM hist_sides o JOIN cur c USING (franchise_id)
    WHERE o.n <= c.g GROUP BY o.franchise_id, o.year, c.g"""

HEAD_TO_HEAD = """
    SELECT franchise_id, opp, max(game_date) FILTER (WHERE r = 'W' AND game_date < %(day)s::date) AS last_win,
           max(game_date) FILTER (WHERE r = 'W' AND NOT home AND game_date < %(day)s::date) AS last_road_win,
           -- Yesterday's own game is the news, not the history: a team that
           -- just ended a drought must see the drought (SLA-109).
           coalesce(bool_or(r = 'W' AND game_date = %(day)s::date), false) AS won_today,
           min(year) AS first_meeting
    FROM hist_sides
    WHERE (franchise_id, opp) IN (SELECT * FROM unnest(%(pairs_f)s::bigint[], %(pairs_o)s::bigint[]))
    GROUP BY 1, 2"""

TITLES = """
    SELECT f.franchise_id,
           max(c.year) FILTER (WHERE wt.franchise_id = f.franchise_id) AS last_title,
           max(c.year) FILTER (WHERE (wt.franchise_id = f.franchise_id OR rt.franchise_id = f.franchise_id)
                                 AND c.runner_up_team_id IS NOT NULL) AS last_title_game,
           (SELECT min(year) FROM v_league_title WHERE league_id = ANY(%(leagues)s)) AS first_title_year
    FROM unnest(%(franchises)s::bigint[]) AS f(franchise_id)
    -- Seasons before the current one only: on a regular-season game day,
    -- this season's title isn't decided yet (a replay of 2026-03-15 must
    -- not see the Knicks' 2025-26 title, won that June).
    LEFT JOIN v_league_title c ON c.league_id = ANY(%(leagues)s) AND NOT coalesce(c.vacated, false)
                              AND c.year < %(year)s
    LEFT JOIN team wt ON wt.team_id = c.team_id
    LEFT JOIN team rt ON rt.team_id = c.runner_up_team_id
    GROUP BY f.franchise_id"""

PLAYOFFS = """
    SELECT t.franchise_id, max(s.year) AS last_playoffs,
           (SELECT min(s2.year) FROM game g2 JOIN season s2 USING (season_id)
            WHERE g2.season_type = 'postseason' AND g2.league_id = %(league)s) AS first_year
    FROM game g JOIN season s USING (season_id)
    JOIN team t ON t.team_id IN (g.home_team_id, g.away_team_id)
    WHERE g.season_type = 'postseason' AND t.franchise_id = ANY(%(franchises)s) AND s.year < %(year)s
    GROUP BY 1"""

WINNING = """
    SELECT t.franchise_id, max(s.year) AS last_winning
    FROM v_team_season_record v JOIN season s USING (season_id) JOIN team t USING (team_id)
    WHERE s.league_id = %(league)s AND t.franchise_id = ANY(%(franchises)s) AND s.year < %(year)s
      AND v.wins > v.losses
    GROUP BY 1"""

POLLS = """
    WITH ap AS (
        SELECT t.franchise_id, s.year, p.season_type, p.week, p.rank
        FROM poll_ranking p JOIN season s USING (season_id) JOIN team t USING (team_id)
        WHERE p.poll = 'AP Top 25' AND s.league_id = 'ncaaf' AND t.franchise_id = ANY(%(franchises)s)),
    latest AS (
        SELECT DISTINCT ON (franchise_id) franchise_id, rank FROM ap WHERE year = %(year)s
        ORDER BY franchise_id, (season_type = 'postseason') DESC, week DESC)
    SELECT l.franchise_id, l.rank,
           (SELECT max(year) FROM ap a WHERE a.franchise_id = l.franchise_id AND a.year < %(year)s) AS last_ranked,
           (SELECT max(year) FROM ap a WHERE a.franchise_id = l.franchise_id AND a.year < %(year)s
                                         AND a.rank <= l.rank) AS last_this_high,
           (SELECT min(year) FROM ap) AS first_year
    FROM latest l"""


def _rows(conn, sql: str, params: dict) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Turning query rows into sentences. Pure, so it's tested without a database.
# ---------------------------------------------------------------------------

def since_phrase(first_year: int, franchise_first: int | None, league: str = "") -> str:
    """How far back "never" reaches: the data's start, or the franchise's own
    if it's younger (the Brewers began in 1969, not 1876)."""
    if franchise_first and franchise_first > first_year:
        return f"in franchise history (since {season(league, franchise_first)})"
    return f"since at least {season(league, first_year)} (game data starts {season(league, first_year)})"


def streak_fact(streak_rows: list[dict], year: int, first_year: int, league: str = "") -> str | None:
    """The current streak, and the last season with one at least as long."""
    cur = next((r for r in streak_rows if r["year"] == year and r["is_last"]), None)
    if cur is None or cur["len"] < STREAK_MIN:
        return None
    word = "wins" if cur["r"] == "W" else "losses"
    past = [r["year"] for r in streak_rows if r["year"] < year and r["r"] == cur["r"] and r["len"] >= cur["len"]]
    if past:
        last = max(past)
        if year - last < NOTABLE_STREAK:
            return None          # it happened lately: not a story
        return f"{cur['len']} straight {word} this season; last {cur['len']}+ within a season: {season(league, last)}."
    first = min((r["year"] for r in streak_rows), default=None)
    return f"{cur['len']} straight {word} this season, their longest within a season {since_phrase(first_year, first, league)}."


def start_fact(start_rows: list[dict], year: int, first_year: int, league: str = "") -> str | None:
    """The record through G games against the same point of every past season.
    Only notable: a start better (or worse) than any in NOTABLE_SEASONS."""
    cur = next((r for r in start_rows if r["year"] == year), None)
    if cur is None or cur["g"] < START_MIN_GAMES:
        return None
    g, w, l = cur["g"], cur["w"], cur["l"]
    # Each season's own count: a past season shorter than G (a strike, a
    # pandemic) is compared over the games it had.
    pct = lambda r: (r["w"] + r["t"] / 2) / max(1, r["w"] + r["l"] + r["t"])
    past = [r for r in start_rows if r["year"] < year]
    record = f"{w}-{l}" + (f"-{cur['t']}" if cur["t"] else "")
    out = None
    if pct(cur) >= 0.6:
        as_good = [r["year"] for r in past if pct(r) >= pct(cur)]
        last = max(as_good) if as_good else None
        if last is None:
            out = f"{record} after {g} games, their best start {since_phrase(first_year, min(r['year'] for r in start_rows), league)}."
        elif year - last >= NOTABLE_SEASONS:
            out = f"{record} after {g} games; last start this good or better: {season(league, last)}."
    elif pct(cur) <= 0.34:
        as_bad = [r["year"] for r in past if pct(r) <= pct(cur)]
        last = max(as_bad) if as_bad else None
        if last is None:
            out = f"{record} after {g} games, their worst start {since_phrase(first_year, min(r['year'] for r in start_rows), league)}."
        elif year - last >= NOTABLE_SEASONS:
            out = f"{record} after {g} games; last start this bad or worse: {season(league, last)}."
    return out


def drought_fact(spec: tuple, titles: dict | None, playoffs: dict | None, winning: dict | None,
                 year: int, first_year: int, playoff_first: int | None, postseason: bool = False) -> str:
    league, label, title_word, game_word = spec
    # The title record is complete: every champion each league has crowned
    # (and every runner-up since there was a title game), so "none in
    # franchise history" is a fact here, not a guess. Only the game-based
    # facts need "since at least".
    parts = []
    t = (titles or {}).get("last_title")
    parts.append(f"last {title_word}: {season(league, t)}" if t else f"no {title_word} in franchise history")
    if game_word:
        tg = (titles or {}).get("last_title_game")
        parts.append(f"last {game_word} appearance: {season(league, tg)}" if tg
                     else f"no {game_word} appearance in franchise history")
    if league in PLAYOFF_GAMES:
        p = (playoffs or {}).get("last_playoffs")
        parts.append(f"last playoff appearance: {season(league, p)}" if p else
                     f"no playoff appearance since at least {season(league, playoff_first)}")
    # A postseason team's story is the title round, not its record.
    if league not in NO_WINNING_SEASON and not postseason:
        wnn = (winning or {}).get("last_winning")
        parts.append(f"last winning season: {season(league, wnn)}" if wnn
                     else f"no winning season since at least {season(league, first_year)}")
    return "; ".join(parts) + "."


def h2h_fact(row: dict | None, opponent: str, opp_city: str | None, year: int, first_year: int) -> str | None:
    """The last win over today's opponent, and at its home, when it's been a
    while. Nothing when this season holds their first meeting in the data:
    "no win since at least 2026" would be nonsense."""
    if row is None or row["first_meeting"] >= year:
        return None
    since = max(first_year, row["first_meeting"])
    lw, lrw = row.get("last_win"), row.get("last_road_win")
    out = []
    if row.get("won_today"):
        # Yesterday's win ended whatever drought there was; say which.
        if lw is None:
            out.append(f"beat the {opponent} yesterday, their first win over them since at least {since}")
        elif year - lw.year >= H2H_NOTABLE:
            out.append(f"beat the {opponent} yesterday, their first win over them since {lw.isoformat()}")
        if opp_city:
            if lrw is None:
                out.append(f"won at {opp_city} yesterday, their first win there since at least {since}")
            elif year - lrw.year >= H2H_NOTABLE:
                out.append(f"won at {opp_city} yesterday, their first win there since {lrw.isoformat()}")
        return ("; ".join(out) + ".") if out else None
    if lw is None:
        out.append(f"no win over the {opponent} since at least {since}")
    elif year - lw.year >= H2H_NOTABLE:
        out.append(f"last win over the {opponent}: {lw.isoformat()}")
    if opp_city:
        if lrw is None and lw is not None:
            out.append(f"no win at {opp_city} since at least {since}")
        elif lrw is not None and year - lrw.year >= H2H_NOTABLE:
            out.append(f"last win at {opp_city}: {lrw.isoformat()}")
    return ("; ".join(out) + ".") if out else None


def poll_fact(row: dict | None, year: int) -> str | None:
    if row is None:
        return None
    rank, last_ranked, last_high, first = row["rank"], row["last_ranked"], row["last_this_high"], row["first_year"]
    parts = [f"AP No. {rank} this week"]
    if last_ranked is None:
        parts.append(f"first AP ranking in any season since at least {first} (AP poll starts {first})")
    elif year - last_ranked > 1:
        parts.append(f"first season ranked since {last_ranked}")
    if last_high is None:
        parts.append(f"highest AP ranking since at least {first}")
    elif year - last_high >= NOTABLE_SEASONS:
        parts.append(f"last ranked this high in {last_high}")
    return ", ".join(parts) + "." if len(parts) > 1 else None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _resolve(conn, sport: str, rows: list[dict]) -> dict[str, dict]:
    """ESPN name (and id) -> database team. College football matches on the
    CFBD id, which is ESPN's; the pros on the full name, then a nickname
    that's unique in the league."""
    league = LEAGUES[sport][0]
    if league == "ncaaf":
        ids = sorted({r["espn_id"] for r in rows} | {r["opponent_espn_id"] for r in rows})
        found = {x["external_id"]: x for x in _rows(conn, CFBD_TEAMS, {"ids": ids})}
        out = {}
        for r in rows:
            for name, eid in ((r["name"], r["espn_id"]), (r["opponent"], r["opponent_espn_id"])):
                if eid in found:
                    out[name] = {**found[eid], "city": None}
        return out
    teams = _rows(conn, CURRENT_TEAMS, {"league": league})
    by_full = {norm(t["full_name"]): t for t in teams}
    nick_count: dict[str, int] = {}
    for t in teams:
        nick_count[norm(t["nickname"] or "")] = nick_count.get(norm(t["nickname"] or ""), 0) + 1
    by_nick = {norm(t["nickname"]): t for t in teams if t["nickname"] and nick_count[norm(t["nickname"])] == 1}
    out = {}
    for r in rows:
        for name in (r["name"], r["opponent"]):
            t = by_full.get(norm(name)) or by_nick.get(norm(name).split(" ")[-1])
            if t:
                out[name] = {**t, "city": t["location"]}
    return out


def build_history(conn, game_state: dict) -> dict:
    day = game_state.get("yesterday_date") or game_state.get("as_of_date")
    leagues_out = {}
    for sport, rows in teams_in_play(game_state).items():
        spec = LEAGUES[sport]
        league = spec[0]
        teams = _resolve(conn, sport, rows)
        # A postseason game can come weeks after the last regular-season one
        # (the NBA Finals, two months): look back far enough to find the
        # season it belongs to. Ten days otherwise, so an offseason date
        # never borrows last season.
        any_post = any(r.get("postseason") for r in rows)
        season = _rows(conn, SEASON, {"league": league, "day": day, "days": 250 if any_post else 10})
        if not season:
            continue
        year, first_year = season[0]["year"], season[0]["first_year"]
        franchises = sorted({t["franchise_id"] for t in teams.values()})
        # Facts as of the issue's date, not the database's: a replay of an
        # old issue must not see games played after it.
        p = {"league": league, "franchises": franchises, "year": year, "day": day}
        # Streaks, starts and head-to-head are regular-season facts: a team
        # in the playoffs gets only its droughts (SLA-110), so the costly
        # game table is built only when a regular-season team needs it.
        regular = [r for r in rows if not r.get("postseason")]
        streaks = starts = h2h = []
        if regular:
            reg_franchises = sorted({teams[r["name"]]["franchise_id"] for r in regular if r["name"] in teams}
                                    | {teams[r["opponent"]]["franchise_id"] for r in regular if r["opponent"] in teams})
            conn.execute("DROP TABLE IF EXISTS hist_sides")
            conn.execute(BUILD_SIDES, {**p, "franchises": reg_franchises})
            streaks = _rows(conn, STREAKS, p)
            starts = _rows(conn, STARTS, p)
            pairs = [(teams[r["name"]]["franchise_id"], teams[r["opponent"]]["franchise_id"])
                     for r in regular if r["name"] in teams and r["opponent"] in teams]
            h2h = _rows(conn, HEAD_TO_HEAD, {**p, "pairs_f": [a for a, _ in pairs], "pairs_o": [b for _, b in pairs]})
        title_leagues = [league] + (["afl", "aafc"] if league == "nfl" else [])
        titles = {r["franchise_id"]: r for r in _rows(conn, TITLES, {"leagues": title_leagues,
                                                                    "franchises": franchises, "year": year})}
        playoffs = {r["franchise_id"]: r for r in _rows(conn, PLAYOFFS, p)} if league in PLAYOFF_GAMES else {}
        playoff_first = next(iter(playoffs.values()), {}).get("first_year") if playoffs else None
        if league in PLAYOFF_GAMES and playoff_first is None:
            first_pg = _rows(conn, "SELECT min(s.year) AS y FROM game g JOIN season s USING (season_id) "
                                   "WHERE g.season_type = 'postseason' AND g.league_id = %(league)s", p)
            playoff_first = first_pg[0]["y"] if first_pg else None
        winning = {r["franchise_id"]: r for r in _rows(conn, WINNING, p)}
        polls = {r["franchise_id"]: r for r in _rows(conn, POLLS, p)} if league == "ncaaf" else {}

        facts = []
        unmatched = []
        for r in rows:
            t = teams.get(r["name"])
            if t is None:
                unmatched.append(r["name"])
                continue
            f = t["franchise_id"]
            tf = TeamFacts(sport, r["name"], r["opponent"])
            post = bool(r.get("postseason"))
            if post:
                lines = (drought_fact(spec, titles.get(f), playoffs.get(f), None, year, first_year,
                                      playoff_first, postseason=True),)
            else:
                lines = (
                streak_fact([s for s in streaks if s["franchise_id"] == f], year, first_year, league),
                start_fact([s for s in starts if s["franchise_id"] == f], year, first_year, league),
                poll_fact(polls.get(f), year),
                h2h_fact(next((h for h in h2h if h["franchise_id"] == f
                               and r["opponent"] in teams and h["opp"] == teams[r["opponent"]]["franchise_id"]), None),
                         r["opponent"], None if r["home"] else (teams.get(r["opponent"]) or {}).get("city"),
                         year, first_year),
                drought_fact(spec, titles.get(f), playoffs.get(f), winning.get(f), year, first_year, playoff_first),
            )
            for line in lines:
                if line:
                    tf.lines.append(line)
            facts.append({"team": tf.name, "opponent": tf.opponent, "facts": tf.lines, "postseason": post})
        leagues_out[sport] = {"label": spec[1], "season": year, "game_data_from": first_year,
                              "teams": facts, "unmatched": sorted(set(unmatched))}
    return {"status": "ok", "as_of": day, "source": "slap-sports-db", "leagues": leagues_out}


def fetch_history(game_state: dict, url: str | None = None, connect=_connect) -> dict:
    """The game_state "history" block. Never raises."""
    url = url if url is not None else os.environ.get("SPORTS_DB_URL", "")
    if not url:
        return {"status": "unavailable", "reason": "SPORTS_DB_URL is not set"}
    secrets = _secrets(url)
    try:
        with connect(url) as conn:
            return build_history(conn, game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        return {"status": "unavailable", "reason": _scrub(f"{type(e).__name__}: {e}", secrets)}


# ---------------------------------------------------------------------------
# Teams the day's STORIES name (SLA-111). The block above covers yesterday's
# games; a trade, an off day, a preview or a feature names teams that didn't
# play, and their history claims could only ever be blurred. After Pass 1
# picks the stories, every team they name gets facts too, matched to where
# its league is: in season (streak, start, droughts), or not (droughts only,
# through the last decided season).
# ---------------------------------------------------------------------------

MAX_STORY_TEAMS = 24    # a hard cap on what a busy day can add to every prompt

# Which current AP Top 25 teams: college football is matched only against
# these. 1,290 current college teams include schools named like places
# ("Washington", "Miami"); the ranked ones are the ones stories are about.
CFB_RANKED = """
    SELECT DISTINCT ON (t.team_id) t.team_id, t.franchise_id, t.full_name, t.nickname, t.location
    FROM poll_ranking p JOIN season s USING (season_id) JOIN team t USING (team_id)
    WHERE p.poll = 'AP Top 25' AND s.league_id = 'ncaaf'
      AND s.year = (SELECT max(s2.year) FROM poll_ranking p2 JOIN season s2 USING (season_id)
                    WHERE p2.poll = 'AP Top 25' AND s2.start_date <= %(day)s::date)
    ORDER BY t.team_id"""

DECIDED = """
    SELECT 1 FROM v_league_title WHERE league_id = ANY(%(leagues)s) AND year = %(year)s LIMIT 1"""


def story_text(story_plan: str | dict) -> str:
    """Every string the plan wrote about its stories, minus the tweets it
    quotes (someone else's words) and URLs."""
    try:
        plan = json.loads(story_plan) if isinstance(story_plan, str) else story_plan
    except (TypeError, ValueError):
        return ""
    out: list[str] = []

    def walk(x, key=""):
        if isinstance(x, dict):
            if "url" in x and "account" in x:          # a quoted tweet
                return
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and key not in ("url", "topic_key"):
            out.append(x)
    walk(plan)
    return "\n".join(out)


def named_teams(text: str, teams_by_sport: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """The database teams `text` names, per sport. A full name, a school
    (college), or a nickname no other team in any league shares, capitalised
    as a name: "the Knicks", not "giants of the game", and never "Giants"
    alone, which is two teams."""
    nick_count: dict[str, int] = {}
    for rows in teams_by_sport.values():
        for t in rows:
            if t.get("nickname"):
                nick_count[t["nickname"]] = nick_count.get(t["nickname"], 0) + 1

    pro_cities = {t.get("location") for sport, rows in teams_by_sport.items() if sport != "ncaafb" for t in rows}

    def says(name: str, prefix: str = "") -> bool:
        # A school is not the start of another school ("Georgia Tech").
        return bool(name) and re.search(
            prefix + r"(?<![\w-])" + re.escape(name) + r"(?![\w-])(?!\s+(?:State|Tech|A&M|Southern|St\.?)(?!\w))",
            text) is not None

    out: dict[str, list[dict]] = {}
    for sport, rows in teams_by_sport.items():
        for t in rows:
            names = [t["full_name"]]
            if sport == "ncaafb":
                school = t.get("location") or ""
                # "Miami" is also the Dolphins, Heat and Marlins: a school
                # sharing a pro city counts only with its ranking in front.
                if school in pro_cities:
                    if says(school, prefix=r"No\.\s?\d{1,2}\s+"):
                        out.setdefault(sport, []).append(t)
                        continue
                else:
                    names.append(school)
            elif t.get("nickname") and nick_count.get(t["nickname"]) == 1:
                names.append(t["nickname"])
            if any(says(n) for n in names):
                out.setdefault(sport, []).append(t)
    return out


def build_story_facts(conn, game_state: dict, text: str) -> int:
    """Add facts for the teams `text` names to game_state["history"] in
    place. Returns how many teams were added."""
    block = game_state.get("history") or {}
    day = block.get("as_of") or game_state.get("yesterday_date")
    have = {norm(t["team"]) for e in (block.get("leagues") or {}).values() for t in e.get("teams") or []}
    pool: dict[str, list[dict]] = {}
    for sport, spec in LEAGUES.items():
        rows = (_rows(conn, CFB_RANKED, {"day": day}) if sport == "ncaafb"
                else _rows(conn, CURRENT_TEAMS, {"league": spec[0]}))
        pool[sport] = [t for t in rows if norm(t["full_name"]) not in have]
    picked = named_teams(text, pool)
    added = 0
    for sport, teams in picked.items():
        teams = teams[:max(0, MAX_STORY_TEAMS - added)]
        if not teams:
            continue
        spec = LEAGUES[sport]
        league = spec[0]
        in_season = _rows(conn, SEASON, {"league": league, "day": day, "days": 10})
        srow = in_season or _rows(conn, SEASON, {"league": league, "day": day, "days": 400})
        if not srow:
            continue
        year, first_year = srow[0]["year"], srow[0]["first_year"]
        title_leagues = [league] + (["afl", "aafc"] if league == "nfl" else [])
        # Out of season, the last season is over: its champion (once decided)
        # and its record count. In season, only seasons before this one do.
        # The calendar must agree too (champions_source's rule: stale means
        # unknown). The database row alone isn't enough: a replay of June 1
        # must not see the Knicks' title won June 13, and a live run must not
        # either if a row ever lands early.
        through = year
        if (not in_season and expected_latest_season(league, date.fromisoformat(day)) >= year
                and _rows(conn, DECIDED, {"leagues": title_leagues, "year": year})):
            through = year + 1
        franchises = sorted({t["franchise_id"] for t in teams})
        p = {"league": league, "franchises": franchises, "year": through, "day": day}
        titles = {r["franchise_id"]: r for r in _rows(conn, TITLES, {"leagues": title_leagues,
                                                                    "franchises": franchises, "year": through})}
        playoffs = {r["franchise_id"]: r for r in _rows(conn, PLAYOFFS, p)} if league in PLAYOFF_GAMES else {}
        playoff_first = next(iter(playoffs.values()), {}).get("first_year") if playoffs else None
        winning = {r["franchise_id"]: r for r in _rows(conn, WINNING, {**p, "year": year if in_season else year + 1})}
        streaks = starts = []
        polls = {}
        if in_season:
            conn.execute("DROP TABLE IF EXISTS hist_sides")
            conn.execute(BUILD_SIDES, {**p, "year": year})
            streaks = _rows(conn, STREAKS, {**p, "year": year})
            starts = _rows(conn, STARTS, {**p, "year": year})
            if league == "ncaaf":
                polls = {r["franchise_id"]: r for r in _rows(conn, POLLS, {**p, "year": year})}
        entry = block.setdefault("leagues", {}).setdefault(
            sport, {"label": spec[1], "season": year, "game_data_from": first_year, "teams": [], "unmatched": []})
        for t in teams:
            f = t["franchise_id"]
            lines = [
                streak_fact([s for s in streaks if s["franchise_id"] == f], year, first_year, league),
                start_fact([s for s in starts if s["franchise_id"] == f], year, first_year, league),
                poll_fact(polls.get(f), year),
                drought_fact(spec, titles.get(f), playoffs.get(f), winning.get(f), through, first_year, playoff_first),
            ]
            entry["teams"].append({"team": t["full_name"], "opponent": None, "facts": [x for x in lines if x],
                                   "postseason": False, "story": True,
                                   # Out of season with this season's title not yet on the
                                   # calendar: say where the facts stop, so a team that just
                                   # won isn't read as still waiting.
                                   "before": season(league, year) if (not in_season and through == year) else None})
            added += 1
    return added


def extend_for_stories(game_state: dict, story_plan, url: str | None = None, connect=_connect) -> str:
    """Called by both runners after Pass 1. Adds facts for the teams the
    stories name to game_state["history"], in place, and says what it did in
    one line for the log. Never raises; with no database it does nothing."""
    block = (game_state or {}).get("history") or {}
    if block.get("status") != "ok":
        return "history: no database today, story teams skipped"
    url = url if url is not None else os.environ.get("SPORTS_DB_URL", "")
    if not url:
        return "history: SPORTS_DB_URL is not set, story teams skipped"
    text = story_text(story_plan)
    if not text:
        return "history: no story text, story teams skipped"
    secrets = _secrets(url)
    before = len("\n".join(summary_lines(game_state)))
    try:
        with connect(url) as conn:
            n = build_story_facts(conn, game_state, text)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        return "history: story teams skipped: " + _scrub(f"{type(e).__name__}: {e}", secrets)
    after = len("\n".join(summary_lines(game_state)))
    return f"history: +{n} team(s) named in today's stories; block {before:,} -> {after:,} chars"


# ---------------------------------------------------------------------------
# Reading the sentences back (SLA-109). Pass 3 checks the draft's history
# claims against these facts, so it needs each one as data: its kind, the
# season(s) it names, or the floor it can't see past. Parsed from the lines
# rather than stored beside them, so a game_state.json written before this
# existed is still checkable; it lives HERE, next to the templates, so a
# change to a sentence and a change to its reader land in the same file
# (test_history_claims.py round-trips every template).
# ---------------------------------------------------------------------------

_TITLE_GAMES = {spec[3] for spec in LEAGUES.values() if spec[3]}


def season_years(label: str) -> set[int]:
    """'1972-73' -> {1972, 1973}; '2018-09-16' -> {2018}; '2015' -> {2015}."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-\d{2})?", label.strip())
    if not m:
        return set()
    y = int(m.group(1))
    if m.group(2) and len(label.strip()) == 7:        # a season, not a date
        return {y, y + 1}
    return {y}


def _fact(kind: str, text: str, last: str | None = None, floor: str | None = None,
          never: bool = False, **extra) -> dict:
    return {"kind": kind, "text": text.strip().rstrip("."),
            "last": sorted(season_years(last)) if last else None,
            "floor": min(season_years(floor)) if floor else None, "never": never, **extra}


_FLOOR = r"(?:since at least (\S+) \(game data starts \S+\)|in franchise history \(since (\S+)\))"


def parse_facts(line: str) -> list[dict]:
    """Every checkable fact in one HISTORICAL CONTEXT line."""
    out = []
    m = re.fullmatch(r"(\d+) straight (wins|losses) this season; last \d+\+ within a season: (\S+)\.", line)
    if m:
        return [_fact("streak_" + m.group(2)[0].upper(), line, last=m.group(3), length=int(m.group(1)))]
    m = re.fullmatch(r"(\d+) straight (wins|losses) this season, their longest within a season " + _FLOOR + r"\.", line)
    if m:
        return [_fact("streak_" + m.group(2)[0].upper(), line, floor=m.group(3) or m.group(4),
                      length=int(m.group(1)))]
    m = re.fullmatch(r"(\S+) after (\d+) games; last start this (good|bad) or (?:better|worse): (\S+)\.", line)
    if m:
        return [_fact("start_" + ("best" if m.group(3) == "good" else "worst"), line,
                      last=m.group(4), record=m.group(1))]
    m = re.fullmatch(r"(\S+) after (\d+) games, their (best|worst) start " + _FLOOR + r"\.", line)
    if m:
        return [_fact("start_" + m.group(3), line, floor=m.group(4) or m.group(5), record=m.group(1))]
    if line.startswith("AP No. "):
        for part in line.rstrip(".").split(", ")[1:]:
            if (m := re.fullmatch(r"first AP ranking in any season since at least (\d+) .*", part)):
                out.append(_fact("ranked", part, floor=m.group(1)))
            elif (m := re.fullmatch(r"first season ranked since (\d+)", part)):
                out.append(_fact("ranked", part, last=m.group(1)))
            elif (m := re.fullmatch(r"highest AP ranking since at least (\d+)", part)):
                out.append(_fact("ranked_high", part, floor=m.group(1)))
            elif (m := re.fullmatch(r"last ranked this high in (\d+)", part)):
                out.append(_fact("ranked_high", part, last=m.group(1)))
        return out
    for part in line.rstrip(".").split("; "):
        if (m := re.fullmatch(r"beat the (.+) yesterday, their first win over them since at least (\d+)", part)):
            out.append(_fact("h2h", part, floor=m.group(2), opponent=m.group(1)))
        elif (m := re.fullmatch(r"beat the (.+) yesterday, their first win over them since (\S+)", part)):
            out.append(_fact("h2h", part, last=m.group(2), opponent=m.group(1)))
        elif (m := re.fullmatch(r"won at (.+) yesterday, their first win there since at least (\d+)", part)):
            out.append(_fact("h2h_at", part, floor=m.group(2), city=m.group(1)))
        elif (m := re.fullmatch(r"won at (.+) yesterday, their first win there since (\S+)", part)):
            out.append(_fact("h2h_at", part, last=m.group(2), city=m.group(1)))
        elif (m := re.fullmatch(r"no win over the (.+) since at least (\d+)", part)):
            out.append(_fact("h2h", part, floor=m.group(2), opponent=m.group(1)))
        elif (m := re.fullmatch(r"last win over the (.+): (\S+)", part)):
            out.append(_fact("h2h", part, last=m.group(2), opponent=m.group(1)))
        elif (m := re.fullmatch(r"no win at (.+) since at least (\d+)", part)):
            out.append(_fact("h2h_at", part, floor=m.group(2), city=m.group(1)))
        elif (m := re.fullmatch(r"last win at (.+): (\S+)", part)):
            out.append(_fact("h2h_at", part, last=m.group(2), city=m.group(1)))
        elif (m := re.fullmatch(r"last playoff appearance: (\S+)", part)):
            out.append(_fact("playoff", part, last=m.group(1)))
        elif (m := re.fullmatch(r"no playoff appearance since at least (\S+)", part)):
            out.append(_fact("playoff", part, floor=m.group(1)))
        elif (m := re.fullmatch(r"last winning season: (\S+)", part)):
            out.append(_fact("winning", part, last=m.group(1)))
        elif (m := re.fullmatch(r"no winning season since at least (\S+)", part)):
            out.append(_fact("winning", part, floor=m.group(1)))
        elif (m := re.fullmatch(r"last (.+) appearance: (\S+)", part)) and m.group(1) in _TITLE_GAMES:
            out.append(_fact("title_game", part, last=m.group(2)))
        elif (m := re.fullmatch(r"no (.+) appearance in franchise history", part)) and m.group(1) in _TITLE_GAMES:
            out.append(_fact("title_game", part, never=True))
        elif (m := re.fullmatch(r"no (.+) in franchise history", part)):
            out.append(_fact("title", part, never=True))
        elif (m := re.fullmatch(r"last (.+): (\S+)", part)):
            out.append(_fact("title", part, last=m.group(2)))
    return out


def team_facts(game_state: dict) -> list[dict]:
    """Every team in the block with its parsed facts:
    [{"team", "opponent", "sport", "season", "facts": [...]}]."""
    block = (game_state or {}).get("history") or {}
    if block.get("status") != "ok":
        return []
    out = []
    for sport, entry in (block.get("leagues") or {}).items():
        for t in entry.get("teams") or []:
            facts = [f for line in t.get("facts") or [] for f in parse_facts(line)]
            out.append({"team": t["team"], "opponent": t.get("opponent"), "sport": sport,
                        "season": entry.get("season"), "facts": facts})
    return out


def summary_lines(game_state: dict, skip: set[tuple[str, str]] = frozenset()) -> list[str]:
    """The HISTORICAL CONTEXT part of the GROUND TRUTH block. `skip` holds
    (sport, team) pairs whose facts the FOOTBALL block already shows verbatim
    (SLA-119); they are left out here so nothing is said twice. team_facts()
    is unaffected: Pass 3 checks against the data, not this text."""
    block = (game_state or {}).get("history") or {}
    if block.get("status") != "ok" or not block.get("leagues"):
        return []
    if skip:
        block = dict(block, leagues={
            sport: dict(entry, teams=[t for t in entry["teams"] if (sport, t["team"]) not in skip])
            for sport, entry in block["leagues"].items()})
        block["leagues"] = {s: e for s, e in block["leagues"].items() if any(t["facts"] for t in e["teams"])}
        if not block["leagues"]:
            return []
    lines = ["## GROUND TRUTH: HISTORICAL CONTEXT",
             f"Source: SLAP sports database, computed from every stored game, title and poll, as of "
             f"{block.get('as_of')}. Streaks, starts and head-to-head count the games each league's heading names; NBA and NHL years are seasons (2025-26). These are SOURCED: you may "
             "state any of them as written, keeping \"since at least\" where it appears (it means the "
             "data starts there, not that it happened then). A history claim not listed here is not "
             "sourced; RULE 3 applies to it.",
             ""]
    for sport, entry in block["leagues"].items():
        scope = SCOPE.get(sport, "regular season")
        lines.append(f"{entry['label']} ({scope}; game data from {entry['game_data_from']}):")
        for t in entry["teams"]:
            if t["facts"]:
                # A playoff team's droughts are as of BEFORE this postseason:
                # "last NBA Finals appearance: 1998-99" for a team now in the
                # Finals is what "first Finals since 1999" is checked against.
                tag = (" (in the postseason; titles and appearances are before this one)" if t.get("postseason")
                       else f" (named in today's stories; titles and appearances before the {t['before']} postseason)"
                       if t.get("story") and t.get("before")
                       else " (named in today's stories)" if t.get("story") else "")
                lines.append(f"  {t['team']}{tag}: " + " ".join(t["facts"]))
        lines.append("")
    return lines
