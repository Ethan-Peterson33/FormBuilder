"""Data table foundation on a real database: the registry created by
``0003_data_tables``, the DDL built by ``services/table_ddl.py`` executed
against PostgreSQL, and the model <-> row mapping. Skipped unless
``DATABASE_URL`` is set.

Each test runs in a transaction on the session's scratch database that is
rolled back afterwards - including the CREATE / ALTER / DROP TABLE statements,
which are transactional in PostgreSQL.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from tests.conftest import requires_database

psycopg = pytest.importorskip("psycopg")
from psycopg import errors, sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from db.mapping import (  # noqa: E402
    data_table_to_row,
    row_to_table_row,
    rows_to_data_table,
    table_column_to_row,
)
from models.tables import DataTable, TableColumn  # noqa: E402
from services import table_ddl  # noqa: E402
from services.table_ddl import USER_SCHEMA, qualified, table_name_for  # noqa: E402

pytestmark = requires_database

WORK_ORDERS = DataTable(
    id="work-orders",
    name="Work Orders",
    description="Maintenance requests",
    columns=[
        TableColumn(id="title", name="Title", type="text", required=True),
        TableColumn(id="notes", name="Notes", type="textarea"),
        TableColumn(id="status", name="Status", type="select", options=["Open", "Closed"]),
        TableColumn(id="cost", name="Cost", type="number"),
        TableColumn(id="due", name="Due", type="date"),
        TableColumn(id="urgent", name="Urgent", type="checkbox"),
    ],
    created_at="2026-01-01T09:00:00.123Z",
    updated_at="2026-01-02T10:00:00.456Z",
)
PHYSICAL = table_name_for(WORK_ORDERS.id)

_INSERT_TABLE = """
INSERT INTO data_tables (id, name, description, created_at, updated_at)
VALUES (%(id)s, %(name)s, %(description)s, COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()))
"""
_INSERT_COLUMN = """
INSERT INTO data_table_columns (table_id, id, position, name, type, required, description, options)
VALUES (%(table_id)s, %(id)s, %(position)s, %(name)s, %(type)s, %(required)s, %(description)s, %(options)s)
"""


@pytest.fixture
def db(pg_url: str) -> Iterator[psycopg.Connection]:
    """Emptied scratch database; the test's own changes are rolled back."""
    with psycopg.connect(pg_url, row_factory=dict_row) as conn:
        with conn.transaction(force_rollback=True):
            yield conn


def _register(db, table: DataTable = WORK_ORDERS) -> None:
    """What the repository will do: registry rows + CREATE TABLE, together."""
    db.execute(_INSERT_TABLE, data_table_to_row(table))
    for position, column in enumerate(table.columns):
        db.execute(_INSERT_COLUMN, table_column_to_row(table.id, position, column))
    db.execute(table_ddl.create_table(table_name_for(table.id), table.columns))


def _insert(db, **cells) -> int:
    columns = sql.SQL(", ").join(sql.Identifier(c) for c in cells)
    values = sql.SQL(", ").join(sql.Placeholder() for _ in cells)
    statement = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) RETURNING id").format(
        t=qualified(PHYSICAL), c=columns, v=values
    )
    return db.execute(statement, list(cells.values())).fetchone()["id"]


def _select_all(db) -> list[dict]:
    return db.execute(sql.SQL("SELECT * FROM {t} ORDER BY id").format(t=qualified(PHYSICAL))).fetchall()


def _catalog_columns(db, table_name: str = PHYSICAL) -> dict[str, tuple[str, bool]]:
    rows = db.execute(table_ddl.CATALOG_COLUMNS, {"schema": USER_SCHEMA, "table": table_name}).fetchall()
    return {r["column_name"]: (r["data_type"], r["not_null"]) for r in rows}


def _violates(db, error: type[Exception], statement, params=None):
    with pytest.raises(error) as info:
        with db.transaction():
            db.execute(statement, params)
    return info.value


# -- registry -----------------------------------------------------------------------

