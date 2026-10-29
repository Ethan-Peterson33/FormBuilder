"""Load the JSON data files into PostgreSQL.

Reads ``forms.json``, ``option_lists.json`` and ``submissions.json`` (the
seed data in ``backend/data`` or a file backup from before PostgreSQL),
validates the records with the same models the API uses, and upserts them
into the tables created by ``db/migrations``.

Forms saved by very early builder versions had ``id: ""`` (and files edited
by hand may have duplicates); such records get a unique slug derived from
their name, the way the API did when it still read the files.

The import is idempotent: re-running it updates rows in place and preserves
original timestamps, so it can be used to seed an empty database and to
refresh a development database from the files.

Usage::

    cd backend
    DATABASE_URL=postgresql://formbuilder:formbuilder@localhost:5432/formbuilder \
        python -m db.import_json [--data-dir PATH] [--orphans fail|skip|placeholder]

Submissions whose form no longer exists in ``forms.json`` ("orphans") violate
the foreign key. ``--orphans fail`` (default) aborts and lists them;
``skip`` leaves them out; ``placeholder`` creates a soft-deleted form with the
submission's ``formName`` so the records are kept.
"""

from __future__ import annotations

import argparse
import logging
import sys
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from core.config import get_data_dir, get_database_url
from db.mapping import form_to_row, option_list_to_row, submission_to_row
from models.forms import FormConfig
from models.option_lists import OptionList
from models.submissions import Submission
from services.ids import unique_slug_id

logger = logging.getLogger(__name__)

OrphanPolicy = Literal["fail", "skip", "placeholder"]


class OrphanSubmissionsError(ValueError):
    def __init__(self, orphans: list[Submission]) -> None:
        self.orphans = orphans
        listing = ", ".join(f"{s.id} (formId={s.form_id!r})" for s in orphans)
        super().__init__(
            f"{len(orphans)} submission(s) reference forms that do not exist: {listing}. "
            "Re-run with --orphans skip or --orphans placeholder."
        )


@dataclass
class ImportReport:
    forms: int = 0
    option_lists: int = 0
    submissions: int = 0
    placeholder_forms: list[str] = field(default_factory=list)
    skipped_submissions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"forms:        {self.forms}",
            f"option lists: {self.option_lists}",
            f"submissions:  {self.submissions}",
        ]
        if self.placeholder_forms:
            lines.append(f"placeholder forms created for orphans: {', '.join(self.placeholder_forms)}")
        if self.skipped_submissions:
            lines.append(f"orphan submissions skipped: {', '.join(self.skipped_submissions)}")
        return "\n".join(lines)


# -- loading ------------------------------------------------------------------

def _read_array(path: Path) -> list[dict]:
    """The JSON array in ``path``; a missing or empty file is an empty list."""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return data


def repair_form_ids(records: list[dict]) -> list[tuple[str, str]]:
    """Give blank and duplicate form ids a unique slug (in place).

    Returns ``(old_id, new_id)`` pairs for the records that changed.
    """
    taken = {record["id"] for record in records if record.get("id")}
    seen: set[str] = set()
    changes: list[tuple[str, str]] = []
    for record in records:
        current = record.get("id") or ""
        if current and current not in seen:
            seen.add(current)
            continue
        new_id = unique_slug_id(record.get("name") or "form", taken.__contains__, fallback="form")
        taken.add(new_id)
        seen.add(new_id)
        record["id"] = new_id
        changes.append((current, new_id))
    return changes


def load_json_data(data_dir: Path) -> tuple[list[FormConfig], list[OptionList], list[Submission]]:
    """Read and validate the three files. Order is the file order."""
    form_records = _read_array(data_dir / "forms.json")
    for old, new in repair_form_ids(form_records):
        logger.warning("Repaired form id %r -> %r before import", old, new)
    forms = [FormConfig.model_validate(record) for record in form_records]
    option_lists = [OptionList.model_validate(r) for r in _read_array(data_dir / "option_lists.json")]
    # File order = insertion order, which the ``seq`` column preserves as the
    # tie-breaker for equal timestamps.
    submissions = [Submission.model_validate(r) for r in _read_array(data_dir / "submissions.json")]
    return forms, option_lists, submissions


# -- writing ------------------------------------------------------------------

_UPSERT_FORM = """
INSERT INTO forms (id, name, description, fields, created_at, updated_at, deleted_at)
VALUES (%(id)s, %(name)s, %(description)s, %(fields)s,
        COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()), %(deleted_at)s)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    fields      = EXCLUDED.fields,
    created_at  = COALESCE(%(created_at)s, forms.created_at),
    updated_at  = COALESCE(%(updated_at)s, forms.updated_at),
    deleted_at  = EXCLUDED.deleted_at
"""

