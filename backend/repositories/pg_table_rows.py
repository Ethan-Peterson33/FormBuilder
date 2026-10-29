"""PostgreSQL implementation of :class:`repositories.base.TableRowRepository`.

Plain ``SELECT`` / ``INSERT`` / ``UPDATE`` / ``DELETE`` on the table the
catalog described. Identifiers are rendered with ``sql.Identifier`` from the
catalog description; values are parameters. Constraint violations raised by
PostgreSQL become :class:`RowConstraintError` with the column / constraint
and a hint, so the API answers ``409`` with the real message.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import errors, sql

from core.database import Database
from models.tables import TableRow
from repositories.base import RowConstraintError
from services.row_query import RowQuery, build_query
from services.table_catalog import TableInfo, load_table
from services.table_rows import row_to_api

_HINTS = {
    "23502": "The column is required.",
    "23514": "The value is not allowed by a check constraint on the table.",
    "23505": "A row with this value already exists.",
    "23503": "The value must reference an existing row (or other rows still reference this one).",
    "22P02": "The value has the wrong format for the column type.",
    "22001": "The value is too long for the column.",
    "22003": "The value is out of range for the column type.",
    "22007": "The value is not a valid date/time.",
    "22008": "The value is not a valid date/time.",
}

# Everything PostgreSQL raises about the *values* of a statement.
_ROW_REFUSALS: tuple[type[psycopg.Error], ...] = (errors.IntegrityError, errors.DataError)


def _refusal(exc: psycopg.Error, table: TableInfo) -> RowConstraintError:
    diag = exc.diag
    message = (diag.message_primary or str(exc)).strip() if diag else str(exc).strip()
    if diag is not None and diag.message_detail:
        message = f"{message} ({diag.message_detail.strip()})"
    return RowConstraintError(
        message,
        table_ref=table.ref,
        column=diag.column_name if diag else None,
        constraint=diag.constraint_name if diag else None,
        sqlstate=exc.sqlstate,
        hint=_HINTS.get(exc.sqlstate or "", "Change the value so the table's constraints allow it."),
    )


def _key_clause(table: TableInfo) -> sql.Composable:
    key = table.primary_key
    assert key is not None, "callers check for a primary key first"
    return sql.SQL("{k} = %s").format(k=sql.Identifier(key.name))


class PgTableRowRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def describe(self, ref: str) -> TableInfo | None:
        with self._db.connection() as conn:
            return load_table(conn, ref)

    def list_rows(self, table: TableInfo, query: RowQuery) -> tuple[list[TableRow], int]:
        where, params, order_by = build_query(table, query)
        where_sql = sql.SQL(" WHERE ") + where if where is not None else sql.SQL("")
        with self._db.connection() as conn:
            total = conn.execute(
                sql.SQL("SELECT count(*) AS n FROM {t}").format(t=table.identifier) + where_sql, params
            ).fetchone()["n"]
            rows = conn.execute(
                sql.SQL("SELECT * FROM {t}").format(t=table.identifier)
                + where_sql
                + sql.SQL(" ORDER BY ") + order_by
                + sql.SQL(" LIMIT %s OFFSET %s"),
                [*params, query.limit, query.offset],
            ).fetchall()
        return [row_to_api(table, row) for row in rows], int(total)

    def get_row(self, table: TableInfo, key: Any) -> TableRow | None:
        with self._db.connection() as conn:
            row = conn.execute(
                sql.SQL("SELECT * FROM {t} WHERE ").format(t=table.identifier) + _key_clause(table), (key,)
            ).fetchone()
        return row_to_api(table, row) if row else None

    def create_row(self, table: TableInfo, values: dict[str, Any]) -> TableRow:
        if values:
            statement = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) RETURNING *").format(
                t=table.identifier,
                c=sql.SQL(", ").join(sql.Identifier(name) for name in values),
                v=sql.SQL(", ").join(sql.Placeholder() for _ in values),
            )
        else:
            statement = sql.SQL("INSERT INTO {t} DEFAULT VALUES RETURNING *").format(t=table.identifier)
        try:
            with self._db.connection() as conn:
                row = conn.execute(statement, list(values.values())).fetchone()
        except _ROW_REFUSALS as exc:
            raise _refusal(exc, table) from exc
        return row_to_api(table, row)

    def patch_row(self, table: TableInfo, key: Any, changes: dict[str, Any]) -> TableRow | None:
        assignments = [sql.SQL("{c} = %s").format(c=sql.Identifier(name)) for name in changes]
        params: list[Any] = list(changes.values())
        stamp = table.column("updated_at")
        if stamp is not None and stamp.writable and stamp.kind == "timestamp" and "updated_at" not in changes:
            assignments.append(sql.SQL("updated_at = now()"))
        if not assignments:
            return self.get_row(table, key)
        statement = (
            sql.SQL("UPDATE {t} SET ").format(t=table.identifier)
            + sql.SQL(", ").join(assignments)
            + sql.SQL(" WHERE ") + _key_clause(table)
            + sql.SQL(" RETURNING *")
        )
        try:
            with self._db.connection() as conn:
                row = conn.execute(statement, [*params, key]).fetchone()
        except _ROW_REFUSALS as exc:
            raise _refusal(exc, table) from exc
        return row_to_api(table, row) if row else None

    def delete_row(self, table: TableInfo, key: Any) -> bool:
        try:
            with self._db.connection() as conn:
                result = conn.execute(
                    sql.SQL("DELETE FROM {t} WHERE ").format(t=table.identifier) + _key_clause(table), (key,)
                )
                return result.rowcount == 1
        except _ROW_REFUSALS as exc:
            raise _refusal(exc, table) from exc
