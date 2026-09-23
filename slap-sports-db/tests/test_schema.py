"""The schema's promises, each tested against real Postgres.

Every rule docs/schema.md says the DATABASE enforces has a test here that
tries to break it. A rule with no failing-insert test is a rule we are only
hoping holds.
"""
from __future__ import annotations

import contextlib

import psycopg
import pytest
from psycopg import errors

from sports_db.adapters import PLANNED, launch_leagues


@contextlib.contextmanager
def rejected(conn, exc=psycopg.IntegrityError, match=None):
    """Assert the block is refused by the database, without poisoning the
    test's outer transaction (the savepoint absorbs the abort)."""
    with pytest.raises(exc, match=match):
        with conn.transaction():
            yield


# ---------------------------------------------------------------------------
# Fixture world: the Oilers/Titans franchise across AFL -> NFL and a move.
# ---------------------------------------------------------------------------

@pytest.fixture
def oilers(build):
    f = build.franchise("Tennessee Titans")
    t = {
        "afl_hou": build.team(f, "afl", "Houston Oilers", 1960, 1969, "HOU"),
        "nfl_hou": build.team(f, "nfl", "Houston Oilers", 1970, 1996, "HOU"),
        "ten_oil": build.team(f, "nfl", "Tennessee Oilers", 1997, 1998, "TEN"),
        "titans":  build.team(f, "nfl", "Tennessee Titans", 1999, None, "TEN"),
    }
    s = {
        "afl1960": build.season("afl", 1960, ties=True),
        "afl1961": build.season("afl", 1961, ties=True),
        "nfl1999": build.season("nfl", 1999, ties=True),
    }
    build.team_season(t["afl_hou"], s["afl1960"])
    build.team_season(t["afl_hou"], s["afl1961"])
    build.team_season(t["titans"], s["nfl1999"])
    return {"franchise": f, "team": t, "season": s}


# ---------------------------------------------------------------------------
# R6 — franchise continuity
# ---------------------------------------------------------------------------

def test_franchise_titles_count_across_league_and_rename(conn, build, oilers):
    t, s = oilers["team"], oilers["season"]
    chargers = build.team(build.franchise("Los Angeles Chargers"), "afl", "Los Angeles Chargers", 1960, 1960)
    build.team_season(chargers, s["afl1960"])
    conn.execute(
        """INSERT INTO champion (league_id, season_id, title, team_id, runner_up_team_id, source_id)
           VALUES ('afl', %s, 'AFL Championship', %s, %s, 'curated'),
                  ('afl', %s, 'AFL Championship', %s, NULL, 'curated')""",
        (s["afl1960"], t["afl_hou"], chargers, s["afl1961"], t["afl_hou"]))
    n = build.one("SELECT count(*) FROM v_champion WHERE franchise_id = %s", (oilers["franchise"],))
    assert n == 2, "the Titans franchise owns the Houston Oilers' AFL titles"


def test_team_cannot_play_a_season_it_has_no_team_season_for(conn, build, oilers):
    t, s = oilers["team"], oilers["season"]
    other = build.team(build.franchise("Jacksonville Jaguars"), "nfl", "Jacksonville Jaguars", 1995)
    # Titans have a 1999 team_season; the Jaguars do not.
    with rejected(conn, errors.ForeignKeyViolation):
        build.game(s["nfl1999"], t["titans"], other, 16, 14)


def test_team_season_must_match_team_league(conn, build, oilers):
    # The AFL-era Oilers team row cannot be placed in an NFL season.
    with rejected(conn, errors.ForeignKeyViolation):
        build.team_season(oilers["team"]["afl_hou"], oilers["season"]["nfl1999"])


