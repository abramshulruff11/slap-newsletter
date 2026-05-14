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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_url(url: str) -> dict | None:
    """Fetch a JSON endpoint with retries. Returns parsed dict or None."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            req = Request(url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError) as e:
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
        f"?dates={format_date(target_date)}&limit=20"
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


def fetch_standings(sport: str, league: str) -> list[dict]:
    """Fetch current standings. Returns a simplified flat list."""
    url = f"{ESPN_BASE}/{sport}/{league}/standings"
    data = fetch_url(url)
    if not data:
        return []

    standings: list[dict] = []

    def parse_entries(entries: list) -> None:
        for entry in entries:
            team_name = entry.get("team", {}).get("displayName", "")
            stats = {
                s.get("name"): s.get("displayValue")
                for s in entry.get("stats", [])
            }
            standings.append({
                "team":         team_name,
                "wins":         stats.get("wins", "?"),
                "losses":       stats.get("losses", "?"),
                "win_pct":      stats.get("winPercent", "?"),
                "games_behind": stats.get("gamesBehind", "0"),
            })

    # ESPN standings nests by conference/division; try both structures
    children = data.get("children", [])
    if children:
        for division in children:
            for group in division.get("children", [division]):
                parse_entries(group.get("standings", {}).get("entries", []))
    else:
        parse_entries(data.get("standings", {}).get("entries", []))

    return standings


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
        standings       = fetch_standings(sport, league)

        output["sports"][key] = {
            "label":           label,
            "yesterday_games": yesterday_games,
            "today_games":     today_games,
            "standings":       standings,
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

    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n✓ game_state.json saved")
    print(f"  {active_count} active sport(s) · {total_games} completed game(s) from yesterday")
    if series_moments:
        print(f"  Series activity: {len(series_moments)} clincher/elimination game(s)")


if __name__ == "__main__":
    main()
