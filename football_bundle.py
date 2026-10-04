"""
SLAP Newsletter — one connected fact bundle per football game (SLA-116)

history_source.py gives each team a list of separate facts. A football
write-up needs them connected: "the unranked Hoosiers beat No. 8 Ohio State,
their first win over an AP top-10 team since 2019". This builds, for every
completed NFL and college football game yesterday, one bundle:

- the result, and each team's record and AP rank GOING IN;
- standings after it (NFL division place and games back, from
  nfl_standings.py; college conference record, from the stored games);
- the series between the two (regular season and postseason kept apart), the
  last meeting before this one, and, for an upset, the winner's last win over
  a team ranked that high (and the loser's last loss to an unranked team);
- NFL playoff games: each team's last playoff win before this one;
- the team's history_source facts, folded in verbatim.

fetch_sports_data.py stores the bundles in game_state.json under "football".
`summary_lines()` renders them in priority order under a hard character cap:
full bundles for the games the day's stories cover, then ranked matchups (and
every NFL game), then one result line for the rest. SLA-119 puts that in front
of every pass, through runner_common.format_game_state_summary.

The same rules as history_source.py, plus one:

- **Sources.** NFL records and standings this season come from ESPN's season
  log (the box score standings use the same one). College records, the
  series, ranks and upsets come from slap-sports-db, through the SLA-115
  views (v_team_game, v_poll_asof).
- **Stale = unknown.** College games reach the database about once a day
  (the CFBD ration), so a late Saturday game can be missing at run time. A
  team whose game is not in the database yet gets nothing this season's
  database rows would have supplied (record, conference record, upset
  facts), and its series counts stop at last season. Never guessed.
  Unless the database is otherwise caught up (SLA-120): when each team's
  stored record before the game equals ESPN's record going in (ESPN's
  record after a final, minus the result), every earlier game is stored, so
  records going in, the series and upset facts are computed from games
  before the day as usual; the conference record after is ESPN's.
- **Depth is stated.** Counts cover stored games; each line says where the
  data starts. College counts are regular season (bowls are not stored).
- **As of the issue's date.** Nothing on or after the game's own date counts
  as history.
- **Fail soft.** No SPORTS_DB_URL or no database: status "unavailable", no
  bundles, the run goes on.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from champions_source import _connect, _scrub, _secrets
from history_source import (_rows, espn_record_before, game_result, named_teams, norm, parse_record,
                            story_text)

# game_state sport key -> (slap-sports-db league, label)
SPORTS = {"nfl": ("nfl", "NFL"), "ncaafb": ("ncaaf", "College football")}
BUDGET = 10_000           # characters for the whole football block (Abram, 2026-10-03)
AP = "AP Top 25"
CFP = "Playoff Committee Rankings"
UPSET_BUCKETS = (5, 10, 25)   # "a top-5 team", "a top-10 team", "a ranked team"

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - no tzdata: a fixed offset is right for the season
    _ET = timezone(timedelta(hours=-4))


def et_date(iso: str | None) -> date | None:
    """ESPN's UTC kickoff -> the Eastern date the game was played on: a
    Thursday 8:15 PM game is "2026-09-18T00:15Z"."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(_ET).date() if dt.tzinfo else dt.date()


def games_in_play(game_state: dict) -> dict[str, list[dict]]:
    """Yesterday's completed NFL and college games, per sport."""
    out = {}
    for sport in SPORTS:
        games = ((game_state.get("sports") or {}).get(sport) or {}).get("yesterday_games") or []
        done = [g for g in games if g.get("completed")]
        if done:
            out[sport] = done
    return out


# ---------------------------------------------------------------------------
# SQL. Every query names a league and season, or a list of franchises, so the
# views read only those games (see slap-sports-db docs/schema.md, SLA-115).
# ---------------------------------------------------------------------------

# Every current pro team, stored with the bundles so story matching (at
# render time, with no database) knows which school names are also pro
# cities and which nicknames two leagues share (history_source.named_teams).
NAME_POOL = """
    SELECT t.league_id, t.full_name, t.nickname, t.location
    FROM team t WHERE t.league_id IN ('nfl', 'mlb', 'nba', 'nhl') AND t.last_season IS NULL"""

NFL_TEAMS = """
    SELECT t.team_id, t.franchise_id, t.full_name, t.nickname, t.location, t.abbrev
    FROM team t WHERE t.league_id = 'nfl' AND t.last_season IS NULL"""

CFB_TEAMS = """
    SELECT x.external_id, t.team_id, t.franchise_id, t.full_name, t.nickname, t.location
    FROM team_xref x JOIN team t USING (team_id)
    WHERE x.source_id = 'cfbd' AND x.external_id = ANY(%(ids)s)"""

FIRST_YEARS = """
    SELECT (SELECT min(s.year) FROM game g JOIN season s USING (season_id)
            WHERE g.league_id = %(league)s AND g.season_type = 'regular') AS regular,
           (SELECT min(s.year) FROM game g JOIN season s USING (season_id)
            WHERE g.league_id = ANY(%(leagues)s) AND g.season_type = 'postseason') AS postseason"""

