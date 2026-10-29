"""Tests for the /forms endpoints."""

from __future__ import annotations

from tests.conftest import CONTACT_FORM, requires_database

pytestmark = requires_database


def _stored_forms(url: str) -> list[dict]:
    """The rows as stored, bypassing the API."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM forms WHERE deleted_at IS NULL ORDER BY seq").fetchall()


def _new_form(**overrides):
    form = {
        "id": "",
        "name": "Resident Inspection",
        "description": "Quarterly walkthrough",
        "fields": [
            {"id": "f1", "type": "text", "label": "Unit", "required": True, "placeholder": ""},
            {"id": "f2", "type": "number", "label": "Occupants", "required": False},
        ],
    }
    form.update(overrides)
    return form


# -- listing / fetching -------------------------------------------------------

def test_list_forms_returns_all_saved_configs(client):
    response = client.get("/forms")
    assert response.status_code == 200
    forms = response.json()
    assert len(forms) == 1
    assert forms[0]["id"] == "contact-form"
    # Full config is returned, not just a summary.
    assert [f["id"] for f in forms[0]["fields"]] == ["firstName", "lastName", "email", "contactMethod"]
    assert forms[0]["fields"][3]["options"] == ["Email", "Phone", "Text"]


def test_get_form_by_id(client):
    response = client.get("/forms/contact-form")
    assert response.status_code == 200
    assert response.json()["name"] == "Contact Form"


def test_get_missing_form_is_404(client):
    response = client.get("/forms/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Form not found"


def test_list_forms_with_empty_table(client):
    assert client.delete("/forms/contact-form").status_code == 204
    assert client.get("/forms").json() == []


# -- creating -----------------------------------------------------------------

def test_create_form_generates_id_from_name_and_persists(client, pg_url: str):
    response = client.post("/forms", json=_new_form())
    assert response.status_code == 201, response.text
    saved = response.json()

    assert saved["id"] == "resident-inspection"
    assert saved["name"] == "Resident Inspection"
    assert saved["createdAt"] and saved["updatedAt"]
    assert [f["id"] for f in saved["fields"]] == ["f1", "f2"]

    # Written to the database, not just echoed back.
    stored = _stored_forms(pg_url)
    assert [f["id"] for f in stored] == ["contact-form", "resident-inspection"]
    assert stored[1]["fields"][0]["label"] == "Unit"


def test_create_form_without_id_key_is_allowed(client):
    payload = _new_form()
    del payload["id"]
    response = client.post("/forms", json=payload)
    assert response.status_code == 201
    assert response.json()["id"] == "resident-inspection"


def test_create_form_with_duplicate_name_gets_unique_id(client):
    first = client.post("/forms", json=_new_form()).json()
    second = client.post("/forms", json=_new_form()).json()
    third = client.post("/forms", json=_new_form()).json()
    assert [first["id"], second["id"], third["id"]] == [
        "resident-inspection",
        "resident-inspection-2",
        "resident-inspection-3",
    ]


def test_create_form_honours_requested_id(client):
    response = client.post("/forms", json=_new_form(id="Move In Checklist"))
    assert response.status_code == 201
    assert response.json()["id"] == "move-in-checklist"


def test_create_form_with_existing_requested_id_is_409(client):
    response = client.post("/forms", json=_new_form(id="contact-form"))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_form_requires_name(client):
    response = client.post("/forms", json=_new_form(name="   "))
    assert response.status_code == 422


def test_create_form_rejects_duplicate_field_ids(client):
    payload = _new_form()
    payload["fields"][1]["id"] = "f1"
    response = client.post("/forms", json=payload)
    assert response.status_code == 422
    assert "Duplicate field id" in response.text


def test_create_form_rejects_unknown_field_type(client):
    payload = _new_form()
    payload["fields"][0]["type"] = "signature"
    assert client.post("/forms", json=payload).status_code == 422


def test_select_field_needs_options(client):
    payload = _new_form()
    payload["fields"].append({"id": "f3", "type": "select", "label": "Choice", "options": []})
    response = client.post("/forms", json=payload)
    assert response.status_code == 422
    assert "needs at least one option" in response.text


def test_new_form_is_listed_after_creation(client):
    client.post("/forms", json=_new_form())
    ids = [form["id"] for form in client.get("/forms").json()]
    assert ids == ["contact-form", "resident-inspection"]


# -- updating / deleting ------------------------------------------------------

def test_update_form_replaces_config_and_keeps_created_at(client, pg_url: str):
    created = client.post("/forms", json=_new_form()).json()

    updated_payload = _new_form(name="Resident Inspection v2")
    updated_payload["fields"].append({"id": "f3", "type": "date", "label": "Inspected On"})
    response = client.put(f"/forms/{created['id']}", json=updated_payload)
    assert response.status_code == 200, response.text
    updated = response.json()

    assert updated["id"] == created["id"]  # id is stable even though the name changed
    assert updated["name"] == "Resident Inspection v2"
    assert updated["createdAt"] == created["createdAt"]
    assert len(updated["fields"]) == 3

    assert _stored_forms(pg_url)[1]["name"] == "Resident Inspection v2"


def test_update_missing_form_is_404(client):
    assert client.put("/forms/nope", json=_new_form()).status_code == 404


def test_delete_form(client, pg_url: str):
    assert client.delete("/forms/contact-form").status_code == 204
    assert client.get("/forms/contact-form").status_code == 404
    assert _stored_forms(pg_url) == []


def test_delete_missing_form_is_404(client):
    assert client.delete("/forms/nope").status_code == 404


# -- legacy data --------------------------------------------------------------

def test_records_seeded_without_timestamps_still_load(client):
    """CONTACT_FORM is inserted without createdAt/updatedAt; the database fills them."""
    assert "createdAt" not in CONTACT_FORM
    response = client.get("/forms/contact-form")
    assert response.status_code == 200 and response.json()["createdAt"]


# -- options ------------------------------------------------------------------

def test_choice_field_rejects_duplicate_options(client):
    payload = _new_form()
    payload["fields"].append(
        {"id": "f3", "type": "radio", "label": "Pet?", "options": ["Yes", "No", " yes "]}
    )
    response = client.post("/forms", json=payload)
    assert response.status_code == 422
    assert "duplicate options" in response.text


def test_choice_field_options_are_trimmed(client):
    payload = _new_form()
    payload["fields"].append(
        {"id": "f3", "type": "select", "label": "How?", "options": [" Email", "Phone ", ""]}
    )
    saved = client.post("/forms", json=payload).json()
    assert saved["fields"][2]["options"] == ["Email", "Phone"]


# -- round trip ---------------------------------------------------------------

def test_get_response_is_valid_put_input(client):
    """Editing means GET -> change -> PUT; the server-managed keys the GET
    returns (id, createdAt, updatedAt) must not be rejected, only ignored."""
    created = client.post("/forms", json=_new_form()).json()
    assert created["createdAt"] and created["updatedAt"]
    tampered = {**created, "name": "Renamed", "id": "other-id", "createdAt": "1999-01-01T00:00:00.000Z"}
    response = client.put(f"/forms/{created['id']}", json=tampered)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["id"] == created["id"] and body["createdAt"] == created["createdAt"]
    assert body["updatedAt"] >= created["updatedAt"]


def test_generated_id_race_is_retried_not_reported(settings, form_repo, submission_repo, option_list_repo):
    """Two concurrent creates with the same name can pick the same generated
    slug; the loser must take the next one instead of answering 409."""
    from fastapi.testclient import TestClient
    from core.dependencies import get_form_repository, get_option_list_repository, get_submission_repository
    from main import create_app

    class Racing:
        """Delegates to the real repository; the first `failures` inserts
        behave as if another request had just taken the id."""

        def __init__(self, inner, failures):
            self.inner, self.failures, self.attempts = inner, failures, []

        def create_form(self, form):
            self.attempts.append(form.id)
            if len(self.attempts) <= self.failures:
                self.inner.create_form(form)  # the "other" request wins the id
                raise ValueError(f"Form '{form.id}' already exists")
            return self.inner.create_form(form)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    racing = Racing(form_repo, failures=2)
    app = create_app(settings)
    app.dependency_overrides[get_form_repository] = lambda: racing
    app.dependency_overrides[get_submission_repository] = lambda: submission_repo
    app.dependency_overrides[get_option_list_repository] = lambda: option_list_repo
    with TestClient(app) as client:
        response = client.post("/forms", json=_new_form())
        assert response.status_code == 201, response.text
        assert response.json()["id"] == "resident-inspection-3"
        assert racing.attempts == ["resident-inspection", "resident-inspection-2", "resident-inspection-3"]

        # An explicitly requested id is the client's choice: a clash is a 409.
        racing.failures, racing.attempts = 10, []
        response = client.post("/forms", json=_new_form(id="explicit-id"))
        assert response.status_code == 409
        assert racing.attempts == ["explicit-id"]


def test_remaining_race_branches_map_to_http_errors(settings, form_repo, submission_repo, option_list_repo):
    """Exhausted id retries -> 409; form deleted between lookup and write -> 404
    (update and submission create)."""
    from fastapi.testclient import TestClient
    from core.dependencies import get_form_repository, get_option_list_repository, get_submission_repository
    from main import create_app

    class AlwaysTaken:
        def __init__(self, inner):
            self.inner = inner

        def create_form(self, form):
            raise ValueError(f"Form '{form.id}' already exists")

        def update_form(self, form):
            raise KeyError(form.id)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class Orphaning:
        def __init__(self, inner):
            self.inner = inner

        def create_submission(self, submission):
            raise KeyError(submission.form_id)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    app = create_app(settings)
    app.dependency_overrides[get_form_repository] = lambda: AlwaysTaken(form_repo)
    app.dependency_overrides[get_submission_repository] = lambda: Orphaning(submission_repo)
    app.dependency_overrides[get_option_list_repository] = lambda: option_list_repo
    with TestClient(app) as client:
        response = client.post("/forms", json=_new_form())
        assert response.status_code == 409 and "too many concurrent creates" in response.json()["detail"]
        assert client.put("/forms/contact-form", json=_new_form()).status_code == 404
        response = client.post("/forms/contact-form/submissions", json={"data": {"firstName": "A", "lastName": "B"}})
        assert response.status_code == 404 and response.json()["detail"] == "Form not found"
