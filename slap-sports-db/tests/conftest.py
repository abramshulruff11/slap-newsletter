"""Fixtures: a throwaway database per test session, a rolled-back transaction
per test.

Needs a Postgres the tests may CREATE DATABASE on:

    DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres pytest

Without DATABASE_URL every database test is SKIPPED, loudly, rather than
passing: a green run that tested nothing is the failure this project exists
to stop. CI always sets it.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from sports_db.migrate import migrate

ADMIN_URL = os.environ.get("DATABASE_URL")


def _with_db(url: str, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(url, dbname=dbname)


@pytest.fixture(scope="session")
def db_url():
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL not set — database tests did not run")
    name = f"slap_sports_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = _with_db(ADMIN_URL, name)
    try:
        with psycopg.connect(url) as conn:
            migrate(conn)
        yield url
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def empty_db_url():
    """A database with NOTHING applied, for migration-runner tests."""
    if not ADMIN_URL:
        pytest.skip("DATABASE_URL not set — database tests did not run")
    name = f"slap_sports_empty_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _with_db(ADMIN_URL, name)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def conn(db_url):
    """A connection whose every change is rolled back after the test."""
    with psycopg.connect(db_url) as c:
        yield c
        c.rollback()


# ---------------------------------------------------------------------------
# Builders. Deliberately thin: the tests should read as the SQL they exercise.
# ---------------------------------------------------------------------------

class Build:
    def __init__(self, conn: psycopg.Connection):
        self.c = conn

    def one(self, sql: str, params=()):
        return self.c.execute(sql, params).fetchone()[0]

    def franchise(self, name: str) -> int:
        return self.one("INSERT INTO franchise (name) VALUES (%s) RETURNING franchise_id", (name,))

    def team(self, franchise_id, league, full_name, first, last=None, abbrev=None, location=None) -> int:
        location = location or full_name.rsplit(" ", 1)[0]
        return self.one(
            """INSERT INTO team (franchise_id, league_id, location, nickname, full_name, abbrev,
                                 first_season, last_season)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING team_id""",
            (franchise_id, league, location, full_name.rsplit(" ", 1)[-1], full_name, abbrev, first, last))

    def season(self, league, year, ties=False, otl=False, label=None) -> int:
        return self.one(
            """INSERT INTO season (league_id, year, label, ties_possible, has_ot_losses)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (league_id, year) DO UPDATE SET label = EXCLUDED.label
               RETURNING season_id""",
            (league, year, label or str(year), ties, otl))

    def team_season(self, team_id, season_id, group_id=None):
        league = self.one("SELECT league_id FROM season WHERE season_id = %s", (season_id,))
        self.c.execute(
            "INSERT INTO team_season (team_id, season_id, league_id, group_id) VALUES (%s, %s, %s, %s)",
            (team_id, season_id, league, group_id))

    def game(self, season_id, home, away, hs=None, as_=None, status="final", date="2024-09-08",
             season_type="regular", decided_in=None, game_number=1, series_id=None, source="nflverse",
             series_game_number=None) -> int:
        league = self.one("SELECT league_id FROM season WHERE season_id = %s", (season_id,))
        return self.one(
            """INSERT INTO game (league_id, season_id, season_type, game_date, home_team_id,
                                 away_team_id, status, home_score, away_score, decided_in,
                                 game_number, series_id, series_game_number, source_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING game_id""",
            (league, season_id, season_type, date, home, away, status, hs, as_, decided_in,
             game_number, series_id, series_game_number, source))

    def player(self, name) -> int:
        first, last = name.split(" ", 1)
        return self.one(
            "INSERT INTO player (full_name, first_name, last_name) VALUES (%s,%s,%s) RETURNING player_id",
            (name, first, last))


@pytest.fixture
def build(conn):
    return Build(conn)
