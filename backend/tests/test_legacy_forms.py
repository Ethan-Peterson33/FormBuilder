"""Stored forms must always load, even if they break rules added later.

Rules are enforced when saving; reading never fails because of them.
"""

from __future__ import annotations

import json

import pytest
from psycopg.types.json import Jsonb

from tests.conftest import requires_database

pytestmark = requires_database

LEGACY = {
    "id": "legacy",
    "name": "Legacy Form",
    "fields": [
        # duplicate option (case-insensitive)
        {"id": "hasPet", "type": "radio", "label": "Pet?", "options": ["Yes", "No", "yes"]},
        # stale condition value
        {
            "id": "petName", "type": "text", "label": "Pet name",
            "logic": {"conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Maybe"}]},
        },
        # forward reference
        {
            "id": "early", "type": "text", "label": "Early",
            "logic": {"conditions": [{"fieldId": "later", "operator": "is_not_empty"}]},
        },
        {"id": "later", "type": "text", "label": "Later"},
        # reference to a field that no longer exists + operator/type mismatch
        {
            "id": "orphan", "type": "text", "label": "Orphan",
            "logic": {"conditions": [
                {"fieldId": "deleted", "operator": "equals", "value": "x"},
                {"fieldId": "later", "operator": "greater_than", "value": 3},
            ]},
        },
    ],
}


@pytest.fixture
def legacy(pg_url: str) -> None:
    """Insert the record straight into the table, as an older release would have stored it."""
    import psycopg

    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO forms (id, name, fields) VALUES (%s, %s, %s)",
            (LEGACY["id"], LEGACY["name"], Jsonb(LEGACY["fields"])),
        )


def test_legacy_form_with_invalid_rules_still_loads(client, legacy):
    listing = client.get("/forms")
    assert listing.status_code == 200
    assert [f["id"] for f in listing.json()] == ["contact-form", "legacy"]

    single = client.get("/forms/legacy")
    assert single.status_code == 200
    # Data is returned as stored so the builder can show what needs fixing.
    assert single.json()["fields"][0]["options"] == ["Yes", "No", "yes"]
    assert single.json()["fields"][1]["logic"]["conditions"][0]["value"] == "Maybe"


def test_legacy_form_can_still_receive_submissions(client, legacy):
    response = client.post("/forms/legacy/submissions", json={"data": {"hasPet": "No"}})
    assert response.status_code == 201, response.text


def test_saving_legacy_form_unchanged_reports_every_problem(client, legacy):
    response = client.put("/forms/legacy", json=LEGACY)
    assert response.status_code == 422
    messages = [item["msg"] for item in response.json()["detail"]]
    assert any("duplicate options: yes" in m for m in messages)
    assert any("'Maybe', which is not one of the options of 'Pet?'" in m for m in messages)
    assert any("'Later', which is not an earlier field" in m and "appears after" in m for m in messages)
    assert any("'deleted', which is not an earlier field" in m and "does not exist" in m for m in messages)
    assert any("'greater_than' cannot be used with a 'text' field" in m for m in messages)
    assert len(messages) == 5


def test_saving_legacy_form_after_fixing_succeeds(client, legacy):
    fixed = json.loads(json.dumps(LEGACY))
    fixed["fields"][0]["options"] = ["Yes", "No"]
    fixed["fields"][1]["logic"]["conditions"][0]["value"] = "Yes"
    fixed["fields"][2]["logic"] = None
    fixed["fields"][4]["logic"]["conditions"] = [{"fieldId": "later", "operator": "is_not_empty"}]
    response = client.put("/forms/legacy", json=fixed)
    assert response.status_code == 200, response.text


def test_other_forms_unaffected_by_a_broken_neighbour(client, legacy):
    assert client.get("/forms/contact-form").status_code == 200
    assert client.post("/forms", json={"name": "New", "fields": []}).status_code == 201
