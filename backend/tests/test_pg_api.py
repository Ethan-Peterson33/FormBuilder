"""Storage behaviour: soft-deleted forms, legacy rows seeded straight into the
tables, and - above all - how database failures surface. Skipped unless
``DATABASE_URL`` is set.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")
from psycopg.types.json import Jsonb  # noqa: E402

from core.config import Settings, StorageConfigurationError  # noqa: E402
from core.database import Database  # noqa: E402
from main import create_app  # noqa: E402
from repositories.pg_forms import PgFormRepository  # noqa: E402
from repositories.pg_option_lists import PgOptionListRepository  # noqa: E402
from repositories.pg_submissions import PgSubmissionRepository  # noqa: E402
from db import migrate  # noqa: E402
from models.forms import FormConfig  # noqa: E402
from models.option_lists import OptionList  # noqa: E402
from tests.conftest import CONTACT_FORM, DATABASE_URL, make_scratch_database, drop_scratch_database, requires_database  # noqa: E402

pytestmark = requires_database

ALL_VERSIONS = [migrate.version_of(p) for p in migrate.migration_files()]

PET_FORM = {
    "id": "pet-registration",
    "name": "Pet Registration",
    "fields": [
        {"id": "residentName", "type": "text", "label": "Resident", "required": True},
        {"id": "hasPet", "type": "radio", "label": "Pet?", "required": True, "options": ["Yes", "No"]},
        {
            "id": "petType", "type": "select", "label": "Type", "required": True,
            "options": ["Dog", "Cat"],
            "logic": {"action": "show", "match": "all", "conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Yes"}]},
        },
    ],
}


def _seed_raw_form(url: str, record: dict) -> None:
    """Insert a form row bypassing the API, as an older version might have."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO forms (id, name, description, fields) VALUES (%s, %s, %s, %s)",
            (record["id"], record["name"], record.get("description", ""), Jsonb(record["fields"])),
        )


# -- soft delete ---------------------------------------------------------------

def test_deleted_form_keeps_its_submissions_and_its_id(pg_client: TestClient, pg_url: str):
    client = pg_client
    assert client.post("/forms", json=PET_FORM).status_code == 201
    sub = client.post("/forms/pet-registration/submissions", json={"data": {"residentName": "Ada", "hasPet": "No"}})
    assert sub.status_code == 201

    assert client.delete("/forms/pet-registration").status_code == 204
    assert client.delete("/forms/pet-registration").status_code == 404  # idempotent
    assert client.get("/forms/pet-registration").status_code == 404
    assert [f["id"] for f in client.get("/forms").json()] == ["contact-form"]
    assert client.put("/forms/pet-registration", json=PET_FORM).status_code == 404

    # Submissions are evidence: still there, still addressable.
    assert client.get(f"/submissions/{sub.json()['id']}").status_code == 200
    assert client.get("/submissions?formId=pet-registration").json()["total"] == 1
    assert client.get("/forms/pet-registration/submissions").json()["total"] == 1

    # The slug stays reserved; a namesake gets the next free one.
    assert client.post("/forms", json=PET_FORM).status_code == 409
    namesake = client.post("/forms", json={**PET_FORM, "id": ""})
    assert namesake.status_code == 201 and namesake.json()["id"] == "pet-registration-2"

    with psycopg.connect(pg_url) as conn:
        row = conn.execute("SELECT deleted_at IS NOT NULL AS gone FROM forms WHERE id = 'pet-registration'").fetchone()
        assert row == (True,)


# -- legacy rows ---------------------------------------------------------------

def test_legacy_row_with_invalid_rules_loads_and_must_be_fixed_before_saving(pg_client: TestClient, pg_url: str):
    broken = {
        "id": "legacy", "name": "Legacy",
        "fields": [
            {"id": "a", "type": "text", "label": "A",
             "logic": {"action": "show", "match": "all", "conditions": [{"fieldId": "ghost", "operator": "equals", "value": "x"}]}},
            {"id": "b", "type": "select", "label": "B", "options": []},
        ],
    }
    _seed_raw_form(pg_url, broken)
    client = pg_client

    loaded = client.get("/forms/legacy")
    assert loaded.status_code == 200
    assert loaded.json()["fields"][0]["logic"]["conditions"][0]["fieldId"] == "ghost"
    assert loaded.json()["createdAt"] and loaded.json()["updatedAt"]  # database defaults

    assert client.post("/forms/legacy/submissions", json={"data": {"b": "anything"}}).status_code == 201

    unchanged = client.put("/forms/legacy", json=loaded.json())
    assert unchanged.status_code == 422
    problems = [p["msg"] for p in unchanged.json()["detail"]]
    assert any("ghost" in p for p in problems) and any("needs at least one option" in p for p in problems)


def test_list_order_is_creation_order(pg_client: TestClient):
    for name in ["Zeta", "Alpha", "Mid"]:
        assert pg_client.post("/forms", json={"name": name, "fields": []}).status_code == 201
    assert [f["id"] for f in pg_client.get("/forms").json()] == ["contact-form", "zeta", "alpha", "mid"]


# -- repositories directly -----------------------------------------------------

