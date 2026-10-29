"""``POST /sql/query``: results, the guard, and the database-level protection
that catches what a keyword scan cannot. Skipped unless ``DATABASE_URL`` is set."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_database

psycopg = pytest.importorskip("psycopg")

pytestmark = requires_database


def _run(client: TestClient, sql: str, **extra):
    return client.post("/sql/query", json={"sql": sql, **extra})


def _ok(client: TestClient, sql: str, **extra) -> dict:
    response = _run(client, sql, **extra)
    assert response.status_code == 200, response.text
    return response.json()


def _error(client: TestClient, sql: str, **extra) -> dict:
    response = _run(client, sql, **extra)
    assert response.status_code == 400, response.text
    return response.json()["detail"]


@pytest.fixture
def work_orders(client: TestClient) -> None:
    client.post("/tables", json={"name": "Work Orders", "columns": [
        {"id": "title", "name": "Title", "type": "text", "required": True},
        {"id": "cost", "name": "Cost", "type": "number"},
        {"id": "due", "name": "Due", "type": "date"},
    ]})
    for i in range(5):
        client.post("/tables/work-orders/rows", json={"data": {"title": f"job {i}", "cost": i * 10, "due": f"2026-01-0{i + 1}"}})


# -- results -------------------------------------------------------------------------

def test_select_returns_typed_columns_and_grid_rows(client: TestClient, work_orders):
    body = _ok(client, "SELECT id, title, cost, due, created_at FROM user_tables.work_orders ORDER BY id")
    assert body["kind"] == "read" and body["rowCount"] == 5 and body["truncated"] is False
    assert [c["name"] for c in body["columns"]] == ["id", "title", "cost", "due", "created_at"]
    assert [c["type"] for c in body["columns"]] == ["int8", "text", "numeric", "date", "timestamptz"]
    assert body["rows"][1][:4] == [2, "job 1", 10, "2026-01-02"] and body["rows"][1][4].endswith("Z")
    assert body["durationMs"] >= 0 and body["targets"] == []


def test_limit_and_truncation(client: TestClient, work_orders):
    body = _ok(client, "SELECT id FROM user_tables.work_orders ORDER BY id", limit=2)
    assert [r[0] for r in body["rows"]] == [1, 2] and body["truncated"] is True and body["rowCount"] == 2
    body = _ok(client, "SELECT id FROM user_tables.work_orders ORDER BY id DESC LIMIT 1")
    assert [r[0] for r in body["rows"]] == [5] and body["truncated"] is False
    assert _run(client, "SELECT 1", limit=0).status_code == 422
    assert _run(client, "SELECT 1", limit=5001).status_code == 422


def test_parameters_and_various_read_forms(client: TestClient, work_orders):
    body = _ok(client, "SELECT title FROM user_tables.work_orders WHERE cost > %s AND title LIKE %s ORDER BY id",
               params=[15, "job%"])
    assert [r[0] for r in body["rows"]] == ["job 2", "job 3", "job 4"]
    assert _ok(client, "WITH c AS (SELECT count(*) AS n FROM user_tables.work_orders) SELECT n FROM c")["rows"] == [[5]]
    assert _ok(client, "VALUES (1, 'a'), (2, 'b')")["rows"] == [[1, "a"], [2, "b"]]
    assert _ok(client, "TABLE forms")["rowCount"] == 1
    assert _ok(client, "select count(*) from information_schema.tables;")["rowCount"] == 1   # catalog is readable
    explain = _ok(client, "EXPLAIN SELECT * FROM forms")
    assert explain["kind"] == "explain" and explain["columns"][0]["name"] == "QUERY PLAN" and explain["rows"]
    assert _ok(client, "SHOW server_version")["rows"][0][0].startswith("1")
    assert _ok(client, "SELECT fields, NULL::text AS n, '{a,b}'::text[] AS arr FROM forms")["rows"][0] == [
        [{"id": "firstName", "type": "text", "label": "First Name", "required": True},
         {"id": "lastName", "type": "text", "label": "Last Name", "required": True},
         {"id": "email", "type": "text", "label": "Email", "required": False},
         {"id": "contactMethod", "type": "select", "label": "Preferred Contact Method", "required": False,
          "options": ["Email", "Phone", "Text"]}],
        None, ["a", "b"]]


def test_update_and_insert(client: TestClient, work_orders):
    body = _ok(client, "UPDATE user_tables.work_orders SET cost = cost * 2 WHERE cost >= 30")
    assert body["kind"] == "write" and body["rowCount"] == 2 and body["rows"] == [] and body["columns"] == []
    assert body["targets"] == ["user_tables.work_orders"]
    assert client.get("/tables/work-orders/rows/5").json()["data"]["cost"] == 80    # really committed

    body = _ok(client, "UPDATE user_tables.work_orders SET title = upper(title) WHERE id = 1 RETURNING id, title")
    assert body["rowCount"] == 1 and body["rows"] == [[1, "JOB 0"]]

    body = _ok(client, "INSERT INTO option_lists (id, name, options) VALUES ('sizes', 'Sizes', '{S,M}') RETURNING id")
    assert body["rowCount"] == 1 and body["rows"] == [["sizes"]] and body["targets"] == ["option_lists"]
    assert client.get("/option-lists/sizes").json()["options"] == ["S", "M"]
    upsert = _ok(client, "INSERT INTO option_lists (id, name, options) VALUES ('sizes', 'Sizes', '{L}') "
                         "ON CONFLICT (id) DO UPDATE SET options = EXCLUDED.options")
    assert upsert["rowCount"] == 1 and client.get("/option-lists/sizes").json()["options"] == ["L"]


# -- the guard (first layer) ------------------------------------------------------------

@pytest.mark.parametrize("sql, fragment", [
    ("DROP TABLE forms", "starting with 'DROP'"),
    ("DELETE FROM forms", "starting with 'DELETE'"),
    ("TRUNCATE forms", "starting with 'TRUNCATE'"),
    ("ALTER TABLE forms DROP COLUMN name", "starting with 'ALTER'"),
    ("SELECT 1; DELETE FROM forms", "one statement at a time"),
    ("WITH d AS (DELETE FROM forms RETURNING *) SELECT * FROM d", "DELETE is not allowed"),
    ("SELECT pg_sleep(30)", "pg_sleep is not allowed"),
    ("UPDATE schema_migrations SET checksum = NULL", "writes to schema_migrations are not allowed"),
])
def test_guard_refusals_are_400_and_change_nothing(client: TestClient, sql: str, fragment: str):
    detail = _error(client, sql)
    assert detail["message"] == "Statement not allowed" and fragment in detail["error"]
    assert client.get("/forms").json()[0]["id"] == "contact-form"
    assert client.get("/health").status_code == 200


# -- the database (second layer) ----------------------------------------------------------

def test_read_statements_cannot_write_even_through_functions(client: TestClient, work_orders):
    detail = _error(client, "SELECT nextval('forms_seq_seq')")
    assert detail["sqlstate"] == "25006" and "read-only" in detail["error"]
    # SELECT INTO would create a table; the subquery wrapper refuses it.
    detail = _error(client, "SELECT * INTO new_table FROM forms")
    assert detail["message"] == "SQL error" and "INTO" in detail["error"]
    assert _ok(client, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'new_table'")["rows"] == [[0]]


def test_sql_errors_carry_position_in_the_users_text(client: TestClient):
    detail = _error(client, "SELECT nope FROM forms")
    assert detail["sqlstate"] == "42703" and 'column "nope" does not exist' in detail["error"]
    assert detail["position"] == 8  # 1-based, in the statement as typed (the wrapper is subtracted)
    detail = _error(client, "SELECT * FORM forms")
    assert detail["sqlstate"] == "42601" and detail["position"] == 10  # at "FORM", 1-based
    detail = _error(client, "SELEC 1")
    assert detail["message"] == "Statement not allowed"  # never reaches PostgreSQL


def test_write_errors_roll_back_and_are_reported(client: TestClient):
    detail = _error(client, "UPDATE forms SET id = '' WHERE id = 'contact-form'")
    assert detail["sqlstate"] == "23514" and "forms_id_not_blank" in detail["error"]
    assert client.get("/forms/contact-form").status_code == 200
    detail = _error(client, "INSERT INTO option_lists (id, name, options) VALUES ('x', 'X', '{}')")
    assert detail["sqlstate"] == "23514"


def test_timeout(client: TestClient):
    detail = _error(client, "SELECT count(*) FROM generate_series(1, 100000000)", timeout_ms=100)
    assert detail["timedOut"] is True and detail["sqlstate"] == "57014"
    assert client.get("/health").status_code == 200  # the connection is fine afterwards


# -- catalog and rules ----------------------------------------------------------------------

def test_tables_listing_and_rules(client: TestClient, work_orders):
    tables = client.get("/sql/tables").json()["tables"]
    refs = [t["ref"] for t in tables]
    assert refs[0] == "work-orders"  # user tables first, by their slug
    assert tables[0]["userTable"] is True and tables[0]["name"] == "Work Orders" and tables[0]["columns"] == 6
    assert "public.forms" in refs and "public.schema_migrations" not in refs
    assert all(t["schema"] in ("public", "user_tables") for t in tables)
    rules = client.get("/sql/rules").json()
    assert "DROP" in rules["blockedKeywords"] and "UPDATE" in rules["writeStatements"] and rules["singleStatement"]