def test_registry_round_trips_the_definition(db):
    _register(db)
    table_row = db.execute("SELECT * FROM data_tables WHERE id = 'work-orders'").fetchone()
    assert table_row["table_name"] == "work_orders"
    column_rows = db.execute(
        "SELECT * FROM data_table_columns WHERE table_id = 'work-orders' ORDER BY position"
    ).fetchall()
    assert rows_to_data_table(table_row, column_rows) == WORK_ORDERS


def test_registry_defaults_match_the_models(db):
    db.execute("INSERT INTO data_tables (id, name) VALUES ('t', 'T')")
    db.execute("INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', 'c', 0, 'C', 'text')")
    table = rows_to_data_table(
        db.execute("SELECT * FROM data_tables").fetchone(), db.execute("SELECT * FROM data_table_columns").fetchall()
    )
    assert (table.description, table.created_at is not None) == ("", True)
    assert table.columns == [TableColumn(id="c", name="C", type="text")]


@pytest.mark.parametrize(
    "sql_text, params",
    [
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("", "T")),
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("Work Orders", "T")),   # not a slug
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("work_orders", "T")),   # '_' is for the physical name
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("a" * 64, "T")),
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("t", "   ")),
        ("INSERT INTO data_tables (id, name) VALUES (%s, %s)", ("t", "x" * 121)),
    ],
)
def test_table_registry_checks(db, sql_text, params):
    _violates(db, errors.CheckViolation, sql_text, params)


@pytest.mark.parametrize(
    "column_id, name, type_",
    [
        ("Title", "T", "text"),        # upper case
        ("9x", "T", "text"),           # digit first
        ("has-dash", "T", "text"),
        ("a" * 49, "T", "text"),
        ("c", "  ", "text"),           # blank name
        ("c", "T", " "),               # blank type
    ],
)
def test_column_registry_checks(db, column_id, name, type_):
    db.execute("INSERT INTO data_tables (id, name) VALUES ('t', 'T')")
    _violates(
        db, errors.CheckViolation,
        "INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', %s, 0, %s, %s)",
        (column_id, name, type_),
    )


def test_physical_names_cannot_collide(db):
    """`work-orders` and `work_orders` are not both valid slugs, and two slugs
    never map to the same physical name."""
    db.execute("INSERT INTO data_tables (id, name) VALUES ('a-b', 'AB')")
    db.execute("INSERT INTO data_tables (id, name) VALUES ('a-b-2', 'AB2')")
    assert {r["table_name"] for r in db.execute("SELECT table_name FROM data_tables")} == {"a_b", "a_b_2"}


def test_table_names_unique_case_insensitively_and_freed_by_delete(db):
    db.execute("INSERT INTO data_tables (id, name) VALUES ('a', 'Assets')")
    exc = _violates(db, errors.UniqueViolation, "INSERT INTO data_tables (id, name) VALUES ('b', 'ASSETS')")
    assert exc.diag.constraint_name == "data_tables_name_unique"  # the repository maps this to 409
    db.execute("DELETE FROM data_tables WHERE id = 'a'")
    db.execute("INSERT INTO data_tables (id, name) VALUES ('b', 'ASSETS')")
    db.execute("INSERT INTO data_tables (id, name) VALUES ('a', 'Other')")  # hard delete: slug free again


def test_column_names_unique_per_table_and_columns_cascade(db):
    db.execute("INSERT INTO data_tables (id, name) VALUES ('t', 'T'), ('u', 'U')")
    db.execute("INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', 'a', 0, 'Title', 'text')")
    db.execute("INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('u', 'a', 0, 'Title', 'text')")
    _violates(
        db, errors.UniqueViolation,
        "INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', 'b', 1, 'TITLE', 'text')",
    )
    _violates(
        db, errors.UniqueViolation,
        "INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', 'a', 1, 'Other', 'text')",
    )
    db.execute("DELETE FROM data_tables WHERE id = 't'")
    assert db.execute("SELECT count(*) AS n FROM data_table_columns").fetchone()["n"] == 1


