"""Data table definitions through the API: every change is checked against
the PostgreSQL catalog afterwards, so the physical table is proven to match
the definition the API reports. Skipped unless ``DATABASE_URL`` is set.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_database

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from repositories.pg_tables import verify_registry  # noqa: E402
from services.table_ddl import SQL_TYPES, SYSTEM_COLUMNS, USER_SCHEMA, table_name_for  # noqa: E402

pytestmark = requires_database

COLUMNS = [
    {"id": "title", "name": "Title", "type": "text", "required": True},
    {"id": "notes", "name": "Notes", "type": "textarea"},
    {"id": "status", "name": "Status", "type": "select", "options": ["Open", "Closed"]},
    {"id": "cost", "name": "Cost", "type": "number"},
    {"id": "due", "name": "Due", "type": "date"},
    {"id": "urgent", "name": "Urgent", "type": "checkbox"},
]


def _payload(**overrides) -> dict:
    return {"name": "Work Orders", "description": "Maintenance requests", "columns": COLUMNS, **overrides}


# -- catalog helpers (independent of the repository's own verify) ----------------

def _physical(url: str, table_id: str) -> dict | None:
    """Columns, NOT NULL flags and CHECK constraints of the real table, or None."""
    name = table_name_for(table_id)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        columns = conn.execute(
            """
            SELECT column_name, data_type, is_nullable = 'NO' AS not_null, column_default
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position
            """,
            (USER_SCHEMA, name),
        ).fetchall()
        if not columns:
            return None
        checks = conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE contype = 'c' AND conrelid = %s::regclass",
            (sql.Identifier(USER_SCHEMA, name).as_string(conn),),
        ).fetchall()
    return {
        "columns": {c["column_name"]: (c["data_type"], c["not_null"], c["column_default"]) for c in columns},
        "checks": {c["conname"]: c["def"] for c in checks},
    }


def assert_schema_matches(url: str, table: dict) -> None:
    """The physical table has exactly the definition's columns with the right
    types, NOT NULL flags, defaults and option constraints - and the
    repository's own verify agrees."""
    physical = _physical(url, table["id"])
    assert physical is not None, f"table {table['id']} does not exist"
    expected_columns = {
        "id": ("bigint", True), "created_at": ("timestamp with time zone", True),
        "updated_at": ("timestamp with time zone", True),
    }
    for column in table["columns"]:
        not_null = column.get("required", False) or column["type"] == "checkbox"
        expected_columns[column["id"]] = (SQL_TYPES[column["type"]], not_null)
    actual = {name: (data_type, not_null) for name, (data_type, not_null, _) in physical["columns"].items()}
    assert actual == expected_columns
    for column in table["columns"]:
        default = physical["columns"][column["id"]][2]
        assert (default == "false") == (column["type"] == "checkbox"), (column["id"], default)
    expected_checks = {f"{c['id']}_options" for c in table["columns"] if c["type"] == "select"}
    assert set(physical["checks"]) == expected_checks
    for column in table["columns"]:
        if column["type"] == "select":
            definition = physical["checks"][f"{column['id']}_options"]
            for option in column["options"]:
                assert f"'{option.replace(chr(39), chr(39) * 2)}'::text" in definition, (option, definition)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        assert verify_registry(conn, table["id"]) == []


def _insert(url: str, table_id: str, **cells) -> int:
    with psycopg.connect(url) as conn:
        statement = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) RETURNING id").format(
            t=sql.Identifier(USER_SCHEMA, table_name_for(table_id)),
            c=sql.SQL(", ").join(sql.Identifier(k) for k in cells),
            v=sql.SQL(", ").join(sql.Placeholder() for _ in cells),
        )
        return conn.execute(statement, list(cells.values())).fetchone()[0]