# This season's games for the teams in play, through the game day: records
# going in come from the row ON the day (wins_before ...), and the row's
# presence is what vouches that the database has caught up.
SEASON_GAMES = """
    SELECT tg.team_id, tg.franchise_id, tg.opp_team_id, tg.game_date, tg.season_type, tg.result,
           tg.venue_side, tg.score_for, tg.score_against, tg.wins_before, tg.losses_before, tg.ties_before,
           ts.group_id AS group_id, coalesce(lg.parent_group_id, lg.group_id) AS conference_id,
           coalesce(plg.name, lg.name) AS conference,
           coalesce(olg.parent_group_id, olg.group_id) AS opp_conference_id
    FROM v_team_game tg
    JOIN team_season ts ON ts.team_id = tg.team_id AND ts.season_id = tg.season_id
    LEFT JOIN league_group lg ON lg.group_id = ts.group_id
    LEFT JOIN league_group plg ON plg.group_id = lg.parent_group_id
    LEFT JOIN team_season ots ON ots.team_id = tg.opp_team_id AND ots.season_id = tg.season_id
    LEFT JOIN league_group olg ON olg.group_id = ots.group_id
    WHERE tg.league_id = %(league)s AND tg.year = %(year)s
      AND tg.franchise_id = ANY(%(franchises)s) AND tg.game_date <= %(day)s::date"""

# The series between each pair, before the game. LATERAL per pair: the
# franchise filter is then pushed below v_team_game's window (2.3 s for 80
# pairs on live), where a join on a pair list would compute every record.
SERIES = """
    SELECT p.f AS franchise_id, p.o AS opp_franchise_id, x.*, lm.*
    FROM unnest(%(pf)s::bigint[], %(po)s::bigint[]) AS p(f, o)
    CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE season_type = 'regular' AND result = 'W') AS reg_w,
               count(*) FILTER (WHERE season_type = 'regular' AND result IN ('L', 'OTL')) AS reg_l,
               count(*) FILTER (WHERE season_type = 'regular' AND result = 'T') AS reg_t,
               count(*) FILTER (WHERE season_type = 'postseason' AND result = 'W') AS post_w,
               count(*) FILTER (WHERE season_type = 'postseason' AND result IN ('L', 'OTL')) AS post_l,
               min(game_date) AS first_meeting
        FROM v_team_game tg
        WHERE tg.franchise_id = p.f AND tg.opp_franchise_id = p.o AND tg.game_date < %(day)s::date
          AND tg.year <= %(max_year)s) x
    LEFT JOIN LATERAL (
        SELECT tg.game_date AS last_date, tg.result AS last_result, tg.score_for AS last_for,
               tg.score_against AS last_against, tg.venue_side AS last_side, tg.season_type AS last_type
        FROM v_team_game tg
        WHERE tg.franchise_id = p.f AND tg.opp_franchise_id = p.o AND tg.game_date < %(day)s::date
          AND tg.year <= %(max_year)s
        ORDER BY tg.game_date DESC LIMIT 1) lm ON true"""

# Every AP and CFP ranking with the dates it was current. Fetched once and
# looked up in Python: joining it to a team's games in SQL took 56 s for 20
# teams on live.
POLL_RANGES = """
    SELECT poll, year, team_id, franchise_id, valid_from, valid_to, rank
    FROM v_poll_asof
    WHERE league_id = 'ncaaf' AND poll IN ('AP Top 25', 'Playoff Committee Rankings')
      AND valid_from IS NOT NULL AND valid_from <= %(day)s::date"""

# Regular-season results of the teams an upset involves, every season: the
# raw material for "first win over a top-10 team since".
UPSET_GAMES = """
    SELECT franchise_id, year, game_date, opp_team_id, result
    FROM v_team_game
    WHERE franchise_id = ANY(%(franchises)s) AND league_id = 'ncaaf' AND season_type = 'regular'
      AND game_date < %(day)s::date AND year <= %(max_year)s"""

# NFL playoff wins, every league the franchise played in (AFL wins count:
# the Bills' 1964 and 1965 titles were AFL titles).
PLAYOFF_WINS = """
    SELECT franchise_id, year
    FROM v_team_game
    WHERE franchise_id = ANY(%(franchises)s) AND season_type = 'postseason' AND result = 'W'
      AND game_date < %(day)s::date AND year <= %(max_year)s
    GROUP BY franchise_id, year"""


# ---------------------------------------------------------------------------
# Pure helpers: rows in, sentences out. Tested without a database.
# ---------------------------------------------------------------------------

def record(w: int, l: int, t: int = 0) -> str:
    return f"{w}-{l}" + (f"-{t}" if t else "")


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def short(name: str, school: str | None = None) -> str:
    """College teams by school ("Georgia"), pros by full name."""
    return school or name


