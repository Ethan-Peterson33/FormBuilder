"""Data table models: user-defined tables with typed columns, and their rows.

A data table is a **real PostgreSQL table** in the ``user_tables`` schema
(see ``services/table_ddl.py``); these models are the API's view of it. Same
conventions as ``models/forms.py``: JSON is camelCase, Python snake_case, and
the models enforce *structure* only - the rules in ``services/table_rules.py``
run when a definition is saved.

Rows are ``{columnId: value}``; a column's ``id`` is its physical column
name, immutable, while its ``name`` is the header shown in the grid.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import Field, field_validator

from models.forms import SERVER_MANAGED_KEYS, CamelModel

# Column types are a strict subset of form field types, so the operator table
# (``models.forms.OPERATORS_BY_FIELD_TYPE``), value coercion and the option
# library serve forms and tables alike. ``radio`` is left out: in a grid it is
# just a ``select`` with a different editor. Each maps to one SQL type in
# ``services.table_ddl.SQL_TYPES``.
ColumnType = Literal["text", "textarea", "number", "date", "select", "checkbox"]
COLUMN_TYPES: tuple[str, ...] = get_args(ColumnType)

# Column types whose values must be one of the column's ``options``.
CHOICE_COLUMN_TYPES: frozenset[str] = frozenset({"select"})

# Keys the server owns on a table definition. Clients may echo them back (a
# GET response is valid PUT input) but they are never taken from the payload.
TABLE_SERVER_MANAGED_KEYS: frozenset[str] = SERVER_MANAGED_KEYS


# -- definition -----------------------------------------------------------------

class TableColumn(CamelModel):
    """One column of a data table."""

    # Physical column name: immutable, lower-case identifier (checked on save).
    id: str = Field(min_length=1)
    # Header shown in the grid. Required and unique per table - checked on save.
    name: str
    type: ColumnType
    # Becomes NOT NULL on the physical column.
    required: bool = False
    description: str = ""
    # Allowed values for choice columns (``select``); a CHECK constraint.
    options: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, name: str) -> str:
        return name.strip()

    @field_validator("options")
    @classmethod
    def _strip_options(cls, options: list[str] | None) -> list[str] | None:
        """Structural clean-up only; duplicates are a save-time rule."""
        if options is None:
            return None
        return [option.strip() for option in options if option and option.strip()]


class DataTableBase(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    columns: list[TableColumn] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Table name is required")
        return name


class DataTableCreate(DataTableBase):
    """Payload for ``POST /tables``. ``id`` is optional (generated from the
    name); ``createdAt``/``updatedAt`` are accepted and ignored."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DataTableUpdate(DataTableBase):
    """Payload for ``PUT /tables/{id}``: the desired definition. The server
    works out the ALTER TABLE steps from the difference with the stored one
    (columns are matched by ``id``). ``id``, ``createdAt`` and ``updatedAt``
    are ignored."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DataTable(DataTableBase):
    """A stored table definition, as registered and created in PostgreSQL."""

    id: str
    created_at: str | None = None
    updated_at: str | None = None

    def column(self, column_id: str) -> TableColumn | None:
        return next((column for column in self.columns if column.id == column_id), None)

    def to_record(self) -> dict[str, Any]:
        record = self.model_dump(by_alias=True, exclude_none=True)
        return {"id": record.pop("id"), **record}


# -- rows -------------------------------------------------------------------------
#
# Rows are served for user tables *and* any other allowed table
# (``/tables/public.option_lists/rows``); the cell types are the PostgreSQL
# column types (``services/table_catalog.py``), so a cell may hold anything
# JSON can carry (a ``jsonb`` column) - not only scalars.

class TableRowCreate(CamelModel):
    """Payload for ``POST /tables/{ref}/rows``."""

    data: dict[str, Any] = Field(default_factory=dict)


class TableRowUpdate(CamelModel):
    """Payload for ``PATCH /tables/{ref}/rows/{rowId}``. ``data`` is *partial*:
    only the cells given are changed and ``null`` clears a cell."""

    data: dict[str, Any] = Field(default_factory=dict)


class TableRow(CamelModel):
    """A stored row. ``id`` is the primary key value (``null`` for a table
    without a single-column primary key); ``createdAt`` / ``updatedAt`` are
    the table's ``created_at`` / ``updated_at`` columns when it has them."""

    id: int | str | None
    table_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class TableRowList(CamelModel):
    total: int
    limit: int
    offset: int
    items: list[TableRow]
