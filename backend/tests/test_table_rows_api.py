"""Row endpoints: user tables and other allowed tables, type enforcement from
the PostgreSQL column types, filtering, sorting and paging. Skipped unless
``DATABASE_URL`` is set.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_database

psycopg = pytest.importorskip("psycopg")

pytestmark = requires_database

COLUMNS = [
    {"id": "title", "name": "Title", "type": "text", "required": True},
    {"id": "notes", "name": "Notes", "type": "textarea"},
    {"id": "status", "name": "Status", "type": "select", "options": ["Open", "Closed"]},
    {"id": "cost", "name": "Cost", "type": "number"},
    {"id": "due", "name": "Due", "type": "date"},
    {"id": "urgent", "name": "Urgent", "type": "checkbox"},
]


@pytest.fixture
def table(client: TestClient) -> dict:
    response = client.post("/tables", json={"name": "Work Orders", "columns": COLUMNS})
    assert response.status_code == 201, response.text
    return response.json()


def _post(client: TestClient, ref: str, data: dict):
    return client.post(f"/tables/{ref}/rows", json={"data": data})


def _seed(client: TestClient, ref: str, rows: list[dict]) -> list[int]:
    ids = []
    for data in rows:
        response = _post(client, ref, data)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


ROWS = [
    {"title": "Leaking tap", "status": "Open", "cost": 12.5, "due": "2026-02-01", "urgent": True},
    {"title": "Broken window", "status": "Closed", "cost": 300, "due": "2026-01-15"},
    {"title": "Paint hallway", "notes": "two coats", "cost": 80},
    {"title": "Door bell", "status": "Open"},
]


# -- schema --------------------------------------------------------------------------

def test_schema_describes_the_table_from_the_catalog(client: TestClient, table: dict):
    body = client.get("/tables/work-orders/schema").json()
    assert body["ref"] == "work-orders" and body["schema"] == "user_tables" and body["table"] == "work_orders"
    assert body["primaryKey"] == "id"
    by_id = {c["id"]: c for c in body["columns"]}
    assert by_id["id"] == {"id": "id", "sqlType": "bigint", "kind": "integer", "nullable": False, "hasDefault": True,
                           "writable": False, "primaryKey": True, "options": None}
    assert by_id["title"]["nullable"] is False and by_id["title"]["writable"] is True
    assert by_id["status"]["options"] == ["Open", "Closed"]
    assert by_id["cost"]["kind"] == "number" and by_id["due"]["kind"] == "date"
    assert by_id["urgent"] == {"id": "urgent", "sqlType": "boolean", "kind": "boolean", "nullable": False,
                               "hasDefault": True, "writable": True, "primaryKey": False, "options": None}
    assert by_id["created_at"]["kind"] == "timestamp"


def test_other_tables_are_addressed_as_schema_table(client: TestClient):
    body = client.get("/tables/public.option_lists/schema").json()
    assert body["primaryKey"] == "id"
    by_id = {c["id"]: c for c in body["columns"]}
    assert by_id["options"] == {"id": "options", "sqlType": "text[]", "kind": "text_array", "nullable": False,
                                "hasDefault": False, "writable": True, "primaryKey": False, "options": None}
    forms = {c["id"]: c for c in client.get("/tables/public.forms/schema").json()["columns"]}
    assert forms["seq"]["writable"] is False and forms["fields"]["kind"] == "json"
    assert forms["id"]["primaryKey"] and forms["id"]["writable"]
    registry = {c["id"]: c for c in client.get("/tables/public.data_tables/schema").json()["columns"]}
    assert registry["table_name"]["writable"] is False  # generated column


@pytest.mark.parametrize("ref", ["nope", "public.nope", "public.schema_migrations", "information_schema.tables",
                                 "pg_catalog.pg_class", "user_tables.nope"])
def test_unknown_or_hidden_tables_are_404(client: TestClient, ref: str):
    for path in (f"/tables/{ref}/schema", f"/tables/{ref}/rows", f"/tables/{ref}/rows/1"):
        response = client.get(path)
        assert response.status_code == 404, (path, response.text)
    assert _post(client, ref, {}).status_code == 404


# -- create / read / update / delete -------------------------------------------------

def test_create_and_read_rows_with_typed_cells(client: TestClient, table: dict):
    response = _post(client, "work-orders", ROWS[0])
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["id"] == 1 and row["tableId"] == "work-orders"
    assert row["data"] == {"title": "Leaking tap", "status": "Open", "cost": 12.5, "due": "2026-02-01", "urgent": True}
    assert row["createdAt"].endswith("Z") and row["createdAt"] == row["updatedAt"]
    assert client.get("/tables/work-orders/rows/1").json() == row

    sparse = _post(client, "work-orders", {"title": "Door bell", "cost": "3", "notes": ""}).json()
    assert sparse["data"] == {"title": "Door bell", "cost": 3, "urgent": False}  # blanks omitted, checkbox default
    assert isinstance(sparse["data"]["cost"], int)


def test_cells_are_enforced_as_the_postgres_column_types(client: TestClient, table: dict):
    response = _post(client, "work-orders", {
        "title": ["not", "text"], "cost": "lots", "due": "soon", "urgent": "maybe", "status": "Pending",
        "bogus": 1, "id": 99,
    })
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert body["message"] == "Row failed validation"
    assert body["errors"] == {
        "title": "must be text",
        "cost": "must be a number",
        "due": "must be a date in YYYY-MM-DD format",
        "urgent": "must be true or false",
        "status": "must be one of: Open, Closed",
        "bogus": "is not a column of this table",
        "id": "is generated by the database and cannot be set",
    }
    assert _post(client, "work-orders", {"due": "2026-02-30"}).json()["detail"]["errors"]["due"].startswith("must be a valid date")
    assert client.get("/tables/work-orders/rows").json()["total"] == 0


def test_required_columns_on_insert(client: TestClient, table: dict):
    response = _post(client, "work-orders", {"notes": "no title"})
    assert response.status_code == 422 and response.json()["detail"]["errors"] == {"title": "is required"}
    assert _post(client, "work-orders", {"title": None}).json()["detail"]["errors"] == {"title": "is required"}
    # Blank text is '' for a NOT NULL text column (the only empty it can hold), NULL otherwise.
    row = _post(client, "work-orders", {"title": "  ", "notes": "  "}).json()
    assert row["data"] == {"title": "", "urgent": False}
    # created_at has a default but may be set explicitly (imports).
    row = _post(client, "work-orders", {"title": "old", "created_at": "2020-01-01T00:00:00Z"}).json()
    assert row["createdAt"] == "2020-01-01T00:00:00.000Z"


def test_patch_changes_only_named_cells_and_null_clears(client: TestClient, table: dict):
    row_id = _seed(client, "work-orders", [ROWS[0]])[0]
    response = client.patch(f"/tables/work-orders/rows/{row_id}", json={"data": {"status": "Closed", "cost": None}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == {"title": "Leaking tap", "status": "Closed", "due": "2026-02-01", "urgent": True}
    assert body["updatedAt"] > body["createdAt"]

    assert client.patch(f"/tables/work-orders/rows/{row_id}", json={"data": {}}).json()["data"] == body["data"]
    response = client.patch(f"/tables/work-orders/rows/{row_id}", json={"data": {"title": None, "cost": "x"}})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == {"title": "cannot be cleared (required)", "cost": "must be a number"}
    assert client.get(f"/tables/work-orders/rows/{row_id}").json()["data"] == body["data"]  # nothing changed

    assert client.patch("/tables/work-orders/rows/999", json={"data": {"title": "x"}}).status_code == 404
    assert client.patch("/tables/work-orders/rows/abc", json={"data": {"title": "x"}}).status_code == 404


def test_delete_row(client: TestClient, table: dict):
    ids = _seed(client, "work-orders", ROWS[:2])
    assert client.delete(f"/tables/work-orders/rows/{ids[0]}").status_code == 204
    assert client.delete(f"/tables/work-orders/rows/{ids[0]}").status_code == 404
    assert client.get(f"/tables/work-orders/rows/{ids[0]}").status_code == 404
    assert [r["id"] for r in client.get("/tables/work-orders/rows").json()["items"]] == [ids[1]]


def test_constraint_violations_that_reach_the_database_are_409(client: TestClient, table: dict):
    """A hand-made constraint the API does not know about: the real message comes back."""
    with psycopg.connect(client.app.state.settings.database_url, autocommit=True) as conn:
        conn.execute("ALTER TABLE user_tables.work_orders ADD CONSTRAINT cost_positive CHECK (cost >= 0)")
    response = _post(client, "work-orders", {"title": "x", "cost": -1})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["message"] == "The row was refused by a table constraint"
    assert detail["table"] == "work-orders" and detail["constraint"] == "cost_positive" and detail["sqlstate"] == "23514"
    assert "cost_positive" in detail["error"] and detail["hint"]


# -- other tables -----------------------------------------------------------------------

def test_rows_of_application_tables(client: TestClient):
    created = _post(client, "public.option_lists", {
        "id": "sizes", "name": "Sizes", "options": ["S", "M"], "description": "",
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "sizes" and body["tableId"] == "public.option_lists"
    assert body["data"] == {"name": "Sizes", "description": "", "options": ["S", "M"]}
    assert body["createdAt"] and body["updatedAt"]
    # The API the table belongs to sees the same row.
    assert client.get("/option-lists/sizes").json()["options"] == ["S", "M"]

    # Text primary key in the path; array and unique constraints enforced.
    assert client.get("/tables/public.option_lists/rows/sizes").json()["data"]["name"] == "Sizes"
    patched = client.patch("/tables/public.option_lists/rows/sizes", json={"data": {"options": ["S", "M", "L"]}})
    assert patched.status_code == 200 and patched.json()["data"]["options"] == ["S", "M", "L"]
    bad = _post(client, "public.option_lists", {"id": "x", "name": "X", "options": "S,M"})
    assert bad.status_code == 422 and bad.json()["detail"]["errors"] == {"options": "must be a list of text values"}
    duplicate = _post(client, "public.option_lists", {"id": "sizes-2", "name": "SIZES", "options": ["x"]})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["constraint"] == "option_lists_name_unique"
    assert duplicate.json()["detail"]["sqlstate"] == "23505"
    assert client.delete("/tables/public.option_lists/rows/sizes").status_code == 204
    assert client.get("/option-lists/sizes").status_code == 404


def test_json_columns_and_identity_columns(client: TestClient):
    fields = [{"id": "a", "type": "text", "label": "A"}]
    created = _post(client, "public.forms", {"id": "via-rows", "name": "Via rows", "fields": fields})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["fields"] == fields and created.json()["data"]["description"] == ""
    assert client.get("/forms/via-rows").json()["fields"][0]["id"] == "a"

    response = _post(client, "public.forms", {"id": "x", "name": "X", "seq": 5})
    assert response.status_code == 422 and response.json()["detail"]["errors"] == {"seq": "is generated by the database and cannot be set"}
    assert _post(client, "public.forms", {"name": "no id"}).json()["detail"]["errors"] == {"id": "is required"}

    # A blank id violates the forms CHECK constraint: 409 with the constraint name.
    blank = _post(client, "public.forms", {"id": " ", "name": "Blank"})
    assert blank.status_code == 409 and blank.json()["detail"]["constraint"] == "forms_id_not_blank"


def test_foreign_keys_and_timestamps(client: TestClient):
    response = _post(client, "public.submissions", {
        "id": "sub-1", "form_id": "contact-form", "form_name": "Contact Form",
        "data": {"firstName": "Ada"}, "submitted_at": "2026-01-01T10:00:00Z", "hidden_fields": [],
    })
    assert response.status_code == 201, response.text
    assert response.json()["data"]["submitted_at"] == "2026-01-01T10:00:00.000Z"
    assert response.json()["data"]["source"] == "web"  # database default applied
    assert client.get("/submissions/sub-1").json()["data"] == {"firstName": "Ada"}

    orphan = _post(client, "public.submissions", {"id": "sub-2", "form_id": "gone", "form_name": "Gone"})
    assert orphan.status_code == 409 and orphan.json()["detail"]["sqlstate"] == "23503"
    bad_time = _post(client, "public.submissions", {"id": "sub-3", "form_id": "contact-form", "form_name": "C",
                                                    "submitted_at": "yesterday"})
    assert bad_time.json()["detail"]["errors"] == {"submitted_at": "must be an ISO-8601 timestamp"}
    # Deleting the form that a submission references is refused by the FK.
    blocked = client.delete("/tables/public.forms/rows/contact-form")
    assert blocked.status_code == 409 and blocked.json()["detail"]["sqlstate"] == "23503"


# -- listing: paging, sorting, filtering -------------------------------------------------

@pytest.fixture
def seeded(client: TestClient, table: dict) -> list[int]:
    return _seed(client, "work-orders", ROWS)


def _titles(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["data"]["title"] for item in response.json()["items"]]


def test_paging(client: TestClient, seeded: list[int]):
    page = client.get("/tables/work-orders/rows?limit=2").json()
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 0)
    assert [r["id"] for r in page["items"]] == seeded[:2]  # insertion order by default
    page = client.get("/tables/work-orders/rows?limit=2&offset=2").json()
    assert [r["id"] for r in page["items"]] == seeded[2:]
    assert client.get("/tables/work-orders/rows?limit=0").json()["items"] == []
    assert client.get("/tables/work-orders/rows?limit=1001").status_code == 422
    assert client.get("/tables/work-orders/rows?offset=-1").status_code == 422


def test_sorting(client: TestClient, seeded: list[int]):
    assert _titles(client.get("/tables/work-orders/rows?sort=-cost")) == ["Broken window", "Paint hallway", "Leaking tap", "Door bell"]
    assert _titles(client.get("/tables/work-orders/rows?sort=cost")) == ["Leaking tap", "Paint hallway", "Broken window", "Door bell"]  # NULLs last
    assert _titles(client.get("/tables/work-orders/rows?sort=status,-cost")) == ["Broken window", "Leaking tap", "Door bell", "Paint hallway"]
    assert _titles(client.get("/tables/work-orders/rows?sort=due")) == ["Broken window", "Leaking tap", "Paint hallway", "Door bell"]
    response = client.get("/tables/work-orders/rows?sort=nope")
    assert response.status_code == 422 and response.json()["detail"]["errors"] == {"sort[0]": "unknown column 'nope'"}


@pytest.mark.parametrize(
    "filters, expected",
    [
        (["status:equals:open"], ["Leaking tap", "Door bell"]),                        # case-insensitive
        (["status:not_equals:Open"], ["Broken window", "Paint hallway"]),              # NULL counts as "not"
        (["title:contains:ow"], ["Broken window"]),
        (["title:starts_with:d"], ["Door bell"]),
        (["title:ends_with:TAP"], ["Leaking tap"]),
        (["title:not_contains:a"], ["Broken window", "Door bell"]),
        (["status:is_one_of:Open,Closed"], ["Leaking tap", "Broken window", "Door bell"]),
        (["status:is_not_one_of:Open"], ["Broken window", "Paint hallway"]),
        (["notes:is_empty"], ["Leaking tap", "Broken window", "Door bell"]),
        (["notes:is_not_empty"], ["Paint hallway"]),
        (["cost:greater_than:50"], ["Broken window", "Paint hallway"]),
        (["cost:less_than_or_equal:80"], ["Leaking tap", "Paint hallway"]),
        (["cost:equals:300"], ["Broken window"]),
        (["cost:is_empty"], ["Door bell"]),
        (["due:before:2026-01-20"], ["Broken window"]),
        (["due:on_or_after:2026-01-15"], ["Leaking tap", "Broken window"]),
        (["urgent:is_checked"], ["Leaking tap"]),
        (["urgent:is_not_checked"], ["Broken window", "Paint hallway", "Door bell"]),
        (["status:equals:Open", "cost:greater_than:10"], ["Leaking tap"]),             # all
        (["created_at:after:2000-01-01T00:00:00Z"], ["Leaking tap", "Broken window", "Paint hallway", "Door bell"]),
        (["id:greater_than:2"], ["Paint hallway", "Door bell"]),
    ],
)
def test_filters(client: TestClient, seeded: list[int], filters: list[str], expected: list[str]):
    query = "&".join(f"filter={f}" for f in filters)
    assert _titles(client.get(f"/tables/work-orders/rows?{query}")) == expected


def test_match_any_and_search(client: TestClient, seeded: list[int]):
    assert _titles(client.get("/tables/work-orders/rows?filter=status:equals:Closed&filter=cost:equals:80&match=any")) == ["Broken window", "Paint hallway"]
    assert _titles(client.get("/tables/work-orders/rows?search=COAT")) == ["Paint hallway"]     # any text column
    assert _titles(client.get("/tables/work-orders/rows?filter=status:equals:Pending")) == []  # not an option: no rows
    assert _titles(client.get("/tables/work-orders/rows?search=%25")) == []                     # LIKE wildcards are literal
    assert _titles(client.get("/tables/work-orders/rows?search=o&sort=-id&limit=2")) == ["Door bell", "Paint hallway"]  # notes match too


def test_invalid_filters_are_422_with_the_part_named(client: TestClient, seeded: list[int]):
    cases = {
        "filter=nope:equals:x": {"filters[0]": "unknown column 'nope'"},
        "filter=cost:contains:1": {"filters[0]": "operator 'contains' cannot be used with a numeric column"},
        "filter=cost:greater_than:lots": {"filters[0]": "value must be a number"},
        "filter=due:before:soon": {"filters[0]": "value must be a date in YYYY-MM-DD format"},
        "filter=title:equals": {"filters[0]": "operator 'equals' needs a value"},
        "filter=title:frobnicate:x": {"filter": "unknown operator 'frobnicate'"},
        "filter=title": {"filter": "'title' is not column:operator[:value]"},
        "filter=urgent:equals:maybe": {"filters[0]": "value must be true or false"},
    }
    for query, errors in cases.items():
        response = client.get(f"/tables/work-orders/rows?{query}")
        assert response.status_code == 422, (query, response.text)
        assert response.json()["detail"] == {"message": "Invalid query", "errors": errors}, query


def test_query_endpoint_takes_a_json_body(client: TestClient, seeded: list[int]):
    response = client.post("/tables/work-orders/rows/query", json={
        "filters": [{"column": "status", "operator": "is_one_of", "value": ["Open", "Closed"]},
                    {"column": "cost", "operator": "greater_than_or_equal", "value": 12.5}],
        "match": "all",
        "sort": [{"column": "cost", "direction": "desc"}],
        "limit": 10,
    })
    assert _titles(response) == ["Broken window", "Leaking tap"]
    assert response.json()["total"] == 2
    assert client.post("/tables/work-orders/rows/query", json={"filters": [{"column": "x", "operator": "equals", "value": 1}],
                                                              "sort": [{"column": "y"}]}).json()["detail"]["errors"] == {
        "filters[0]": "unknown column 'x'", "sort[0]": "unknown column 'y'"}
    assert client.post("/tables/work-orders/rows/query", json={"match": "some"}).status_code == 422


def test_listing_other_tables(client: TestClient):
    _post(client, "public.option_lists", {"id": "a", "name": "Alpha", "options": ["x", "y"]})
    _post(client, "public.option_lists", {"id": "b", "name": "Beta", "options": ["y"]})
    assert [r["id"] for r in client.get("/tables/public.option_lists/rows?filter=options:contains:x").json()["items"]] == ["a"]
    assert [r["id"] for r in client.get("/tables/public.option_lists/rows?sort=-name").json()["items"]] == ["b", "a"]
    assert client.get("/tables/public.forms/rows?filter=fields:is_not_empty").json()["total"] == 1
    assert client.get("/tables/public.forms/rows?sort=fields").json()["detail"]["errors"] == {"sort[0]": "cannot sort by a jsonb column"}
    page = client.get("/tables/public.forms/rows").json()
    assert page["items"][0]["id"] == "contact-form" and "seq" in page["items"][0]["data"]