def test_positions_can_be_swapped_in_one_transaction(db):
    db.execute("INSERT INTO data_tables (id, name) VALUES ('t', 'T')")
    db.execute("INSERT INTO data_table_columns (table_id, id, position, name, type) VALUES ('t', 'a', 0, 'A', 'text'), ('t', 'b', 1, 'B', 'text')")
    with db.transaction():
        db.execute("UPDATE data_table_columns SET position = 1 WHERE id = 'a'")
        db.execute("UPDATE data_table_columns SET position = 0 WHERE id = 'b'")
    order = [r["id"] for r in db.execute("SELECT id FROM data_table_columns ORDER BY position")]
    assert order == ["b", "a"]


# -- the physical table --------------------------------------------------------------

def test_create_table_makes_a_real_table_with_the_right_types(db):
    _register(db)
    assert _catalog_columns(db) == {
        "id": ("bigint", True),
        "created_at": ("timestamp with time zone", True),
        "updated_at": ("timestamp with time zone", True),
        "title": ("text", True),
        "notes": ("text", False),
        "status": ("text", False),
        "cost": ("numeric", False),
        "due": ("date", False),
        "urgent": ("boolean", True),
    }
    tables = db.execute(table_ddl.CATALOG_TABLES, {"schema": USER_SCHEMA}).fetchall()
    assert [r["table_name"] for r in tables] == ["work_orders"]


def test_rows_round_trip_with_api_types(db):
    _register(db)
    row_id = _insert(db, title="Leak", status="Open", cost=12.5, due="2026-02-01", urgent=True)
    _insert(db, title="Whole", cost=3)
    column_ids = [c.id for c in WORK_ORDERS.columns]
    first, second = (row_to_table_row(r, "work-orders", column_ids) for r in _select_all(db))
    assert first.id == row_id and first.table_id == "work-orders"
    assert first.data == {"title": "Leak", "status": "Open", "cost": 12.5, "due": "2026-02-01", "urgent": True}
    assert second.data == {"title": "Whole", "cost": 3, "urgent": False}  # NULLs omitted, checkbox defaults
    assert isinstance(second.data["cost"], int) and isinstance(first.data["cost"], float)
    assert first.created_at and first.created_at == first.updated_at


def test_constraints_are_real(db):
    _register(db)
    _violates(db, errors.NotNullViolation, sql.SQL("INSERT INTO {t} (notes) VALUES ('no title')").format(t=qualified(PHYSICAL)))
    exc = _violates(
        db, errors.CheckViolation,
        sql.SQL("INSERT INTO {t} (title, status) VALUES ('x', 'Maybe')").format(t=qualified(PHYSICAL)),
    )
    assert exc.diag.constraint_name == "status_options"
    _violates(db, errors.InvalidTextRepresentation,
              sql.SQL("INSERT INTO {t} (title, cost) VALUES ('x', 'lots')").format(t=qualified(PHYSICAL)))
    _violates(db, errors.InvalidDatetimeFormat,
              sql.SQL("INSERT INTO {t} (title, due) VALUES ('x', 'soon')").format(t=qualified(PHYSICAL)))


def test_add_and_drop_column(db):
    _register(db)
    _insert(db, title="A", cost=1)
    db.execute(table_ddl.add_column(PHYSICAL, TableColumn(id="owner", name="Owner", type="text")))
    db.execute(table_ddl.drop_column(PHYSICAL, "cost"))
    columns = _catalog_columns(db)
    assert "owner" in columns and "cost" not in columns
    assert _select_all(db)[0]["owner"] is None


def test_adding_a_required_column_to_a_table_with_rows_is_refused_by_postgres(db):
    _register(db)
    _insert(db, title="A")
    _violates(db, errors.NotNullViolation,
              table_ddl.add_column(PHYSICAL, TableColumn(id="owner", name="Owner", type="text", required=True)))
    # Without rows it is fine.
    db.execute(sql.SQL("DELETE FROM {t}").format(t=qualified(PHYSICAL)))
    db.execute(table_ddl.add_column(PHYSICAL, TableColumn(id="owner", name="Owner", type="text", required=True)))
    assert _catalog_columns(db)["owner"] == ("text", True)