class Polls:
    """AP / CFP rankings by date, and which dates the AP poll covers at all."""

    def __init__(self, rows: list[dict]):
        self.by_team: dict[tuple[str, int], list[dict]] = defaultdict(list)
        self.covered: dict[int, list[tuple[date, date]]] = defaultdict(list)
        seen = set()
        for r in rows:
            self.by_team[(r["poll"], r["team_id"])].append(r)
            if r["poll"] == AP and (r["year"], r["valid_from"], r["valid_to"]) not in seen:
                seen.add((r["year"], r["valid_from"], r["valid_to"]))
                self.covered[r["year"]].append((r["valid_from"], r["valid_to"]))
        self.first_year = min((r["year"] for r in rows if r["poll"] == AP), default=None)

    def rank(self, poll: str, team_id: int, day: date) -> int | None:
        for r in self.by_team.get((poll, team_id), ()):
            if r["valid_from"] <= day <= r["valid_to"]:
                return r["rank"]
        return None

    def is_covered(self, year: int, day: date) -> bool:
        """Was there a current AP poll on this date? If not, nobody's rank
        that day is known, ranked or unranked."""
        return any(a <= day <= b for a, b in self.covered.get(year, ()))


def bucket_for(rank: int) -> int:
    return next(b for b in UPSET_BUCKETS if rank <= b)


def bucket_words(b: int) -> str:
    return "an AP-ranked team" if b == 25 else f"an AP top-{b} team"


def last_win_over_ranked(games: list[dict], polls: Polls, bucket: int) -> tuple[str, dict | None]:
    """The most recent regular-season win over a team ranked `bucket` or
    better going in. ('ok', game) / ('never', None) / ('unknown', None): a
    game after the answer that fell in an AP gap could have been such a win,
    so the answer can't be vouched for."""
    wins = sorted((g for g in games if g["result"] == "W"), key=lambda g: g["game_date"], reverse=True)
    for g in wins:
        if polls.first_year and g["year"] < polls.first_year:
            break
        if not polls.is_covered(g["year"], g["game_date"]):
            return "unknown", None
        r = polls.rank(AP, g["opp_team_id"], g["game_date"])
        if r is not None and r <= bucket:
            g = dict(g, opp_rank=r)
            return "ok", g
    return "never", None


def last_loss_to_unranked(games: list[dict], polls: Polls) -> tuple[str, dict | None]:
    losses = sorted((g for g in games if g["result"] in ("L", "OTL")), key=lambda g: g["game_date"], reverse=True)
    for g in losses:
        if polls.first_year and g["year"] < polls.first_year:
            break
        if not polls.is_covered(g["year"], g["game_date"]):
            return "unknown", None
        if polls.rank(AP, g["opp_team_id"], g["game_date"]) is None:
            return "ok", g
    return "never", None


def rank_phrase(rank: int | None, cfp: int | None = None, espn: int | None = None) -> str:
    if rank is not None:
        return f"AP No. {rank}" + (f", CFP No. {cfp}" if cfp else "")
    if espn is not None:
        return f"No. {espn} (ESPN's rank; not in the database's AP poll)"
    return "unranked in the AP poll"


def _lead(a: str, b: str, w: int, l: int, t: int = 0) -> str:
    if w == l:
        return f"tied {record(w, l, t)}"
    return f"{a} {record(w, l, t)}" if w > l else f"{b} {record(l, w, t)}"


def series_line(row: dict | None, a: str, b: str, *, regular_from: int | None,
                post_from: int | None, through: str, college: bool) -> str | None:
    """Head-to-head before this game, from `a`'s side. Each count says where
    its data starts; college counts say bowls are not in them."""
    if regular_from is None:
        return None
    reg_scope = f"regular season since at least {regular_from}" + ("; bowls not stored" if college else "")
    stop = f"; {through}" if through else ""
    if row is None or not row.get("first_meeting"):
        return f"Series: no earlier meeting in the stored games ({reg_scope}{stop})."
    parts = []
    if row["reg_w"] + row["reg_l"] + row["reg_t"]:
        parts.append(f"{_lead(a, b, row['reg_w'], row['reg_l'], row['reg_t'])} ({reg_scope}{stop})")
    else:
        parts.append(f"no regular-season meeting ({reg_scope}{stop})")
    if row["post_w"] + row["post_l"]:
        parts.append(f"playoffs {_lead(a, b, row['post_w'], row['post_l'])} (since at least {post_from})")
    out = "Series: " + "; ".join(parts) + "."
    if row.get("last_date"):
        res = {"W": f"{a} won", "T": "tied", "L": f"{b} won", "OTL": f"{b} won"}[row["last_result"]]
        where = {"home": f"at {a}", "away": f"at {b}", "neutral": "neutral site"}[row["last_side"]]
        hi, lo = max(row["last_for"], row["last_against"]), min(row["last_for"], row["last_against"])
        kind = " (playoffs)" if row.get("last_type") == "postseason" else ""
        out += f" Last meeting before this: {row['last_date'].isoformat()}{kind}, {res} {hi}-{lo}, {where}."
    return out


