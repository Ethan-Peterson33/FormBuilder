"""PostgreSQL implementation of :class:`repositories.base.DataTableRepository`.

A data table is a real table in the ``user_tables`` schema plus its registry
rows in ``data_tables`` / ``data_table_columns``. Every method changes both
**inside one transaction** (DDL is transactional in PostgreSQL), so a failure
anywhere leaves neither half changed.

``update_table`` works out the difference between the stored and the desired
definition and runs the matching ``ALTER TABLE`` steps
(``services/table_ddl.py``). When existing data makes a step impossible -
``SET NOT NULL`` with NULLs present, a cast that fails, an option still in
use - PostgreSQL raises, the transaction rolls back, and the error is turned
into :class:`DefinitionConflictError` naming the column, so the router can
answer ``409`` with the real message.

``verify`` compares the registry with the catalog (``information_schema``
and ``pg_constraint``) and lists every discrepancy; ``/health`` and
``scripts/check_db.py`` report them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import errors, sql

from core.database import Database
from db.mapping import row_to_table_column, rows_to_data_table, table_column_to_row
from models.tables import DataTable, TableColumn
from repositories.base import DefinitionConflictError, TableSchemaMismatchError
from services import table_ddl
from services.table_ddl import SQL_TYPES, SYSTEM_COLUMNS, USER_SCHEMA, options_constraint_name, table_name_for

_TABLE_COLUMNS = "id, table_name, seq, name, description, created_at, updated_at"
_COLUMN_COLUMNS = "table_id, id, position, name, type, required, description, options, created_at, updated_at"

# Verify every column the repository uses exists (start-up / health check).
SCHEMA_PROBES = (
    f"SELECT {_TABLE_COLUMNS} FROM data_tables WHERE false",
    f"SELECT {_COLUMN_COLUMNS} FROM data_table_columns WHERE false",
)

_SELECT_COLUMNS = f"SELECT {_COLUMN_COLUMNS} FROM data_table_columns WHERE table_id = %s ORDER BY position"

_INSERT_TABLE = """
INSERT INTO data_tables (id, name, description, created_at, updated_at)
VALUES (%(id)s, %(name)s, %(description)s, COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()))
"""
_INSERT_COLUMN = """
INSERT INTO data_table_columns (table_id, id, position, name, type, required, description, options)
VALUES (%(table_id)s, %(id)s, %(position)s, %(name)s, %(type)s, %(required)s, %(description)s, %(options)s)
"""
_UPDATE_COLUMN = """
UPDATE data_table_columns
   SET position = %(position)s, name = %(name)s, type = %(type)s, required = %(required)s,
       description = %(description)s, options = %(options)s, updated_at = now()
 WHERE table_id = %(table_id)s AND id = %(id)s
"""

# Errors that mean "the data in the table does not allow this change".
_DATA_REFUSALS: tuple[type[psycopg.Error], ...] = (
    errors.IntegrityError,      # 23xxx: NOT NULL / CHECK violations
    errors.DataError,           # 22xxx: cast failures (invalid text / date / out of range)
    errors.CannotCoerce,        # 42846: no cast between the types
    errors.DatatypeMismatch,    # 42804
)
# Errors that mean "the physical table is not what the registry says".
_MISMATCHES: tuple[type[psycopg.Error], ...] = (
    errors.UndefinedTable, errors.UndefinedColumn, errors.UndefinedObject,
    errors.DuplicateTable, errors.DuplicateColumn, errors.DuplicateObject,
)

_HINTS = {
    "23502": "Fill the empty cells first (or keep the column optional).",
    "23514": "Some rows use a value that is not among the new options; change those cells first or keep the option.",
    "22P02": "Some stored values cannot be converted to the new type; fix or clear them first, or keep the type.",
    "22007": "Some stored values are not dates; fix or clear them first, or keep the type.",
    "22008": "Some stored values are not dates; fix or clear them first, or keep the type.",
    "22003": "Some stored values are out of range for the new type.",
    "42846": "Values of this type cannot be converted to the new type; clear the column first.",
    "42804": "Values of this type cannot be converted to the new type; clear the column first.",
}


def _conflict(exc: psycopg.Error, table_id: str, column_id: str | None) -> DefinitionConflictError:
    diag = exc.diag
    message = (diag.message_primary or str(exc)).strip() if diag else str(exc).strip()
    if diag is not None and diag.message_detail:
        message = f"{message} ({diag.message_detail.strip()})"
    return DefinitionConflictError(
        message,
        table_id=table_id,
        column_id=column_id or (diag.column_name if diag else None),
        sqlstate=exc.sqlstate,
        hint=_HINTS.get(exc.sqlstate or "", "Change the data first, or keep the current definition."),
    )


@dataclass(frozen=True)
class ColumnChange:
    """One column's path from the stored definition to the desired one."""

    before: TableColumn | None
    after: TableColumn | None

    @property
    def id(self) -> str:
        return (self.after or self.before).id  # type: ignore[union-attr]