def test_type_change_converts_values_or_is_refused(db):
    _register(db)
    _insert(db, title="A", notes="42")
    db.execute(table_ddl.alter_column_type(PHYSICAL, "notes", "number"))
    assert _catalog_columns(db)["notes"] == ("numeric", False)
    assert _select_all(db)[0]["notes"] == 42
    db.execute(table_ddl.alter_column_type(PHYSICAL, "notes", "text"))
    _insert(db, title="B", notes="not a number")
    exc = _violates(db, errors.InvalidTextRepresentation, table_ddl.alter_column_type(PHYSICAL, "notes", "number"))
    assert "not a number" in str(exc)


def test_required_toggle_is_refused_while_nulls_exist(db):
    _register(db)
    _insert(db, title="A")  # cost is NULL
    _violates(db, errors.NotNullViolation, table_ddl.set_required(PHYSICAL, "cost", True))
    db.execute(sql.SQL("UPDATE {t} SET cost = 0").format(t=qualified(PHYSICAL)))
    db.execute(table_ddl.set_required(PHYSICAL, "cost", True))
    assert _catalog_columns(db)["cost"] == ("numeric", True)
    db.execute(table_ddl.set_required(PHYSICAL, "cost", False))
    assert _catalog_columns(db)["cost"] == ("numeric", False)


def test_options_change_is_refused_while_a_removed_option_is_in_use(db):
    _register(db)
    _insert(db, title="A", status="Closed")
    narrowed = TableColumn(id="status", name="Status", type="select", options=["Open", "On hold"])
    with pytest.raises(errors.CheckViolation):
        with db.transaction():
            db.execute(table_ddl.drop_options_check(PHYSICAL, "status"))
            db.execute(table_ddl.add_options_check(PHYSICAL, narrowed))
    # The failed transaction left the old constraint in place.
    _violates(db, errors.CheckViolation,
              sql.SQL("INSERT INTO {t} (title, status) VALUES ('x', 'On hold')").format(t=qualified(PHYSICAL)))
    widened = TableColumn(id="status", name="Status", type="select", options=["Open", "Closed", "On hold"])
    with db.transaction():
        db.execute(table_ddl.drop_options_check(PHYSICAL, "status"))
        db.execute(table_ddl.add_options_check(PHYSICAL, widened))
    _insert(db, title="x", status="On hold")


def test_drop_table_and_registry_go_together(db):
    _register(db)
    with db.transaction():
        db.execute(table_ddl.drop_table(PHYSICAL))
        db.execute("DELETE FROM data_tables WHERE id = 'work-orders'")
    assert db.execute(table_ddl.CATALOG_TABLES, {"schema": USER_SCHEMA}).fetchall() == []
    assert db.execute("SELECT count(*) AS n FROM data_table_columns").fetchone()["n"] == 0


def test_sql_keywords_and_hostile_names_are_harmless(db):
    """Identifiers are always quoted: keywords work, injection attempts just
    become odd names (the rules reject them before this point anyway)."""
    keywords = DataTable(id="kw", name="Keywords", columns=[
        TableColumn(id="select", name="S", type="text"), TableColumn(id="order", name="O", type="number"),
    ])
    _register(db, keywords)
    db.execute(sql.SQL("INSERT INTO {t} ({a}, {b}) VALUES ('x', 1)").format(
        t=qualified("kw"), a=sql.Identifier("select"), b=sql.Identifier("order")))
    db.execute(table_ddl.create_table('x"; DROP TABLE forms; --', []))
    assert db.execute("SELECT count(*) AS n FROM forms").fetchone()["n"] >= 0  # still there
    names = [r["table_name"] for r in db.execute(table_ddl.CATALOG_TABLES, {"schema": USER_SCHEMA})]
    assert 'x"; DROP TABLE forms; --' in names


def test_user_tables_cannot_shadow_application_tables(db):
    """A user table called `forms` lives in user_tables, not public."""
    db.execute(table_ddl.create_table("forms", [TableColumn(id="x", name="X", type="text")]))
    assert _catalog_columns(db, "forms") == {
        "id": ("bigint", True), "created_at": ("timestamp with time zone", True),
        "updated_at": ("timestamp with time zone", True), "x": ("text", False),
    }
    assert "fields" in {r["column_name"] for r in db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'forms'")}
