"""The migration runner: applies once, in order, and refuses an edited file."""
from __future__ import annotations

import shutil

import psycopg
import pytest

from sports_db.migrate import MIGRATIONS_DIR, MigrationError, discover, migrate, status


def test_applies_everything_then_nothing(empty_db_url):
    with psycopg.connect(empty_db_url) as conn:
        first = migrate(conn)
        assert first == [p.name for _, p in discover()]
        assert migrate(conn) == []
        assert {state for _, _, state in status(conn)} == {"applied"}


def test_edited_migration_is_refused(empty_db_url, tmp_path):
    mdir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, mdir)
    with psycopg.connect(empty_db_url) as conn:
        migrate(conn, mdir)
        target = sorted(mdir.glob("*.sql"))[-1]
        target.write_text(target.read_text(encoding="utf-8") + "\n-- sneaky edit\n", encoding="utf-8")
        with pytest.raises(MigrationError, match="edited"):
            migrate(conn, mdir)


def test_crlf_checkout_is_not_an_edit(empty_db_url, tmp_path):
    mdir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, mdir)
    with psycopg.connect(empty_db_url) as conn:
        migrate(conn, mdir)
        for p in mdir.glob("*.sql"):
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        assert migrate(conn, mdir) == []


def test_failed_migration_leaves_no_trace(empty_db_url, tmp_path):
    mdir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, mdir)
    (mdir / "9999_broken.sql").write_text("CREATE TABLE half_done (x int);\nSELECT 1/0;\n")
    with psycopg.connect(empty_db_url) as conn:
        with pytest.raises(psycopg.errors.DivisionByZero):
            migrate(conn, mdir)
        conn.rollback()
        assert conn.execute("SELECT to_regclass('half_done')").fetchone()[0] is None
        assert [s for _, n, s in status(conn, mdir) if n == "9999_broken.sql"] == ["pending"]


def test_badly_named_file_is_refused(tmp_path):
    (tmp_path / "add_stuff.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="NNNN"):
        discover(tmp_path)