def diff_columns(stored: list[TableColumn], desired: list[TableColumn]) -> list[ColumnChange]:
    """Columns matched by id: removed (``after=None``), added (``before=None``)
    and kept (both). Kept columns come first so a rename-and-readd never
    collides on the physical name."""
    before = {column.id: column for column in stored}
    after = {column.id: column for column in desired}
    changes = [ColumnChange(before[column_id], after.get(column_id)) for column_id in before]
    changes += [ColumnChange(None, column) for column in desired if column.id not in before]
    return changes


def ddl_for_change(table_name: str, change: ColumnChange) -> list[sql.Composed]:
    """The ALTER TABLE statements that take one column from ``before`` to ``after``."""
    before, after = change.before, change.after
    if before is None:
        assert after is not None
        return [table_ddl.add_column(table_name, after)]
    if after is None:
        return [table_ddl.drop_column(table_name, before.id)]

    statements: list[sql.Composed] = []
    type_changed = before.type != after.type
    was_select, is_select = before.type == "select", after.type == "select"
    options_changed = is_select and (not was_select or (before.options or []) != (after.options or []))

    # A CHECK on the old values would fight a type change, so it goes first.
    if was_select and (options_changed or not is_select):
        statements.append(table_ddl.drop_options_check(table_name, before.id))

    if type_changed and SQL_TYPES[before.type] != SQL_TYPES[after.type]:
        statements.append(table_ddl.alter_column_type(table_name, after.id, after.type))
    if type_changed and before.type == "checkbox":
        statements.append(table_ddl.drop_default(table_name, after.id))
    if type_changed and after.type == "checkbox":
        # An unset checkbox is false - not data, so NULLs become false.
        statements.append(table_ddl.fill_nulls_with_false(table_name, after.id))
        statements.append(table_ddl.set_default_false(table_name, after.id))

    not_null_before = before.required or before.type == "checkbox"
    not_null_after = after.required or after.type == "checkbox"
    if not_null_before != not_null_after:
        statements.append(table_ddl.set_required(table_name, after.id, not_null_after))

    if options_changed:
        statements.append(table_ddl.add_options_check(table_name, after))
    return statements


class PgDataTableRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- reading -------------------------------------------------------------
    def _load(self, conn: psycopg.Connection[dict[str, Any]], table_row: dict[str, Any]) -> DataTable:
        column_rows = conn.execute(_SELECT_COLUMNS, (table_row["id"],)).fetchall()
        return rows_to_data_table(table_row, column_rows)

    def list_tables(self) -> list[DataTable]:
        with self._db.connection() as conn:
            rows = conn.execute(f"SELECT {_TABLE_COLUMNS} FROM data_tables ORDER BY seq").fetchall()
            return [self._load(conn, row) for row in rows]

    def get_table(self, table_id: str) -> DataTable | None:
        with self._db.connection() as conn:
            row = conn.execute(f"SELECT {_TABLE_COLUMNS} FROM data_tables WHERE id = %s", (table_id,)).fetchone()
            return self._load(conn, row) if row else None

    def exists(self, table_id: str) -> bool:
        with self._db.connection() as conn:
            return conn.execute("SELECT 1 FROM data_tables WHERE id = %s", (table_id,)).fetchone() is not None

    def find_by_name(self, name: str) -> DataTable | None:
        with self._db.connection() as conn:
            row = conn.execute(
                f"SELECT {_TABLE_COLUMNS} FROM data_tables WHERE lower(name) = lower(%s)", (name.strip(),)
            ).fetchone()
            return self._load(conn, row) if row else None

    # -- writing -------------------------------------------------------------
    def create_table(self, table: DataTable) -> DataTable:
        table_name = table_name_for(table.id)
        try:
            with self._db.connection() as conn:
                conn.execute(
                    _INSERT_TABLE,
                    {"id": table.id, "name": table.name, "description": table.description,
                     "created_at": None, "updated_at": None},
                )
                for position, column in enumerate(table.columns):
                    conn.execute(_INSERT_COLUMN, table_column_to_row(table.id, position, column))
                try:
                    conn.execute(table_ddl.create_table(table_name, table.columns))
                except _MISMATCHES as exc:
                    raise TableSchemaMismatchError(table.id, _message(exc)) from exc
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "data_tables_name_unique":
                raise ValueError(f"A table named '{table.name}' already exists") from exc
            raise ValueError(f"Table '{table.id}' already exists") from exc
        except errors.CheckViolation as exc:
            # The rules reject these before we get here; the registry is the backstop.
            raise ValueError(f"Invalid table definition: {_message(exc)}") from exc
        return self.get_table(table.id) or table

    def update_table(self, table: DataTable) -> DataTable:
        table_name = table_name_for(table.id)
        with self._db.connection() as conn:
            stored_row = conn.execute(
                f"SELECT {_TABLE_COLUMNS} FROM data_tables WHERE id = %s FOR UPDATE", (table.id,)
            ).fetchone()
            if stored_row is None:
                raise KeyError(table.id)
            stored = self._load(conn, stored_row)

            current: str | None = None
            try:
                for change in diff_columns(stored.columns, table.columns):
                    current = change.id
                    for statement in ddl_for_change(table_name, change):
                        conn.execute(statement)
                current = None
                for change in diff_columns(stored.columns, table.columns):
                    if change.after is None:
                        conn.execute(
                            "DELETE FROM data_table_columns WHERE table_id = %s AND id = %s", (table.id, change.id)
                        )
                positions = {column.id: position for position, column in enumerate(table.columns)}
                for change in diff_columns(stored.columns, table.columns):
                    if change.after is None:
                        continue
                    params = table_column_to_row(table.id, positions[change.id], change.after)
                    if change.before is None:
                        conn.execute(_INSERT_COLUMN, params)
                    else:
                        conn.execute(_UPDATE_COLUMN, params)
                conn.execute(
                    "UPDATE data_tables SET name = %s, description = %s, updated_at = now() WHERE id = %s",
                    (table.name, table.description, table.id),
                )
                stamped = conn.execute(
                    f"SELECT {_TABLE_COLUMNS} FROM data_tables WHERE id = %s", (table.id,)
                ).fetchone()
                return self._load(conn, stamped)
            except errors.UniqueViolation as exc:
                if exc.diag.constraint_name == "data_tables_name_unique":
                    raise ValueError(f"A table named '{table.name}' already exists") from exc
                raise
            except _DATA_REFUSALS as exc:
                raise _conflict(exc, table.id, current) from exc
            except _MISMATCHES as exc:
                raise TableSchemaMismatchError(table.id, _message(exc), column_id=current) from exc

    def delete_table(self, table_id: str) -> bool:
        with self._db.connection() as conn:
            row = conn.execute("SELECT table_name FROM data_tables WHERE id = %s FOR UPDATE", (table_id,)).fetchone()
            if row is None:
                return False
            try:
                conn.execute(table_ddl.drop_table(row["table_name"]))
            except errors.UndefinedTable as exc:
                raise TableSchemaMismatchError(table_id, _message(exc)) from exc
            conn.execute("DELETE FROM data_tables WHERE id = %s", (table_id,))
            return True

    # -- consistency ---------------------------------------------------------
    def verify(self, table_id: str | None = None) -> list[str]:
        """Discrepancies between the registry and the catalog, one line each.
        Empty means every registered table exists with exactly the registered
        columns, types, NOT NULL flags and option constraints."""
        with self._db.connection() as conn:
            return verify_registry(conn, table_id)


