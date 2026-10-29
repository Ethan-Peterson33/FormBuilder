"""Apply pending SQL migrations.

Migrations are plain SQL files in ``backend/db/migrations`` named
``NNNN_description.sql``. Each file wraps itself in ``BEGIN``/``COMMIT`` and,
as its last statement, inserts its version (the file name without ``.sql``)
into ``schema_migrations``. This module runs the files whose version is not
yet recorded, in name order.

The same files are mounted into the Postgres container's
``/docker-entrypoint-initdb.d`` so a brand-new container applies them by
itself; this runner is for databases that already exist (new migrations after
the initial start, CI, and the test-suite). New installs and existing
installations therefore go through the identical sequence of files.

Drift protection: the runner stores the sha256 of every file it applies (and
adopts the current file's checksum for migrations that were applied without
one, e.g. by the container). If a file that is recorded as applied no longer
matches, the runner stops with :class:`MigrationDriftError` instead of
pretending the database is up to date. Never edit an applied migration; add
a new one.

Usage::

    cd backend
    DATABASE_URL=postgresql://formbuilder:formbuilder@localhost:5432/formbuilder \
        python -m db.migrate            # apply
    python -m db.migrate --status       # show applied / pending / modified
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

from core.config import get_database_url

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class MigrationDriftError(RuntimeError):
    """An applied migration file was modified afterwards."""


@dataclass(frozen=True)
class MigrationState:
    version: str
    applied: bool
    checksum_recorded: str | None
    checksum_file: str

    @property
    def modified(self) -> bool:
        return self.applied and self.checksum_recorded is not None and self.checksum_recorded != self.checksum_file

    @property
    def label(self) -> str:
        if not self.applied:
            return "pending"
        if self.modified:
            return "MODIFIED"
        if self.checksum_recorded is None:
            return "applied (checksum not recorded)"
        return "applied"


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """All migration files, in the order they must be applied."""
    return sorted(path for path in directory.glob("*.sql") if path.is_file())


def version_of(path: Path) -> str:
    return path.stem


def checksum_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_checksum_column(conn: psycopg.Connection) -> bool:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'schema_migrations' AND column_name = 'checksum'
            """
        )
        return cur.fetchone() is not None


def applied_migrations(conn: psycopg.Connection) -> dict[str, str | None]:
    """``{version: recorded checksum or None}`` for every applied migration."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        if cur.fetchone()[0] is None:
            return {}
        if _has_checksum_column(conn):
            cur.execute("SELECT version, checksum FROM schema_migrations")
        else:
            cur.execute("SELECT version, NULL FROM schema_migrations")
        return {row[0]: row[1] for row in cur.fetchall()}


def applied_versions(conn: psycopg.Connection) -> set[str]:
    return set(applied_migrations(conn))


def migration_states(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[MigrationState]:
    applied = applied_migrations(conn)
    return [
        MigrationState(
            version=version_of(path),
            applied=version_of(path) in applied,
            checksum_recorded=applied.get(version_of(path)),
            checksum_file=checksum_of(path),
        )
        for path in migration_files(directory)
    ]


def pending_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[Path]:
    done = applied_versions(conn)
    return [path for path in migration_files(directory) if version_of(path) not in done]


def check_drift(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[MigrationState]:
    """Raise :class:`MigrationDriftError` if an applied file was changed."""
    states = migration_states(conn, directory)
    modified = [s for s in states if s.modified]
    if modified:
        names = ", ".join(s.version for s in modified)
        raise MigrationDriftError(
            f"Migration file(s) changed after being applied: {names}. "
            "Applied migrations are immutable - restore the original file and put the change "
            "in a new NNNN_*.sql migration (see docs/database.md)."
        )
    return states


def _record_checksum(conn: psycopg.Connection, version: str, checksum: str) -> None:
    if _has_checksum_column(conn):
        conn.execute("UPDATE schema_migrations SET checksum = %s WHERE version = %s", (checksum, version))


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Run every pending migration. Returns the versions applied, in order.

    ``conn`` must be in autocommit mode: each file manages its own transaction
    (``BEGIN`` ... ``COMMIT``), so a failing file rolls itself back and the
    exception propagates before anything after it runs.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection (psycopg.connect(..., autocommit=True))")

    check_drift(conn, directory)
    _adopt_missing_checksums(conn, directory)

    applied: list[str] = []
    for path in pending_migrations(conn, directory):
        version = version_of(path)
        checksum = checksum_of(path)
        logger.info("Applying migration %s", version)
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
        if version not in applied_versions(conn):
            raise RuntimeError(
                f"Migration {version} ran but did not record itself in schema_migrations; "
                "add `INSERT INTO schema_migrations (version) VALUES ('<version>');` "
                "before COMMIT."
            )
        _record_checksum(conn, version, checksum)
        applied.append(version)

    # The checksum column itself arrives with 0002; a second pass records the
    # earlier migrations once it exists.
    if applied:
        _adopt_missing_checksums(conn, directory)
    return applied


def _adopt_missing_checksums(conn: psycopg.Connection, directory: Path) -> None:
    """Migrations applied without a checksum (container init, or before the
    checksum column existed) adopt the current file's, so drift is detected
    from now on."""
    if not _has_checksum_column(conn):
        return
    for state in migration_states(conn, directory):
        if state.applied and state.checksum_recorded is None:
            _record_checksum(conn, state.version, state.checksum_file)
            logger.info("Recorded checksum for already applied migration %s", state.version)


def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations to the database at ``database_url``."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        return apply_migrations(conn, directory)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--database-url", default=get_database_url(), help="defaults to $DATABASE_URL")
    parser.add_argument("--status", action="store_true", help="list applied / pending / modified migrations only")
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("DATABASE_URL is not set (or pass --database-url)")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        with psycopg.connect(args.database_url, autocommit=True) as conn:
            if args.status:
                states = migration_states(conn)
                for state in states:
                    print(f"{state.label:32} {state.version}")
                if any(s.modified for s in states):
                    print("\nerror: an applied migration file was modified; see docs/database.md", file=sys.stderr)
                    return 2
                return 0
            applied = apply_migrations(conn)
    except MigrationDriftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except psycopg.OperationalError as exc:
        print(f"error: cannot connect to the database: {str(exc).strip()}", file=sys.stderr)
        return 1

    if applied:
        print("Applied: " + ", ".join(applied))
    else:
        print("Database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
