"""Shared pytest fixtures.

Every API test runs against PostgreSQL: with ``DATABASE_URL`` set, a scratch
database is created once per session on that server, migrated, and emptied
before every test. The app is built exactly as in production
(``create_app(Settings(database_url=...))``), so the real wiring is exercised.
Without ``DATABASE_URL`` the database-backed tests are skipped; the pure
unit tests (rules engine, models, scripts) still run.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

# The real setting, or None. Read before the placeholder below is installed.
DATABASE_URL = os.environ.get("DATABASE_URL")

# ``main`` builds the production ``app`` at import time, which (correctly)
# refuses to start without DATABASE_URL. Tests never use that object - every
# fixture calls ``create_app(Settings(...))`` with an explicit URL - so let the
# import succeed on machines without a database. Nothing connects to this.
os.environ.setdefault("DATABASE_URL", "postgresql://not-configured.invalid/formbuilder")

from core.config import Settings  # noqa: E402
from core.database import Database  # noqa: E402
from main import create_app  # noqa: E402
from repositories.pg_forms import PgFormRepository  # noqa: E402
from repositories.pg_option_lists import PgOptionListRepository  # noqa: E402
from repositories.pg_submissions import PgSubmissionRepository  # noqa: E402

requires_database = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

CONTACT_FORM = {
    "id": "contact-form",
    "name": "Contact Form",
    "description": "A simple contact information form",
    "fields": [
        {"id": "firstName", "type": "text", "label": "First Name", "required": True},
        {"id": "lastName", "type": "text", "label": "Last Name", "required": True},
        {"id": "email", "type": "text", "label": "Email", "required": False},
        {
            "id": "contactMethod",
            "type": "select",
            "label": "Preferred Contact Method",
            "required": False,
            "options": ["Email", "Phone", "Text"],
        },
    ],
}

# Everything a test may have created, in an order TRUNCATE accepts.
APP_TABLES = ("submissions", "forms", "option_lists", "data_table_columns", "data_tables")


def make_scratch_database(base_url: str) -> str:
    """Create an empty database on the server at ``base_url``; returns its URL."""
    import psycopg

    name = f"formbuilder_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    return psycopg.conninfo.make_conninfo(base_url, dbname=name)


def drop_scratch_database(base_url: str, url: str) -> None:
    import psycopg

    name = psycopg.conninfo.conninfo_to_dict(url)["dbname"]
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def pg_scratch_url() -> Iterator[str]:
    """A migrated scratch database on the DATABASE_URL server, dropped afterwards."""
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from db.migrate import migrate

    try:
        url = make_scratch_database(DATABASE_URL)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable at DATABASE_URL: {exc}")
    try:
        migrate(url)
        yield url
    finally:
        drop_scratch_database(DATABASE_URL, url)


def reset_database(url: str, *, seed_contact_form: bool = True) -> None:
    """Empty the application tables, drop every user data table, and
    (optionally) seed ``CONTACT_FORM``."""
    import psycopg
    from psycopg.types.json import Jsonb

    from services.table_ddl import USER_SCHEMA

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(APP_TABLES)} RESTART IDENTITY")
        conn.execute(f"DROP SCHEMA {USER_SCHEMA} CASCADE; CREATE SCHEMA {USER_SCHEMA}")
        if seed_contact_form:
            conn.execute(
                "INSERT INTO forms (id, name, description, fields) VALUES (%s, %s, %s, %s)",
                (CONTACT_FORM["id"], CONTACT_FORM["name"], CONTACT_FORM["description"], Jsonb(CONTACT_FORM["fields"])),
            )


@pytest.fixture
def pg_url(pg_scratch_url: str) -> str:
    """The scratch database, emptied and seeded with CONTACT_FORM for one test."""
    reset_database(pg_scratch_url)
    return pg_scratch_url


@pytest.fixture
def settings(pg_url: str) -> Settings:
    return Settings(database_url=pg_url)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """The API on the scratch database, wired exactly like production."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# Alias kept for tests that want to say explicitly that they need PostgreSQL
# behaviour (soft delete, SQL errors); identical to ``client``.
pg_client = client


@pytest.fixture
def database(pg_url: str) -> Iterator[Database]:
    """An open pool on the scratch database, for using repositories directly."""
    db = Database(pg_url)
    db.open()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def form_repo(database: Database) -> PgFormRepository:
    return PgFormRepository(database)


@pytest.fixture
def submission_repo(database: Database) -> PgSubmissionRepository:
    return PgSubmissionRepository(database)


@pytest.fixture
def option_list_repo(database: Database) -> PgOptionListRepository:
    return PgOptionListRepository(database)