def test_source_abbreviation_is_valid_for_a_season_range(conn, build):
    rams = build.franchise("Los Angeles Rams")
    stl = build.team(rams, "nfl", "St. Louis Rams", 1995, 2015, "STL", "St. Louis")
    la = build.team(rams, "nfl", "Los Angeles Rams", 2016, None, "LAR", "Los Angeles")
    conn.execute(
        """INSERT INTO team_xref (source_id, external_id, team_id, seasons) VALUES
           ('nflverse', 'STL', %s, '[1999,2016)'),
           ('nflverse', 'LA',  %s, '[2016,)'),
           ('espn',     'LAR', %s, '[2016,)')""", (stl, la, la))
    lookup = "SELECT team_id FROM team_xref WHERE source_id = %s AND external_id = %s AND seasons @> %s::int"
    assert build.one(lookup, ("nflverse", "STL", 2010)) == stl
    assert build.one(lookup, ("nflverse", "LA", 2024)) == la
    # Both xrefs resolve to one franchise, so a franchise query spans the move.
    assert build.one("SELECT count(DISTINCT franchise_id) FROM team WHERE team_id IN (%s, %s)", (stl, la)) == 1
    # One source key cannot mean two teams in the same season.
    with rejected(conn, errors.ExclusionViolation):
        conn.execute("INSERT INTO team_xref (source_id, external_id, team_id, seasons) "
                     "VALUES ('nflverse', 'LA', %s, '[2020,2021)')", (stl,))


# ---------------------------------------------------------------------------
# R1 — games
# ---------------------------------------------------------------------------

@pytest.fixture
def nfl2024(build):
    s = build.season("nfl", 2024, ties=True)
    kc = build.team(build.franchise("Kansas City Chiefs"), "nfl", "Kansas City Chiefs", 1963, None, "KC")
    bal = build.team(build.franchise("Baltimore Ravens"), "nfl", "Baltimore Ravens", 1996, None, "BAL")
    build.team_season(kc, s)
    build.team_season(bal, s)
    return s, kc, bal


def test_winner_is_derived_from_the_score(build, nfl2024):
    s, kc, bal = nfl2024
    g = build.game(s, kc, bal, 27, 20)
    assert build.one("SELECT winner_team_id FROM game WHERE game_id = %s", (g,)) == kc
    g2 = build.game(s, bal, kc, 17, 10, date="2024-12-21")
    assert build.one("SELECT winner_team_id FROM game WHERE game_id = %s", (g2,)) == bal


def test_final_game_needs_a_score(conn, build, nfl2024):
    s, kc, bal = nfl2024
    with rejected(conn, errors.CheckViolation, "final_has_score"):
        build.game(s, kc, bal, None, None, status="final")
    build.game(s, kc, bal, None, None, status="scheduled", date="2025-01-01")   # fine


def test_decided_in_only_on_a_finished_game(conn, build, nfl2024):
    s, kc, bal = nfl2024
    with rejected(conn, errors.CheckViolation, "decided_only_when_final"):
        build.game(s, kc, bal, None, None, status="scheduled", decided_in="overtime")


def test_team_cannot_play_itself(conn, build, nfl2024):
    s, kc, _ = nfl2024
    with rejected(conn, errors.CheckViolation):
        build.game(s, kc, kc, 1, 0)


def test_nfl_tie_is_allowed_nba_tie_is_not(conn, build, nfl2024):
    s, kc, bal = nfl2024
    g = build.game(s, kc, bal, 20, 20, decided_in="overtime")
    assert build.one("SELECT is_tie FROM game WHERE game_id = %s", (g,)) is True
    assert build.one("SELECT winner_team_id FROM game WHERE game_id = %s", (g,)) is None

    nba = build.season("nba", 2024, label="2024-25")
    bos = build.team(build.franchise("Boston Celtics"), "nba", "Boston Celtics", 1946)
    nyk = build.team(build.franchise("New York Knicks"), "nba", "New York Knicks", 1946)
    build.team_season(bos, nba)
    build.team_season(nyk, nba)
    with rejected(conn, errors.CheckViolation, "does not allow ties"):
        build.game(nba, bos, nyk, 100, 100, date="2024-10-22")


