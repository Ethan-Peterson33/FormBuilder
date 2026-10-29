"""PostgreSQL schema reproducibility tests.

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL server whose
role may ``CREATE DATABASE`` (the Docker Compose setup does). Every test
module run gets a brand-new scratch database: the migrations are applied from
nothing, the seed files ``backend/data/*.json`` are imported, and the data is
read back and compared with the files. The scratch database is dropped
afterwards, so the development database is never touched.

    DATABASE_URL=postgresql://formbuilder:formbuilder@localhost:5432/formbuilder \
        python -m pytest tests/test_db_schema.py -v
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from core.config import BACKEND_DIR  # noqa: E402
from db import import_json, migrate  # noqa: E402
from db.mapping import (  # noqa: E402
    format_timestamp,
    parse_timestamp,
    row_to_form,
    row_to_option_list,
    row_to_submission,
)
from models.forms import FormConfig  # noqa: E402
from models.submissions import Submission, SubmissionMetadata  # noqa: E402
from tests.conftest import DATABASE_URL, requires_database  # noqa: E402

REAL_DATA_DIR = BACKEND_DIR / "data"

pytestmark = requires_database


# -- fixtures -----------------------------------------------------------------

@pytest.fixture(scope="module")
def scratch_database_url() -> Iterator[str]:
    """Create an empty database on the configured server and drop it afterwards."""
    name = f"formbuilder_test_{uuid.uuid4().hex[:12]}"
    try:
        admin = psycopg.connect(DATABASE_URL, autocommit=True)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable at DATABASE_URL: {exc}")
    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield psycopg.conninfo.make_conninfo(DATABASE_URL, dbname=name)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def migrated_db(scratch_database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to the scratch database with all migrations applied."""
    applied = migrate.migrate(scratch_database_url)
    assert applied == [migrate.version_of(p) for p in migrate.migration_files()]
    with psycopg.connect(scratch_database_url, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def db(migrated_db: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """Per-test connection: every test runs in a transaction that is rolled back."""
    with migrated_db.transaction(force_rollback=True):
        yield migrated_db


def _scalar(conn: psycopg.Connection, sql: str, params=None):
    row = conn.execute(sql, params).fetchone()
    return next(iter(row.values()))


# -- migrations ---------------------------------------------------------------

def test_migrations_apply_from_scratch_and_are_idempotent(migrated_db, scratch_database_url):
    recorded = {r["version"]: r["checksum"] for r in migrated_db.execute("SELECT version, checksum FROM schema_migrations")}
    assert recorded == {migrate.version_of(p): migrate.checksum_of(p) for p in migrate.migration_files()}
    # Second run: nothing pending, nothing applied, no error.
    assert migrate.pending_migrations(migrated_db) == []
    assert migrate.migrate(scratch_database_url) == []
    assert [s.label for s in migrate.migration_states(migrated_db)] == ["applied"] * len(migrate.migration_files())


def test_apply_migrations_refuses_non_autocommit_connection(migrated_db):
    with pytest.raises(ValueError, match="autocommit"):
        migrate.apply_migrations(migrated_db)


def test_migration_files_are_well_formed():
    files = migrate.migration_files()
    assert files, "no migrations found"
    for path in files:
        sql = path.read_text(encoding="utf-8")
        assert sql.lstrip().startswith(("--", "BEGIN;")), f"{path.name}: must start with BEGIN;"
        assert "BEGIN;" in sql and sql.rstrip().endswith("COMMIT;"), f"{path.name}: must be wrapped in BEGIN/COMMIT"
        assert f"VALUES ('{path.stem}')" in sql, f"{path.name}: must record itself in schema_migrations"
    versions = [p.stem for p in files]
    assert versions == sorted(versions)
    assert all(v[:4].isdigit() and v[4] == "_" for v in versions), "name migrations NNNN_description.sql"


def test_expected_tables_and_columns(db):
    rows = db.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    columns = {(r["table_name"], r["column_name"]): (r["data_type"], r["is_nullable"]) for r in rows}

    expected = {
        ("forms", "id"): ("text", "NO"),
        ("forms", "name"): ("text", "NO"),
        ("forms", "description"): ("text", "NO"),
        ("forms", "fields"): ("jsonb", "NO"),
        ("forms", "created_at"): ("timestamp with time zone", "NO"),
        ("forms", "updated_at"): ("timestamp with time zone", "NO"),
        ("forms", "deleted_at"): ("timestamp with time zone", "YES"),
        ("forms", "seq"): ("bigint", "NO"),  # added by 0002, hence last
        ("option_lists", "id"): ("text", "NO"),
        ("option_lists", "name"): ("text", "NO"),
        ("option_lists", "description"): ("text", "NO"),
        ("option_lists", "options"): ("ARRAY", "NO"),
        ("option_lists", "created_at"): ("timestamp with time zone", "NO"),
        ("option_lists", "updated_at"): ("timestamp with time zone", "NO"),
        ("submissions", "id"): ("text", "NO"),
        ("submissions", "seq"): ("bigint", "NO"),
        ("submissions", "form_id"): ("text", "NO"),
        ("submissions", "form_name"): ("text", "NO"),
        ("submissions", "data"): ("jsonb", "NO"),
        ("submissions", "submitted_at"): ("timestamp with time zone", "NO"),
        ("submissions", "submitted_by"): ("text", "YES"),
        ("submissions", "source"): ("text", "NO"),
        ("submissions", "user_agent"): ("text", "YES"),
        ("submissions", "ip_address"): ("text", "YES"),
        ("submissions", "hidden_fields"): ("ARRAY", "NO"),
        ("submissions", "metadata"): ("jsonb", "NO"),
        ("data_tables", "id"): ("text", "NO"),
        ("data_tables", "table_name"): ("text", "YES"),  # generated columns report nullable
        ("data_tables", "seq"): ("bigint", "NO"),
        ("data_tables", "name"): ("text", "NO"),
        ("data_tables", "description"): ("text", "NO"),
        ("data_tables", "created_at"): ("timestamp with time zone", "NO"),
        ("data_tables", "updated_at"): ("timestamp with time zone", "NO"),
        ("data_table_columns", "table_id"): ("text", "NO"),
        ("data_table_columns", "id"): ("text", "NO"),
        ("data_table_columns", "position"): ("integer", "NO"),
        ("data_table_columns", "name"): ("text", "NO"),
        ("data_table_columns", "type"): ("text", "NO"),
        ("data_table_columns", "required"): ("boolean", "NO"),
        ("data_table_columns", "description"): ("text", "NO"),
        ("data_table_columns", "options"): ("ARRAY", "YES"),
        ("data_table_columns", "created_at"): ("timestamp with time zone", "NO"),
        ("data_table_columns", "updated_at"): ("timestamp with time zone", "NO"),
        ("schema_migrations", "version"): ("text", "NO"),
        ("schema_migrations", "applied_at"): ("timestamp with time zone", "NO"),
        ("schema_migrations", "checksum"): ("text", "YES"),
    }
    assert columns == expected
    schemas = {r["schema_name"] for r in db.execute("SELECT schema_name FROM information_schema.schemata")}
    assert "user_tables" in schemas
    identity = {
        (r["table_name"], r["column_name"])
        for r in rows
        if r["column_name"] == "seq"
    }
    assert identity == {("forms", "seq"), ("submissions", "seq"), ("data_tables", "seq")}

    indexes = {r["indexname"] for r in db.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    assert {
        "forms_pkey", "forms_active_seq_idx",
        "option_lists_pkey", "option_lists_name_unique",
        "submissions_pkey", "submissions_form_recent_idx", "submissions_recent_idx", "submissions_data_gin",
        "data_tables_pkey", "data_tables_name_unique", "data_tables_table_name_unique",
        "data_table_columns_pkey", "data_table_columns_name_unique", "data_table_columns_position_unique",
        "schema_migrations_pkey",
    } <= indexes


# -- importing the real data files --------------------------------------------

def _json_records(data_dir: Path):
    """The files as the importer reads them, in the API's listing orders."""
    raw_forms = json.loads((data_dir / "forms.json").read_text(encoding="utf-8"))
    assert import_json.repair_form_ids(raw_forms) == [], "checked-in forms.json must not need repair"
    forms, lists, subs = import_json.load_json_data(data_dir)
    lists = sorted(lists, key=lambda item: item.name.lower())
    # Newest first; ties broken by file position (later = newer).
    subs = [s for _, s in sorted(enumerate(subs), key=lambda p: (p[1].metadata.submitted_at, p[0]), reverse=True)]
    return forms, lists, subs


def _db_forms(conn) -> list[FormConfig]:
    rows = conn.execute("SELECT * FROM forms WHERE deleted_at IS NULL ORDER BY seq").fetchall()
    return [row_to_form(r) for r in rows]


def _db_option_lists(conn):
    # COLLATE "C" reproduces Python's ``sorted(key=str.lower)`` byte order.
    rows = conn.execute('SELECT * FROM option_lists ORDER BY lower(name) COLLATE "C"').fetchall()
    return [row_to_option_list(r) for r in rows]


def _db_submissions(conn, form_id: str | None = None) -> list[Submission]:
    rows = conn.execute(
        """
        SELECT * FROM submissions
        WHERE %(form_id)s::text IS NULL OR form_id = %(form_id)s
        ORDER BY submitted_at DESC, seq DESC
        """,
        {"form_id": form_id},
    ).fetchall()
    return [row_to_submission(r) for r in rows]


def test_real_data_files_import_and_round_trip(db):
    report = import_json.import_data_dir(db, REAL_DATA_DIR)
    json_forms, json_lists, json_subs = _json_records(REAL_DATA_DIR)
    assert (report.forms, report.option_lists, report.submissions) == (len(json_forms), len(json_lists), len(json_subs))
    assert report.forms >= 1 and report.submissions >= 1, "the checked-in data should not be empty"

    # Forms: identical apart from timestamps that the JSON never had; same order.
    by_id = {f.id: f for f in _db_forms(db)}
    assert list(by_id) == [f.id for f in json_forms]
    for original in json_forms:
        stored = by_id[original.id]
        assert stored.model_dump(exclude={"created_at", "updated_at"}) == original.model_dump(
            exclude={"created_at", "updated_at"}
        )
        assert stored.fields == original.fields  # order, logic, options preserved
        if original.updated_at:
            assert stored.updated_at == original.updated_at
        if original.created_at:
            assert stored.created_at == original.created_at
        # Never "created after last update" for legacy records.
        assert parse_timestamp(stored.created_at) <= parse_timestamp(stored.updated_at)

    # Option lists: identical, including listing order.
    assert [o.model_dump() for o in _db_option_lists(db)] == [o.model_dump() for o in json_lists]

    # Submissions: identical, including newest-first order and client extras.
    assert [s.model_dump() for s in _db_submissions(db)] == [s.model_dump() for s in json_subs]
    for form in json_forms:
        expected = [s for s in json_subs if s.form_id == form.id]
        assert [s.id for s in _db_submissions(db, form.id)] == [s.id for s in expected]


def test_import_is_idempotent_and_preserves_timestamps(db):
    first = import_json.import_data_dir(db, REAL_DATA_DIR)
    snapshot = db.execute("SELECT id, created_at, updated_at FROM forms ORDER BY id").fetchall()
    seqs = db.execute("SELECT id, seq FROM submissions ORDER BY seq").fetchall()

    second = import_json.import_data_dir(db, REAL_DATA_DIR)

    assert (second.forms, second.option_lists, second.submissions) == (first.forms, first.option_lists, first.submissions)
    assert _scalar(db, "SELECT count(*) FROM forms") == first.forms
    assert _scalar(db, "SELECT count(*) FROM option_lists") == first.option_lists
    assert _scalar(db, "SELECT count(*) FROM submissions") == first.submissions
    assert db.execute("SELECT id, created_at, updated_at FROM forms ORDER BY id").fetchall() == snapshot
    assert db.execute("SELECT id, seq FROM submissions ORDER BY seq").fetchall() == seqs


def test_submission_metadata_extras_survive_round_trip(db):
    """Unknown metadata keys (pageUrl, clientHiddenFields, ...) are kept verbatim."""
    import_json.import_data_dir(db, REAL_DATA_DIR)
    raw = json.loads((REAL_DATA_DIR / "submissions.json").read_text(encoding="utf-8"))
    for record in raw:
        stored = _db_submissions(db, record["formId"])
        match = next(s for s in stored if s.id == record["id"])
        assert match.to_record() == Submission.model_validate(record).to_record()


def test_import_of_legacy_file_shape(db, tmp_path: Path):
    """The baseline file with a blank id imports after the id repair."""
    (tmp_path / "forms.json").write_text(json.dumps([
        {"id": "contact-form", "name": "Contact Form", "fields": [{"id": "a", "type": "text", "label": "A"}]},
        {"id": "", "name": "Contact Form", "fields": [{"id": "b", "type": "text", "label": "B"}]},
    ]))
    report = import_json.import_data_dir(db, tmp_path)
    assert report.forms == 2 and report.option_lists == 0 and report.submissions == 0
    assert [f.id for f in _db_forms(db)] == ["contact-form", "contact-form-2"]
    # Legacy records get real timestamps in the database.
    assert all(f.created_at and f.updated_at for f in _db_forms(db))


def test_orphan_submission_policies(db, tmp_path: Path):
    sub = {
        "id": "sub-orphan", "formId": "gone", "formName": "Gone Form", "data": {"x": "1"},
        "metadata": {"submittedAt": "2025-01-01T00:00:00.000Z", "submittedBy": None, "source": "web",
                     "userAgent": None, "ipAddress": None, "hiddenFields": []},
    }
    (tmp_path / "forms.json").write_text("[]")
    (tmp_path / "submissions.json").write_text(json.dumps([sub]))

    with pytest.raises(import_json.OrphanSubmissionsError):
        import_json.import_data_dir(db, tmp_path)  # default: fail loudly

    report = import_json.import_data_dir(db, tmp_path, orphans="skip")
    assert report.submissions == 0 and report.skipped_submissions == ["sub-orphan"]

    report = import_json.import_data_dir(db, tmp_path, orphans="placeholder")
    assert report.submissions == 1 and report.placeholder_forms == ["gone"]
    assert _db_forms(db) == []  # placeholder is soft-deleted: hidden from listings
    assert _scalar(db, "SELECT deleted_at IS NOT NULL FROM forms WHERE id = 'gone'") is True
    assert [s.id for s in _db_submissions(db, "gone")] == ["sub-orphan"]


# -- constraints the database enforces ---------------------------------------

def _insert_form(conn, form_id="f", name="F", fields="[]"):
    conn.execute(
        "INSERT INTO forms (id, name, fields) VALUES (%s, %s, %s::jsonb)", (form_id, name, fields)
    )


def test_blank_ids_and_names_are_rejected(db):
    for sql, params in [
        ("INSERT INTO forms (id, name) VALUES (%s, %s)", ("", "Contact Form")),
        ("INSERT INTO forms (id, name) VALUES (%s, %s)", ("  ", "Contact Form")),
        ("INSERT INTO forms (id, name) VALUES (%s, %s)", ("ok", "   ")),
        ("INSERT INTO option_lists (id, name, options) VALUES (%s, %s, %s)", ("", "Yes / No", ["Yes"])),
        ("INSERT INTO submissions (id, form_id, form_name) VALUES (%s, %s, %s)", ("", "x", "X")),
    ]:
        with pytest.raises(errors.CheckViolation), db.transaction():
            db.execute(sql, params)


def test_form_fields_must_be_a_json_array(db):
    with pytest.raises(errors.CheckViolation), db.transaction():
        _insert_form(db, fields='{"id": "x"}')
    _insert_form(db, fields='[{"id": "x", "type": "text", "label": "X"}]')


def test_option_list_names_are_unique_case_insensitively(db):
    db.execute("INSERT INTO option_lists (id, name, options) VALUES ('yes-no', 'Yes / No', %s)", (["Yes", "No"],))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute("INSERT INTO option_lists (id, name, options) VALUES ('yes-no-2', 'YES / no', %s)", (["Y"],))


def test_option_list_needs_at_least_one_option(db):
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO option_lists (id, name, options) VALUES ('empty', 'Empty', %s)", ([],))


def test_option_list_name_length_limit_matches_model(db):
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO option_lists (id, name, options) VALUES ('long', %s, %s)", ("x" * 121, ["a"]))


def test_submission_requires_existing_form(db):
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        db.execute("INSERT INTO submissions (id, form_id, form_name) VALUES ('sub-1', 'nope', 'Nope')")


def test_form_with_submissions_cannot_be_hard_deleted(db):
    _insert_form(db)
    db.execute("INSERT INTO submissions (id, form_id, form_name) VALUES ('sub-1', 'f', 'F')")
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        db.execute("DELETE FROM forms WHERE id = 'f'")
    # Soft delete is the supported path; the submission stays reachable.
    db.execute("UPDATE forms SET deleted_at = now() WHERE id = 'f'")
    assert _db_forms(db) == []
    assert [s.id for s in _db_submissions(db, "f")] == ["sub-1"]


def test_submission_json_columns_must_be_objects(db):
    _insert_form(db)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO submissions (id, form_id, form_name, data) VALUES ('s', 'f', 'F', '[]'::jsonb)")
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO submissions (id, form_id, form_name, metadata) VALUES ('s', 'f', 'F', '\"x\"'::jsonb)")


def test_submissions_listing_order_ties_broken_by_insertion(db):
    """Same timestamp: the later insert is listed first, as with the JSON file."""
    _insert_form(db)
    at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for sub_id in ("sub-a", "sub-b", "sub-c"):
        db.execute(
            "INSERT INTO submissions (id, form_id, form_name, submitted_at) VALUES (%s, 'f', 'F', %s)", (sub_id, at)
        )
    assert [s.id for s in _db_submissions(db, "f")] == ["sub-c", "sub-b", "sub-a"]
    assert all(s.metadata.submitted_at == "2025-01-01T00:00:00.000Z" for s in _db_submissions(db, "f"))


def test_submission_defaults_match_the_model(db):
    _insert_form(db)
    db.execute("INSERT INTO submissions (id, form_id, form_name) VALUES ('s', 'f', 'F')")
    stored = _db_submissions(db, "f")[0]
    assert stored.data == {}
    assert stored.metadata.source == "web"
    assert stored.metadata.hidden_fields == []
    assert stored.metadata.submitted_by is None and stored.metadata.user_agent is None
    assert set(stored.metadata.model_dump(by_alias=True)) == {
        info.alias for info in SubmissionMetadata.model_fields.values()
    }


def test_jsonb_answer_queries(db):
    """The GIN index target: containment and key extraction on submission data."""
    import_json.import_data_dir(db, REAL_DATA_DIR)
    with_pets = db.execute("SELECT id FROM submissions WHERE data @> %s ORDER BY id", (Jsonb({"hasPet": "Yes"}),)).fetchall()
    assert {r["id"] for r in with_pets} == {
        s.id for s in _db_submissions(db) if s.data.get("hasPet") == "Yes"
    }
    heavy = db.execute("SELECT count(*) AS n FROM submissions WHERE (data ->> 'petWeight')::numeric > 50").fetchone()
    assert heavy["n"] == sum(1 for s in _db_submissions(db) if isinstance(s.data.get("petWeight"), (int, float)) and s.data["petWeight"] > 50)


def test_timestamp_format_round_trip():
    assert format_timestamp(parse_timestamp("2026-10-25T16:34:52.425Z")) == "2026-10-25T16:34:52.425Z"
    assert format_timestamp(parse_timestamp("2026-10-25T16:34:52Z")) == "2026-10-25T16:34:52.000Z"
    assert format_timestamp(parse_timestamp("2026-10-25T18:34:52.425+02:00")) == "2026-10-25T16:34:52.425Z"
    assert parse_timestamp(None) is None and format_timestamp(None) is None


# -- existing installations: the upgrade path ---------------------------------
#
# New installs run every migration file in order on an empty database (the
# container does this on first start). Existing installations run the files
# they have not seen yet. Both must end in the same schema, and an applied
# file must never change. These tests pin that down.

import shutil  # noqa: E402
import subprocess  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from core.config import Settings  # noqa: E402
from core.database import Database  # noqa: E402
from main import SCHEMA_PROBES, create_app  # noqa: E402


@pytest.fixture
def make_db():
    """Factory for empty scratch databases, all dropped after the test."""
    created: list[str] = []

    def _make() -> str:
        name = f"formbuilder_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return psycopg.conninfo.make_conninfo(DATABASE_URL, dbname=name)

    yield _make
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        for name in created:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _catalog(url: str) -> dict:
    """Everything that defines the schema: columns, identity, indexes, constraints, comments."""
    with psycopg.connect(url, row_factory=dict_row) as conn:
        columns = conn.execute(
            """
            SELECT table_name, column_name, ordinal_position, data_type, is_nullable, column_default,
                   is_identity, identity_generation
            FROM information_schema.columns WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
        indexes = conn.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2"
        ).fetchall()
        constraints = conn.execute(
            """
            SELECT conrelid::regclass::text AS table_name, conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2
            """
        ).fetchall()
        comments = conn.execute(
            """
            SELECT c.relname, a.attname, d.description
            FROM pg_description d
            JOIN pg_class c ON c.oid = d.objoid
            LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid AND d.objsubid > 0
            WHERE c.relnamespace = 'public'::regnamespace ORDER BY 1, 2
            """
        ).fetchall()
    return {"columns": columns, "indexes": indexes, "constraints": constraints, "comments": comments}


def _partial_migrations_dir(tmp_path: Path, count: int) -> Path:
    """A migrations directory containing only the first ``count`` files."""
    directory = tmp_path / f"migrations_{count}"
    directory.mkdir()
    for path in migrate.migration_files()[:count]:
        shutil.copy(path, directory / path.name)
    return directory


def _seed_like_an_older_release(url: str, version_count: int) -> None:
    """Insert data using only what the schema had at that version."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO forms (id, name, fields, created_at) VALUES "
            "('second', 'Second', '[]', '2025-02-01T00:00:00Z'), "
            "('first', 'First', '[{\"id\":\"a\",\"type\":\"text\",\"label\":\"A\"}]', '2025-01-01T00:00:00Z'), "
            "('third', 'Third', '[]', '2025-03-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO option_lists (id, name, options) VALUES ('yes-no', 'Yes / No', %s)", (["Yes", "No"],))
        conn.execute(
            "INSERT INTO submissions (id, form_id, form_name, data) VALUES ('sub-1', 'first', 'First', '{\"a\": \"x\"}')"
        )


def test_upgrade_from_every_earlier_version_matches_a_fresh_install(make_db, tmp_path: Path):
    files = migrate.migration_files()
    assert len(files) >= 2, "needs at least one upgrade step"

    fresh = make_db()
    assert migrate.migrate(fresh) == [migrate.version_of(p) for p in files]
    expected = _catalog(fresh)

    for count in range(1, len(files)):
        existing = make_db()
        older = _partial_migrations_dir(tmp_path, count)
        with psycopg.connect(existing, autocommit=True) as conn:
            assert migrate.apply_migrations(conn, older) == [migrate.version_of(p) for p in files[:count]]
        _seed_like_an_older_release(existing, count)

        # The upgrade: the full migrations directory against the older database.
        applied = migrate.migrate(existing)
        assert applied == [migrate.version_of(p) for p in files[count:]], f"upgrade from {count} file(s)"
        assert migrate.migrate(existing) == []  # idempotent

        assert _catalog(existing) == expected, f"schema after upgrading from {count} file(s) differs from a fresh install"

        with psycopg.connect(existing, row_factory=dict_row) as conn:
            # Data preserved. Forms seeded before 0002 are numbered by creation
            # order (0002's back-fill); from 0002 on, seq already existed when
            # they were inserted, so it is insertion order. New ones continue the sequence.
            expected_order = ["first", "second", "third"] if count < 2 else ["second", "first", "third"]
            order = [r["id"] for r in conn.execute("SELECT id FROM forms ORDER BY seq")]
            assert order == expected_order, f"upgrade from {count} file(s)"
            conn.execute("INSERT INTO forms (id, name) VALUES ('fourth', 'Fourth')")
            assert [r["id"] for r in conn.execute("SELECT id FROM forms ORDER BY seq")] == [*expected_order, "fourth"]
            assert conn.execute("SELECT count(*) AS n FROM submissions").fetchone()["n"] == 1
            assert conn.execute("SELECT options FROM option_lists").fetchone()["options"] == ["Yes", "No"]
            recorded = {r["version"]: r["checksum"] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            assert recorded == {migrate.version_of(p): migrate.checksum_of(p) for p in files}


def test_forms_route_works_on_an_upgraded_database(make_db, tmp_path: Path):
    """The scenario from the field: a database created by the first release,
    then the current code pointed at it."""
    url = make_db()
    with psycopg.connect(url, autocommit=True) as conn:
        migrate.apply_migrations(conn, _partial_migrations_dir(tmp_path, 1))
    _seed_like_an_older_release(url, 1)

    app = create_app(Settings(database_url=url))
    with TestClient(app) as client:
        # Before upgrading: the problem is named, not hidden behind "up to date".
        health = client.get("/health")
        assert health.status_code == 503
        assert health.json()["database"]["pendingMigrations"] == [migrate.version_of(p) for p in migrate.migration_files()[1:]]
        assert client.get("/forms").status_code == 500

        assert migrate.migrate(url) == [migrate.version_of(p) for p in migrate.migration_files()[1:]]

        # After upgrading, with the same running process.
        assert client.get("/health").status_code == 200
        assert [f["id"] for f in client.get("/forms").json()] == ["first", "second", "third"]
        created = client.post("/forms", json={"name": "Fourth", "fields": []})
        assert created.status_code == 201, created.text
        assert [f["id"] for f in client.get("/forms").json()] == ["first", "second", "third", "fourth"]
        assert client.put("/forms/fourth", json={**created.json(), "name": "4th"}).json()["name"] == "4th"
        assert client.get("/forms/first").json()["fields"][0]["id"] == "a"
        assert client.post("/forms/first/submissions", json={"data": {"a": "hello"}}).status_code == 201
        assert client.get("/forms/first/submissions").json()["total"] == 2
        assert client.delete("/forms/fourth").status_code == 204
        assert [f["id"] for f in client.get("/forms").json()] == ["first", "second", "third"]


def test_editing_an_applied_migration_is_refused(make_db, tmp_path: Path):
    url = make_db()
    directory = _partial_migrations_dir(tmp_path, len(migrate.migration_files()))
    with psycopg.connect(url, autocommit=True) as conn:
        migrate.apply_migrations(conn, directory)
        first = next(iter(sorted(directory.glob("*.sql"))))
        first.write_text(first.read_text() + "\n-- edited after it was applied\n")

        states = migrate.migration_states(conn, directory)
        assert states[0].modified and states[0].label == "MODIFIED"
        with pytest.raises(migrate.MigrationDriftError, match="changed after being applied"):
            migrate.apply_migrations(conn, directory)


def test_0002_is_harmless_on_a_schema_that_already_had_seq(make_db, tmp_path: Path):
    """A database built from the short-lived 0001 that already contained
    forms.seq (as shipped between the first two releases) upgrades cleanly."""
    url = make_db()
    with psycopg.connect(url, autocommit=True) as conn:
        migrate.apply_migrations(conn, _partial_migrations_dir(tmp_path, 1))
        conn.execute("ALTER TABLE forms ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY")
        conn.execute("DROP INDEX forms_active_name_idx")
        conn.execute("CREATE INDEX forms_active_seq_idx ON forms (seq) WHERE deleted_at IS NULL")
    _seed_like_an_older_release(url, 1)
    assert "0002_forms_seq" in migrate.migrate(url)
    fresh = make_db()
    migrate.migrate(fresh)
    upgraded, expected = _catalog(url), _catalog(fresh)
    # Column order differs (seq was added earlier) but nothing else may.
    strip = lambda cat: {**cat, "columns": sorted(({k: v for k, v in c.items() if k != "ordinal_position"} for c in cat["columns"]), key=lambda c: (c["table_name"], c["column_name"]))}
    assert strip(upgraded) == strip(expected)


@pytest.mark.skipif(shutil.which("psql") is None, reason="psql not installed")
def test_container_init_path_and_runner_agree(make_db):
    """The Postgres image runs the files with psql on first start; the runner
    must then see a fully applied database and adopt checksums."""
    url = make_db()
    for path in migrate.migration_files():
        subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-q", "-d", url, "-f", str(path)], check=True)
    with psycopg.connect(url, autocommit=True) as conn:
        assert migrate.pending_migrations(conn) == []
        assert all(s.checksum_recorded is None for s in migrate.migration_states(conn))
        assert migrate.apply_migrations(conn) == []
        assert [s.label for s in migrate.migration_states(conn)] == ["applied"] * len(migrate.migration_files())
    fresh = make_db()
    migrate.migrate(fresh)
    assert _catalog(url) == _catalog(fresh)


def test_health_reports_a_schema_that_does_not_match_its_migration_record(make_db):
    """schema_migrations says everything is applied, but a column is missing
    (what an edited migration file leaves behind)."""
    url = make_db()
    migrate.migrate(url)
    db = Database(url, schema_probes=SCHEMA_PROBES)
    db.open()
    try:
        assert db.check().ok
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("ALTER TABLE forms DROP COLUMN seq")
        status = db.check()
        assert not status.ok and status.pending_migrations == []
        assert status.error["message"] == "Database schema does not match the applied migrations"
        assert status.error["sqlstate"] == "42703" and 'column "seq" does not exist' in status.error["error"]
        assert "--status" in status.error["hint"]
    finally:
        db.close()
