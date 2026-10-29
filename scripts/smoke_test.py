#!/usr/bin/env python3
"""End-to-end smoke test of every backend endpoint against a running API.

    python scripts/smoke_test.py [--url http://localhost:8000]

Exercises, in order: /health, /images, the forms CRUD (including validation,
conflicts and the GET->PUT round trip), the option-list CRUD, data tables
(CREATE / ALTER / DROP TABLE through the API, consistency check), table rows
(typed cells, filter / sort / page, other tables), the SQL endpoint (results,
refused statements, read-only protection), submissions
(validation, conditional logic, listing/paging/filtering, lookup), and the
soft-delete behaviour (submissions survive the form). Every request's expected
status is checked and the first mismatch stops the run with the response body.

It creates a form, an option list and a data table named
``smoke-test-<timestamp>`` and removes them at the end; the deleted form stays
as a soft-deleted row (plus its two submissions), by design. The data table
is really dropped.

Standard library only. Exit code 0 = all passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fail, info, ok, step  # noqa: E402


class SmokeFailure(AssertionError):
    pass


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.passed = 0

    def call(self, method: str, path: str, body=None, *, expect: int, headers: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, raw, ctype = response.status, response.read(), response.headers.get("content-type", "")
        except urllib.error.HTTPError as exc:
            status, raw, ctype = exc.code, exc.read(), exc.headers.get("content-type", "")
        except urllib.error.URLError as exc:
            raise SmokeFailure(f"{method} {path}: not reachable ({exc.reason})") from exc
        parsed = json.loads(raw) if raw and "json" in ctype else (len(raw) if raw else None)
        if status != expect:
            raise SmokeFailure(f"{method} {path}: expected {expect}, got {status}: {raw[:500].decode(errors='replace')}")
        self.passed += 1
        ok(f"{method} {path} -> {status}")
        return parsed


def run_smoke(base: str) -> int:
    api = Api(base)
    tag = f"smoke-test-{int(time.time())}"

    step("System")
    health = api.call("GET", "/health", expect=200)
    info(f"PostgreSQL {health.get('database', {}).get('serverVersion', 'n/a')}")
    images = api.call("GET", "/images", expect=200)
    if images["images"]:
        api.call("GET", "/images/files/" + images["images"][0]["name"], expect=200)

    step("Forms")
    before = api.call("GET", "/forms", expect=200)
    api.call("GET", "/forms/does-not-exist", expect=404)
    api.call("POST", "/forms", {"name": "", "fields": []}, expect=422)                       # name required
    api.call("POST", "/forms", {"name": "x", "fields": [{"id": "a", "type": "nope", "label": "A"}]}, expect=422)
    api.call("POST", "/forms", {"name": "x", "fields": [{"id": "a", "type": "select", "label": "A"}]}, expect=422)  # options
    api.call("POST", "/forms", {"name": "x", "fields": [
        {"id": "a", "type": "text", "label": "A"},
        {"id": "b", "type": "text", "label": "B", "logic": {"conditions": [{"fieldId": "zzz", "operator": "equals", "value": "1"}]}},
    ]}, expect=422)                                                                           # logic references unknown field
    form_body = {
        "id": tag, "name": f"Smoke Test {tag}", "description": "created by scripts/smoke_test.py",
        "fields": [
            {"id": "name", "type": "text", "label": "Name", "required": True},
            {"id": "hasPet", "type": "radio", "label": "Pet?", "required": True, "options": ["Yes", "No"]},
            {"id": "petWeight", "type": "number", "label": "Weight", "required": True,
             "logic": {"action": "show", "match": "all", "conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Yes"}]}},
            {"id": "heavy", "type": "checkbox", "label": "Heavy", "required": True,
             "logic": {"action": "show", "match": "all", "conditions": [{"fieldId": "petWeight", "operator": "greater_than", "value": 50}]}},
            {"id": "since", "type": "date", "label": "Since"},
        ],
    }
    created = api.call("POST", "/forms", form_body, expect=201)
    assert created["id"] == tag and created["createdAt"], created
    api.call("POST", "/forms", form_body, expect=409)                                         # explicit id taken
    namesake = api.call("POST", "/forms", {**form_body, "id": ""}, expect=201)                # generated id = slug of the name
    assert namesake["id"] == f"smoke-test-{tag}", namesake["id"]
    namesake2 = api.call("POST", "/forms", {**form_body, "id": ""}, expect=201)               # same name again -> "-2"
    assert namesake2["id"] == f"smoke-test-{tag}-2", namesake2["id"]
    fetched = api.call("GET", f"/forms/{tag}", expect=200)
    assert fetched["fields"][2]["logic"]["conditions"][0]["fieldId"] == "hasPet"
    updated = api.call("PUT", f"/forms/{tag}", {**fetched, "name": f"Smoke Test {tag} v2"}, expect=200)  # GET -> PUT
    assert updated["name"].endswith("v2") and updated["createdAt"] == created["createdAt"] and updated["id"] == tag
    api.call("PUT", f"/forms/{tag}", {**fetched, "fields": [{"id": "a", "type": "select", "label": "A", "options": ["x", "X"]}]}, expect=422)
    api.call("PUT", "/forms/does-not-exist", form_body, expect=404)
    listed = api.call("GET", "/forms", expect=200)
    assert [f["id"] for f in listed][-3:] == [tag, namesake["id"], namesake2["id"]], "forms list in creation order"
    assert len(listed) == len(before) + 3

    step("Option lists")
    list_body = {"name": f"Smoke {tag}", "description": "temp", "options": [" Red ", "Green", "", "Blue"]}
    api.call("POST", "/option-lists", {"name": "", "options": ["x"]}, expect=422)
    api.call("POST", "/option-lists", {"name": "x", "options": []}, expect=422)
    api.call("POST", "/option-lists", {"name": "x", "options": ["a", "A"]}, expect=422)       # duplicate options
    olist = api.call("POST", "/option-lists", list_body, expect=201)
    assert olist["options"] == ["Red", "Green", "Blue"], olist["options"]                     # trimmed, empties dropped
    api.call("POST", "/option-lists", {**list_body, "name": list_body["name"].upper()}, expect=409)  # name taken (case-insensitive)
    api.call("POST", "/option-lists", {**list_body, "id": olist["id"], "name": "other"}, expect=409)  # id taken
    fetched_list = api.call("GET", f"/option-lists/{olist['id']}", expect=200)
    api.call("GET", "/option-lists/does-not-exist", expect=404)
    updated_list = api.call("PUT", f"/option-lists/{olist['id']}", {**fetched_list, "options": ["Red", "Green", "Blue", "Black"]}, expect=200)
    assert updated_list["options"][-1] == "Black" and updated_list["createdAt"] == olist["createdAt"]
    api.call("PUT", "/option-lists/does-not-exist", list_body, expect=404)
    all_lists = api.call("GET", "/option-lists", expect=200)
    names = [item["name"].lower() for item in all_lists]
    assert names == sorted(names), "option lists sorted by name"
    assert any(item["id"] == olist["id"] for item in all_lists)

    step("Tables")
    table_body = {"name": f"Smoke {tag}", "description": "temp", "columns": [
        {"id": "title", "name": "Title", "type": "text", "required": True},
        {"id": "status", "name": "Status", "type": "select", "options": ["Open", "Closed"]},
        {"id": "cost", "name": "Cost", "type": "number"},
        {"id": "done", "name": "Done", "type": "checkbox"},
    ]}
    api.call("POST", "/tables", {"name": "", "columns": []}, expect=422)                                 # name required
    api.call("POST", "/tables", {"name": "x", "columns": [{"id": "Bad Id", "name": "B", "type": "text"}]}, expect=422)
    api.call("POST", "/tables", {"name": "x", "columns": [{"id": "id", "name": "B", "type": "text"}]}, expect=422)  # reserved
    api.call("POST", "/tables", {"name": "x", "columns": [{"id": "s", "name": "S", "type": "select"}]}, expect=422)  # options
    dtable = api.call("POST", "/tables", table_body, expect=201)
    assert dtable["id"] == f"smoke-{tag}", dtable["id"]
    assert dtable["columns"][3] == {"id": "done", "name": "Done", "type": "checkbox", "required": False, "description": ""}
    api.call("POST", "/tables", {**table_body, "name": table_body["name"].upper()}, expect=409)            # name taken
    api.call("POST", "/tables", {**table_body, "id": dtable["id"], "name": "other"}, expect=409)           # id taken
    api.call("GET", "/tables/does-not-exist", expect=404)
    check = api.call("GET", f"/tables/{dtable['id']}/check", expect=200)
    assert check["ok"] and check["problems"] == [], check
    altered = api.call("PUT", f"/tables/{dtable['id']}", {**dtable, "columns": [                         # GET -> PUT
        {**dtable["columns"][1], "name": "State", "options": ["Open", "Closed", "On hold"]},              # rename + widen
        {**dtable["columns"][0], "required": False},                                                     # DROP NOT NULL
        dtable["columns"][3],
        {"id": "owner", "name": "Owner", "type": "text"},                                                # ADD COLUMN
    ]}, expect=200)                                                                                      # (cost: DROP COLUMN)
    assert [c["id"] for c in altered["columns"]] == ["status", "title", "done", "owner"], altered["columns"]
    assert altered["columns"][0]["name"] == "State" and altered["createdAt"] == dtable["createdAt"]
    check = api.call("GET", f"/tables/{dtable['id']}/check", expect=200)
    assert check["ok"], check
    api.call("PUT", f"/tables/{dtable['id']}", {**altered, "columns": [{"id": "Bad", "name": "B", "type": "text"}]}, expect=422)
    api.call("PUT", "/tables/does-not-exist", table_body, expect=404)
    assert any(t["id"] == dtable["id"] for t in api.call("GET", "/tables", expect=200))

    step("Table rows")
    rows_path = f"/tables/{dtable['id']}/rows"
    schema = api.call("GET", f"/tables/{dtable['id']}/schema", expect=200)
    assert schema["primaryKey"] == "id" and {c["id"] for c in schema["columns"]} >= {"status", "title", "done", "owner"}
    bad_row = api.call("POST", rows_path, {"data": {"status": "Nope", "done": "maybe", "bogus": 1}}, expect=422)
    assert set(bad_row["detail"]["errors"]) == {"status", "done", "bogus"}, bad_row
    row1 = api.call("POST", rows_path, {"data": {"title": "Leak", "status": "Open", "done": True}}, expect=201)
    row2 = api.call("POST", rows_path, {"data": {"title": "Window", "status": "Closed", "owner": "Ada"}}, expect=201)
    assert row1["data"] == {"title": "Leak", "status": "Open", "done": True} and row1["id"] < row2["id"]
    api.call("GET", f"{rows_path}/{row1['id']}", expect=200)
    api.call("GET", f"{rows_path}/999999", expect=404)
    page = api.call("GET", f"{rows_path}?filter=status:equals:open&sort=-id&limit=10", expect=200)
    assert page["total"] == 1 and page["items"][0]["id"] == row1["id"], page
    page = api.call("GET", f"{rows_path}?search=ada", expect=200)
    assert [r["id"] for r in page["items"]] == [row2["id"]]
    api.call("GET", f"{rows_path}?filter=nope:equals:1", expect=422)
    queried = api.call("POST", f"{rows_path}/query", {"filters": [{"column": "done", "operator": "is_not_checked"}],
                                                      "sort": [{"column": "title", "direction": "desc"}]}, expect=200)
    assert [r["id"] for r in queried["items"]] == [row2["id"]]
    patched = api.call("PATCH", f"{rows_path}/{row1['id']}", {"data": {"status": None, "owner": "Bob"}}, expect=200)
    assert patched["data"] == {"title": "Leak", "done": True, "owner": "Bob"} and patched["updatedAt"] >= row1["updatedAt"]
    api.call("PATCH", f"{rows_path}/{row1['id']}", {"data": {"status": "Nope"}}, expect=422)
    api.call("DELETE", f"{rows_path}/{row2['id']}", expect=204)
    api.call("DELETE", f"{rows_path}/{row2['id']}", expect=404)
    assert api.call("GET", rows_path, expect=200)["total"] == 1
    # Any allowed table, not only user tables; hidden ones stay hidden.
    lists_page = api.call("GET", f"/tables/public.option_lists/rows?filter=id:equals:{olist['id']}", expect=200)
    assert lists_page["total"] == 1 and lists_page["items"][0]["data"]["options"] == updated_list["options"]
    api.call("GET", "/tables/public.schema_migrations/rows", expect=404)
    api.call("GET", "/tables/pg_catalog.pg_class/rows", expect=404)

    step("SQL")
    rules = api.call("GET", "/sql/rules", expect=200)
    assert "DROP" in rules["blockedKeywords"] and "UPDATE" in rules["writeStatements"]
    catalog = api.call("GET", "/sql/tables", expect=200)["tables"]
    assert any(t["ref"] == dtable["id"] and t["userTable"] for t in catalog) and not any(t["table"] == "schema_migrations" for t in catalog)
    physical = f"user_tables.{dtable['id'].replace('-', '_')}"
    result = api.call("POST", "/sql/query", {"sql": f"SELECT id, title FROM {physical} ORDER BY id", "limit": 10}, expect=200)
    assert result["kind"] == "read" and result["rowCount"] == 1 and result["rows"][0][1] == "Leak", result
    assert [c["name"] for c in result["columns"]] == ["id", "title"]
    result = api.call("POST", "/sql/query", {"sql": f"UPDATE {physical} SET title = 'Leak fixed' WHERE id = %s RETURNING title", "params": [row1["id"]]}, expect=200)
    assert result["kind"] == "write" and result["rowCount"] == 1 and result["rows"] == [["Leak fixed"]]
    for bad in (f"DELETE FROM {physical}", f"DROP TABLE {physical}", f"TRUNCATE {physical}",
                f"ALTER TABLE {physical} DROP COLUMN title", f"SELECT 1; DELETE FROM {physical}",
                f"WITH d AS (DELETE FROM {physical} RETURNING *) SELECT * FROM d", "SELECT pg_sleep(5)",
                "UPDATE schema_migrations SET checksum = NULL"):
        denied = api.call("POST", "/sql/query", {"sql": bad}, expect=400)
        assert denied["detail"]["message"] == "Statement not allowed", denied
    denied = api.call("POST", "/sql/query", {"sql": "SELECT nextval('forms_seq_seq')"}, expect=400)       # read-only transaction
    assert denied["detail"]["sqlstate"] == "25006", denied
    failed = api.call("POST", "/sql/query", {"sql": f"SELECT nope FROM {physical}"}, expect=400)
    assert failed["detail"]["sqlstate"] == "42703" and failed["detail"]["position"] == 8, failed
    assert api.call("GET", f"{rows_path}/{row1['id']}", expect=200)["data"]["title"] == "Leak fixed"

    step("Submissions")
    api.call("POST", "/forms/does-not-exist/submissions", {"data": {}}, expect=404)
    bad = api.call("POST", f"/forms/{tag}/submissions", {"data": {"hasPet": "Maybe", "bogus": 1, "since": "yesterday"}}, expect=422)
    errors = bad["detail"]["errors"]
    assert "name" in errors and "hasPet" in errors and "bogus" in errors and "since" in errors, errors
    sub1 = api.call("POST", f"/forms/{tag}/submissions",
                    {"data": {"name": "Ada", "hasPet": "No", "petWeight": 999, "since": "2025-01-15"},
                     "metadata": {"pageUrl": "/smoke", "ipAddress": "spoofed"}},
                    expect=201, headers={"x-client-ip": "203.0.113.9"})
    assert "petWeight" not in sub1["data"], "hidden field answers are discarded"
    assert sub1["metadata"]["hiddenFields"] == ["petWeight", "heavy"], sub1["metadata"]["hiddenFields"]
    assert sub1["metadata"]["ipAddress"] == "203.0.113.9" and sub1["metadata"]["pageUrl"] == "/smoke"
    assert sub1["formName"].endswith("v2") and sub1["id"].startswith("sub-")
    sub2 = api.call("POST", f"/forms/{tag}/submissions",
                    {"data": {"name": "Bob", "hasPet": "Yes", "petWeight": "75", "heavy": True}, "submittedBy": "smoke"}, expect=201)
    assert sub2["data"]["petWeight"] == 75 and sub2["data"]["heavy"] is True and sub2["metadata"]["submittedBy"] == "smoke"
    api.call("POST", f"/forms/{tag}/submissions", {"data": {"name": "Cy", "hasPet": "Yes", "petWeight": 80, "heavy": False}}, expect=422)  # required checkbox
    api.call("POST", f"/forms/{tag}/submissions", {"data": {"name": "Dee", "hasPet": "Yes"}}, expect=422)  # visible required
    page = api.call("GET", f"/forms/{tag}/submissions", expect=200)
    assert page["total"] == 2 and [s["id"] for s in page["items"]] == [sub2["id"], sub1["id"]], "newest first"
    page = api.call("GET", f"/forms/{tag}/submissions?limit=1&offset=1", expect=200)
    assert page["total"] == 2 and [s["id"] for s in page["items"]] == [sub1["id"]]
    api.call("GET", "/forms/does-not-exist/submissions", expect=404)
    everything = api.call("GET", "/submissions?limit=1000", expect=200)
    assert everything["total"] >= 2 and everything["total"] == len(everything["items"])
    filtered = api.call("GET", f"/submissions?formId={tag}", expect=200)
    assert filtered["total"] == 2
    one = api.call("GET", f"/submissions/{sub1['id']}", expect=200)
    assert one == sub1, "stored record is returned verbatim"
    api.call("GET", "/submissions/sub-does-not-exist", expect=404)
    api.call("GET", f"/forms/{tag}/submissions?limit=5000", expect=422)                       # limit bound

    step("Deletion")
    api.call("DELETE", f"/option-lists/{olist['id']}", expect=204)
    api.call("DELETE", f"/option-lists/{olist['id']}", expect=404)
    api.call("DELETE", f"/forms/{namesake['id']}", expect=204)
    api.call("DELETE", f"/forms/{namesake2['id']}", expect=204)
    api.call("DELETE", f"/forms/{tag}", expect=204)
    api.call("DELETE", f"/forms/{tag}", expect=404)
    api.call("GET", f"/forms/{tag}", expect=404)
    after = api.call("GET", "/forms", expect=200)
    assert [f["id"] for f in after] == [f["id"] for f in before], "listing back to where it started"
    survivors = api.call("GET", f"/submissions?formId={tag}", expect=200)
    assert survivors["total"] == 2, "submissions outlive their form (soft delete)"
    api.call("GET", f"/submissions/{sub1['id']}", expect=200)
    api.call("POST", "/forms", form_body, expect=409)                                     # id stays reserved
    info("soft delete verified: submissions kept, id reserved")

    api.call("DELETE", f"/tables/{dtable['id']}", expect=204)
    api.call("DELETE", f"/tables/{dtable['id']}", expect=404)
    api.call("GET", f"/tables/{dtable['id']}", expect=404)
    recreated = api.call("POST", "/tables", {**table_body, "columns": []}, expect=201)                   # id and name free again
    assert recreated["id"] == dtable["id"]
    api.call("DELETE", f"/tables/{dtable['id']}", expect=204)

    api.call("GET", "/health", expect=200)
    return api.passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("FORMBUILDER_API_URL", "http://localhost:8000"))
    args = parser.parse_args(argv)
    step(f"Smoke test: {args.url}")
    try:
        passed = run_smoke(args.url)
    except SmokeFailure as exc:
        fail(str(exc))
        print("RESULT: FAILED")
        return 1
    except AssertionError as exc:
        fail(f"assertion failed: {exc}")
        print("RESULT: FAILED")
        return 1
    print(f"RESULT: OK ({passed} requests behaved as expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
