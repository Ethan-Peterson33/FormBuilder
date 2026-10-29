"""Describe a PostgreSQL table from the catalog.

The row API (``/tables/{ref}/rows``) works on **any allowed table**, not only
the user tables it created: what it needs to know - column names, SQL types,
NOT NULL, defaults, identity / generated columns, the primary key and option
constraints - is read from ``information_schema`` and ``pg_constraint``. The
column *types* PostgreSQL reports are what the endpoints enforce.

Table references
----------------
* ``work-orders``            - a registered user table (slug -> ``user_tables.work_orders``)
* ``public.option_lists``    - any table as ``schema.table``

Only schemas in :data:`ALLOWED_SCHEMAS` are reachable, and never
:data:`HIDDEN_TABLES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from psycopg import sql

from services.table_ddl import USER_SCHEMA

# Schemas the row API may address as ``schema.table``.
ALLOWED_SCHEMAS: frozenset[str] = frozenset({"public", USER_SCHEMA})
# Tables that are never exposed, whatever the schema.
HIDDEN_TABLES: frozenset[str] = frozenset({"schema_migrations"})

# Columns lifted out of ``data`` into ``createdAt`` / ``updatedAt`` when present.
TIMESTAMP_COLUMNS: tuple[str, ...] = ("created_at", "updated_at")

# What the row API can do with a column, derived from its SQL type.
ColumnKind = Literal["text", "integer", "number", "boolean", "date", "timestamp", "json", "text_array", "uuid", "unknown"]

_KINDS: dict[str, ColumnKind] = {
    "text": "text", "character varying": "text", "character": "text", "citext": "text",
    "smallint": "integer", "integer": "integer", "bigint": "integer",
    "numeric": "number", "real": "number", "double precision": "number",
    "boolean": "boolean",
    "date": "date",
    "timestamp with time zone": "timestamp", "timestamp without time zone": "timestamp",
    "json": "json", "jsonb": "json",
    "uuid": "uuid",
}
_ARRAY_KINDS: dict[str, ColumnKind] = {"_text": "text_array", "_varchar": "text_array", "_bpchar": "text_array"}


def kind_of(data_type: str, udt_name: str) -> ColumnKind:
    if data_type == "ARRAY":
        return _ARRAY_KINDS.get(udt_name, "unknown")
    return _KINDS.get(data_type, "unknown")


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    sql_type: str                 # as information_schema reports it (``numeric``, ``text[]`` ...)
    kind: ColumnKind
    nullable: bool
    has_default: bool             # a DEFAULT, an identity or a generated expression
    writable: bool                # False for GENERATED ALWAYS identity / generated columns
    primary_key: bool = False
    options: list[str] | None = None   # values of a ``col IN (...)`` CHECK, when there is one

    @property
    def required_on_insert(self) -> bool:
        return not self.nullable and not self.has_default and self.writable

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.name, "sqlType": self.sql_type, "kind": self.kind, "nullable": self.nullable,
            "hasDefault": self.has_default, "writable": self.writable, "primaryKey": self.primary_key,
            "options": self.options,
        }


@dataclass(frozen=True)
class TableInfo:
    ref: str                      # how the request addressed it
    schema: str
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def identifier(self) -> sql.Identifier:
        return sql.Identifier(self.schema, self.name)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def primary_key(self) -> ColumnInfo | None:
        keys = [column for column in self.columns if column.primary_key]
        return keys[0] if len(keys) == 1 else None

    def column(self, name: str) -> ColumnInfo | None:
        return next((column for column in self.columns if column.name == name), None)

    @property
    def data_columns(self) -> list[ColumnInfo]:
        """Everything that appears in a row's ``data``: not the primary key,
        not the lifted timestamps."""
        pk = self.primary_key
        return [c for c in self.columns if c is not pk and c.name not in TIMESTAMP_COLUMNS]

    def as_dict(self) -> dict[str, Any]:
        pk = self.primary_key
        return {
            "ref": self.ref, "schema": self.schema, "table": self.name,
            "primaryKey": pk.name if pk else None,
            "columns": [column.as_dict() for column in self.columns],
        }


# -- resolving a reference -----------------------------------------------------

class TableNotAllowedError(LookupError):
    """The reference points outside the schemas the row API exposes."""


def split_reference(ref: str) -> tuple[str, str] | None:
    """``schema.table`` -> ``(schema, table)``; a slug -> ``None``."""
    if "." not in ref:
        return None
    schema, _, name = ref.partition(".")
    return schema, name


def resolve_reference(conn: psycopg.Connection[dict[str, Any]], ref: str) -> tuple[str, str] | None:
    """``(schema, table)`` for a reference, or ``None`` when nothing matches.
    Raises :class:`TableNotAllowedError` for schemas/tables that are off limits."""
    parts = split_reference(ref)
    if parts is None:
        row = conn.execute("SELECT table_name FROM data_tables WHERE id = %s", (ref,)).fetchone()
        return (USER_SCHEMA, row["table_name"]) if row else None
    schema, name = parts
    if schema not in ALLOWED_SCHEMAS or name in HIDDEN_TABLES:
        raise TableNotAllowedError(f"'{ref}' is not available through the row API")
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s AND table_type = 'BASE TABLE'",
        (schema, name),
    ).fetchone()
    return (schema, name) if exists else None


# -- reading the catalog -------------------------------------------------------

_COLUMNS_SQL = """
SELECT column_name, data_type, udt_name, is_nullable = 'YES' AS nullable,
       column_default IS NOT NULL AS has_column_default,
       is_identity = 'YES' AS is_identity, identity_generation,
       is_generated = 'ALWAYS' AS is_generated
  FROM information_schema.columns
 WHERE table_schema = %s AND table_name = %s
 ORDER BY ordinal_position