def nfl_standings_after(season_games: list[dict], abbr: str, upto: date) -> str | None:
    """Division place and games back after the game day, from ESPN's log."""
    import nfl_standings
    games = [g for g in season_games if (et_date(g.get("date")) or date.max) <= upto]
    try:
        st = nfl_standings.build_standings(games)
    except Exception:  # noqa: BLE001 - standings are a nicety; never fatal
        return None
    for conf in st["conferences"]:
        for div in conf["divisions"]:
            abbrs = [t["abbr"] for t in div["teams"]]
            if abbr not in abbrs:
                continue
            i = abbrs.index(abbr)
            me, lead = div["teams"][i], div["teams"][0]
            gb = ((lead["wins"] - me["wins"]) + (me["losses"] - lead["losses"])) / 2
            same = [t for t in div["teams"] if t is not me and abs(t["win_pct"] - me["win_pct"]) < 1e-9]
            place = f"{ordinal(i + 1)} in the {div['label']}"
            if same:
                why = me.get("tiebreak") or ""
                place += f" (tied on record; order by tiebreaker{': ' + why if why else ''})"
            back = "" if gb <= 0 else f", {gb:g} game{'s' if gb != 1 else ''} back"
            return f"{me['record']}, {place}{back}"
    return None


def nfl_record_before(season_games: list[dict], abbr: str, day: date, year: int) -> tuple[int, int, int] | None:
    w = l = t = 0
    seen = False
    for g in season_games:
        gd = et_date(g.get("date"))
        if (not g.get("completed") or gd is None or gd >= day or g.get("season_year") not in (year, None)
                or int(g.get("season_type") or 2) != 2):
            continue
        if abbr not in (g.get("home_abbr"), g.get("away_abbr")):
            continue
        seen = True
        mine = g["home_score"] if g.get("home_abbr") == abbr else g["away_score"]
        theirs = g["away_score"] if g.get("home_abbr") == abbr else g["home_score"]
        w, l, t = w + (mine > theirs), l + (mine < theirs), t + (mine == theirs)
    return (w, l, t) if seen or season_games else None


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _team_lookup(conn, sport: str, games: list[dict]) -> dict[str, dict]:
    """ESPN team name -> database team (with nickname/location for story
    matching). College by CFBD id (= ESPN id); NFL by abbreviation, then name."""
    if sport == "ncaafb":
        ids = sorted({str(g.get(f"{s}_id") or "") for g in games for s in ("home", "away")} - {""})
        found = {r["external_id"]: r for r in _rows(conn, CFB_TEAMS, {"ids": ids})}
        return {g[f"{s}_team"]: found[str(g.get(f"{s}_id"))] for g in games for s in ("home", "away")
                if str(g.get(f"{s}_id")) in found}
    teams = _rows(conn, NFL_TEAMS, {})
    by_name = {norm(t["full_name"]): t for t in teams}
    out = {}
    for g in games:
        for s in ("home", "away"):
            t = by_name.get(norm(g[f"{s}_team"]))
            if t:
                out[g[f"{s}_team"]] = t
    return out


def _history_lines(game_state: dict) -> dict[tuple[str, str], list[str]]:
    block = game_state.get("history") or {}
    if block.get("status") != "ok":
        return {}
    return {(sport, t["team"]): t.get("facts") or []
            for sport, e in (block.get("leagues") or {}).items() for t in e.get("teams") or []}


