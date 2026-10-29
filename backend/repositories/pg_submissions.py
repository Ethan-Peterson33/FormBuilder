"""PostgreSQL implementation of :class:`repositories.base.SubmissionRepository`.

Listings are newest first; equal timestamps are ordered by insertion
(``seq``), exactly like the JSON file's "later in the file = newer".
"""

from __future__ import annotations

from psycopg import errors

from core.database import Database
from db.mapping import row_to_submission, submission_to_row
from models.submissions import Submission

_COLUMNS = (
    "id, form_id, form_name, data, submitted_at, submitted_by, source, "
    "user_agent, ip_address, hidden_fields, metadata"
)

# Verifies every column this repository uses exists (start-up / health check).
SCHEMA_PROBE = f"SELECT {_COLUMNS}, seq FROM submissions WHERE false"


class PgSubmissionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_submissions(
        self,
        form_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Submission]:
        with self._db.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM submissions
                 WHERE %(form_id)s::text IS NULL OR form_id = %(form_id)s
                 ORDER BY submitted_at DESC, seq DESC
                 LIMIT %(limit)s OFFSET %(offset)s
                """,
                {"form_id": form_id, "limit": limit, "offset": offset},
            ).fetchall()
        return [row_to_submission(row) for row in rows]

    def count_submissions(self, form_id: str | None = None) -> int:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM submissions WHERE %(form_id)s::text IS NULL OR form_id = %(form_id)s",
                {"form_id": form_id},
            ).fetchone()
        return int(row["n"])

    def get_submission(self, submission_id: str) -> Submission | None:
        with self._db.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM submissions WHERE id = %s", (submission_id,)
            ).fetchone()
        return row_to_submission(row) if row else None

    def create_submission(self, submission: Submission) -> Submission:
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO submissions (id, form_id, form_name, data, submitted_at, submitted_by,
                                             source, user_agent, ip_address, hidden_fields, metadata)
                    VALUES (%(id)s, %(form_id)s, %(form_name)s, %(data)s, %(submitted_at)s, %(submitted_by)s,
                            %(source)s, %(user_agent)s, %(ip_address)s, %(hidden_fields)s, %(metadata)s)
                    """,
                    submission_to_row(submission),
                )
        except errors.ForeignKeyViolation as exc:
            raise KeyError(submission.form_id) from exc
        return submission
