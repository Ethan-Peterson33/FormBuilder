"""Tests for saving and retrieving form submissions."""

from __future__ import annotations

import pytest

from tests.conftest import requires_database

pytestmark = requires_database

PET_FORM = {
    "id": "pet-registration",
    "name": "Pet Registration",
    "fields": [
        {"id": "residentName", "type": "text", "label": "Resident Name", "required": True},
        {"id": "moveInDate", "type": "date", "label": "Move-in Date", "required": False},
        {"id": "hasPet", "type": "radio", "label": "Pet?", "required": True, "options": ["Yes", "No"]},
        {
            "id": "petType", "type": "select", "label": "Type", "required": True,
            "options": ["Dog", "Cat", "Other"],
            "logic": {"conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Yes"}]},
        },
        {
            "id": "petWeight", "type": "number", "label": "Weight", "required": True,
            "logic": {"conditions": [{"fieldId": "petType", "operator": "is_one_of", "value": ["Dog", "Cat"]}]},
        },
        {
            "id": "largePetDeposit", "type": "checkbox", "label": "Deposit ack", "required": True,
            "logic": {"conditions": [{"fieldId": "petWeight", "operator": "greater_than", "value": 50}]},
        },
        {"id": "comments", "type": "textarea", "label": "Comments", "required": False},
    ],
}


@pytest.fixture(autouse=True)
def _pet_form(client):
    assert client.post("/forms", json=PET_FORM).status_code == 201


def _submit(client, data, **extra):
    return client.post("/forms/pet-registration/submissions", json={"data": data, **extra})


# -- saving -------------------------------------------------------------------