"""
_PRIMARY_KEY_SQL = """
SELECT a.attname AS column_name
  FROM pg_index i
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
 WHERE i.indrelid = %s::regclass AND i.indisprimary
"""
_CHECKS_SQL = """
SELECT pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE contype = 'c' AND conrelid = %s::regclass
"""
# ``CHECK ((status = ANY (ARRAY['Open'::text, 'Closed'::text])))`` or, for a
# single value, ``CHECK ((status = 'Open'::text))``. Quoted column names appear
# as ``"select"``.
_OPTIONS_CHECK_RE = re.compile(r'^CHECK \(\((?:"((?:[^"]|"")+)"|([a-z_][a-z0-9_]*)) = (.*)\)\)$', re.DOTALL)
_LITERAL_RE = re.compile(r"'((?:[^']|'')*)'::text")


def options_from_check(definition: str) -> tuple[str, list[str]] | None:
    """``(column, options)`` when a CHECK constraint is a plain ``col IN (...)``."""
    match = _OPTIONS_CHECK_RE.match(definition.strip())
    if not match:
        return None
    column = (match.group(1) or "").replace('""', '"') or match.group(2)
    rest = match.group(3)
    if not (rest.startswith("ANY (ARRAY[") or rest.startswith("'")):
        return None
    options = [m.group(1).replace("''", "'") for m in _LITERAL_RE.finditer(rest)]
    return (column, options) if options else None


def describe_table(conn: psycopg.Connection[dict[str, Any]], ref: str, schema: str, name: str) -> TableInfo:
    regclass = sql.Identifier(schema, name).as_string(conn)
    primary = {r["column_name"] for r in conn.execute(_PRIMARY_KEY_SQL, (regclass,))}
    options: dict[str, list[str]] = {}
    for row in conn.execute(_CHECKS_SQL, (regclass,)):
        parsed = options_from_check(row["definition"])
        if parsed:
            options[parsed[0]] = parsed[1]
    columns = []
    for row in conn.execute(_COLUMNS_SQL, (schema, name)):
        sql_type = row["data_type"]
        if sql_type == "ARRAY":
            sql_type = row["udt_name"].lstrip("_") + "[]"
        columns.append(
            ColumnInfo(
                name=row["column_name"],
                sql_type=sql_type,
                kind=kind_of(row["data_type"], row["udt_name"]),
                nullable=row["nullable"],
                has_default=row["has_column_default"] or row["is_identity"] or row["is_generated"],
                writable=not row["is_generated"] and not (row["is_identity"] and row["identity_generation"] == "ALWAYS"),
                primary_key=row["column_name"] in primary,
                options=options.get(row["column_name"]),
            )
        )
    return TableInfo(ref=ref, schema=schema, name=name, columns=columns)


def load_table(conn: psycopg.Connection[dict[str, Any]], ref: str) -> TableInfo | None:
    located = resolve_reference(conn, ref)
    if located is None:
        return None
    return describe_table(conn, ref, *located)