def test_tie_rule_is_per_season_not_per_league(conn, build):
    """The NHL had ties until 2003-04. The same league must accept a tie in
    one season and refuse it in another."""
    old = build.season("nhl", 1995, ties=True, label="1995-96")
    new = build.season("nhl", 2010, ties=False, otl=True, label="2010-11")
    mtl = build.team(build.franchise("Montreal Canadiens"), "nhl", "Montreal Canadiens", 1917)
    tor = build.team(build.franchise("Toronto Maple Leafs"), "nhl", "Toronto Maple Leafs", 1927)
    for s in (old, new):
        build.team_season(mtl, s)
        build.team_season(tor, s)
    build.game(old, mtl, tor, 2, 2, date="1995-12-02")
    with rejected(conn, errors.CheckViolation, "does not allow ties"):
        build.game(new, mtl, tor, 2, 2, date="2010-12-02")


def test_linescore_team_must_be_in_the_game(conn, build, nfl2024):
    s, kc, bal = nfl2024
    g = build.game(s, kc, bal, 27, 20)
    stranger = build.team(build.franchise("Denver Broncos"), "nfl", "Denver Broncos", 1970)
    conn.execute("INSERT INTO game_period_score VALUES (%s, %s, 1, 7), (%s, %s, 1, 3)", (g, kc, g, bal))
    with rejected(conn, errors.ForeignKeyViolation, "did not play"):
        conn.execute("INSERT INTO game_period_score VALUES (%s, %s, 1, 0)", (g, stranger))


# ---------------------------------------------------------------------------
# R4 — records from games, and the NHL's third column
# ---------------------------------------------------------------------------

def test_record_view_counts_wins_losses_ties(build, nfl2024):
    s, kc, bal = nfl2024
    build.game(s, kc, bal, 27, 20, date="2024-09-05")
    build.game(s, bal, kc, 30, 10, date="2024-10-05")
    build.game(s, kc, bal, 17, 17, date="2024-11-05", decided_in="overtime")
    build.game(s, kc, bal, 99, 0, date="2025-01-19", season_type="postseason")   # excluded
    row = build.c.execute(
        "SELECT games, wins, losses, ties, ot_losses, points_for, points_against "
        "FROM v_team_season_record WHERE team_id = %s AND season_id = %s", (kc, s)).fetchone()
    assert row == (3, 1, 1, 1, 0, 54, 67)


def test_record_view_splits_ot_losses_when_the_season_has_them(build):
    s = build.season("nhl", 2023, otl=True, label="2023-24")
    fla = build.team(build.franchise("Florida Panthers"), "nhl", "Florida Panthers", 1993)
    edm = build.team(build.franchise("Edmonton Oilers"), "nhl", "Edmonton Oilers", 1979)
    build.team_season(fla, s)
    build.team_season(edm, s)
    build.game(s, fla, edm, 4, 3, date="2023-11-01", decided_in="shootout", source="nhl_api")
    build.game(s, edm, fla, 3, 2, date="2023-12-01", decided_in="overtime", source="nhl_api")
    build.game(s, edm, fla, 5, 1, date="2024-01-01", decided_in="regulation", source="nhl_api")
    rec = "SELECT wins, losses, ot_losses FROM v_team_season_record WHERE team_id = %s"
    assert build.c.execute(rec, (edm,)).fetchone() == (2, 0, 1)
    assert build.c.execute(rec, (fla,)).fetchone() == (1, 1, 1)


def test_only_one_final_standing_per_team_season(conn, build, nfl2024):
    s, kc, _ = nfl2024
    ins = ("INSERT INTO standing (team_id, season_id, as_of_date, is_final, wins, losses, ties, source_id) "
           "VALUES (%s, %s, %s, %s, %s, %s, 0, 'espn')")
    conn.execute(ins, (kc, s, "2024-12-01", False, 11, 1))
    conn.execute(ins, (kc, s, "2025-01-06", True, 15, 2))
    with rejected(conn, errors.UniqueViolation):
        conn.execute(ins, (kc, s, "2025-01-07", True, 15, 2))