def test_submission_is_saved_with_metadata(client, pg_url: str):
    response = client.post(
        "/forms/pet-registration/submissions",
        json={
            "data": {"residentName": "Ada", "hasPet": "No", "comments": "none"},
            "submittedBy": "ada@example.com",
            "source": "web",
            "metadata": {"pageUrl": "/forms/pet-registration"},
        },
        headers={"X-Client-User-Agent": "TestBrowser/1.0", "X-Client-IP": "203.0.113.5"},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["id"].startswith("sub-")
    assert body["formId"] == "pet-registration"
    assert body["formName"] == "Pet Registration"
    assert body["data"] == {"residentName": "Ada", "hasPet": "No", "comments": "none"}

    meta = body["metadata"]
    assert meta["submittedAt"].endswith("Z")
    assert meta["submittedBy"] == "ada@example.com"
    assert meta["source"] == "web"
    assert meta["userAgent"] == "TestBrowser/1.0"
    assert meta["ipAddress"] == "203.0.113.5"
    assert meta["pageUrl"] == "/forms/pet-registration"
    # Fields hidden by logic are recorded.
    assert meta["hiddenFields"] == ["petType", "petWeight", "largePetDeposit"]

    # Persisted to its own table, with the server-owned metadata in real columns.
    import psycopg

    with psycopg.connect(pg_url) as conn:
        stored = conn.execute(
            "SELECT id, form_id, submitted_by, ip_address, hidden_fields, metadata FROM submissions"
        ).fetchall()
    assert stored == [(body["id"], "pet-registration", "ada@example.com", "203.0.113.5",
                       ["petType", "petWeight", "largePetDeposit"], {"pageUrl": "/forms/pet-registration"})]


def test_hidden_field_answers_are_discarded(client):
    # petType was answered while visible, then hasPet flipped to No.
    response = _submit(client, {"residentName": "Ada", "hasPet": "No", "petType": "Dog", "petWeight": 80})
    assert response.status_code == 201
    assert response.json()["data"] == {"residentName": "Ada", "hasPet": "No"}


def test_full_conditional_chain(client):
    response = _submit(
        client,
        {"residentName": "Ada", "hasPet": "Yes", "petType": "Dog", "petWeight": "75", "largePetDeposit": True},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data"]["petWeight"] == 75  # numeric strings are coerced
    assert body["data"]["largePetDeposit"] is True
    assert body["metadata"]["hiddenFields"] == []


def test_client_extras_cannot_override_server_metadata(client):
    response = _submit(
        client,
        {"residentName": "Ada", "hasPet": "No"},
        metadata={"submittedAt": "1999-01-01T00:00:00Z", "ipAddress": "spoofed", "note": "kept"},
    )
    meta = response.json()["metadata"]
    assert meta["submittedAt"] != "1999-01-01T00:00:00Z"
    assert meta["ipAddress"] != "spoofed"
    assert meta["note"] == "kept"


def test_submission_for_unknown_form_is_404(client):
    response = client.post("/forms/nope/submissions", json={"data": {}})
    assert response.status_code == 404


# -- validation ---------------------------------------------------------------

def test_required_visible_fields_are_enforced(client):
    response = _submit(client, {"hasPet": "Yes"})
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert errors == {"residentName": "is required", "petType": "is required"}


def test_required_hidden_fields_are_not_enforced(client):
    # petType is required but hidden because hasPet == No.
    response = _submit(client, {"residentName": "Ada", "hasPet": "No"})
    assert response.status_code == 201


def test_required_checkbox_must_be_checked(client):
    response = _submit(
        client,
        {"residentName": "Ada", "hasPet": "Yes", "petType": "Dog", "petWeight": 60, "largePetDeposit": False},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == {"largePetDeposit": "must be checked"}


def test_unknown_fields_are_rejected(client):
    response = _submit(client, {"residentName": "Ada", "hasPet": "No", "bogus": 1})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == {"bogus": "is not a field on this form"}


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("petWeight", "heavy", "must be a number"),
        ("moveInDate", "01/02/2024", "must be a date in YYYY-MM-DD format"),
        ("hasPet", "Maybe", "must be one of: Yes, No"),
    ],
)
def test_type_validation(client, field, value, message):
    data = {"residentName": "Ada", "hasPet": "Yes", "petType": "Dog", "petWeight": 10}
    data[field] = value
    response = _submit(client, data)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["errors"][field] == message


def test_empty_strings_count_as_unanswered(client):
    response = _submit(client, {"residentName": "   ", "hasPet": "No", "comments": ""})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == {"residentName": "is required"}


# -- retrieving ---------------------------------------------------------------

def _seed(client):
    ids = []
    for name in ["Ada", "Grace", "Linus"]:
        ids.append(_submit(client, {"residentName": name, "hasPet": "No"}).json()["id"])
    # A submission on another form.
    other = client.post(
        "/forms/contact-form/submissions",
        json={"data": {"firstName": "Other", "lastName": "Person"}},
    )
    assert other.status_code == 201, other.text
    ids.append(other.json()["id"])
    return ids


def test_list_submissions_for_a_form(client):
    ids = _seed(client)
    response = client.get("/forms/pet-registration/submissions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["id"] for item in body["items"]} == set(ids[:3])
    assert all(item["formId"] == "pet-registration" for item in body["items"])


def test_list_submissions_for_unknown_form_is_404(client):
    assert client.get("/forms/nope/submissions").status_code == 404


def test_list_all_submissions_with_optional_filter(client):
    _seed(client)
    everything = client.get("/submissions").json()
    assert everything["total"] == 4

    filtered = client.get("/submissions", params={"formId": "contact-form"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["data"] == {"firstName": "Other", "lastName": "Person"}


def test_list_supports_limit_and_offset(client):
    _seed(client)
    page = client.get("/submissions", params={"limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2

    count_only = client.get("/submissions", params={"limit": 0}).json()
    assert count_only == {"total": 4, "items": []}

    rest = client.get("/submissions", params={"offset": 2}).json()
    assert len(rest["items"]) == 2
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in rest["items"]})


def test_get_single_submission(client):
    ids = _seed(client)
    response = client.get(f"/submissions/{ids[1]}")
    assert response.status_code == 200
    assert response.json()["data"]["residentName"] == "Grace"


def test_get_missing_submission_is_404(client):
    assert client.get("/submissions/sub-missing").status_code == 404


def test_submissions_survive_form_rename(client):
    """Stored submissions keep the form name at the time of submission."""
    _seed(client)
    renamed = dict(PET_FORM, name="Animal Registration")
    assert client.put("/forms/pet-registration", json=renamed).status_code == 200
    item = client.get("/forms/pet-registration/submissions").json()["items"][0]
    assert item["formName"] == "Pet Registration"
