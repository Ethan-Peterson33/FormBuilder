"""Repository interfaces.

Routers depend on these protocols rather than on the PostgreSQL classes in
``repositories/pg_*.py``; tests substitute small doubles through
``app.dependency_overrides`` to exercise error branches (lost id races,
records vanishing between lookup and write).
"""

from __future__ import annotations

from typing import Any, Protocol

from models.forms import FormConfig
from models.option_lists import OptionList
from models.submissions import Submission
from models.tables import DataTable, TableRow
from services.row_query import RowQuery
from services.table_catalog import TableInfo


class DefinitionConflictError(Exception):
    """A definition change is refused by the data already in the table: a
    column cannot become NOT NULL while it holds NULLs, values cannot be
    converted to the new type, or a stored value is not among the new
    options. Carries the PostgreSQL message, the column and a hint.
    Routers answer ``409``."""

    def __init__(
        self,
        message: str,
        *,
        table_id: str,
        column_id: str | None = None,
        sqlstate: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.table_id = table_id
        self.column_id = column_id
        self.sqlstate = sqlstate
        self.hint = hint


class RowConstraintError(Exception):
    """A row write was refused by a table constraint (NOT NULL, CHECK,
    UNIQUE, FOREIGN KEY) or by the column type after validation let it
    through. Carries the PostgreSQL message. Routers answer ``409``."""

    def __init__(
        self,
        message: str,
        *,
        table_ref: str,
        column: str | None = None,
        constraint: str | None = None,
        sqlstate: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.table_ref = table_ref
        self.column = column
        self.constraint = constraint
        self.sqlstate = sqlstate
        self.hint = hint


class TableSchemaMismatchError(Exception):
    """The physical table does not match its registry rows (a column or the
    table itself is missing or already exists). Only possible after changes
    made outside the API. Routers answer ``500`` pointing at check_db."""

    def __init__(self, table_id: str, message: str, *, column_id: str | None = None) -> None:
        super().__init__(message)
        self.table_id = table_id
        self.column_id = column_id


class FormRepository(Protocol):
    def list_forms(self) -> list[FormConfig]: ...

    def get_form(self, form_id: str) -> FormConfig | None: ...

    def create_form(self, form: FormConfig) -> FormConfig: ...

    def update_form(self, form: FormConfig) -> FormConfig: ...

    def delete_form(self, form_id: str) -> bool: ...

    def exists(self, form_id: str) -> bool: ...


class SubmissionRepository(Protocol):
    def list_submissions(
        self,
        form_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Submission]: ...

    def count_submissions(self, form_id: str | None = None) -> int: ...

    def get_submission(self, submission_id: str) -> Submission | None: ...

    def create_submission(self, submission: Submission) -> Submission: ...


class OptionListRepository(Protocol):
    def list_option_lists(self) -> list[OptionList]: ...

    def get_option_list(self, list_id: str) -> OptionList | None: ...

    def exists(self, list_id: str) -> bool: ...

    def find_by_name(self, name: str) -> OptionList | None: ...

    def create_option_list(self, option_list: OptionList) -> OptionList: ...

    def update_option_list(self, option_list: OptionList) -> OptionList: ...

    def delete_option_list(self, list_id: str) -> bool: ...


class DataTableRepository(Protocol):
    """User tables: the registry rows *and* the physical table, always
    changed together in one transaction."""

    def list_tables(self) -> list[DataTable]: ...

    def get_table(self, table_id: str) -> DataTable | None: ...

    def exists(self, table_id: str) -> bool: ...

    def find_by_name(self, name: str) -> DataTable | None:
        """Table whose name matches case-insensitively."""
        ...

    def create_table(self, table: DataTable) -> DataTable:
        """``CREATE TABLE user_tables.<name>`` + registry rows.
        Raises ``ValueError`` if the id or the name is taken."""
        ...

    def update_table(self, table: DataTable) -> DataTable:
        """Bring the physical table and the registry to ``table``: ADD COLUMN
        for new column ids, DROP COLUMN for missing ones, ALTER COLUMN for
        changed type / required / options, plus name, description, order.
        Returns the stored definition.

        Raises ``KeyError`` (no such table), ``ValueError`` (name taken) and
        ``DefinitionConflictError`` when existing rows do not allow a change.
        """
        ...

    def delete_table(self, table_id: str) -> bool:
        """``DROP TABLE`` + registry rows. The slug becomes free again."""
        ...


class TableRowRepository(Protocol):
    """Rows of any allowed table (``services/table_catalog.py`` decides which
    and describes their columns). Cell values arrive validated and coerced to
    the column types (``services/table_rows.py``); column names come from
    the catalog description, never from the request, before they reach SQL.
    """

    def describe(self, ref: str) -> TableInfo | None:
        """Catalog description of the table, or ``None`` if unknown.
        Raises ``TableNotAllowedError`` for tables the API does not expose."""
        ...

    def list_rows(self, table: TableInfo, query: RowQuery) -> tuple[list[TableRow], int]:
        """Matching page and the total number of matching rows."""
        ...

    def get_row(self, table: TableInfo, key: Any) -> TableRow | None: ...

    def create_row(self, table: TableInfo, values: dict[str, Any]) -> TableRow:
        """``INSERT`` the given cells; the database fills defaults and
        identities. Raises ``RowConstraintError``."""
        ...

    def patch_row(self, table: TableInfo, key: Any, changes: dict[str, Any]) -> TableRow | None:
        """``UPDATE ... SET <only the named columns>`` (``None`` -> NULL);
        ``updated_at`` is bumped when the table has it. Cells not named are
        untouched. ``None`` when the row does not exist. Raises
        ``RowConstraintError``."""
        ...

    def delete_row(self, table: TableInfo, key: Any) -> bool:
        """``DELETE``. ``False`` when the row did not exist. Raises
        ``RowConstraintError`` when another table still references it."""
        ...
