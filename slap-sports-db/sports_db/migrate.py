"""Apply migrations/NNNN_*.sql to a Postgres database, in order, once each.

    python -m sports_db.migrate                 # uses $DATABASE_URL
    python -m sports_db.migrate --status        # list applied / pending, change nothing
    python -m sports_db.migrate --database-url postgresql://...

Each file runs in its own transaction, so a failure leaves the database at
the last good migration rather than half-way through one.

An applied migration is never edited. Its checksum is recorded, and a file
whose content no longer matches what was applied is refused rather than
silently skipped: a schema that differs between two databases built "from
the same migrations" is exactly the drift this repo exists to avoid. Change
the schema with a NEW numbered file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    filename   text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """Return [(version, path)] sorted by version. Refuses a stray .sql file
    that doesn't follow NNNN_name.sql, and two files with the same number."""
    found: dict[str, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if not m:
            raise MigrationError(f"{path.name}: migration files must be named NNNN_lower_snake.sql")
        version = m.group(1)
        if version in found:
            raise MigrationError(f"duplicate migration number {version}: {found[version].name}, {path.name}")
        found[version] = path
    return sorted(found.items())


def checksum(path: Path) -> str:
    # Normalise line endings so a Windows checkout (CRLF) and CI (LF) agree.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def status(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str, str]]:
    """[(version, filename, state)] where state is applied / pending / CHANGED."""
    conn.execute(_LEDGER_DDL)
    applied = {v: c for v, c in conn.execute("SELECT version, checksum FROM schema_migrations")}
    rows = []
    for version, path in discover(directory):
        if version not in applied:
            rows.append((version, path.name, "pending"))
        elif applied[version] != checksum(path):
            rows.append((version, path.name, "CHANGED"))
        else:
            rows.append((version, path.name, "applied"))
    return rows


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration. Returns the filenames applied."""
    rows = status(conn, directory)
    conn.commit()
    changed = [name for _, name, state in rows if state == "CHANGED"]
    if changed:
        raise MigrationError(
            "already-applied migration(s) were edited: " + ", ".join(changed)
            + ". Put the change in a new numbered file instead.")
    applied = []
    paths = dict(discover(directory))
    for version, name, state in rows:
        if state != "pending":
            continue
        path = paths[version]
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                (version, name, checksum(path)))
        applied.append(name)
    return applied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--status", action="store_true", help="report only; apply nothing")
    args = ap.parse_args(argv)
    if not args.database_url:
        print("No database: pass --database-url or set DATABASE_URL.", file=sys.stderr)
        return 2
    with psycopg.connect(args.database_url) as conn:
        if args.status:
            for version, name, state in status(conn):
                print(f"  {state:8}  {name}")
            return 0
        try:
            done = migrate(conn)
        except MigrationError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
    print("Applied: " + ", ".join(done) if done else "Up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
