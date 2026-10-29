"""PostgreSQL implementation of :class:`repositories.base.FormRepository`.

Forms are soft-deleted (``deleted_at``) because submissions reference them
with ``ON DELETE RESTRICT``: ``DELETE /forms/{id}`` hides the form from the
API while its submissions stay queryable. ``exists`` deliberately sees
deleted rows so a deleted slug is never handed to a new form.

Database errors propagate unchanged; ``main.py`` turns them into clear
responses.
"""

from __future__ import annotations

from psycopg import errors

from core.database import Database
from db.mapping import form_to_row, row_to_form
from models.forms import FormConfig

_COLUMNS = "id, name, description, fields, created_at, updated_at"

# Verifies every column this repository uses exists (start-up / health check).
SCHEMA_PROBE = f"SELECT {_COLUMNS}, seq, deleted_at FROM forms WHERE false"


class PgFormRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_forms(self) -> list[FormConfig]:
        with self._db.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM forms WHERE deleted_at IS NULL ORDER BY seq"
            ).fetchall()
        return [row_to_form(row) for row in rows]

    def get_form(self, form_id: str) -> FormConfig | None:
        with self._db.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM forms WHERE id = %s AND deleted_at IS NULL", (form_id,)
            ).fetchone()
        return row_to_form(row) if row else None

    def exists(self, form_id: str) -> bool:
        """True for live *and* deleted forms: the id is taken either way."""
        with self._db.connection() as conn:
            return conn.execute("SELECT 1 FROM forms WHERE id = %s", (form_id,)).fetchone() is not None

    def create_form(self, form: FormConfig) -> FormConfig:
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO forms (id, name, description, fields, created_at, updated_at)
                    VALUES (%(id)s, %(name)s, %(description)s, %(fields)s,
                            COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()))
                    """,
                    form_to_row(form),
                )
        except errors.UniqueViolation as exc:
            raise ValueError(f"Form '{form.id}' already exists") from exc
        return form

    def update_form(self, form: FormConfig) -> FormConfig:
        with self._db.connection() as conn:
            result = conn.execute(
                """
                UPDATE forms
                   SET name = %(name)s, description = %(description)s, fields = %(fields)s,
                       updated_at = COALESCE(%(updated_at)s, now())
                 WHERE id = %(id)s AND deleted_at IS NULL
                """,
                form_to_row(form),
            )
            if result.rowcount == 0:
                raise KeyError(form.id)
        return form

    def delete_form(self, form_id: str) -> bool:
        with self._db.connection() as conn:
            result = conn.execute(
                "UPDATE forms SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL", (form_id,)
            )
            return result.rowcount == 1
