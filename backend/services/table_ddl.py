"""DDL for user-defined data tables.

Every data table is a real PostgreSQL table in the ``user_tables`` schema::

    CREATE TABLE user_tables.work_orders (
        id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        created_at  timestamptz NOT NULL DEFAULT now(),
        updated_at  timestamptz NOT NULL DEFAULT now(),
        title       text        NOT NULL,
        status      text        CONSTRAINT status_options CHECK (status IN ('Open', 'Closed')),
        cost        numeric,
        due         date,
        urgent      boolean     NOT NULL DEFAULT false
    );

This module only *builds* statements (``psycopg.sql.Composed``); the
repository executes them in the same transaction as the registry update, so
the catalog and ``data_tables`` / ``data_table_columns`` never disagree
(DDL is transactional in PostgreSQL).

Safety: table and column names are validated by ``services.table_rules``
and the registry's CHECK constraints, and are *always* rendered through
``sql.Identifier`` (quoted), never interpolated. Option values go through
``sql.Literal``. No user text is ever concatenated into SQL.
"""

from __future__ import annotations

from psycopg import sql

from models.tables import TableColumn

# Schema that holds every user table (created by migration 0003).
USER_SCHEMA = "user_tables"

# Columns every user table has. Column ids may not use these names.
SYSTEM_COLUMNS: tuple[str, ...] = ("id", "created_at", "updated_at")

# API column type -> SQL type. ``numeric`` (not float) so values round-trip
# exactly; the API returns ints for whole numbers.
SQL_TYPES: dict[str, str] = {
    "text": "text",
    "textarea": "text",
    "number": "numeric",
    "date": "date",
    "select": "text",
    "checkbox": "boolean",
}


def table_name_for(table_id: str) -> str:
    """Physical table name for a table slug (``work-orders`` -> ``work_orders``).
    Must match the generated column ``data_tables.table_name``."""
    return table_id.replace("-", "_")


def qualified(table_name: str) -> sql.Identifier:
    return sql.Identifier(USER_SCHEMA, table_name)


def options_constraint_name(column_id: str) -> str:
    """CHECK constraint holding a select column's allowed values.
    Constraint names are per table, so the column id alone keeps it unique."""
    return f"{column_id}_options"


def options_check(column: TableColumn) -> sql.Composed:
    """``CONSTRAINT <id>_options CHECK (<id> IN ('a', 'b'))``."""
    values = sql.SQL(", ").join(sql.Literal(option) for option in column.options or [])
    return sql.SQL("CONSTRAINT {name} CHECK ({column} IN ({values}))").format(
        name=sql.Identifier(options_constraint_name(column.id)),
        column=sql.Identifier(column.id),
        values=values,
    )


def column_definition(column: TableColumn) -> sql.Composed:
    """``<id> <type> [NOT NULL] [DEFAULT ...] [CHECK ...]`` for CREATE TABLE / ADD COLUMN."""
    parts: list[sql.Composable] = [sql.Identifier(column.id), sql.SQL(SQL_TYPES[column.type])]
    if column.type == "checkbox":
        # A checkbox is always true or false; an unset box is false.
        parts.append(sql.SQL("NOT NULL DEFAULT false"))
    elif column.required:
        parts.append(sql.SQL("NOT NULL"))
    if column.type == "select":
        parts.append(options_check(column))
    return sql.SQL(" ").join(parts)


# -- tables -------------------------------------------------------------------

def create_table(table_name: str, columns: list[TableColumn]) -> sql.Composed:
    definitions: list[sql.Composable] = [
        sql.SQL("id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY"),
        sql.SQL("created_at timestamptz NOT NULL DEFAULT now()"),
        sql.SQL("updated_at timestamptz NOT NULL DEFAULT now()"),
        *(column_definition(column) for column in columns),
    ]
    return sql.SQL("CREATE TABLE {table} ({definitions})").format(
        table=qualified(table_name), definitions=sql.SQL(", ").join(definitions)
    )


def drop_table(table_name: str) -> sql.Composed:
    return sql.SQL("DROP TABLE {table}").format(table=qualified(table_name))


# -- columns ------------------------------------------------------------------

def add_column(table_name: str, column: TableColumn) -> sql.Composed:
    """Adding a NOT NULL column to a table with rows fails in PostgreSQL
    (no default to fill it); the repository reports that as a conflict."""
    return sql.SQL("ALTER TABLE {table} ADD COLUMN {definition}").format(
        table=qualified(table_name), definition=column_definition(column)
    )


def drop_column(table_name: str, column_id: str) -> sql.Composed:
    return sql.SQL("ALTER TABLE {table} DROP COLUMN {column}").format(
        table=qualified(table_name), column=sql.Identifier(column_id)
    )


def alter_column_type(table_name: str, column_id: str, new_type: str) -> sql.Composed:
    """Convert the stored values with a cast; PostgreSQL refuses (and the
    repository reports a conflict) when a value cannot be converted."""
    sql_type = sql.SQL(SQL_TYPES[new_type])
    return sql.SQL("ALTER TABLE {table} ALTER COLUMN {column} TYPE {type} USING {column}::{type}").format(
        table=qualified(table_name), column=sql.Identifier(column_id), type=sql_type
    )


def set_required(table_name: str, column_id: str, required: bool) -> sql.Composed:
    """``SET NOT NULL`` fails while the column still holds NULLs."""
    action = sql.SQL("SET NOT NULL") if required else sql.SQL("DROP NOT NULL")
    return sql.SQL("ALTER TABLE {table} ALTER COLUMN {column} {action}").format(
        table=qualified(table_name), column=sql.Identifier(column_id), action=action
    )


def set_default_false(table_name: str, column_id: str) -> sql.Composed:
    """A column that became a checkbox: new rows default to unchecked."""
    return sql.SQL("ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT false").format(
        table=qualified(table_name), column=sql.Identifier(column_id)
    )


def drop_default(table_name: str, column_id: str) -> sql.Composed:
    """A column that stops being a checkbox loses the ``false`` default."""
    return sql.SQL("ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT").format(
        table=qualified(table_name), column=sql.Identifier(column_id)
    )


def fill_nulls_with_false(table_name: str, column_id: str) -> sql.Composed:
    """Before a column becomes a checkbox (``NOT NULL``): an unset box is
    false, so this is not a data conversion."""
    return sql.SQL("UPDATE {table} SET {column} = false WHERE {column} IS NULL").format(
        table=qualified(table_name), column=sql.Identifier(column_id)
    )


def drop_options_check(table_name: str, column_id: str) -> sql.Composed:
    return sql.SQL("ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}").format(
        table=qualified(table_name), name=sql.Identifier(options_constraint_name(column_id))
    )


def add_options_check(table_name: str, column: TableColumn) -> sql.Composed:
    """Fails when a stored value is not among the new options."""
    return sql.SQL("ALTER TABLE {table} ADD {check}").format(
        table=qualified(table_name), check=options_check(column)
    )


# -- catalog ------------------------------------------------------------------

# Physical columns of a user table as PostgreSQL sees them, in attnum order.
# The repository compares this with the registry to detect drift.
CATALOG_COLUMNS = """
SELECT column_name, data_type, is_nullable = 'NO' AS not_null
  FROM information_schema.columns
 WHERE table_schema = %(schema)s AND table_name = %(table)s
 ORDER BY ordinal_position
"""

# Every user table in the schema (to spot tables missing from the registry).
CATALOG_TABLES = """
SELECT table_name FROM information_schema.tables
 WHERE table_schema = %(schema)s AND table_type = 'BASE TABLE'
 ORDER BY table_name
"""
