"""
SLAP Newsletter — Fetch structured sports data from ESPN JSON API.
Outputs game_state.json with yesterday's results, series state, and standings.

Runs after fetch_content.py, before the LLM pipeline.
No API key required. ESPN endpoints are free and stable.
"""

import json
import re
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

SCRIPT_DIR  = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "game_state.json"

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Active calendar months for each league (1-indexed).
# Only fetch endpoints for leagues currently in season.
LEAGUES = {
    "nba": {
        "sport": "basketball", "league": "nba",
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
        "label": "NBA",
    },
    "nfl": {
        "sport": "football", "league": "nfl",
        "months": [9, 10, 11, 12, 1, 2],
        "label": "NFL",
    },
    "mlb": {
        "sport": "baseball", "league": "mlb",
        "months": [3, 4, 5, 6, 7, 8, 9, 10],
        "label": "MLB",
    },
    "nhl": {
        "sport": "hockey", "league": "nhl",
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
        "label": "NHL",
    },
    "wnba": {
        "sport": "basketball", "league": "wnba",
        "months": [5, 6, 7, 8, 9, 10],
        "label": "WNBA",
    },
    "ncaafb": {
        "sport": "football", "league": "college-football",
        "months": [8, 9, 10, 11, 12, 1],
        "label": "College Football",
    },
    "ncaamb": {
        "sport": "basketball", "league": "mens-college-basketball",
        "months": [11, 12, 1, 2, 3, 4],
        "label": "College Basketball",
    },
}

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 2  # seconds between retries

# Golf majors and tennis grand slams — matched against tournament name (lowercase).
MAJOR_GOLF_KEYWORDS = {
    "the masters", "masters tournament",
    "pga championship",
    "u.s. open", "us open",
    "the open championship", "open championship", "british open",
}
GRAND_SLAM_KEYWORDS = {
    "australian open",
    "french open", "roland garros",
    "wimbledon",
    "u.s. open", "us open",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

# Browser-like User-Agent strings to avoid ESPN 403 blocks
_UA_LIST = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15"),
    "SLAP-Newsletter/1.0",
]
_ua_idx = 0


def _next_ua() -> str:
    global _ua_idx
    ua = _UA_LIST[_ua_idx % len(_UA_LIST)]
    _ua_idx += 1
    return ua


def _browser_headers(url: str) -> dict:
    return {
        "User-Agent": _UA_LIST[0],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": "https://www.espn.com/",
    }