# -- consistency check (shared with scripts/check_db.py) ----------------------

_LITERAL_RE = re.compile(r"'((?:[^']|'')*)'::text")


def _options_in_constraint(definition: str) -> list[str]:
    """Option values out of ``pg_get_constraintdef`` output, e.g.
    ``CHECK ((status = ANY (ARRAY['Open'::text, 'Closed'::text])))`` or
    ``CHECK ((status = 'Open'::text))`` for a single option."""
    return [match.group(1).replace("''", "'") for match in _LITERAL_RE.finditer(definition)]


def _message(exc: psycopg.Error) -> str:
    diag = exc.diag
    return (diag.message_primary if diag and diag.message_primary else str(exc)).strip()


def verify_registry(conn: psycopg.Connection[dict[str, Any]], table_id: str | None = None) -> list[str]:
    problems: list[str] = []
    if table_id is None:
        registered = conn.execute("SELECT id, table_name FROM data_tables ORDER BY seq").fetchall()
    else:
        registered = conn.execute("SELECT id, table_name FROM data_tables WHERE id = %s", (table_id,)).fetchall()

    physical = {r["table_name"] for r in conn.execute(table_ddl.CATALOG_TABLES, {"schema": USER_SCHEMA})}
    if table_id is None:
        for orphan in sorted(physical - {r["table_name"] for r in registered}):
            problems.append(f"table {USER_SCHEMA}.{orphan} exists but is not registered in data_tables")

    for table in registered:
        name = table["table_name"]
        prefix = f"table '{table['id']}' ({USER_SCHEMA}.{name})"
        if name not in physical:
            problems.append(f"{prefix}: registered but the table does not exist")
            continue
        columns = [row_to_table_column(r) for r in conn.execute(_SELECT_COLUMNS, (table["id"],))]
        catalog = {
            r["column_name"]: r
            for r in conn.execute(table_ddl.CATALOG_COLUMNS, {"schema": USER_SCHEMA, "table": name})
        }
        checks = {
            r["conname"]: r["definition"]
            for r in conn.execute(
                """
                SELECT conname, pg_get_constraintdef(oid) AS definition
                  FROM pg_constraint
                 WHERE contype = 'c' AND conrelid = %s::regclass
                """,
                (sql.Identifier(USER_SCHEMA, name).as_string(conn),),
            )
        }
        for system in SYSTEM_COLUMNS:
            if system not in catalog:
                problems.append(f"{prefix}: system column '{system}' is missing")
        for column in columns:
            actual = catalog.get(column.id)
            where = f"{prefix}: column '{column.id}'"
            if actual is None:
                problems.append(f"{where} is registered but does not exist")
                continue
            expected_type = SQL_TYPES.get(column.type)
            if expected_type is None:
                problems.append(f"{where} has unknown type '{column.type}'")
            elif actual["data_type"] != expected_type:
                problems.append(f"{where} is {actual['data_type']}, registry says {column.type} ({expected_type})")
            expected_not_null = column.required or column.type == "checkbox"
            if actual["not_null"] != expected_not_null:
                problems.append(
                    f"{where} is {'NOT NULL' if actual['not_null'] else 'nullable'}, "
                    f"registry says {'required' if expected_not_null else 'optional'}"
                )
            constraint = checks.get(options_constraint_name(column.id))
            if column.type == "select":
                if constraint is None:
                    problems.append(f"{where} has no options constraint")
                elif _options_in_constraint(constraint) != list(column.options or []):
                    problems.append(
                        f"{where} allows {_options_in_constraint(constraint)}, registry says {column.options}"
                    )
            elif constraint is not None:
                problems.append(f"{where} has an options constraint but is not a select column")
        registered_ids = {column.id for column in columns}
        for extra in catalog:
            if extra not in registered_ids and extra not in SYSTEM_COLUMNS:
                problems.append(f"{prefix}: column '{extra}' exists but is not registered")
    return problems