def build_bundles(conn, game_state: dict) -> dict:
    day_s = game_state.get("yesterday_date") or game_state.get("as_of_date")
    history = _history_lines(game_state)
    polls: Polls | None = None
    out_sports = {}
    for sport, games in games_in_play(game_state).items():
        league, label = SPORTS[sport]
        college = sport == "ncaafb"
        teams = _team_lookup(conn, sport, games)
        fy = _rows(conn, FIRST_YEARS, {"league": league,
                                       "leagues": [league] + (["afl", "aafc"] if league == "nfl" else [])})[0]
        if college and polls is None:
            polls = Polls(_rows(conn, POLL_RANGES, {"day": day_s}))
        franchises = sorted({t["franchise_id"] for t in teams.values()})
        years = sorted({int(g.get("season_year") or date.fromisoformat(day_s).year) for g in games})
        season_rows: dict[int, list[dict]] = defaultdict(list)
        for year in years:
            for r in _rows(conn, SEASON_GAMES, {"league": league, "year": year, "franchises": franchises,
                                                "day": day_s}):
                season_rows[r["team_id"]].append(r)
        sides = [_sides(g, sport, teams, season_rows, polls, day_s) for g in games]

        # One query per kind for the whole slate, not one per game: a
        # Saturday has 80 college games and every query crosses the pooler.
        series: dict[tuple, dict] = {}
        for key in sorted({(x["max_year"], x["day"]) for x in sides if x["pair"]}):
            pairs = [x["pair"] for x in sides if x["pair"] and (x["max_year"], x["day"]) == key]
            for r in _rows(conn, SERIES, {"pf": [p[0] for p in pairs], "po": [p[1] for p in pairs],
                                          "day": key[1].isoformat(), "max_year": key[0]}):
                series[(r["franchise_id"], r["opp_franchise_id"], key[1])] = r
        upset_f = sorted({f for x in sides for f in x["upset_franchises"]})
        upset_rows: dict[int, list[dict]] = defaultdict(list)
        if upset_f:
            for r in _rows(conn, UPSET_GAMES, {"franchises": upset_f, "day": day_s, "max_year": max(years)}):
                upset_rows[r["franchise_id"]].append(r)
        post_f = sorted({x[s]["db"]["franchise_id"] for x in sides if x["post"] and not college
                         for s in ("first", "second") if x[s]["db"]})
        playoff_wins: dict[int, list[int]] = defaultdict(list)
        if post_f:
            for r in _rows(conn, PLAYOFF_WINS, {"franchises": post_f, "day": day_s, "max_year": max(years)}):
                playoff_wins[r["franchise_id"]].append(r["year"])

        bundles = [_bundle(x, sport, fy, polls, series, upset_rows, playoff_wins, history, game_state)
                   for x in sides]
        out_sports[sport] = {"label": label, "games": bundles,
                             "unmatched": sorted({g[f"{s}_team"] for g in games for s in ("home", "away")}
                                                 - set(teams))}
    return {"status": "ok", "as_of": day_s, "source": "slap-sports-db + ESPN", "sports": out_sports,
            "name_pool": [{k: r[k] for k in ("league_id", "full_name", "nickname", "location")}
                          for r in _rows(conn, NAME_POOL, {})] if out_sports else []}


def _sides(g: dict, sport: str, teams: dict, season_rows: dict, polls: "Polls | None", day_s: str) -> dict:
    """Everything about one game that the batched queries need to know."""
    college = sport == "ncaafb"
    gday = et_date(g.get("date")) or date.fromisoformat(day_s)
    year = int(g.get("season_year") or gday.year)
    side = {}
    for s in ("home", "away"):
        db = teams.get(g[f"{s}_team"])
        rows = season_rows.get(db["team_id"], []) if db else []
        today = next((r for r in rows if r["game_date"] == gday), None)
        tid = (db or {}).get("team_id")
        side[s] = {"name": g[f"{s}_team"], "score": g.get(f"{s}_score"), "espn_rank": g.get(f"{s}_rank"),
                   "records": g.get(f"{s}_records") or {},
                   "abbr": g.get(f"{s}_abbr"), "db": db, "rows": rows, "today": today,
                   "short": short(g[f"{s}_team"], (db or {}).get("location") if college else None),
                   "ap": polls.rank(AP, tid, gday) if (college and polls and tid) else None,
                   "cfp": polls.rank(CFP, tid, gday) if (college and polls and tid) else None}
    hs, as_ = g.get("home_score") or 0, g.get("away_score") or 0
    winner = "home" if hs > as_ else "away" if as_ > hs else None
    first, second = (side["away"], side["home"]) if winner == "away" else (side["home"], side["away"])
    post = bool(g.get("playoffs")) or int(g.get("season_type") or 2) == 3
    # Stale = unknown: both teams' game must be in the database before this
    # season's database rows count for either...
    vouched = bool(first["db"] and second["db"] and first["today"] and second["today"])
    # ...or, when it isn't stored yet (college games reach the database a day
    # late, so every Saturday game on a Sunday morning: SLA-120), every
    # EARLIER game must be: the stored record before this game has to equal
    # ESPN's record going in. Then everything dated before the game is
    # complete and can be used; nothing about this game comes from the database.
    for s in ("home", "away"):
        side[s]["espn_before"] = None if post else espn_record_before(g.get(f"{s}_records"), game_result(g, s))
        side[s]["caught_up"] = bool(side[s]["db"] and (side[s]["today"] or (
            side[s]["espn_before"] is not None and side[s]["espn_before"] == stored_before(side[s]["rows"], gday))))
    trusted = vouched or bool(side["home"]["caught_up"] and side["away"]["caught_up"])
    known = bool(college and polls and polls.is_covered(year, gday))
    upset = bool(college and trusted and winner is not None and not post and known
                 and second["ap"] is not None and (first["ap"] is None or first["ap"] > second["ap"]))
    pair = (first["db"]["franchise_id"], second["db"]["franchise_id"]) if first["db"] and second["db"] else None
    return {"g": g, "day": gday, "year": year, "first": first, "second": second, "home": side["home"],
            "winner": winner, "vouched": vouched, "trusted": trusted, "post": post, "known": known,
            "upset": upset, "max_year": year if trusted else year - 1, "pair": pair,
            "upset_franchises": list(pair) if upset else []}


