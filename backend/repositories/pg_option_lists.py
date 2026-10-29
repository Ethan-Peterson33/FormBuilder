"""PostgreSQL implementation of :class:`repositories.base.OptionListRepository`."""

from __future__ import annotations

from psycopg import errors

from core.database import Database
from db.mapping import option_list_to_row, row_to_option_list
from models.option_lists import OptionList

_COLUMNS = "id, name, description, options, created_at, updated_at"

# Verifies every column this repository uses exists (start-up / health check).
SCHEMA_PROBE = f"SELECT {_COLUMNS} FROM option_lists WHERE false"


class PgOptionListRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_option_lists(self) -> list[OptionList]:
        # COLLATE "C": byte order, the same as Python's sorted(key=str.lower).
        with self._db.connection() as conn:
            rows = conn.execute(
                f'SELECT {_COLUMNS} FROM option_lists ORDER BY lower(name) COLLATE "C", id'
            ).fetchall()
        return [row_to_option_list(row) for row in rows]

    def get_option_list(self, list_id: str) -> OptionList | None:
        with self._db.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM option_lists WHERE id = %s", (list_id,)).fetchone()
        return row_to_option_list(row) if row else None

    def exists(self, list_id: str) -> bool:
        with self._db.connection() as conn:
            return conn.execute("SELECT 1 FROM option_lists WHERE id = %s", (list_id,)).fetchone() is not None

    def find_by_name(self, name: str) -> OptionList | None:
        with self._db.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM option_lists WHERE lower(name) = lower(%s)", (name.strip(),)
            ).fetchone()
        return row_to_option_list(row) if row else None

    def create_option_list(self, option_list: OptionList) -> OptionList:
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO option_lists (id, name, description, options, created_at, updated_at)
                    VALUES (%(id)s, %(name)s, %(description)s, %(options)s,
                            COALESCE(%(created_at)s, now()), COALESCE(%(updated_at)s, now()))
                    """,
                    option_list_to_row(option_list),
                )
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "option_lists_name_unique":
                raise ValueError(f"An option list named '{option_list.name}' already exists") from exc
            raise ValueError(f"Option list '{option_list.id}' already exists") from exc
        return option_list

    def update_option_list(self, option_list: OptionList) -> OptionList:
        try:
            with self._db.connection() as conn:
                result = conn.execute(
                    """
                    UPDATE option_lists
                       SET name = %(name)s, description = %(description)s, options = %(options)s,
                           updated_at = COALESCE(%(updated_at)s, now())
                     WHERE id = %(id)s
                    """,
                    option_list_to_row(option_list),
                )
                if result.rowcount == 0:
                    raise KeyError(option_list.id)
        except errors.UniqueViolation as exc:
            raise ValueError(f"An option list named '{option_list.name}' already exists") from exc
        return option_list

    def delete_option_list(self, list_id: str) -> bool:
        with self._db.connection() as conn:
            return conn.execute("DELETE FROM option_lists WHERE id = %s", (list_id,)).rowcount == 1
