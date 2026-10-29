"""Run a statement from the SQL page and shape its result for a grid.

``services/sql_guard.py`` has already decided the statement is allowed and
of which kind; this module adds the database-level protection:

* read statements (``SELECT``/``WITH``/``VALUES``/``TABLE``) run inside a
  ``READ ONLY`` transaction, wrapped as ``SELECT * FROM (<sql>) AS _q LIMIT
  n+1`` - so a data-modifying CTE, ``SELECT INTO``, ``FOR UPDATE`` or a
  function that writes is refused by PostgreSQL, and at most ``limit`` rows
  come back (``truncated`` says if there were more);
* ``EXPLAIN`` / ``SHOW`` run as-is, also read-only;
* ``INSERT`` / ``UPDATE`` run read-write; ``rowCount`` reports the affected
  rows and ``RETURNING`` rows are returned (capped like reads);
* every statement has a ``statement_timeout``.

PostgreSQL errors about the statement itself (syntax, unknown column,
constraint, read-only violation, timeout) become :class:`SqlExecutionError`
with the message, SQLSTATE and the character position in the *user's* text;
connection problems propagate as usual.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import errors, sql

from core.database import Database
from services.sql_guard import Verdict
from services.table_catalog import ALLOWED_SCHEMAS, HIDDEN_TABLES
from services.table_rows import cell_to_api

DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
DEFAULT_TIMEOUT_MS = 10_000
MAX_TIMEOUT_MS = 60_000

_READ_WRAPPER = "SELECT * FROM (\n"  # the user's text starts after this, for error positions


class SqlExecutionError(Exception):
    """PostgreSQL refused or failed the statement. Routers answer ``400``."""

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str | None = None,
        position: int | None = None,
        detail: str | None = None,
        hint: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.position = position
        self.detail = detail
        self.hint = hint
        self.timed_out = timed_out


@dataclass
class SqlResult:
    kind: str
    columns: list[dict[str, str]]          # [{name, type}]
    rows: list[list[Any]]
    row_count: int | None                  # affected rows for writes, returned rows for reads
    truncated: bool
    duration_ms: float
    targets: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "columns": self.columns,
            "rows": self.rows,
            "rowCount": self.row_count,
            "truncated": self.truncated,
            "durationMs": self.duration_ms,
            "targets": self.targets,
        }


def _columns(conn: psycopg.Connection[Any], cursor: psycopg.Cursor[Any]) -> list[dict[str, str]]:
    if cursor.description is None:
        return []
    columns = []
    for column in cursor.description:
        type_info = conn.adapters.types.get(column.type_code)
        columns.append({"name": column.name, "type": type_info.name if type_info else str(column.type_code)})
    return columns


def _translate(exc: psycopg.Error, offset: int) -> SqlExecutionError:
    diag = exc.diag
    message = (diag.message_primary if diag and diag.message_primary else str(exc)).strip()
    position = None
    if diag is not None and diag.statement_position:
        position = max(int(diag.statement_position) - offset, 1)
    if isinstance(exc, errors.QueryCanceled):
        return SqlExecutionError("the statement took too long and was cancelled", sqlstate=exc.sqlstate, timed_out=True)
    if isinstance(exc, errors.ReadOnlySqlTransaction):
        return SqlExecutionError(
            f"{message}: SELECT statements run read-only; use an UPDATE or INSERT statement to change data",
            sqlstate=exc.sqlstate, position=position,
        )
    return SqlExecutionError(
        message,
        sqlstate=exc.sqlstate,
        position=position,
        detail=diag.message_detail.strip() if diag and diag.message_detail else None,
        hint=diag.message_hint.strip() if diag and diag.message_hint else None,
    )


class PgSqlRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def run(
        self,
        verdict: Verdict,
        params: list[Any] | None = None,
        *,
        limit: int = DEFAULT_LIMIT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> SqlResult:
        params = list(params or [])
        if verdict.kind == "read":
            statement: sql.Composable = (
                sql.SQL(_READ_WRAPPER) + sql.SQL(verdict.statement) + sql.SQL("\n) AS _q LIMIT {n}").format(n=sql.Literal(limit + 1))
            )
            offset = len(_READ_WRAPPER)
        else:
            statement = sql.SQL(verdict.statement)
            offset = 0

        started = time.perf_counter()
        with self._db.connection() as conn:
            if verdict.kind != "write":
                conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(sql.SQL("SET LOCAL statement_timeout = {t}").format(t=sql.Literal(timeout_ms)))
            try:
                with conn.cursor() as cursor:
                    cursor.execute(statement, params or None)
                    columns = _columns(conn, cursor)
                    rows: list[list[Any]] = []
                    truncated = False
                    if cursor.description is not None:
                        fetched = cursor.fetchmany(limit + 1)
                        truncated = len(fetched) > limit
                        rows = [[cell_to_api(value) for value in record.values()] for record in fetched[:limit]]
                    row_count = cursor.rowcount if verdict.kind == "write" else len(rows)
            except errors.QueryCanceled as exc:
                raise _translate(exc, offset) from exc
            except psycopg.OperationalError:
                raise  # connection problems are reported by main.py
            except psycopg.Error as exc:
                raise _translate(exc, offset) from exc
        duration = round((time.perf_counter() - started) * 1000, 1)
        return SqlResult(
            kind=verdict.kind, columns=columns, rows=rows, row_count=row_count,
            truncated=truncated, duration_ms=duration, targets=verdict.targets,
        )

    def list_tables(self) -> list[dict[str, Any]]:
        """Every table the SQL and row APIs expose, with column and row estimates."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                SELECT n.nspname AS schema, c.relname AS table,
                       (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns,
                       GREATEST(c.reltuples, 0)::bigint AS estimated_rows,
                       obj_description(c.oid, 'pg_class') AS comment,
                       t.id AS table_id, t.name AS display_name
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  LEFT JOIN data_tables t ON n.nspname = %(user_schema)s AND t.table_name = c.relname
                 WHERE c.relkind = 'r' AND n.nspname = ANY(%(schemas)s) AND NOT (c.relname = ANY(%(hidden)s))
                 ORDER BY n.nspname <> %(user_schema)s, n.nspname, c.relname
                """,
                {"schemas": sorted(ALLOWED_SCHEMAS), "hidden": sorted(HIDDEN_TABLES), "user_schema": "user_tables"},
            ).fetchall()
        return [
            {
                "ref": row["table_id"] or f"{row['schema']}.{row['table']}",
                "schema": row["schema"],
                "table": row["table"],
                "name": row["display_name"] or row["table"],
                "userTable": row["table_id"] is not None,
                "columns": int(row["columns"]),
                "estimatedRows": int(row["estimated_rows"]),
                "comment": row["comment"],
            }
            for row in rows
        ]