def _bundle(x: dict, sport: str, fy: dict, polls, series: dict, upset_rows: dict, playoff_wins: dict,
            history: dict, game_state: dict) -> dict:
    college = sport == "ncaafb"
    g, gday, year, first, second, h = x["g"], x["day"], x["year"], x["first"], x["second"], x["home"]
    vouched, trusted, post, known, winner = x["vouched"], x["trusted"], x["post"], x["known"], x["winner"]

    def label(s):
        r = s["ap"] if s["ap"] is not None else (s["espn_rank"] if not known else None)
        return f"No. {r} {s['name']}" if r else s["name"]

    neutral = ((h["today"] or {}).get("venue_side") == "neutral" if h["today"]
               else bool(g.get("neutral_site")))
    where = ("neutral site" if neutral else f"at {h['short']}" if college
             else f"{h['name']} home game")
    result = (f"{label(first)} {first['score']}, {label(second)} {second['score']}"
              f"{' (OT)' if g.get('overtime') else ''} ({gday.strftime('%a %Y-%m-%d')}, {where}"
              f"{', playoffs' if post else ''})")
    lines: list[str] = []

    going, after = [], []
    sg = ((game_state.get("sports") or {}).get("nfl") or {}).get("season_games") or []
    for s in (first, second):
        if college:
            rp = rank_phrase(s["ap"], s["cfp"], s["espn_rank"] if not known else None)
            if trusted:
                t = s["today"]
                before = ((t["wins_before"], t["losses_before"], t["ties_before"]) if t
                          else s["espn_before"])
                going.append(f"{s['short']} {record(*before)}, {rp}")
                won = s is first and winner is not None
                lost = winner is not None and not won
                # Not stored yet: the conference record after comes from
                # ESPN, which is what game_state holds for this game anyway.
                conf = (_conference_record(s["rows"], gday) if t
                        else _espn_conference_record(s["records"], s["rows"]))
                after.append(f"{s['short']} {record(before[0] + won, before[1] + lost, before[2])}"
                             + (f" ({conf})" if conf else ""))
            else:
                going.append(f"{s['short']}: {rp}")
        else:
            rb = nfl_record_before(sg, s["abbr"], gday, year)
            if rb:
                going.append(f"{s['name']} {record(*rb)}" + (" in the regular season" if post else ""))
            st = None if post else nfl_standings_after(sg, s["abbr"], gday)
            if st:
                after.append(f"{s['name']} {st}")
    if going:
        lines.append("Going in: " + "; ".join(going) + ".")
    if after:
        lines.append("After: " + "; ".join(after) + ".")
    if college and not trusted:
        lines.append("This game is not in the database yet: records, conference records and upset facts "
                     "left out; series counts stop at last season.")

    if x["pair"]:
        line = series_line(series.get((*x["pair"], gday)), first["short"], second["short"],
                           regular_from=fy["regular"], post_from=fy["postseason"],
                           through="" if trusted else "through last season", college=college)
        if line:
            lines.append(line)

    if x["upset"]:
        w, l = first, second
        b = bucket_for(l["ap"])
        who = f"{w['short']} ({'unranked' if w['ap'] is None else 'No. ' + str(w['ap'])})"
        state, last = last_win_over_ranked(upset_rows.get(w["db"]["franchise_id"], []), polls, b)
        if state == "ok":
            lines.append(f"Upset: {who} beat No. {l['ap']} {l['short']}; their last regular-season win over "
                         f"{bucket_words(b)} before this: {last['game_date'].isoformat()} "
                         f"(over No. {last['opp_rank']}).")
        elif state == "never":
            lines.append(f"Upset: {who} beat No. {l['ap']} {l['short']}; their first regular-season win over "
                         f"{bucket_words(b)} since at least {polls.first_year} (AP poll starts {polls.first_year}).")
        if w["ap"] is None:
            state, last = last_loss_to_unranked(upset_rows.get(l["db"]["franchise_id"], []), polls)
            if state == "ok":
                lines.append(f"{l['short']}'s last regular-season loss to an unranked team before this: "
                             f"{last['game_date'].isoformat()}.")
            elif state == "never":
                lines.append(f"{l['short']}'s first regular-season loss to an unranked team since at least "
                             f"{polls.first_year} (AP poll starts {polls.first_year}).")

    if post and not college and first["db"] and second["db"]:
        # Not stored yet: an earlier round THIS postseason may be missing
        # too, so the claim stops at last season and says so.
        parts = []
        for s in (first, second):
            y = max((v for v in playoff_wins.get(s["db"]["franchise_id"], []) if v <= x["max_year"]), default=None)
            parts.append(f"{s['name']} {y} season" if y else
                         f"{s['name']} none since at least {fy['postseason']} (playoff data starts {fy['postseason']})")
        lines.append(f"Last playoff win before {'this game' if trusted else 'this postseason'}: "
                     + "; ".join(parts) + ".")

    hist_lines = [f"{s['name']} history: " + " ".join(history[(sport, s["name"])])
                  for s in (first, second) if history.get((sport, s["name"]))]

    return {"game_id": g.get("game_id"), "date": gday.isoformat(), "result": result, "lines": lines,
            "history": hist_lines,
            "ranked": bool(college and any(s["ap"] or s["espn_rank"] for s in (first, second))),
            "upset": bool(x["upset"]),
            "best_rank": min([r for s in (first, second) for r in (s["ap"] or s["espn_rank"],) if r] or [99]),
            "postseason": post, "vouched": vouched, "trusted": trusted,
            "teams": [{"name": s["name"], "full_name": (s["db"] or {}).get("full_name") or s["name"],
                       "nickname": (s["db"] or {}).get("nickname"), "location": (s["db"] or {}).get("location")}
                      for s in (first, second)]}


