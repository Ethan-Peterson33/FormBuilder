"""The very first data file shape (project baseline) must still import.

That file contained a form saved by an early builder with ``id: ""``. Such a
record cannot be addressed by any route, so ``db.import_json`` gives it a
unique slug instead of dropping it or failing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import import_json
from tests.conftest import requires_database

# Verbatim copy of backend/data/forms.json at the project baseline.
BASELINE_FORMS = [
    {
        "id": "contact-form",
        "name": "Contact Form",
        "description": "A simple contact information form",
        "fields": [
            {"id": "firstName", "type": "text", "label": "First Name", "required": True, "placeholder": "Enter first name"},
            {"id": "lastName", "type": "text", "label": "Last Name", "required": True, "placeholder": "Enter last name"},
            {"id": "email", "type": "text", "label": "Email", "required": False, "placeholder": "Enter email"},
            {"id": "explain", "type": "textarea", "label": "Explain why you are reaching out", "required": False, "placeholder": "Enter email"},
            {"id": "contactMethod", "type": "select", "label": "Preferred Contact Method", "required": False, "options": ["Email", "Phone", "Text"]},
        ],
    },
    {
        "id": "",
        "name": "Contact Form",
        "description": "",
        "fields": [
            {"id": "field-b41ed818-e9cf-497b-8fc8-a2a94316a4aa", "type": "text", "label": "Name", "required": True, "placeholder": ""},
        ],
    },
]


def _import(url: str, path: Path) -> None:
    import psycopg

    with psycopg.connect(url) as conn:
        import_json.import_data_dir(conn, path.parent)


def test_repair_ids_gives_blank_and_duplicate_ids_unique_slugs():
    records = json.loads(json.dumps(BASELINE_FORMS)) + [
        {"id": "contact-form", "name": "Contact Form", "fields": []},  # duplicate id
        {"id": "odd id!", "name": "Odd", "fields": []},  # unusual but unique: left alone
    ]
    changes = import_json.repair_form_ids(records)
    assert changes == [("", "contact-form-2"), ("contact-form", "contact-form-3")]
    assert [r["id"] for r in records] == ["contact-form", "contact-form-2", "contact-form-3", "odd id!"]
    assert import_json.repair_form_ids(records) == []  # idempotent


def test_repair_is_a_no_op_for_healthy_records():
    records = [dict(BASELINE_FORMS[0])]
    assert import_json.repair_form_ids(records) == []
    assert records == [BASELINE_FORMS[0]]


@requires_database
def test_baseline_file_is_fully_usable_after_import(client: TestClient, pg_url: str, tmp_path: Path):
    path = tmp_path / "forms.json"
    path.write_text(json.dumps(BASELINE_FORMS))
    _import(pg_url, path)

    listing = client.get("/forms").json()
    assert [f["id"] for f in listing] == ["contact-form", "contact-form-2"]
    assert all(f["id"] for f in listing)

    # The previously unaddressable record can now be fetched, edited and submitted to.
    repaired = client.get("/forms/contact-form-2")
    assert repaired.status_code == 200
    assert repaired.json()["fields"][0]["label"] == "Name"

    update = client.put("/forms/contact-form-2", json={**repaired.json(), "name": "Contact Form (old)"})
    assert update.status_code == 200, update.text
    assert update.json()["id"] == "contact-form-2"

    submit = client.post(
        "/forms/contact-form-2/submissions",
        json={"data": {"field-b41ed818-e9cf-497b-8fc8-a2a94316a4aa": "Ada"}},
    )
    assert submit.status_code == 201, submit.text

    # The original record keeps working without logic; timestamps were assigned on import.
    original = client.get("/forms/contact-form").json()
    assert original["createdAt"] and "logic" not in original["fields"][0]
    assert client.post("/forms/contact-form/submissions", json={"data": {"firstName": "A", "lastName": "B"}}).status_code == 201


@requires_database
def test_baseline_records_accept_every_field_default(client: TestClient, pg_url: str, tmp_path: Path):
    """Fields written before `required`/`placeholder`/`options` were consistent."""
    path = tmp_path / "forms.json"
    path.write_text(json.dumps([{
        "id": "sparse", "name": "Sparse",
        "fields": [{"id": "a", "type": "text", "label": "A"}, {"id": "b", "type": "date", "label": "B"}],
    }]))
    _import(pg_url, path)
    form = client.get("/forms/sparse").json()
    assert form["fields"][0]["required"] is False
    assert "description" in form and form["description"] == ""