def fetch_url(url: str) -> dict | None:
    """Fetch a JSON endpoint with retries. Auto-retries with browser UA on 403."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            req = Request(url, headers={"User-Agent": _next_ua()})
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 403:
                # ESPN bot detection — retry immediately with full browser headers
                try:
                    req2 = Request(url, headers=_browser_headers(url))
                    with urlopen(req2, timeout=15) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as e2:
                    print(f"      ✗ 403 even with browser UA: {e2}")
                    return None
            if attempt < RETRY_ATTEMPTS:
                print(f"      Retry {attempt} (HTTP {e.code}): {url}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"      ✗ Failed after {RETRY_ATTEMPTS} attempts (HTTP {e.code})")
                return None
        except URLError as e:
            if attempt < RETRY_ATTEMPTS:
                print(f"      Retry {attempt}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"      ✗ Failed after {RETRY_ATTEMPTS} attempts: {e}")
                return None
        except json.JSONDecodeError as e:
            print(f"      ✗ JSON parse error: {e}")
            return None
    return None


def format_date(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Game parsing
# ---------------------------------------------------------------------------

def is_overtime(competition: dict) -> bool:
    """Detect overtime/extra innings/shootout from competition status."""
    status = competition.get("status", {})
    detail = status.get("type", {}).get("detail", "").lower()
    desc   = status.get("type", {}).get("description", "").lower()
    for term in ("ot", "overtime", "extra innings", "shootout", "so"):
        if term in detail or term in desc:
            return True
    return False


def parse_series(competition: dict, home_abbr: str, away_abbr: str) -> dict | None:
    """
    Extract and compute playoff series state from a competition dict.
    Returns None for regular season games (no series data).

    Key computed fields:
      series_over              — True when a team reaches 4 wins (best-of-7)
      clinching_game_for_home  — Home team just won their 4th game
      clinching_game_for_away  — Away team just won their 4th game
      elimination_game_for_*   — That team's season just ended
      next_game_number         — What game number comes next (if series continues)
    """
    series_data = competition.get("series")
    if not series_data:
        return None

    competitors = series_data.get("competitors", [])
    if not competitors:
        return None

    # Build abbreviation → wins map from ESPN series.competitors
    wins_map: dict[str, int] = {}
    for c in competitors:
        abbr = c.get("abbreviation", "")
        wins = int(c.get("wins", 0))
        if abbr:
            wins_map[abbr] = wins

    home_wins = wins_map.get(home_abbr, 0)
    away_wins = wins_map.get(away_abbr, 0)

    # Fallback: if competitors array was empty, parse the summary string.
    # ESPN often returns "CLE leads series 3-2" or "Series tied 2-2" even
    # when the competitors array is missing or empty.
    if not wins_map:
        summary_str = series_data.get("summary", "")
        # Pattern: "ABBR leads/wins series X-Y" or "ABBR leads X-Y"
        m = re.search(
            r'\b([A-Z]{2,5})\s+(?:leads?|wins?)\s+(?:series\s+)?(\d+)-(\d+)',
            summary_str, re.IGNORECASE,
        )
        if m:
            leader = m.group(1).upper()
            leader_wins  = int(m.group(2))
            trailer_wins = int(m.group(3))
            if leader == home_abbr.upper():
                home_wins, away_wins = leader_wins, trailer_wins
            elif leader == away_abbr.upper():
                away_wins, home_wins = leader_wins, trailer_wins
        else:
            # Pattern: "tied X-X" or "Series tied X-X"
            m2 = re.search(r'tied\s+(\d+)-(\d+)', summary_str, re.IGNORECASE)
            if m2:
                home_wins = away_wins = int(m2.group(1))
    total     = home_wins + away_wins

    WINS_TO_ADVANCE = 4  # Best-of-7

    series_over = home_wins >= WINS_TO_ADVANCE or away_wins >= WINS_TO_ADVANCE
    home_leads  = home_wins > away_wins
    tied        = home_wins == away_wins

    # Which team just clinched (reached WINS_TO_ADVANCE)?
    clinching_home = home_wins == WINS_TO_ADVANCE and not away_wins >= WINS_TO_ADVANCE
    clinching_away = away_wins == WINS_TO_ADVANCE and not home_wins >= WINS_TO_ADVANCE

    # A team is eliminated if the opponent just clinched
    elim_home = clinching_away
    elim_away = clinching_home

    return {
        "home_wins":               home_wins,
        "away_wins":               away_wins,
        "total_games_played":      total,
        "next_game_number":        total + 1 if not series_over else None,
        "series_over":             series_over,
        "home_leads":              home_leads,
        "tied":                    tied,
        "clinching_game_for_home": clinching_home,
        "clinching_game_for_away": clinching_away,
        "elimination_game_for_home": elim_home,
        "elimination_game_for_away": elim_away,
        "summary":                 series_data.get("summary", ""),
        "title":                   series_data.get("title", ""),
    }


def parse_game(event: dict) -> dict | None:
    """Parse a single ESPN event into a clean, flat game dict."""
    competitions = event.get("competitions", [])
    if not competitions:
        return None

    competition = competitions[0]
    competitors = competition.get("competitors", [])
    if len(competitors) < 2:
        return None

    # Home / away split
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    home_team  = home.get("team", {}).get("displayName", "Unknown")
    away_team  = away.get("team", {}).get("displayName", "Unknown")
    home_abbr  = home.get("team", {}).get("abbreviation", "")
    away_abbr  = away.get("team", {}).get("abbreviation", "")

    try:
        home_score = int(home.get("score", 0) or 0)
        away_score = int(away.get("score", 0) or 0)
    except (ValueError, TypeError):
        home_score = away_score = 0

    status_obj = competition.get("status", {})
    type_obj   = status_obj.get("type", {})
    completed  = type_obj.get("completed", False)
    status_str = type_obj.get("description", "").lower()

    winner = loser = None
    if completed:
        if home.get("winner"):
            winner, loser = home_team, away_team
        elif away.get("winner"):
            winner, loser = away_team, home_team

    # Detect playoff from series presence or competition notes
    is_playoff = bool(competition.get("series"))
    if not is_playoff:
        notes    = competition.get("notes", [])
        comp_type = competition.get("type", {}).get("abbreviation", "").lower()
        if any("playoff" in str(n).lower() or "postseason" in str(n).lower()
               for n in notes):
            is_playoff = True
        if comp_type in ("post", "po", "playoff"):
            is_playoff = True

    series = parse_series(competition, home_abbr, away_abbr) if is_playoff else None

    return {
        "game_id":    event.get("id", ""),
        "date":       event.get("date", ""),
        "matchup":    f"{away_team} @ {home_team}",
        "home_team":  home_team,
        "home_abbr":  home_abbr,
        "away_team":  away_team,
        "away_abbr":  away_abbr,
        "home_score": home_score,
        "away_score": away_score,
        "winner":     winner,
        "loser":      loser,
        "completed":  completed,
        "status":     status_str,
        "overtime":   is_overtime(competition),
        "playoffs":   is_playoff,
        "series":     series,
    }


# ---------------------------------------------------------------------------
# ESPN API fetchers
# ---------------------------------------------------------------------------

def fetch_scoreboard(sport: str, league: str, target_date: date) -> list[dict]:
    """Fetch all games for a given date. Returns list of parsed game dicts."""
    url = (
        f"{ESPN_BASE}/{sport}/{league}/scoreboard"
        f"?dates={format_date(target_date)}&limit=50"
    )
    data = fetch_url(url)
    if not data:
        return []

    games = []
    for event in data.get("events", []):
        game = parse_game(event)
        if game:
            games.append(game)
    return games


def _stat(stats: dict, *names: str, default: str = "?") -> str:
    """Try multiple ESPN stat field name variants, return first match."""
    for n in names:
        v = stats.get(n)
        if v is not None:
            return str(v)
    return default


def _parse_entries(entries: list) -> list[dict]:
    """Parse ESPN standings entries. Handles all known ESPN stat field name variants."""
    result = []
    for entry in entries:
        team_name = entry.get("team", {}).get("displayName", "")
        if not team_name:
            continue
        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
        result.append({
            "team":         team_name,
            "wins":         _stat(stats, "wins", "gamesWon", "W", "w"),
            "losses":       _stat(stats, "losses", "gamesLost", "L", "l"),
            "win_pct":      _stat(stats, "winPercent", "winningPercentage",
                                  "pct", "PCT", "percentage"),
            "games_behind": _stat(stats, "gamesBehind", "gb", "GB",
                                  "gamesBehindLeader", default="-"),
        })
    return result


def _drill_for_entries(node: dict) -> list:
    """Recursively find standings entries in any ESPN response shape."""
    entries = node.get("standings", {}).get("entries", [])
    if entries:
        return entries
    for key in ("children", "groups", "divisions"):
        for child in node.get(key, []):
            found = _drill_for_entries(child)
            if found:
                return found
    return []


def _extract_divisions(node: dict) -> dict:
    """
    Walk ESPN standings tree → {division_name: [team_dict, ...]}.
    Handles conference→division nesting and flat structures.
    """
    divisions: dict = {}

    def _walk(n: dict) -> None:
        for key in ("children", "groups", "divisions"):
            for child in n.get(key, []):
                name    = child.get("name", "") or child.get("abbreviation", "")
                entries = child.get("standings", {}).get("entries", [])
                if entries and name:
                    divisions[name] = _parse_entries(entries)
                else:
                    _walk(child)

    _walk(node)
    return divisions


def fetch_standings(sport: str, league: str, sport_key: str = "") -> dict | list:
    """
    Fetch current standings. Handles all known ESPN response shapes.

    Returns:
      - MLB: dict keyed by division  e.g. {'AL East': [...], 'NL East': [...], ...}
      - All others: flat list sorted by conference position.

    Tries /standings, then /standings?season=YEAR as fallback.
    """
    is_mlb = sport_key == "mlb"
    empty  = {} if is_mlb else []
    # Try multiple ESPN endpoints — some return stub {"fullViewLink":...} responses.
    # site.web.api.espn.com is what the ESPN website actually calls (most reliable).
    season = date.today().year
    urls = [
        (f"https://site.web.api.espn.com/apis/v2/sports/{sport}/{league}/standings"
         f"?region=us&lang=en&contentorigin=espn&type=0&level=3"),
        f"{ESPN_BASE}/{sport}/{league}/standings?level=3",
        f"{ESPN_BASE}/{sport}/{league}/standings",
        f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/standings?season={season}",
    ]

    data = None
    for url in urls:
        data = fetch_url(url)
        # Skip stub responses — ESPN sometimes returns {"fullViewLink": "..."}
        # instead of actual standings data
        if data and len(data) > 1:
            break
        data = None
    if not data:
        print(f"      ✗ Standings unavailable for {sport}/{league}")
        return empty

    # ── MLB: per-division dict ──────────────────────────────────────────────
    if is_mlb:
        divisions = _extract_divisions(data)
        if divisions:
            total = sum(len(v) for v in divisions.values())
            print(f"      Standings: {len(divisions)} divisions, {total} teams")
            return divisions
        all_entries = _drill_for_entries(data)
        if all_entries:
            teams = _parse_entries(all_entries)
            print(f"      Standings (flat fallback): {len(teams)} teams")
            return {"MLB": teams}
        print(f"      ✗ Could not parse MLB standings "
              f"(top-level keys: {list(data.keys())})")
        return {}

    # ── Non-MLB: flat list ──────────────────────────────────────────────
    # Use _extract_divisions first so we get ALL conferences/divisions,
    # not just the first one _drill_for_entries stops at.
    divisions = _extract_divisions(data)
    if divisions:
        all_teams = [t for v in divisions.values() for t in v]
        # Sort by win pct descending (handle ".780" and "0.780" formats)
        def _wpct(t: dict) -> float:
            try:
                return float(t.get("win_pct", "0") or "0")
            except ValueError:
                return 0.0
        all_teams.sort(key=_wpct, reverse=True)
        print(f"      Standings: {len(all_teams)} teams")
        return all_teams
    # Flat fallback — no division structure present
    all_entries = _drill_for_entries(data)
    if all_entries:
        teams = _parse_entries(all_entries)
        print(f"      Standings: {len(teams)} teams")
        return teams
    print(f"      ✗ Could not parse standings for {sport}/{league} "
          f"(top-level keys: {list(data.keys())})")
    return []


# ---------------------------------------------------------------------------
# Box Score (player stats per game)
# ---------------------------------------------------------------------------

def fetch_game_summary(sport: str, league: str, game_id: str) -> dict | None:
    """Fetch ESPN summary endpoint for a single game. Returns raw JSON or None."""
    url = f"{ESPN_BASE}/{sport}/{league}/summary?event={game_id}"
    return fetch_url(url)


def _parse_period_scores(summary: dict) -> dict:
    """
    Extract per-period scores from ESPN summary header.
    Works for NBA (quarters), NHL (periods), and MLB (innings).
    Returns {away_periods: [...], home_periods: [...], period_labels: [...]}
    """
    header_comps = summary.get("header", {}).get("competitions", [])
    comp = header_comps[0] if header_comps else {}

    away_scores: list[str] = []
    home_scores: list[str] = []

    for competitor in comp.get("competitors", []):
        ha = competitor.get("homeAway", "").lower()
        ls = [
            x.get("displayValue", str(int(x.get("value", 0))))
            for x in competitor.get("linescores", [])
        ]
        if ha == "away":
            away_scores = ls
        elif ha == "home":
            home_scores = ls

    n = max(len(away_scores), len(home_scores))
    # Pad shorter list
    away_scores = away_scores + ["-"] * (n - len(away_scores))
    home_scores = home_scores + ["-"] * (n - len(home_scores))

    return {"away_periods": away_scores, "home_periods": home_scores, "count": n}


def _parse_team_totals(summary: dict) -> dict:
    """Extract team-level R/H/E (MLB) or points totals from boxscore.teams."""
    result: dict = {}
    for team in summary.get("boxscore", {}).get("teams", []):
        ha    = team.get("homeAway", "").lower()
        stats = {s.get("name", ""): s.get("displayValue", "") for s in team.get("statistics", [])}
        result[ha] = stats
    return result


def _parse_game_notes(summary: dict) -> list[str]:
    """Extract game notes (2B, HR, WP, LP, T, A, etc.) from ESPN summary."""
    notes: list[str] = []

    # Primary: summary-level notes array
    for note in summary.get("notes", []):
        text = note.get("headline", note.get("text", ""))
        if text and text not in notes:
            notes.append(text)

    # Also check header competition notes
    header_comps = summary.get("header", {}).get("competitions", [])
    if header_comps:
        for note in header_comps[0].get("notes", []):
            text = note.get("headline", note.get("text", ""))
            if text and text not in notes:
                notes.append(text)

    return notes


def _parse_nba_box(summary: dict) -> dict:
    """
    Parse NBA summary into {home, away, linescore}.
    Players include MIN, FG, 3PT, FT, REB, AST, STL, BLK, PTS.
    Linescore includes per-quarter scores.
    """
    KEY = ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "PTS"]
    result: dict = {}

    for team_entry in summary.get("boxscore", {}).get("players", []):
        side      = team_entry.get("homeAway", "home")
        team_abbr = team_entry.get("team", {}).get("abbreviation", "")
        players: list[dict] = []

        for stats_group in team_entry.get("statistics", []):
            labels = stats_group.get("names", stats_group.get("labels", []))
            for ae in stats_group.get("athletes", []):
                raw = ae.get("stats", [])
                if not raw:
                    continue
                stats: dict[str, str] = {}
                for k in KEY:
                    if k in labels:
                        idx = labels.index(k)
                        if idx < len(raw):
                            stats[k] = raw[idx]
                if stats.get("MIN", "0") in ("0", "--", "", None):
                    continue
                ath = ae.get("athlete", {})
                players.append({
                    "name":    ath.get("shortName", ath.get("displayName", "?")),
                    "pos":     ath.get("position", {}).get("abbreviation", ""),
                    "starter": ae.get("starter", False),
                    "stats":   stats,
                })

        result[side] = {"team": team_abbr, "players": players}

    # Quarter/OT scores
    periods = _parse_period_scores(summary)
    n = periods["count"]
    if n <= 4:
        labels = ["Q1", "Q2", "Q3", "Q4"][:n]
    else:
        labels = ["Q1", "Q2", "Q3", "Q4"] + [f"OT{i}" for i in range(1, n - 3)]
    result["linescore"] = {
        "period_labels":  labels,
        "away_periods":   periods["away_periods"],
        "home_periods":   periods["home_periods"],
    }

    return result


def _parse_mlb_box(summary: dict) -> dict:
    """
    Parse MLB summary into:
      home/away: {team, batting, pitching}
      linescore: {away_innings, home_innings, away_rhe, home_rhe, num_innings}
      notes:     [str, ...]   game notes (2B, HR, WP, LP, T, A, etc.)
    """
    BAT = ["AB", "R", "H", "RBI", "BB", "SO", "AVG"]
    PIT = ["IP", "H", "R", "ER", "BB", "SO", "ERA"]
    result: dict = {}

    players_list = summary.get("boxscore", {}).get("players", [])
    for i, team_entry in enumerate(players_list):
        side_raw  = team_entry.get("homeAway", "")
        side_norm = side_raw.lower()
        if side_norm in ("away", "visitor", "visitors"):
            side = "away"
        elif side_norm == "home":
            side = "home"
        else:
            side = "away" if i == 0 else "home"
        team_abbr = team_entry.get("team", {}).get("abbreviation", "")
        batting, pitching = [], []

        for sg in team_entry.get("statistics", []):
            type_raw  = sg.get("type", "")
            type_text = (
                type_raw.get("text", "").lower()
                if isinstance(type_raw, dict)
                else str(type_raw).lower()
            )
            labels = sg.get("labels", sg.get("names", []))
            is_bat = "batt" in type_text
            is_pit = "pitch" in type_text
            if not is_bat and not is_pit:
                continue
            target = BAT if is_bat else PIT

            for ae in sg.get("athletes", []):
                raw = ae.get("stats", [])
                if not raw:
                    continue
                stats = {
                    k: raw[labels.index(k)]
                    for k in target
                    if k in labels and labels.index(k) < len(raw)
                }
                ath = ae.get("athlete", {})
                entry = {
                    "name": ath.get("shortName", ath.get("displayName", "?")),
                    "pos":  ath.get("position", {}).get("abbreviation", ""),
                    "stats": stats,
                    "note":  ae.get("didNotPlay", {}).get("text", ""),
                }
                if is_bat:
                    batting.append(entry)
                else:
                    pitching.append(entry)

        result[side] = {"team": team_abbr, "batting": batting, "pitching": pitching}

    # ── Linescore (inning-by-inning) ────────────────────────────────────────
    periods      = _parse_period_scores(summary)
    team_totals  = _parse_team_totals(summary)
    num_innings  = max(periods["count"], 9)

    def _rhe(ha: str) -> dict:
        s = team_totals.get(ha, {})
        return {
            "R": s.get("runs",   s.get("R", "-")),
            "H": s.get("hits",   s.get("H", "-")),
            "E": s.get("errors", s.get("E", "-")),
        }

    # Pad innings to at least 9
    away_inn = periods["away_periods"] + ["-"] * max(0, 9 - len(periods["away_periods"]))
    home_inn = periods["home_periods"] + ["-"] * max(0, 9 - len(periods["home_periods"]))

    result["linescore"] = {
        "num_innings":  num_innings,
        "away_innings": away_inn,
        "home_innings": home_inn,
        "away_rhe":     _rhe("away"),
        "home_rhe":     _rhe("home"),
    }

    # ── Game notes ──────────────────────────────────────────────────────────
    result["notes"] = _parse_game_notes(summary)

    return result


def _parse_nhl_box(summary: dict) -> dict:
    """
    Parse NHL summary into:
      home/away: {team, players (placeholder for future skater/goalie stats)}
      linescore: {period_labels, away_periods, home_periods}
    """
    result: dict = {}

    # Team sides from boxscore.teams (NHL may not have per-player stats in summary)
    for team in summary.get("boxscore", {}).get("teams", []):
        ha        = team.get("homeAway", "").lower()
        team_abbr = team.get("team", {}).get("abbreviation", "")
        if ha in ("home", "away"):
            result[ha] = {"team": team_abbr, "players": []}

    # Period scores
    periods = _parse_period_scores(summary)
    n       = periods["count"]
    if n <= 3:
        labels = ["P1", "P2", "P3"][:n]
    else:
        labels = ["P1", "P2", "P3"] + [f"OT{i}" for i in range(1, n - 2)]
    result["linescore"] = {
        "period_labels": labels,
        "away_periods":  periods["away_periods"],
        "home_periods":  periods["home_periods"],
    }
    result["notes"] = _parse_game_notes(summary)
    return result


def parse_box_score(summary: dict, sport_key: str) -> dict:
    """Dispatch to the correct sport parser."""
    if sport_key == "nba" or sport_key == "wnba":
        return _parse_nba_box(summary)
    if sport_key == "mlb":
        return _parse_mlb_box(summary)
    if sport_key == "nhl":
        return _parse_nhl_box(summary)
    return {}


# ---------------------------------------------------------------------------
# League Leaders
# ---------------------------------------------------------------------------

# Stat categories to pull per sport
_LEADERS_CONFIG = {
    "mlb": {
        "batting":  ["battingAverage", "homeRuns", "RBIs", "onBasePlusSlugging", "stolenBases"],
        "pitching": ["ERA", "wins", "strikeouts", "WHIP", "saves"],
    },
    "nba": {
        "offense":  ["points", "rebounds", "assists", "steals", "blocks"],
    },
    "nhl": {
        "skaters":  ["points", "goals", "assists"],
        "goalies":  ["goalsAgainstAverage", "savePct"],
    },
}

_STAT_LABELS = {
    # MLB batting
    "battingAverage":    "AVG",
    "homeRuns":          "HR",
    "RBIs":             "RBI",
    "onBasePlusSlugging":"OPS",
    "stolenBases":       "SB",
    # MLB pitching
    "ERA":               "ERA",
    "wins":              "W",
    "strikeouts":        "SO",
    "WHIP":              "WHIP",
    "saves":             "SV",
    # NBA
    "points":            "PTS",
    "rebounds":          "REB",
    "assists":           "AST",
    "steals":            "STL",
    "blocks":            "BLK",
    # NHL
    "goals":             "G",
    "goalsAgainstAverage":"GAA",
    "savePct":           "SV%",
}


def fetch_league_leaders(sport: str, league: str, sport_key: str) -> dict:
    """
    Fetch top-5 leaders for key stats.
    Returns:
      {category_name: {label, leaders: [{name, team, value}, ...]}}
    """
    config = _LEADERS_CONFIG.get(sport_key, {})
    if not config:
        return {}

    url  = f"{ESPN_BASE}/{sport}/{league}/leaders"
    data = fetch_url(url)
    if not data:
        return {}

    # ESPN returns: {"categories": [{"name": ..., "leaders": [{...}]}]}
    # OR:           {"leaders":    [{"name": ..., "leaders": [{...}]}]}
    raw_cats = data.get("categories", data.get("leaders", []))
    cat_map  = {c.get("name", ""): c for c in raw_cats}

    all_wanted = [s for group in config.values() for s in group]
    result: dict = {}

    for stat_name in all_wanted:
        cat = cat_map.get(stat_name)
        if not cat:
            continue
        leaders_raw = cat.get("leaders", cat.get("athletes", []))
        leaders: list[dict] = []
        for entry in leaders_raw[:5]:
            athlete = entry.get("athlete", entry.get("displayName", {}))
            if isinstance(athlete, str):
                name = athlete
                team = ""
            else:
                name = athlete.get("shortName", athlete.get("displayName", "?"))
                team = (entry.get("team", {}) or athlete.get("team", {})).get("abbreviation", "")
            leaders.append({
                "name":  name,
                "team":  team,
                "value": entry.get("displayValue", entry.get("value", "?")),
            })
        if leaders:
            result[stat_name] = {
                "label":   _STAT_LABELS.get(stat_name, stat_name),
                "leaders": leaders,
            }

    total = sum(len(v["leaders"]) for v in result.values())
    if total:
        print(f"      Leaders: {len(result)} categories, {total} entries")
    return result


# ---------------------------------------------------------------------------
# Playoff Bracket
# ---------------------------------------------------------------------------

def fetch_playoff_bracket(sport: str, league: str, season: int) -> list[dict]:
    """
    Fetch playoff bracket from ESPN's /playoffs endpoint.
    Returns a list of round dicts compatible with build_box_score.render_playoff_bracket():
      [
        {
          "round_title": "First Round",
          "series": [
            {"away_abbr":"CLE","away_seed":"1","away_wins":3,
             "home_abbr":"DET","home_seed":"8","home_wins":2,
             "summary":"CLE leads 3-2","series_over":False},
          ]
        }
      ]
    Returns empty list if endpoint is unavailable.
    """
    # Try several ESPN bracket endpoint variants
    bracket_urls = [
        f"{ESPN_BASE}/{sport}/{league}/playoffs?season={season}",
        f"https://site.api.espn.com/apis/v2/sports/{sport}/{league}/playoffs?season={season}",
        f"{ESPN_BASE}/{sport}/{league}/bracket?season={season}",
    ]
    data = None
    for url in bracket_urls:
        data = fetch_url(url)
        if data and len(data) > 1:
            break
        data = None
    if not data:
        return []

    rounds_out: list[dict] = []

    # ESPN bracket structure: data.bracket.rounds or data.rounds
    bracket = data.get("bracket", data)
    raw_rounds = bracket.get("rounds", bracket.get("series", []))
    if not raw_rounds:
        return []

    # Some endpoints return a flat list of series (not grouped by round)
    if isinstance(raw_rounds, list) and raw_rounds and "series" not in raw_rounds[0]:
        # Flat series list — group by round name
        by_round: dict[str, list] = {}
        for s in raw_rounds:
            rnd = s.get("round", {}).get("name", s.get("type", {}).get("text", "Playoffs"))
            by_round.setdefault(rnd, []).append(s)
        raw_rounds = [{"name": k, "series": v} for k, v in by_round.items()]

    for rnd in raw_rounds:
        title        = rnd.get("name", rnd.get("title", ""))
        series_list  = rnd.get("series", rnd.get("matchups", []))
        parsed_series: list[dict] = []

        for s in series_list:
            competitors = s.get("competitors", [])
            if len(competitors) < 2:
                continue
            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[1])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[0])
            summary = s.get("summary", s.get("note", {}).get("headline", ""))
            h_wins  = int(home.get("wins", 0) or 0)
            a_wins  = int(away.get("wins", 0) or 0)
            done    = h_wins >= 4 or a_wins >= 4
            parsed_series.append({
                "away_abbr": away.get("team", {}).get("abbreviation", "?"),
                "away_seed": str(away.get("seed", "")),
                "away_wins": a_wins,
                "home_abbr": home.get("team", {}).get("abbreviation", "?"),
                "home_seed": str(home.get("seed", "")),
                "home_wins": h_wins,
                "summary":   summary,
                "series_over": done,
            })

        if parsed_series:
            rounds_out.append({"round_title": title, "series": parsed_series})

    return rounds_out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    today     = date.today()
    yesterday = today - timedelta(days=1)
    month     = today.month

    print(f"Fetching sports data — today: {today}, yesterday's games: {yesterday}")

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "as_of_date":    today.isoformat(),
        "yesterday_date": yesterday.isoformat(),
        "sports":        {},
    }

    active_count    = 0
    total_games     = 0
    series_moments  = []

    for key, config in LEAGUES.items():
        if month not in config["months"]:
            print(f"  Skipping {config['label']} (off-season)")
            continue

        label  = config["label"]
        sport  = config["sport"]
        league = config["league"]
        print(f"  Fetching {label}...")

        yesterday_games = fetch_scoreboard(sport, league, yesterday)
        today_games     = fetch_scoreboard(sport, league, today)
        standings       = fetch_standings(sport, league, sport_key=key)

        # Fetch individual game box scores (player stats)
        # NHL and NBA get box scores; MLB gets box scores + linescore + notes
        box_sports = {"nba", "mlb", "nhl", "wnba"}
        box_count = 0
        if key in box_sports:
            for game in yesterday_games:
                if game.get("completed") and game.get("game_id"):
                    summary = fetch_game_summary(sport, league, game["game_id"])
                    if summary:
                        game["box_score"] = parse_box_score(summary, key)
                        box_count += 1
                    time.sleep(0.25)
        if box_count:
            print(f"    {box_count} box score(s) fetched")

        # League leaders
        leaders = fetch_league_leaders(sport, league, key)

        # Fetch playoff bracket if any game (yesterday OR today) is a playoff game
        any_playoff = any(g.get("playoffs") for g in yesterday_games + today_games)
        bracket: list[dict] = []
        if any_playoff:
            # ESPN bracket endpoints all 404 — build from game series data.
            # Use yesterday + today so series that didn't play yesterday still appear.
            all_games_for_bracket = yesterday_games + today_games
            bracket = _infer_bracket_from_games(all_games_for_bracket, label)
            if bracket:
                print(f"    Playoff bracket inferred — {sum(len(r['series']) for r in bracket)} series")

        output["sports"][key] = {
            "label":           label,
            "yesterday_games": yesterday_games,
            "today_games":     today_games,
            "standings":       standings,
            "bracket":         bracket,
            "leaders":         leaders,
        }

        completed = [g for g in yesterday_games if g["completed"]]
        total_games += len(completed)
        active_count += 1
        print(f"    {len(completed)} completed game(s) yesterday")

        # Surface series moments for quick operator visibility
        for game in completed:
            s = game.get("series")
            if not s:
                continue
            if s.get("series_over"):
                msg = f"    ✓ {label} series over: {game['winner']} advances ({s['summary']})"
                print(msg)
                series_moments.append(msg.strip())
            elif s.get("clinching_game_for_home") or s.get("clinching_game_for_away"):
                clincher = game["winner"] if game["winner"] else "?"
                msg = f"    → {label} clincher: {game['matchup']} | {s['summary']}"
                print(msg)
                series_moments.append(msg.strip())
            elif s.get("elimination_game_for_home") or s.get("elimination_game_for_away"):
                msg = f"    → {label} elimination game: {game['matchup']} | {s['summary']}"
                print(msg)

    # ── Golf majors + Tennis grand slams ──────────────────────────────────
    print("  Fetching golf majors...")
    golf_data = fetch_golf_majors()
    if not golf_data:
        print("    No active major golf tournaments")

    print("  Fetching tennis grand slams...")
    tennis_data = fetch_tennis_grand_slams()
    if not tennis_data:
        print("    No active grand slam tennis tournaments")

    output["golf"]   = golf_data
    output["tennis"] = tennis_data

    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n✓ game_state.json saved")
    print(f"  {active_count} active sport(s) · {total_games} completed game(s) from yesterday")
    if series_moments:
        print(f"  Series activity: {len(series_moments)} clincher/elimination game(s)")


# ---------------------------------------------------------------------------
# Golf Majors
# ---------------------------------------------------------------------------

def fetch_golf_majors() -> list[dict]:
    """
    Fetch leaderboards for any currently active major golf tournaments.
    Checks PGA Tour and European Tour (DP World Tour) endpoints.
    Returns a list of tournament dicts consumable by build_box_score.render_golf_leaderboard().
    """
    urls = [
        f"{ESPN_BASE}/golf/leaderboard?league=pga",
        f"{ESPN_BASE}/golf/leaderboard?league=lpga",
    ]
    results: list[dict] = []

    for url in urls:
        data = fetch_url(url)
        if not data:
            continue

        for event in data.get("events", []):
            name_lower = event.get("name", "").lower()
            if not any(kw in name_lower for kw in MAJOR_GOLF_KEYWORDS):
                continue

            status_obj = event.get("status", {})
            type_obj   = status_obj.get("type", {})
            status_str = type_obj.get("description", "")
            period     = status_obj.get("period", 0)

            # Skip if tournament is over and wasn't yesterday
            if type_obj.get("name") == "STATUS_FINAL" and period < 4:
                continue

            players: list[dict] = []
            competitions = event.get("competitions", [])
            comp = competitions[0] if competitions else {}

            for c in comp.get("competitors", []):
                ath   = c.get("athlete", {})
                stats = {s.get("name", ""): s.get("displayValue", "") for s in c.get("statistics", [])}
                linescores = c.get("linescores", [])
                rounds = [ls.get("displayValue", "") for ls in linescores]

                # toPar can be in statistics or top-level score
                to_par = (
                    stats.get("toPar")
                    or stats.get("scoreToPar")
                    or c.get("score", "")
                )
                total = stats.get("totalScore") or c.get("totalScore", "")

                pos_obj = c.get("status", {}).get("position", {})
                pos_str = pos_obj.get("displayText", "") if pos_obj else ""

                players.append({
                    "pos":     pos_str,
                    "name":    ath.get("displayName", "?"),
                    "country": ath.get("flag", {}).get("alt", "") or ath.get("nationality", ""),
                    "to_par":  to_par,
                    "rounds":  rounds,
                    "total":   total,
                })

            if players:
                results.append({
                    "tournament_name": event.get("name", ""),
                    "is_major":        True,
                    "status":          status_str,
                    "round":           f"Round {period}" if period else "",
                    "players":         players,   # all players, renderer will show all
                })
                print(f"    Golf: {event.get('name','')} — {len(players)} players")

    return results


# ---------------------------------------------------------------------------
# Tennis Grand Slams
# ---------------------------------------------------------------------------

def fetch_tennis_grand_slams() -> list[dict]:
    """
    Fetch match results for any currently active grand slam tennis tournament.
    Tries ATP men's and WTA women's scoreboards.
    Returns list of tournament dicts for build_box_score.render_tennis_results().
    """
    urls = [
        f"{ESPN_BASE}/tennis/atp/scoreboard",
        f"{ESPN_BASE}/tennis/wta/scoreboard",
    ]
    seen_names: set[str] = set()
    results: list[dict] = []

    for url in urls:
        data = fetch_url(url)
        if not data:
            continue

        for event in data.get("events", []):
            name       = event.get("name", "")
            name_lower = name.lower()
            if not any(kw in name_lower for kw in GRAND_SLAM_KEYWORDS):
                continue
            if name in seen_names:
                continue
            seen_names.add(name)

            # Determine surface
            surface = ""
            for note in event.get("competitions", [{}])[0].get("notes", []):
                if "surface" in note.get("headline", "").lower():
                    surface = note.get("headline", "")
                    break

            # Find the current round from the first competition's note
            rnd_label = ""
            first_comp = (event.get("competitions") or [{}])[0]
            for note in first_comp.get("notes", []):
                h = note.get("headline", "")
                if h:
                    rnd_label = h
                    break

            matches: list[dict] = []
            for comp in event.get("competitions", []):
                comps = comp.get("competitors", [])
                if len(comps) < 2:
                    continue
                status_type = comp.get("status", {}).get("type", {})
                completed   = status_type.get("completed", False)
                if not completed:
                    continue

                winner = next((c for c in comps if c.get("winner")), comps[0])
                loser  = next((c for c in comps if not c.get("winner")), comps[1])

                # Build score from winner's linescores (sets won)
                winner_scores = [ls.get("displayValue", "") for ls in winner.get("linescores", [])]
                loser_scores  = [ls.get("displayValue", "") for ls in loser.get("linescores", [])]
                score_parts   = [
                    f"{w}-{l}" for w, l in zip(winner_scores, loser_scores)
                ]
                score_str = " ".join(score_parts) or comp.get("status", {}).get("type", {}).get("shortDetail", "")

                # Round from this specific competition's notes
                comp_rnd = ""
                for note in comp.get("notes", []):
                    h = note.get("headline", "")
                    if h:
                        comp_rnd = h
                        break

                matches.append({
                    "winner":    winner.get("athlete", {}).get("displayName", "?"),
                    "loser":     loser.get("athlete", {}).get("displayName", "?"),
                    "score":     score_str,
                    "round":     comp_rnd or rnd_label,
                    "completed": True,
                })

            if matches:
                results.append({
                    "tournament_name": name,
                    "is_grand_slam":   True,
                    "surface":         surface,
                    "round":           rnd_label,
                    "matches":         matches,
                })
                print(f"    Tennis: {name} — {len(matches)} completed match(es)")

    return results


def _infer_bracket_from_games(games: list[dict], league_label: str) -> list[dict]:
    """
    Fallback: build a minimal bracket from series data embedded in game dicts.
    Groups all series into one 'Current Round' block.
    """
    seen: set[str] = set()
    series_list: list[dict] = []

    for game in games:
        if not game.get("playoffs"):
            continue
        s = game.get("series")
        if not s:
            continue
        key = f"{game.get('away_abbr','')}-{game.get('home_abbr','')}"
        if key in seen:
            continue
        seen.add(key)
        series_list.append({
            "away_abbr":   game.get("away_abbr", "?"),
            "away_seed":   "",
            "away_wins":   s.get("away_wins", 0),
            "home_abbr":   game.get("home_abbr", "?"),
            "home_seed":   "",
            "home_wins":   s.get("home_wins", 0),
            "summary":     s.get("summary", ""),
            "series_over": s.get("series_over", False),
        })

    if not series_list:
        return []
    return [{"round_title": f"{league_label} Playoffs", "series": series_list}]


if __name__ == "__main__":
    main()