_UPSERT_OPTION_LIST = """
INSERT INTO option_lists (id, name, description, options, created_at, updated_at)
VALUES (%(id)s, %(name)s, %(description)s, %(options)s,
        COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()))
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    options     = EXCLUDED.options,
    created_at  = COALESCE(%(created_at)s, option_lists.created_at),
    updated_at  = COALESCE(%(updated_at)s, option_lists.updated_at)
"""

_UPSERT_SUBMISSION = """
INSERT INTO submissions (id, form_id, form_name, data, submitted_at, submitted_by, source,
                         user_agent, ip_address, hidden_fields, metadata)
VALUES (%(id)s, %(form_id)s, %(form_name)s, %(data)s, %(submitted_at)s, %(submitted_by)s, %(source)s,
        %(user_agent)s, %(ip_address)s, %(hidden_fields)s, %(metadata)s)
ON CONFLICT (id) DO UPDATE SET
    form_id       = EXCLUDED.form_id,
    form_name     = EXCLUDED.form_name,
    data          = EXCLUDED.data,
    submitted_at  = EXCLUDED.submitted_at,
    submitted_by  = EXCLUDED.submitted_by,
    source        = EXCLUDED.source,
    user_agent    = EXCLUDED.user_agent,
    ip_address    = EXCLUDED.ip_address,
    hidden_fields = EXCLUDED.hidden_fields,
    metadata      = EXCLUDED.metadata
"""

_INSERT_PLACEHOLDER_FORM = """
INSERT INTO forms (id, name, description, fields, created_at, updated_at, deleted_at)
VALUES (%(id)s, %(name)s, %(description)s, %(fields)s, %(now)s, %(now)s, %(now)s)
ON CONFLICT (id) DO NOTHING
"""


def import_records(
    conn: psycopg.Connection,
    forms: list[FormConfig],
    option_lists: list[OptionList],
    submissions: list[Submission],
    *,
    orphans: OrphanPolicy = "fail",
) -> ImportReport:
    """Upsert the given records in a single transaction."""
    report = ImportReport()
    now = datetime.now(timezone.utc)  # only used for placeholder forms
    known_form_ids = {form.id for form in forms}
    orphan_submissions = [s for s in submissions if s.form_id not in known_form_ids]
    if orphan_submissions and orphans == "fail":
        raise OrphanSubmissionsError(orphan_submissions)

    with conn.transaction(), conn.cursor() as cur:
        # No fallback time here: a record without timestamps gets the database
        # default now() when first inserted and keeps that value on re-import.
        for form in forms:
            cur.execute(_UPSERT_FORM, form_to_row(form))
            report.forms += 1

        for option_list in option_lists:
            cur.execute(_UPSERT_OPTION_LIST, option_list_to_row(option_list))
            report.option_lists += 1

        if orphan_submissions and orphans == "placeholder":
            for form_id in sorted({s.form_id for s in orphan_submissions}):
                name = next(s.form_name for s in orphan_submissions if s.form_id == form_id)
                cur.execute(
                    _INSERT_PLACEHOLDER_FORM,
                    {
                        "id": form_id,
                        "name": name,
                        "description": "Placeholder created by db.import_json for submissions whose form was deleted.",
                        "fields": Jsonb([]),
                        "now": now,
                    },
                )
                report.placeholder_forms.append(form_id)

        for submission in submissions:
            if submission.form_id not in known_form_ids and orphans == "skip":
                report.skipped_submissions.append(submission.id)
                continue
            cur.execute(_UPSERT_SUBMISSION, submission_to_row(submission))
            report.submissions += 1

    return report


def import_data_dir(conn: psycopg.Connection, data_dir: Path, *, orphans: OrphanPolicy = "fail") -> ImportReport:
    forms, option_lists, submissions = load_json_data(data_dir)
    return import_records(conn, forms, option_lists, submissions, orphans=orphans)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import backend/data/*.json into PostgreSQL.")
    parser.add_argument("--database-url", default=get_database_url(), help="defaults to $DATABASE_URL")
    parser.add_argument("--data-dir", type=Path, default=None, help="defaults to $FORMBUILDER_DATA_DIR or backend/data")
    parser.add_argument("--orphans", choices=("fail", "skip", "placeholder"), default="fail")
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("DATABASE_URL is not set (or pass --database-url)")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    data_dir = args.data_dir or get_data_dir()
    try:
        with psycopg.connect(args.database_url) as conn:
            report = import_data_dir(conn, data_dir, orphans=args.orphans)
    except OrphanSubmissionsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Imported from {data_dir}:")
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(_main())