def test_repository_race_conditions_raise_the_protocol_errors(pg_url: str):
    db = Database(pg_url)
    db.open()
    try:
        forms = PgFormRepository(db)
        form = FormConfig.model_validate(PET_FORM)
        forms.create_form(form)
        with pytest.raises(ValueError, match="already exists"):
            forms.create_form(form)
        assert forms.delete_form(form.id) is True
        with pytest.raises(KeyError):
            forms.update_form(form)
        assert forms.exists(form.id) is True and forms.get_form(form.id) is None

        lists = PgOptionListRepository(db)
        lists.create_option_list(OptionList(id="yes-no", name="Yes / No", options=["Yes", "No"]))
        with pytest.raises(ValueError, match="named"):
            lists.create_option_list(OptionList(id="other", name="yes / NO", options=["Y"]))
        with pytest.raises(ValueError, match="'yes-no' already exists"):
            lists.create_option_list(OptionList(id="yes-no", name="Different", options=["Y"]))
        with pytest.raises(KeyError):
            lists.update_option_list(OptionList(id="missing", name="Missing", options=["Y"]))

        subs = PgSubmissionRepository(db)
        from services.submissions import build_submission
        from models.submissions import SubmissionCreate
        orphan = build_submission(FormConfig(id="nope", name="Nope", fields=[]), SubmissionCreate(data={}))
        with pytest.raises(KeyError):
            subs.create_submission(orphan)
    finally:
        db.close()


# -- health --------------------------------------------------------------------

def test_health_reports_database_status(pg_client: TestClient):
    body = pg_client.get("/health").json()
    assert body["status"] == "ok" and set(body) == {"status", "database"}
    assert body["database"]["ok"] is True
    assert body["database"]["appliedMigrations"] == ALL_VERSIONS
    assert body["database"]["pendingMigrations"] == [] and body["database"]["error"] is None
    assert body["database"]["serverVersion"].startswith("1")


# -- failures are loud ---------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_unreachable_database_is_a_clear_503(caplog):
    url = f"postgresql://formbuilder:formbuilder@127.0.0.1:{_free_port()}/formbuilder"
    app = create_app(Settings(database_url=url, database_timeout=0.5))
    with TestClient(app) as client:
        assert "Database problem at start-up" in caplog.text

        for method, path, kwargs in [
            ("GET", "/forms", {}),
            ("POST", "/forms", {"json": PET_FORM}),
            ("GET", "/forms/contact-form/submissions", {}),
            ("GET", "/submissions", {}),
            ("GET", "/option-lists", {}),
            ("POST", "/option-lists", {"json": {"name": "X", "options": ["a"]}}),
        ]:
            response = client.request(method, path, **kwargs)
            assert response.status_code == 503, (path, response.text)
            detail = response.json()["detail"]
            assert detail["message"] == "Database unavailable"
            assert detail["type"] in {"PoolTimeout", "OperationalError"}
            assert detail["error"]
            assert "DATABASE_URL" in detail["hint"]

        health = client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "error"
        assert health.json()["database"]["error"]["message"] == "Database unavailable"


def test_missing_schema_is_a_clear_500_with_migrate_hint():
    """A reachable database that was never migrated."""
    url = make_scratch_database(DATABASE_URL)
    try:
        app = create_app(Settings(database_url=url))
        with TestClient(app) as client:
            health = client.get("/health")
            assert health.status_code == 503
            assert health.json()["database"]["pendingMigrations"] == ALL_VERSIONS
            assert "db.migrate" in health.json()["database"]["error"]["hint"]

            response = client.get("/forms")
            assert response.status_code == 500
            detail = response.json()["detail"]
            assert detail["message"] == "Database schema is missing or out of date"
            assert detail["type"] == "UndefinedTable" and detail["sqlstate"] == "42P01"
            assert 'relation "forms" does not exist' in detail["error"]
            assert "db.migrate" in detail["hint"]

            # Fix it the documented way; the running app recovers without a restart.
            assert migrate.migrate(url) == ALL_VERSIONS
            assert client.get("/health").status_code == 200
            assert client.get("/forms").json() == []
    finally:
        drop_scratch_database(DATABASE_URL, url)


def test_constraint_violations_bypassing_the_api_are_reported(pg_client: TestClient, pg_url: str):
    """Errors from inside a statement carry SQLSTATE and constraint name."""
    from psycopg import errors as pg_errors
    from core.database import describe_database_error

    with psycopg.connect(pg_url) as conn, pytest.raises(pg_errors.CheckViolation) as info:
        conn.execute("INSERT INTO forms (id, name) VALUES ('', 'Blank')")
    status, detail = describe_database_error(info.value)
    assert status == 500
    assert detail["sqlstate"] == "23514" and detail["constraint"] == "forms_id_not_blank"
    assert "forms_id_not_blank" in detail["error"]


def test_misconfiguration_stops_startup():
    with pytest.raises(StorageConfigurationError, match="DATABASE_URL is not set"):
        create_app(Settings(database_url=None))
    with pytest.raises(StorageConfigurationError, match="DATABASE_URL is not set"):
        Settings(database_url="")


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("FORMBUILDER_DB_TIMEOUT", "2.5")
    settings = Settings.from_env()
    assert settings.database_url == "postgresql://u:p@h/db" and settings.database_timeout == 2.5

    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(StorageConfigurationError):
        Settings.from_env()
