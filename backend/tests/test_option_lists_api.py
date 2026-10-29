"""Tests for the reusable option list endpoints and their persistence."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.dependencies import get_option_list_repository
from main import create_app
from models.option_lists import OptionList
from tests.conftest import requires_database

pytestmark = requires_database


def _stored(url: str, table: str) -> list[dict]:
    """The rows as stored, bypassing the API."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


def _payload(**overrides):
    data = {"name": "Contact Methods", "description": "How to reach people", "options": ["Email", "Phone", "Text"]}
    data.update(overrides)
    return data


# -- create -------------------------------------------------------------------

def test_create_option_list_persists_to_its_own_table(client, pg_url: str):
    response = client.post("/option-lists", json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == "contact-methods"
    assert body["name"] == "Contact Methods"
    assert body["options"] == ["Email", "Phone", "Text"]
    assert body["createdAt"] and body["updatedAt"]

    stored = _stored(pg_url, "option_lists")
    assert [(r["id"], r["name"], r["description"], r["options"]) for r in stored] == [
        ("contact-methods", "Contact Methods", "How to reach people", ["Email", "Phone", "Text"])
    ]
    # forms are untouched
    assert [f["id"] for f in _stored(pg_url, "forms")] == ["contact-form"]


def test_options_are_trimmed_and_empties_dropped(client):
    response = client.post("/option-lists", json=_payload(options=[" Email ", "", "Phone", "   "]))
    assert response.status_code == 201
    assert response.json()["options"] == ["Email", "Phone"]


def test_duplicate_options_rejected_case_insensitively(client):
    response = client.post("/option-lists", json=_payload(options=["Yes", "No", "yes"]))
    assert response.status_code == 422
    assert "Duplicate options: yes" in response.text


def test_empty_options_rejected(client):
    response = client.post("/option-lists", json=_payload(options=["", " "]))
    assert response.status_code == 422
    assert "at least one option" in response.text


def test_name_required(client):
    assert client.post("/option-lists", json=_payload(name="  ")).status_code == 422


def test_duplicate_name_is_409(client):
    assert client.post("/option-lists", json=_payload()).status_code == 201
    response = client.post("/option-lists", json=_payload(name="contact methods", options=["A"]))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_requested_id_is_honoured_and_conflicts_are_409(client):
    assert client.post("/option-lists", json=_payload(id="My Methods")).json()["id"] == "my-methods"
    response = client.post("/option-lists", json=_payload(name="Other", id="my-methods"))
    assert response.status_code == 409


# -- read ---------------------------------------------------------------------

def test_list_is_sorted_by_name(client):
    client.post("/option-lists", json=_payload(name="Zeta", options=["1"]))
    client.post("/option-lists", json=_payload(name="alpha", options=["1"]))
    client.post("/option-lists", json=_payload(name="Mid", options=["1"]))
    names = [item["name"] for item in client.get("/option-lists").json()]
    assert names == ["alpha", "Mid", "Zeta"]


def test_get_by_id_and_404(client):
    created = client.post("/option-lists", json=_payload()).json()
    assert client.get(f"/option-lists/{created['id']}").json()["options"] == ["Email", "Phone", "Text"]
    assert client.get("/option-lists/nope").status_code == 404


# -- update / delete ----------------------------------------------------------

def test_update_replaces_options_and_keeps_id(client, pg_url: str):
    created = client.post("/option-lists", json=_payload()).json()
    response = client.put(
        f"/option-lists/{created['id']}",
        json=_payload(name="Preferred Contact", options=["Phone", "Email", "Text", "Mail"]),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["id"] == "contact-methods"
    assert updated["name"] == "Preferred Contact"
    assert updated["options"] == ["Phone", "Email", "Text", "Mail"]
    assert updated["createdAt"] == created["createdAt"]

    assert _stored(pg_url, "option_lists")[0]["options"] == ["Phone", "Email", "Text", "Mail"]


def test_update_cannot_take_another_lists_name(client):
    client.post("/option-lists", json=_payload(name="A", options=["1"]))
    b = client.post("/option-lists", json=_payload(name="B", options=["1"])).json()
    assert client.put(f"/option-lists/{b['id']}", json=_payload(name="a", options=["1"])).status_code == 409
    # Renaming to its own name (different case) is fine.
    assert client.put(f"/option-lists/{b['id']}", json=_payload(name="b", options=["1"])).status_code == 200


def test_update_missing_is_404(client):
    assert client.put("/option-lists/nope", json=_payload()).status_code == 404


def test_delete(client, pg_url: str):
    created = client.post("/option-lists", json=_payload()).json()
    assert client.delete(f"/option-lists/{created['id']}").status_code == 204
    assert client.get(f"/option-lists/{created['id']}").status_code == 404
    assert client.delete(f"/option-lists/{created['id']}").status_code == 404
    assert _stored(pg_url, "option_lists") == []


# -- independence from forms --------------------------------------------------

def test_editing_a_list_does_not_change_forms_that_used_it(client):
    """Loading a list copies options into a field; forms hold their own copy."""
    created = client.post("/option-lists", json=_payload()).json()
    form = {
        "name": "Uses List",
        "fields": [
            {"id": "how", "type": "select", "label": "How?", "options": created["options"]},
        ],
    }
    saved_form = client.post("/forms", json=form).json()

    client.put(f"/option-lists/{created['id']}", json=_payload(options=["Carrier Pigeon"]))
    client.delete(f"/option-lists/{created['id']}")

    fetched = client.get(f"/forms/{saved_form['id']}").json()
    assert fetched["fields"][0]["options"] == ["Email", "Phone", "Text"]


# -- persistence across restarts ----------------------------------------------

def test_saved_lists_survive_an_application_restart(settings):
    """Simulate a restart: a brand-new app (and pool) over the same database."""
    with TestClient(create_app(settings)) as first:
        created = first.post("/option-lists", json=_payload()).json()
        first.post("/option-lists", json=_payload(name="Yes / No", options=["Yes", "No"]))

    with TestClient(create_app(settings)) as second:  # fresh process state, same database
        names = [item["name"] for item in second.get("/option-lists").json()]
        assert names == ["Contact Methods", "Yes / No"]
        assert second.get(f"/option-lists/{created['id']}").json()["options"] == ["Email", "Phone", "Text"]


def test_get_response_is_valid_put_input(client):
    created = client.post("/option-lists", json={"name": "Sizes", "options": ["S", "M"]}).json()
    response = client.put(f"/option-lists/{created['id']}", json={**created, "options": ["S", "M", "L"]})
    assert response.status_code == 200, response.text
    assert response.json()["options"] == ["S", "M", "L"]
    assert response.json()["createdAt"] == created["createdAt"]


def test_generated_id_race_is_retried_but_name_conflicts_are_409(settings, option_list_repo, form_repo, submission_repo):
    """Mirror of the forms test: a clash on a generated id is retried, a clash
    on the (unique) name is a real conflict."""
    from core.dependencies import get_form_repository, get_submission_repository

    class Racing:
        def __init__(self, inner):
            self.inner, self.id_failures, self.name_failure, self.attempts = inner, 0, False, []

        def create_option_list(self, option_list):
            self.attempts.append(option_list.id)
            if self.name_failure:
                raise ValueError(f"An option list named '{option_list.name}' already exists")
            if self.id_failures:
                self.id_failures -= 1
                # The other request wins the id: a different name with the same slug ("Sizes!").
                self.inner.create_option_list(option_list.model_copy(update={"name": option_list.name + "!"}))
                raise ValueError(f"Option list '{option_list.id}' already exists")
            return self.inner.create_option_list(option_list)

        def update_option_list(self, option_list):
            if self.name_failure:
                raise ValueError(f"An option list named '{option_list.name}' already exists")
            return self.inner.update_option_list(option_list)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    racing = Racing(option_list_repo)
    app = create_app(settings)
    app.dependency_overrides[get_option_list_repository] = lambda: racing
    app.dependency_overrides[get_form_repository] = lambda: form_repo
    app.dependency_overrides[get_submission_repository] = lambda: submission_repo
    with TestClient(app) as client:
        racing.id_failures = 1
        response = client.post("/option-lists", json={"name": "Sizes", "options": ["S"]})
        assert response.status_code == 201 and response.json()["id"] == "sizes-2"
        assert racing.attempts == ["sizes", "sizes-2"]

        racing.name_failure = True
        assert client.post("/option-lists", json={"name": "Colours", "options": ["Red"]}).status_code == 409
        assert client.put("/option-lists/sizes-2", json={"name": "Sizes", "options": ["S", "M"]}).status_code == 409
        racing.name_failure = False

        # Explicit id that loses the race -> 409, no retry.
        racing.id_failures, racing.attempts = 5, []
        assert client.post("/option-lists", json={"id": "fixed", "name": "Fixed", "options": ["x"]}).status_code == 409
        assert racing.attempts == ["fixed"]


def test_exhausted_id_retries_and_vanished_list_on_update(settings, option_list_repo, form_repo, submission_repo):
    from core.dependencies import get_form_repository, get_submission_repository

    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def create_option_list(self, option_list):
            raise ValueError(f"Option list '{option_list.id}' already exists")

        def update_option_list(self, option_list):
            raise KeyError(option_list.id)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    app = create_app(settings)
    app.dependency_overrides[get_option_list_repository] = lambda: Broken(option_list_repo)
    app.dependency_overrides[get_form_repository] = lambda: form_repo
    app.dependency_overrides[get_submission_repository] = lambda: submission_repo
    with TestClient(app) as client:
        response = client.post("/option-lists", json={"name": "Sizes", "options": ["S"]})
        assert response.status_code == 409 and "too many concurrent creates" in response.json()["detail"]
        option_list_repo.create_option_list(OptionList(id="sizes", name="Sizes", options=["S"]))
        assert client.put("/option-lists/sizes", json={"name": "Sizes", "options": ["S", "M"]}).status_code == 404