# ---------------------------------------------------------------------------
# R2 — postseason as series, champions tied to them
# ---------------------------------------------------------------------------

def test_super_bowl_is_a_best_of_one_series(conn, build):
    s = build.season("nfl", 2023, ties=True)
    kc = build.team(build.franchise("Kansas City Chiefs"), "nfl", "Kansas City Chiefs", 1963)
    sf = build.team(build.franchise("San Francisco 49ers"), "nfl", "San Francisco 49ers", 1946)
    build.team_season(kc, s)
    build.team_season(sf, s)
    rnd = build.one("INSERT INTO postseason_round (season_id, name, round_order, best_of, is_final) "
                    "VALUES (%s, 'Super Bowl', 4, 1, true) RETURNING round_id", (s,))
    ser = build.one(
        "INSERT INTO postseason_series (round_id, season_id, team_a_id, team_b_id, team_a_wins, "
        "team_b_wins, winner_team_id, source_id) VALUES (%s,%s,%s,%s,1,0,%s,'nflverse') RETURNING series_id",
        (rnd, s, kc, sf, kc))
    build.game(s, sf, kc, 22, 25, date="2024-02-11", season_type="postseason",
               decided_in="overtime", series_id=ser, series_game_number=1)
    conn.execute("INSERT INTO champion (league_id, season_id, title, team_id, runner_up_team_id, "
                 "series_id, source_id) VALUES ('nfl', %s, 'Super Bowl LVIII', %s, %s, %s, 'nflverse')",
                 (s, kc, sf, ser))
    assert build.one("SELECT team_name FROM v_champion WHERE season_label = '2023'") == "Kansas City Chiefs"

    # A game from another season cannot join this series.
    s2024 = build.season("nfl", 2024, ties=True)
    build.team_season(kc, s2024)
    build.team_season(sf, s2024)
    with rejected(conn, errors.ForeignKeyViolation):
        build.game(s2024, kc, sf, 20, 17, date="2025-02-09", season_type="postseason",
                   series_id=ser, series_game_number=2)

    other = build.team(build.franchise("Detroit Lions"), "nfl", "Detroit Lions", 1934)
    build.team_season(other, s)
    with rejected(conn, errors.CheckViolation):
        conn.execute("UPDATE postseason_series SET winner_team_id = %s WHERE series_id = %s", (other, ser))


def test_champion_needs_no_games(conn, build):
    """Pre-1999 curated titles exist without a single game row."""
    s = build.season("nfl", 1958, ties=True)
    bal = build.team(build.franchise("Indianapolis Colts"), "nfl", "Baltimore Colts", 1953, 1983)
    build.team_season(bal, s)
    conn.execute("INSERT INTO champion (league_id, season_id, title, team_id, source_id) "
                 "VALUES ('nfl', %s, 'NFL Championship', %s, 'curated')", (s, bal))
    assert build.one("SELECT franchise_name FROM v_champion WHERE year = 1958") == "Indianapolis Colts"


# ---------------------------------------------------------------------------
# R8 / R9 / R14 — rosters, season stats, careers
# ---------------------------------------------------------------------------

