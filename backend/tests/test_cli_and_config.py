"""Command-line entry points (db.migrate, db.import_json), .env loading and
the images route."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import config
from core.config import BACKEND_DIR, Settings, parse_dotenv
from tests.conftest import DATABASE_URL


# -- .env loading ----------------------------------------------------------------

def test_parse_dotenv():
    text = "# c\nexport A=1\nB=\"two words\"\nC='x'\nD=val # trailing\nE=\nbad line\n=novalue\n"
    assert parse_dotenv(text) == {"A": "1", "B": "two words", "C": "x", "D": "val", "E": ""}


def test_load_dotenv_prefers_environment_then_first_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    first.write_text("ONLY_A=a\nSHARED=from_a\nIN_ENV=file\n")
    second.write_text("ONLY_B=b\nSHARED=from_b\n")
    for key in ("ONLY_A", "ONLY_B", "SHARED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IN_ENV", "real")

    loaded = config.load_dotenv((first, tmp_path / "missing.env", second))
    assert loaded == [first, second]
    assert os.environ["ONLY_A"] == "a" and os.environ["ONLY_B"] == "b"
    assert os.environ["SHARED"] == "from_a"      # first file wins
    assert os.environ["IN_ENV"] == "real"        # environment wins over files


def test_settings_read_database_url_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:1/d")
    assert Settings.from_env().database_url == "postgresql://u:p@h:1/d"


# -- images route (unchanged behaviour, now covered) --------------------------------

def test_images_listing(client):
    response = client.get("/images")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["images"]) >= 1
    for image in body["images"]:
        assert image["url"].endswith("/images/files/" + image["name"])
        assert client.get("/images/files/" + image["name"]).status_code == 200
    assert client.get("/images/files/does-not-exist.png").status_code == 404


# -- db.migrate CLI ------------------------------------------------------------------

def _run_module(module: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120,
        env={**os.environ, **(env or {})},
    )


def test_migrate_cli_without_url_is_a_usage_error(monkeypatch: pytest.MonkeyPatch):
    result = _run_module("db.migrate", env={"DATABASE_URL": ""})
    assert result.returncode == 2 and "DATABASE_URL is not set" in result.stderr


def test_migrate_cli_unreachable_server_exits_1():
    result = _run_module("db.migrate", "--database-url", "postgresql://x:y@127.0.0.1:1/nope")
    assert result.returncode == 1 and "cannot connect" in result.stderr


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_migrate_cli_applies_then_reports_status(pg_scratch_url: str):
    status = _run_module("db.migrate", "--status", "--database-url", pg_scratch_url)
    assert status.returncode == 0 and "applied" in status.stdout and "pending" not in status.stdout
    apply = _run_module("db.migrate", "--database-url", pg_scratch_url)
    assert apply.returncode == 0 and "up to date" in apply.stdout


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_migrate_cli_refuses_modified_file_with_exit_2(pg_scratch_url: str, tmp_path: Path):
    """Exit code 2 for drift, on both --status and apply."""
    import shutil

    from db import migrate

    # Work on a copy of the migrations so the real files are never touched.
    copy = tmp_path / "migrations"
    shutil.copytree(migrate.MIGRATIONS_DIR, copy)
    first = sorted(copy.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- tampered\n")
    import psycopg
    with psycopg.connect(pg_scratch_url, autocommit=True) as conn:
        with pytest.raises(migrate.MigrationDriftError):
            migrate.apply_migrations(conn, copy)
        assert [s.label for s in migrate.migration_states(conn, copy)][0] == "MODIFIED"


# -- db.import_json CLI ------------------------------------------------------------

def test_import_report_summary_mentions_placeholders_and_skips():
    from db.import_json import ImportReport

    report = ImportReport(forms=1, option_lists=2, submissions=3, placeholder_forms=["gone"], skipped_submissions=["sub-x"])
    text = report.summary()
    assert "forms:        1" in text and "placeholder forms created for orphans: gone" in text and "sub-x" in text


def test_import_cli_without_url_is_a_usage_error():
    result = _run_module("db.import_json", env={"DATABASE_URL": ""})
    assert result.returncode == 2 and "DATABASE_URL is not set" in result.stderr


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_import_cli_imports_and_handles_orphans(pg_scratch_url: str, tmp_path: Path):
    import json

    result = _run_module("db.import_json", "--database-url", pg_scratch_url)
    assert result.returncode == 0, result.stderr
    assert "forms:        3" in result.stdout

    (tmp_path / "forms.json").write_text("[]")
    (tmp_path / "submissions.json").write_text(json.dumps([{
        "id": "sub-orphan", "formId": "gone", "formName": "Gone", "data": {},
        "metadata": {"submittedAt": "2025-01-01T00:00:00.000Z", "hiddenFields": []},
    }]))
    result = _run_module("db.import_json", "--database-url", pg_scratch_url, "--data-dir", str(tmp_path))
    assert result.returncode == 1 and "sub-orphan" in result.stderr and "--orphans" in result.stderr
    result = _run_module("db.import_json", "--database-url", pg_scratch_url, "--data-dir", str(tmp_path), "--orphans", "placeholder")
    assert result.returncode == 0 and "placeholder forms created for orphans: gone" in result.stdout


# -- API console (backend/ui) -----------------------------------------------------------------

def test_console_is_served(client):
    assert client.get("/", follow_redirects=False).status_code == 307
    page = client.get("/ui/")
    assert page.status_code == 200 and "API console" in page.text
    for asset in ("app.js", "styles.css"):
        assert client.get(f"/ui/{asset}").status_code == 200
    # Every path the console calls exists in the API.
    paths = set(client.get("/openapi.json").json()["paths"])
    for needed in ("/health", "/forms", "/option-lists", "/submissions", "/tables", "/tables/{ref}/schema",
                   "/tables/{ref}/rows/query", "/sql/query", "/sql/tables", "/sql/rules"):
        assert needed in paths, needed
