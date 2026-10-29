"""PostgreSQL connection handling and error reporting.

:class:`Database` wraps a ``psycopg_pool.ConnectionPool``. Repositories borrow
a connection per operation; the connection's transaction is committed when the
``with`` block exits normally and rolled back on an exception.

Failures are never hidden. A database that cannot be reached makes requests
fail with ``503`` and a schema problem with ``500``, both carrying the real
PostgreSQL message (see :func:`describe_database_error` and the exception
handlers in ``main.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger(__name__)

# How long a request waits for a pooled connection before failing with 503.
DEFAULT_CONNECT_TIMEOUT = 5.0

MIGRATE_HINT = "Run `python -m db.migrate` from the backend directory to apply pending migrations."
SCHEMA_MISMATCH_HINT = (
    "schema_migrations lists every migration as applied, yet a column or table is missing. "
    "A migration file was probably changed after it had been applied. Run `python -m db.migrate --status`, "
    "restore the original file and add the change as a new migration (see docs/database.md)."
)
CONSISTENCY_HINT = (
    "A user table was changed outside the API (or the registry was). Run `python scripts/check_db.py` "
    "for the list and repair the table or the registry by hand."
)
CONNECT_HINT = (
    "Check DATABASE_URL and that PostgreSQL is running and reachable "
    "(`docker compose up -d`, `docker compose ps`)."
)

# Exceptions that mean "could not talk to the database at all".
UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (PoolTimeout, psycopg.OperationalError)
# Everything psycopg raises, plus the pool's timeout.
DATABASE_ERRORS: tuple[type[BaseException], ...] = (psycopg.Error, PoolTimeout)


@dataclass
class DatabaseStatus:
    ok: bool
    server_version: str | None = None
    applied_migrations: list[str] = field(default_factory=list)
    pending_migrations: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "serverVersion": self.server_version,
            "appliedMigrations": self.applied_migrations,
            "pendingMigrations": self.pending_migrations,
            "error": self.error,
        }


class Database:
    """A connection pool for one PostgreSQL database."""

    def __init__(
        self,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        schema_probes: Sequence[str] = (),
        consistency_checks: Sequence[Callable[[psycopg.Connection[dict[str, Any]]], list[str]]] = (),
    ) -> None:
        self.url = url
        self._timeout = connect_timeout
        # Statements that touch every column the repositories use, e.g.
        # ``SELECT id, seq, ... FROM forms WHERE false``. They catch a schema
        # that schema_migrations claims is current but is not.
        self._schema_probes = tuple(schema_probes)
        # Deeper checks that return a list of problems (e.g. data tables whose
        # physical columns differ from their registry). Run after the probes.
        self._consistency_checks = tuple(consistency_checks)
        self._pool = ConnectionPool(
            url,
            open=False,
            min_size=min_size,
            max_size=max_size,
            timeout=connect_timeout,
            # Verify a connection before handing it out, so connections that
            # died (server restart) are discarded instead of failing a request.
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row},
        )

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        """Start the pool without blocking; connections are made in the background."""
        self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @property
    def closed(self) -> bool:
        return self._pool.closed

    # -- access ----------------------------------------------------------------
    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """Borrow a connection. Commits on clean exit, rolls back on error.

        Raises ``psycopg_pool.PoolTimeout`` when no connection could be made
        within ``connect_timeout`` (the database is down or unreachable).
        """
        try:
            with self._pool.connection(timeout=self._timeout) as conn:
                yield conn
        except PoolTimeout as exc:
            raise self._explain_timeout(exc) from exc

    def _explain_timeout(self, exc: PoolTimeout) -> BaseException:
        """Find out *why* the pool could not supply a connection.

        The pool only reports "couldn't get a connection after N sec"; the real
        reason (server down, wrong host, bad credentials, unknown database) is
        in its log. One direct connection attempt recovers it so the API
        response can show it. If that attempt succeeds the pool was merely
        exhausted and the original timeout stands.
        """
        try:
            psycopg.connect(self.url, connect_timeout=max(1, int(min(self._timeout, 3)))).close()
        except psycopg.OperationalError as cause:
            return psycopg.OperationalError(f"{exc}: {str(cause).strip()}")
        return exc

    # -- diagnostics -----------------------------------------------------------
    def check(self) -> DatabaseStatus:
        """Connectivity + schema status. Never raises; errors are in the result.

        Verified in order: the server answers, every migration file is
        recorded as applied, the tables really have the columns the
        repositories use (``schema_probes``), and the deeper
        ``consistency_checks`` report no problems.
        """
        from db.migrate import applied_versions, migration_files, version_of

        try:
            with self.connection() as conn:
                version = conn.execute("SHOW server_version").fetchone()["server_version"]
                applied = applied_versions(conn)
        except DATABASE_ERRORS as exc:
            _, detail = describe_database_error(exc)
            return DatabaseStatus(ok=False, error=detail)

        all_versions = [version_of(path) for path in migration_files()]
        pending = [v for v in all_versions if v not in applied]
        status = DatabaseStatus(
            ok=not pending,
            server_version=version,
            applied_migrations=[v for v in all_versions if v in applied],
            pending_migrations=pending,
        )
        if pending:
            status.error = {
                "message": "Database schema is out of date",
                "type": "PendingMigrations",
                "error": f"Pending migrations: {', '.join(pending)}",
                "hint": MIGRATE_HINT,
            }
            return status

        for probe in self._schema_probes:
            try:
                with self.connection() as conn:
                    conn.execute(probe)
            except DATABASE_ERRORS as exc:
                _, detail = describe_database_error(exc)
                detail["message"] = "Database schema does not match the applied migrations"
                detail["hint"] = SCHEMA_MISMATCH_HINT
                status.ok = False
                status.error = detail
                return status

        for check in self._consistency_checks:
            try:
                with self.connection() as conn:
                    problems = check(conn)
            except DATABASE_ERRORS as exc:
                _, detail = describe_database_error(exc)
                status.ok = False
                status.error = detail
                return status
            if problems:
                status.ok = False
                status.error = {
                    "message": "Data tables do not match their registry",
                    "type": "ConsistencyCheck",
                    "error": "; ".join(problems),
                    "problems": problems,
                    "hint": CONSISTENCY_HINT,
                }
                return status
        return status

    def log_startup_status(self) -> DatabaseStatus:
        status = self.check()
        if status.ok:
            logger.info(
                "PostgreSQL %s reachable; schema up to date (%s)",
                status.server_version,
                ", ".join(status.applied_migrations) or "no migrations",
            )
        else:
            assert status.error is not None
            logger.error(
                "Database problem at start-up: %s - %s. %s Requests will fail until this is fixed.",
                status.error["message"],
                status.error["error"],
                status.error.get("hint", ""),
            )
        return status


# -- error reporting ---------------------------------------------------------

def describe_database_error(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """Map a psycopg / pool exception to ``(http_status, detail)``.

    ``detail`` is what the API returns in the response body; it always has
    ``message``, ``type`` and ``error`` (the PostgreSQL text), plus
    ``sqlstate`` / ``constraint`` / ``hint`` when known.
    """
    detail: dict[str, Any] = {
        "message": "Database error",
        "type": type(exc).__name__,
        "error": str(exc).strip() or type(exc).__name__,
    }

    if isinstance(exc, UNAVAILABLE_ERRORS):
        detail["message"] = "Database unavailable"
        detail["hint"] = CONNECT_HINT
        status = 503
    else:
        status = 500

    if isinstance(exc, psycopg.Error):
        if exc.sqlstate:
            detail["sqlstate"] = exc.sqlstate
        diag = exc.diag
        if diag is not None:
            if diag.message_primary:
                detail["error"] = diag.message_primary
            if diag.message_detail:
                detail["detail"] = diag.message_detail
            if diag.constraint_name:
                detail["constraint"] = diag.constraint_name
            if diag.message_hint:
                detail["hint"] = diag.message_hint
        if isinstance(exc, (errors.UndefinedTable, errors.UndefinedColumn, errors.UndefinedFunction)):
            detail["message"] = "Database schema is missing or out of date"
            detail["hint"] = MIGRATE_HINT
        elif isinstance(exc, errors.InsufficientPrivilege):
            detail["message"] = "Database permission denied"
            detail["hint"] = "Grant the DATABASE_URL role access to the tables (see docs/database.md)."

    return status, detail