def _rows(url: str, table_id: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute(
            sql.SQL("SELECT * FROM {t} ORDER BY id").format(t=sql.Identifier(USER_SCHEMA, table_name_for(table_id)))
        ).fetchall()


@pytest.fixture
def table(client: TestClient, pg_url: str) -> dict:
    response = client.post("/tables", json=_payload())
    assert response.status_code == 201, response.text
    assert_schema_matches(pg_url, response.json())
    return response.json()


def _put(client: TestClient, table: dict, **changes):
    return client.put(f"/tables/{table['id']}", json={**table, **changes})


def _with_column(columns: list[dict], column_id: str, **changes) -> list[dict]:
    return [{**c, **changes} if c["id"] == column_id else c for c in columns]


# -- create / read -----------------------------------------------------------------

def test_create_registers_and_creates_a_real_table(client: TestClient, pg_url: str):
    response = client.post("/tables", json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == "work-orders" and body["name"] == "Work Orders"
    assert body["createdAt"] and body["updatedAt"]
    assert [c["id"] for c in body["columns"]] == [c["id"] for c in COLUMNS]
    assert body["columns"][0] == {"id": "title", "name": "Title", "type": "text", "required": True, "description": ""}
    assert body["columns"][2]["options"] == ["Open", "Closed"]
    assert_schema_matches(pg_url, body)
    assert client.get("/tables/work-orders").json() == body
    assert [t["id"] for t in client.get("/tables").json()] == ["work-orders"]
    assert client.get("/tables/work-orders/check").json() == {"tableId": "work-orders", "ok": True, "problems": []}


def test_table_can_start_without_columns(client: TestClient, pg_url: str):
    body = client.post("/tables", json={"name": "Empty"}).json()
    assert body["columns"] == [] and body["description"] == ""
    assert_schema_matches(pg_url, body)


def test_ids_are_generated_from_the_name_and_suffixed(client: TestClient, pg_url: str):
    first = client.post("/tables", json={"name": "Work Orders"}).json()
    second = client.post("/tables", json={"name": "Work Orders!"}).json()  # same slug, different name
    assert (first["id"], second["id"]) == ("work-orders", "work-orders-2")
    assert_schema_matches(pg_url, second)
    with psycopg.connect(pg_url) as conn:
        names = [r[0] for r in conn.execute("SELECT table_name FROM data_tables ORDER BY seq")]
    assert names == ["work_orders", "work_orders_2"]
    requested = client.post("/tables", json={"id": "My Table", "name": "Mine"}).json()
    assert requested["id"] == "my-table"


def test_get_missing_table_is_404(client: TestClient):
    assert client.get("/tables/nope").status_code == 404
    assert client.get("/tables/nope").json()["detail"] == "Table not found"
    assert client.get("/tables/nope/check").status_code == 404


def test_list_is_creation_order(client: TestClient):
    for name in ("Zeta", "Alpha", "Mid"):
        client.post("/tables", json={"name": name})
    assert [t["name"] for t in client.get("/tables").json()] == ["Zeta", "Alpha", "Mid"]


# -- conflicts and validation --------------------------------------------------------

def test_name_and_id_conflicts_are_409(client: TestClient, table: dict):
    assert client.post("/tables", json={"name": "work orders"}).status_code == 409  # case-insensitive
    response = client.post("/tables", json={"id": "work-orders", "name": "Other"})
    assert response.status_code == 409 and "ID" in response.json()["detail"]
    other = client.post("/tables", json={"name": "Other"}).json()
    assert _put(client, other, name="WORK ORDERS").status_code == 409
    assert _put(client, table, name="Work orders").status_code == 200  # own name, different case


@pytest.mark.parametrize(
    "columns, message",
    [
        ([{"id": "Title", "name": "T", "type": "text"}], "lower-case"),
        ([{"id": "id", "name": "T", "type": "text"}], "reserved"),
        ([{"id": "a", "name": "A", "type": "text"}, {"id": "a", "name": "B", "type": "text"}], "Duplicate column id"),
        ([{"id": "a", "name": "X", "type": "text"}, {"id": "b", "name": "x", "type": "text"}], "Duplicate column name"),
        ([{"id": "s", "name": "S", "type": "select"}], "at least one option"),
        ([{"id": "s", "name": "S", "type": "select", "options": ["a", "A"]}], "duplicate options"),
        ([{"id": "n", "name": "N", "type": "number", "options": ["1"]}], "only allowed on select"),
        ([{"id": "c", "name": "C", "type": "checkbox", "required": True}], "does not apply"),
    ],
)
def test_rule_problems_are_422_and_create_nothing(client: TestClient, pg_url: str, columns, message):
    response = client.post("/tables", json={"name": "Bad", "columns": columns})
    assert response.status_code == 422, response.text
    assert any(message in item["msg"] for item in response.json()["detail"]), response.json()
    assert client.get("/tables").json() == []
    assert _physical(pg_url, "bad") is None


def test_structural_problems_are_422(client: TestClient):
    assert client.post("/tables", json={"name": ""}).status_code == 422
    assert client.post("/tables", json={"name": "X", "columns": [{"id": "a", "name": "A", "type": "radio"}]}).status_code == 422
    assert client.post("/tables", json={"name": "X", "colour": "red"}).status_code == 422


def test_failed_rule_check_on_update_changes_nothing(client: TestClient, pg_url: str, table: dict):
    response = _put(client, table, columns=[{"id": "Bad Id", "name": "B", "type": "text"}])
    assert response.status_code == 422
    assert client.get("/tables/work-orders").json() == table
    assert_schema_matches(pg_url, table)


# -- update = ALTER TABLE ------------------------------------------------------------

def test_get_response_is_valid_put_input_and_metadata_only_changes_need_no_ddl(client: TestClient, pg_url: str, table: dict):
    renamed = _with_column(table["columns"], "title", name="Summary", description="One line")
    response = _put(client, table, name="Work Orders v2", description="", columns=renamed)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Work Orders v2" and body["columns"][0]["name"] == "Summary"
    assert body["columns"][0]["description"] == "One line"
    assert body["createdAt"] == table["createdAt"] and body["updatedAt"] >= table["updatedAt"]
    assert_schema_matches(pg_url, body)


def test_add_and_remove_columns(client: TestClient, pg_url: str, table: dict):
    row = _insert(pg_url, "work-orders", title="Leak", cost=10)
    added = table["columns"] + [{"id": "owner", "name": "Owner", "type": "text"}]
    body = _put(client, table, columns=added).json()
    assert [c["id"] for c in body["columns"]][-1] == "owner"
    assert_schema_matches(pg_url, body)
    assert _rows(pg_url, "work-orders")[0]["owner"] is None  # existing row kept

    without_cost = [c for c in body["columns"] if c["id"] != "cost"]
    body = _put(client, body, columns=without_cost).json()
    assert "cost" not in [c["id"] for c in body["columns"]]
    assert_schema_matches(pg_url, body)
    assert _rows(pg_url, "work-orders")[0]["id"] == row and "cost" not in _rows(pg_url, "work-orders")[0]


def test_reorder_columns(client: TestClient, pg_url: str, table: dict):
    reordered = list(reversed(table["columns"]))
    body = _put(client, table, columns=reordered).json()
    assert [c["id"] for c in body["columns"]] == [c["id"] for c in reordered]
    assert [c["id"] for c in client.get("/tables/work-orders").json()["columns"]] == [c["id"] for c in reordered]
    assert_schema_matches(pg_url, body)


def test_type_change_converts_values(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A", notes="42")
    body = _put(client, table, columns=_with_column(table["columns"], "notes", type="number")).json()
    assert body["columns"][1]["type"] == "number"
    assert_schema_matches(pg_url, body)
    assert _rows(pg_url, "work-orders")[0]["notes"] == 42


def test_type_change_refused_by_data_is_409_and_changes_nothing(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A", notes="not a number")
    response = _put(client, table, name="Renamed too", columns=_with_column(table["columns"], "notes", type="number"))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["message"] == "The table's data does not allow this change"
    assert detail["table"] == "work-orders" and detail["column"] == "notes"
    assert detail["sqlstate"] == "22P02" and "not a number" in detail["error"]
    assert "cannot be converted" in detail["hint"]
    # Whole update rolled back: name, registry and physical table untouched.
    assert client.get("/tables/work-orders").json() == table
    assert_schema_matches(pg_url, table)
    assert _rows(pg_url, "work-orders")[0]["notes"] == "not a number"


def test_required_toggle(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A")  # cost NULL
    response = _put(client, table, columns=_with_column(table["columns"], "cost", required=True))
    assert response.status_code == 409
    assert response.json()["detail"]["column"] == "cost" and response.json()["detail"]["sqlstate"] == "23502"
    assert "Fill the empty cells" in response.json()["detail"]["hint"]

    with psycopg.connect(pg_url) as conn:
        conn.execute(f"UPDATE {USER_SCHEMA}.work_orders SET cost = 0")
    body = _put(client, table, columns=_with_column(table["columns"], "cost", required=True)).json()
    assert body["columns"][3]["required"] is True
    assert_schema_matches(pg_url, body)

    body = _put(client, body, columns=_with_column(body["columns"], "title", required=False)).json()
    assert body["columns"][0]["required"] is False
    assert_schema_matches(pg_url, body)


def test_adding_a_required_column_to_a_populated_table_is_409(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A")
    added = table["columns"] + [{"id": "owner", "name": "Owner", "type": "text", "required": True}]
    response = _put(client, table, columns=added)
    assert response.status_code == 409 and response.json()["detail"]["column"] == "owner"
    assert_schema_matches(pg_url, table)
    # Optional first, then required once filled.
    added[-1]["required"] = False
    body = _put(client, table, columns=added).json()
    assert_schema_matches(pg_url, body)


def test_options_widen_and_narrow(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A", status="Closed")
    widened = _with_column(table["columns"], "status", options=["Open", "Closed", "On hold"])
    body = _put(client, table, columns=widened).json()
    assert body["columns"][2]["options"] == ["Open", "Closed", "On hold"]
    assert_schema_matches(pg_url, body)

    narrowed = _with_column(body["columns"], "status", options=["Open", "On hold"])
    response = _put(client, body, columns=narrowed)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["column"] == "status" and detail["sqlstate"] == "23514"
    assert "not among the new options" in detail["hint"]
    assert_schema_matches(pg_url, body)  # old constraint still in force

    with psycopg.connect(pg_url) as conn:
        conn.execute(f"UPDATE {USER_SCHEMA}.work_orders SET status = 'Open'")
    body = _put(client, body, columns=narrowed).json()
    assert_schema_matches(pg_url, body)
    with psycopg.connect(pg_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(f"UPDATE {USER_SCHEMA}.work_orders SET status = 'Closed'")


def test_options_with_quotes_round_trip(client: TestClient, pg_url: str):
    body = client.post("/tables", json={"name": "Q", "columns": [
        {"id": "kind", "name": "Kind", "type": "select", "options": ["Owner's", "Tenant"]},
    ]}).json()
    assert_schema_matches(pg_url, body)
    _insert(pg_url, "q", kind="Owner's")


def test_select_to_number_drops_the_options_check_first(client: TestClient, pg_url: str):
    """The CHECK on the text values would make the type change fail; it must go first."""
    body = client.post("/tables", json={"name": "Grades", "columns": [
        {"id": "grade", "name": "Grade", "type": "select", "options": ["1", "2", "3"]},
    ]}).json()
    _insert(pg_url, "grades", grade="2")
    body = _put(client, body, columns=[{"id": "grade", "name": "Grade", "type": "number"}]).json()
    assert body["columns"][0]["type"] == "number" and "options" not in body["columns"][0]
    assert_schema_matches(pg_url, body)
    assert _rows(pg_url, "grades")[0]["grade"] == 2


def test_select_to_text_and_back(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A", status="Open")
    as_text = _with_column(table["columns"], "status", type="text", options=None)
    body = _put(client, table, columns=as_text).json()
    assert body["columns"][2]["type"] == "text" and "options" not in body["columns"][2]
    assert_schema_matches(pg_url, body)
    with psycopg.connect(pg_url) as conn:
        conn.execute(f"UPDATE {USER_SCHEMA}.work_orders SET status = 'anything'")  # no constraint now

    back = _with_column(body["columns"], "status", type="select", options=["anything", "Open"])
    body = _put(client, body, columns=back).json()
    assert_schema_matches(pg_url, body)


def test_checkbox_conversions(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A")             # notes NULL
    _insert(pg_url, "work-orders", title="B", notes="true")
    body = _put(client, table, columns=_with_column(table["columns"], "notes", type="checkbox")).json()
    assert_schema_matches(pg_url, body)
    assert [r["notes"] for r in _rows(pg_url, "work-orders")] == [False, True]  # NULL -> false, not data loss

    body = _put(client, body, columns=_with_column(body["columns"], "urgent", type="text")).json()
    assert_schema_matches(pg_url, body)  # default and NOT NULL gone
    with psycopg.connect(pg_url) as conn:
        conn.execute(f"UPDATE {USER_SCHEMA}.work_orders SET urgent = NULL")


def test_text_to_date_refusal_names_the_column(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A", notes="soon")
    response = _put(client, table, columns=_with_column(table["columns"], "notes", type="date"))
    assert response.status_code == 409
    assert response.json()["detail"]["column"] == "notes" and "dates" in response.json()["detail"]["hint"]


def test_update_missing_table_is_404(client: TestClient):
    assert client.put("/tables/nope", json=_payload()).status_code == 404


# -- delete -------------------------------------------------------------------------

def test_delete_drops_the_table_and_frees_id_and_name(client: TestClient, pg_url: str, table: dict):
    _insert(pg_url, "work-orders", title="A")
    assert client.delete("/tables/work-orders").status_code == 204
    assert client.delete("/tables/work-orders").status_code == 404
    assert client.get("/tables/work-orders").status_code == 404
    assert _physical(pg_url, "work-orders") is None
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT count(*) FROM data_table_columns").fetchone()[0] == 0
    again = client.post("/tables", json=_payload())
    assert again.status_code == 201 and again.json()["id"] == "work-orders"
    assert_schema_matches(pg_url, again.json())


# -- drift: changes made outside the API ---------------------------------------------

def test_hand_made_changes_are_reported_not_hidden(client: TestClient, pg_url: str, table: dict):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {USER_SCHEMA}.work_orders DROP COLUMN cost")
        conn.execute(f"ALTER TABLE {USER_SCHEMA}.work_orders ALTER COLUMN notes TYPE integer USING NULL")
        conn.execute(f"ALTER TABLE {USER_SCHEMA}.work_orders ADD COLUMN extra text")
        conn.execute(f"ALTER TABLE {USER_SCHEMA}.work_orders DROP CONSTRAINT status_options")
        conn.execute(f"CREATE TABLE {USER_SCHEMA}.stray (id int)")

    check = client.get("/tables/work-orders/check").json()
    assert check["ok"] is False
    problems = check["problems"]
    assert any("'cost' is registered but does not exist" in p for p in problems)
    assert any("'notes' is integer, registry says textarea (text)" in p for p in problems)
    assert any("'extra' exists but is not registered" in p for p in problems)
    assert any("'status' has no options constraint" in p for p in problems)
    assert not any("stray" in p for p in problems)  # per-table check

    health = client.get("/health")
    assert health.status_code == 503
    error = health.json()["database"]["error"]
    assert error["message"] == "Data tables do not match their registry"
    assert any("stray exists but is not registered" in p for p in error["problems"])
    assert "check_db" in error["hint"]

    # The API still serves the definition it knows, and names the mismatch on write.
    assert client.get("/tables/work-orders").json() == table
    response = _put(client, table, columns=_with_column(table["columns"], "cost", required=True))
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["message"] == "Data table does not match its registry"
    assert detail["table"] == "work-orders" and detail["column"] == "cost" and "check_db" in detail["hint"]


def test_registered_table_whose_physical_table_vanished(client: TestClient, pg_url: str, table: dict):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(f"DROP TABLE {USER_SCHEMA}.work_orders")
    assert client.get("/tables/work-orders/check").json()["problems"] == [
        "table 'work-orders' (user_tables.work_orders): registered but the table does not exist"
    ]
    response = client.delete("/tables/work-orders")
    assert response.status_code == 500 and response.json()["detail"]["table"] == "work-orders"
    assert client.get("/tables/work-orders").status_code == 200  # registry row kept for repair