def test_career_totals_span_teams_and_skip_rates(conn, build, oilers):
    t, s = oilers["team"], oilers["season"]
    s2000 = build.season("nfl", 2000, ties=True)
    build.team_season(t["titans"], s2000)
    ind = build.team(build.franchise("Indianapolis Colts"), "nfl", "Indianapolis Colts", 1984)
    build.team_season(ind, s2000)
    p = build.player("Test Quarterback")
    ins = ("INSERT INTO player_season_stat (player_id, team_id, season_id, season_type, stats, source_id) "
           "VALUES (%s, %s, %s, %s, %s, 'nflverse')")
    conn.execute(ins, (p, t["titans"], s["nfl1999"], "regular",
                       '{"passing_yards": 3000, "passing_tds": 20, "passer_rating": 90.1}'))
    conn.execute(ins, (p, t["titans"], s2000, "regular", '{"passing_yards": 1000, "passing_tds": 5}'))
    conn.execute(ins, (p, ind, s2000, "regular", '{"passing_yards": 500, "passing_tds": 2}'))
    conn.execute(ins, (p, ind, s2000, "postseason", '{"passing_yards": 9999}'))   # excluded
    got = dict(conn.execute("SELECT stat_key, career_total FROM v_player_career_stat WHERE player_id = %s",
                            (p,)).fetchall())
    assert got == {"passing_yards": 4500, "passing_tds": 27}, "rates are never summed"
    assert build.one("SELECT seasons FROM v_player_career_stat WHERE player_id = %s "
                     "AND stat_key = 'passing_yards'", (p,)) == 2


def test_unregistered_stat_keys_are_visible(conn, build, oilers):
    t, s = oilers["team"], oilers["season"]
    p = build.player("Some Receiver")
    assert build.one("SELECT count(*) FROM v_unknown_stat_keys") == 0
    conn.execute("INSERT INTO player_season_stat (player_id, team_id, season_id, season_type, stats, source_id) "
                 "VALUES (%s, %s, %s, 'regular', '{\"receiving_yards\": 900, \"rec_yds\": 900}', 'nflverse')",
                 (p, t["titans"], s["nfl1999"]))
    unknown = conn.execute("SELECT stat_key, source_id FROM v_unknown_stat_keys").fetchall()
    assert unknown == [("rec_yds", "nflverse")]


def test_player_team_history_follows_the_franchise(conn, build, oilers):
    t, s = oilers["team"], oilers["season"]
    s1996 = build.season("nfl", 1996, ties=True)
    build.team_season(t["nfl_hou"], s1996)
    p = build.player("Steve McNair")
    for team, season in ((t["nfl_hou"], s1996), (t["titans"], s["nfl1999"])):
        conn.execute("INSERT INTO roster_entry (player_id, team_id, season_id, position, source_id) "
                     "VALUES (%s, %s, %s, 'QB', 'nflverse')", (p, team, season))
    rows = conn.execute("SELECT year, team_name, franchise_id FROM v_player_team_history "
                        "WHERE player_id = %s ORDER BY year", (p,)).fetchall()
    assert [r[1] for r in rows] == ["Houston Oilers", "Tennessee Titans"]
    assert {r[2] for r in rows} == {oilers["franchise"]}


def test_roster_entry_needs_the_team_to_exist_that_season(conn, build, oilers):
    p = build.player("Nobody Special")
    with rejected(conn, errors.ForeignKeyViolation):
        conn.execute("INSERT INTO roster_entry (player_id, team_id, season_id, source_id) "
                     "VALUES (%s, %s, %s, 'nflverse')",
                     (p, oilers["team"]["ten_oil"], oilers["season"]["nfl1999"]))


# ---------------------------------------------------------------------------
# Provenance and ingest honesty
# ---------------------------------------------------------------------------

def test_every_fact_table_requires_a_source(conn):
    facts = ["roster_entry", "postseason_series", "game", "standing", "champion",
             "player_season_stat", "team_season_stat"]
    rows = conn.execute(
        """SELECT table_name FROM information_schema.columns
           WHERE table_schema = 'public' AND column_name = 'source_id' AND is_nullable = 'NO'
             AND table_name = ANY(%s)""", (facts,)).fetchall()
    assert sorted(r[0] for r in rows) == sorted(facts)