def stored_before(rows: list[dict], day: date) -> tuple[int, int, int]:
    """A team's record this season from the stored games dated before `day`."""
    prior = [r for r in rows if r["game_date"] < day]
    return (sum(r["result"] == "W" for r in prior), sum(r["result"] in ("L", "OTL") for r in prior),
            sum(r["result"] == "T" for r in prior))


def _espn_conference_record(records: dict, rows: list[dict]) -> str | None:
    """ESPN's conference record after the game, named by the stored season."""
    conf = parse_record((records or {}).get("vsconf"))
    name = next((r.get("conference") for r in rows if r.get("conference")), None)
    return f"{record(*conf)} {name}" if conf and name else None


def _conference_record(rows: list[dict], upto: date) -> str | None:
    """Conference record through the game day, from the stored games: games
    against a team in the same conference that season."""
    mine = [r for r in rows if r["season_type"] == "regular" and r["game_date"] <= upto
            and r.get("conference_id") and r.get("conference_id") == r.get("opp_conference_id")]
    if not rows or not rows[0].get("conference_id"):
        return None
    w = sum(r["result"] == "W" for r in mine)
    l = sum(r["result"] in ("L", "OTL") for r in mine)
    return f"{record(w, l)} {rows[0].get('conference') or 'conference'}"


def fetch_bundles(game_state: dict, url: str | None = None, connect=_connect) -> dict:
    """The game_state "football" block. Never raises."""
    url = url if url is not None else os.environ.get("SPORTS_DB_URL", "")
    if not url:
        return {"status": "unavailable", "reason": "SPORTS_DB_URL is not set"}
    if not games_in_play(game_state):
        return {"status": "ok", "as_of": game_state.get("yesterday_date"), "sports": {}}
    secrets = _secrets(url)
    try:
        with connect(url) as conn:
            return build_bundles(conn, game_state)
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        return {"status": "unavailable", "reason": _scrub(f"{type(e).__name__}: {e}", secrets)}


# ---------------------------------------------------------------------------
# Rendering, under the budget
# ---------------------------------------------------------------------------

def priorities(block: dict, text: str = "") -> dict[str, int]:
    """game_id -> 1 (a story covers it), 2 (ranked college matchup, or any
    NFL game), 3 (everything else: one result line)."""
    in_play = {norm(t["full_name"]) for e in (block.get("sports") or {}).values()
               for b in e["games"] for t in b["teams"]}
    pool = {sport: [t for b in e["games"] for t in b["teams"]]
            for sport, e in (block.get("sports") or {}).items()}
    # The other pro teams only lend their nicknames and cities to the
    # ambiguity checks: their own names can't match (empty full_name).
    pool["_other"] = [dict(t, full_name="") for t in block.get("name_pool") or []
                      if norm(t["full_name"]) not in in_play]
    named = {norm(t["full_name"]) for ts in (named_teams(text, pool).values() if text else ()) for t in ts}
    named.discard("")
    out = {}
    for sport, e in (block.get("sports") or {}).items():
        for b in e["games"]:
            if any(norm(t["full_name"]) in named for t in b["teams"]):
                out[b["game_id"]] = 1
            elif sport == "nfl" or b["ranked"]:
                out[b["game_id"]] = 2
            else:
                out[b["game_id"]] = 3
    return out


def order_key(prio: dict, sports: list[str]):
    """Within a tier: upsets first, then the best-ranked matchups, then the
    feed's order."""
    def key(sb):
        sport, b = sb
        return (prio[b["game_id"]], not b.get("upset"), b.get("best_rank", 99), sports.index(sport))
    return key


HEADER = ["## GROUND TRUTH: FOOTBALL GAMES",
          "Source: SLAP sports database and ESPN, as of {as_of}. One bundle per game: records and AP ranks "
          "GOING IN, standings after, the series before this game and the history of both teams. These are "
          "SOURCED: state them as written, keeping \"since at least\" where it appears (the data starts "
          "there). College counts are regular season; bowls are not stored.",
          ""]


def _story(story) -> str:
    if not story:
        return ""
    if isinstance(story, (dict, list)):
        return story_text(story)
    return story_text(story) or story


def _cost(lines: list[str], indent: int) -> int:
    return sum(len(x) + indent + 1 for x in lines)