def test_short_load_cannot_be_recorded_as_succeeded(conn, build):
    run = build.one("INSERT INTO ingest_run (source_id, league_id, asset, rows_expected) "
                    "VALUES ('nflverse', 'nfl', 'games.parquet', 7548) RETURNING ingest_run_id")
    with rejected(conn, errors.CheckViolation, "succeeded_means_complete"):
        conn.execute("UPDATE ingest_run SET status = 'succeeded', rows_written = 7000, "
                     "finished_at = now() WHERE ingest_run_id = %s", (run,))
    conn.execute("UPDATE ingest_run SET status = 'partial', rows_written = 7000, "
                 "finished_at = now() WHERE ingest_run_id = %s", (run,))
    with rejected(conn, errors.CheckViolation, "finished_when_done"):
        conn.execute("INSERT INTO ingest_run (source_id, status) VALUES ('espn', 'failed')")


# ---------------------------------------------------------------------------
# Extensibility: other sports fit with no schema change
# ---------------------------------------------------------------------------

def test_mlb_doubleheader_with_extra_innings_fits(conn, build):
    s = build.season("mlb", 1920, ties=True)   # MLB has had called ties
    nyy = build.team(build.franchise("New York Yankees"), "mlb", "New York Yankees", 1913)
    bos = build.team(build.franchise("Boston Red Sox"), "mlb", "Boston Red Sox", 1908)
    build.team_season(nyy, s)
    build.team_season(bos, s)
    g1 = build.game(s, nyy, bos, 3, 2, date="1920-07-05", game_number=1, source="retrosheet")
    build.game(s, nyy, bos, 1, 4, date="1920-07-05", game_number=2, source="retrosheet")
    for inning in range(1, 11):
        conn.execute("INSERT INTO game_period_score VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
                     (g1, nyy, inning, 1 if inning in (1, 5, 10) else 0,
                      g1, bos, inning, 1 if inning in (3, 7) else 0))
    assert build.one("SELECT sum(score) FROM game_period_score WHERE game_id = %s AND team_id = %s",
                     (g1, nyy)) == 3
    with rejected(conn, errors.UniqueViolation):
        build.game(s, nyy, bos, 5, 0, date="1920-07-05", game_number=2, source="retrosheet")


def test_college_teams_without_nicknames_fit(conn, build):
    s = build.season("ncaaf", 1869, ties=True)
    f1, f2 = build.franchise("Rutgers"), build.franchise("Princeton")
    r = build.one("INSERT INTO team (franchise_id, league_id, location, full_name, first_season) "
                  "VALUES (%s, 'ncaaf', 'Rutgers', 'Rutgers', 1869) RETURNING team_id", (f1,))
    p = build.one("INSERT INTO team (franchise_id, league_id, location, full_name, first_season) "
                  "VALUES (%s, 'ncaaf', 'Princeton', 'Princeton', 1869) RETURNING team_id", (f2,))
    build.team_season(r, s)
    build.team_season(p, s)
    build.game(s, r, p, 6, 4, date="1869-11-06", source="cfbd")


# ---------------------------------------------------------------------------
# Reference data vs the adapter plan
# ---------------------------------------------------------------------------

def test_every_planned_adapter_names_a_seeded_source_and_league(conn):
    sources = {r[0] for r in conn.execute("SELECT source_id FROM source")}
    leagues = {r[0] for r in conn.execute("SELECT league_id FROM league")}
    for p in PLANNED:
        assert p.source_id in sources, f"{p.source_id} is planned but not in `source`"
        assert p.league_id in leagues, f"{p.league_id} is planned but not in `league`"


def test_every_launch_league_has_a_history_and_a_daily_source():
    for league in launch_leagues() - {"afl"}:
        lanes = {p.lane for p in PLANNED if p.league_id == league}
        assert lanes & {"backfill", "curated"}, f"{league}: no history source planned"
        assert "delta" in lanes, f"{league}: no same-day source planned"


def test_no_individual_sport_at_launch(conn):
    # SLA-58 §5.4: team sports only. Tennis/golf need their own tables.
    assert conn.execute("SELECT count(*) FROM sport WHERE kind <> 'team'").fetchone()[0] == 0