def render(game_state: dict, story: str | dict | None = None,
           budget: int = BUDGET) -> tuple[list[str], dict[str, str]]:
    """The FOOTBALL part of the GROUND TRUTH block, at most `budget`
    characters, filled in the approved order (SLA-114): story games, then
    ranked matchups and NFL games, each at the richest size that still fits
    (full bundle; bundle without its history lines; result line), then one
    result line for every other game while room is left. Story and ranked
    games always keep at least their result line; whatever else doesn't fit
    is counted in the last line, never silently lost.

    Returns the lines and, per game shown, the size it was shown at
    ("full", "core" or "result"): format_game_state_summary needs to know
    which games and which teams' history the block already carries.
    `story` defaults to what attach_story() stored after Pass 1."""
    block = (game_state or {}).get("football") or {}
    if block.get("status") != "ok" or not any(e["games"] for e in (block.get("sports") or {}).values()):
        return [], {}
    if story is None:
        story = block.get("story_text")
    prio = priorities(block, _story(story))
    sports = list(block["sports"])
    games = [(sport, b) for sport, e in block["sports"].items() for b in e["games"]]
    order = sorted(games, key=order_key(prio, sports))
    head = [HEADER[0], HEADER[1].format(as_of=block.get("as_of")), ""]
    # Fixed costs: the header, one label per sport, and room for the
    # "... not shown" line.
    used = _cost(head, 0) + sum(len(block["sports"][sp]["label"]) + 2 for sp in sports) + 80
    size: dict[str, str] = {}
    top = [(sp, b) for sp, b in order if prio[b["game_id"]] < 3]
    for sp, b in top:                      # their result lines are guaranteed
        size[b["game_id"]] = "result"
        used += len(b["result"]) + 3
    for sp, b in top:
        full = _cost(b["lines"], 4) + _cost(b.get("history") or [], 4)
        core = _cost(b["lines"], 4)
        if used + full <= budget:
            size[b["game_id"]], used = "full", used + full
        elif b.get("history") and used + core <= budget:
            size[b["game_id"]], used = "core", used + core
    for sp, b in order:
        if prio[b["game_id"]] == 3 and used + len(b["result"]) + 3 <= budget:
            size[b["game_id"]] = "result"
            used += len(b["result"]) + 3
    out = list(head)
    for sport in sports:
        part = [b for sp, b in order if sp == sport and b["game_id"] in size]
        if not part:
            continue
        out.append(f"{block['sports'][sport]['label']}:")
        for b in part:
            out.append("  " + b["result"])
            if size[b["game_id"]] in ("full", "core"):
                out.extend("    " + x for x in b["lines"])
            if size[b["game_id"]] == "full":
                out.extend("    " + x for x in b.get("history") or [])
    if len(size) < len(games):
        out.append(f"  ... and {len(games) - len(size)} more football result(s) not shown (budget).")
    return out, size


def summary_lines(game_state: dict, story: str | dict | None = None, budget: int = BUDGET) -> list[str]:
    """The FOOTBALL part of the GROUND TRUTH block (see render())."""
    return render(game_state, story, budget)[0]


def shown(game_state: dict) -> tuple[set[str], set[tuple[str, str]]]:
    """What the block, as rendered right now, already carries: the game ids
    it shows at any size (their result line is in it), and the (sport, team)
    pairs whose history it shows in full. format_game_state_summary leaves
    exactly those out of YESTERDAY'S GAME RESULTS and HISTORICAL CONTEXT, so
    nothing is said twice and nothing the budget cut is lost (SLA-119)."""
    _, size = render(game_state)
    games, teams = set(size), set()
    for sport, e in (((game_state or {}).get("football") or {}).get("sports") or {}).items():
        for b in e["games"]:
            if size.get(b["game_id"]) == "full":
                teams.update((sport, t["name"]) for t in b["teams"])
    return games, teams


def attach_story(game_state: dict, story_plan) -> str:
    """Called by both runners after Pass 1 (SLA-119). Stores the plan's story
    text in game_state["football"], in memory only, so every later pass's
    ground truth puts the games the stories cover first, in full. Returns one
    line for the log. Never raises."""
    block = (game_state or {}).get("football") or {}
    if block.get("status") != "ok":
        return "football: no bundles today" + (f" ({block['reason']})" if block.get("reason") else "")
    try:
        before = len("\n".join(summary_lines(game_state)))
        block["story_text"] = story_text(story_plan)
        lines, size = render(game_state)
        prio = priorities(block, block["story_text"])
    except Exception as e:  # noqa: BLE001 - reported, never fatal
        block.pop("story_text", None)
        return f"football: story order skipped: {type(e).__name__}: {e}"
    story = [g for g, p in prio.items() if p == 1]
    full = sum(1 for g in story if size.get(g) == "full")
    after = len("\n".join(lines))
    return (f"football: block {before:,} -> {after:,} chars (cap {BUDGET:,}); "
            f"{len(story)} story game(s), {full} in full; {len(size)} of {len(prio)} game(s) shown")
