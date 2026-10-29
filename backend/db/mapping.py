"""Conversion between pydantic models and table rows.

One place defines how a ``FormConfig`` / ``OptionList`` / ``Submission`` /
``DataTable`` / ``TableRow`` becomes a row of ``forms`` / ``option_lists`` /
``submissions`` / ``data_tables`` / ``data_table_rows`` and back.
``db.import_json`` uses it now; the PostgreSQL repositories will use it next,
so the API keeps producing byte-for-byte the same JSON as the file-based
storage.

Conventions
-----------
* Timestamps are ``timestamptz`` in the database and ISO-8601 UTC strings with
  millisecond precision and a ``Z`` suffix in the API (``services.ids.utc_now_iso``).
* ``forms.fields`` stores the camelCase JSON exactly as the API serves it.
* ``submissions.metadata`` stores only the *extras*; the declared
  ``SubmissionMetadata`` fields are columns and are merged back on read.
* Data tables: the registry rows (``data_tables`` + ``data_table_columns``)
  become one ``DataTable``; a row of the user table itself becomes a
  ``TableRow`` with PostgreSQL types converted to API values.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from models.forms import FieldConfig, FormConfig
from models.option_lists import OptionList
from models.submissions import Submission, SubmissionMetadata
from models.tables import DataTable, TableColumn, TableRow

# -- timestamps ---------------------------------------------------------------

def parse_timestamp(value: str | None) -> datetime | None:
    """``'2026-10-25T16:34:52.425Z'`` -> aware UTC datetime (``None`` stays ``None``)."""
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime | None) -> str | None:
    """Aware datetime -> the API's ``...T...sss Z`` format."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_timestamps(
    created_at: str | None, updated_at: str | None, fallback: datetime | None
) -> tuple[datetime | None, datetime | None]:
    created = parse_timestamp(created_at)
    updated = parse_timestamp(updated_at)
    created = created or updated or fallback
    updated = updated or created
    return created, updated


# -- forms --------------------------------------------------------------------

FORM_COLUMNS = ("id", "name", "description", "fields", "created_at", "updated_at", "deleted_at")


def form_to_row(form: FormConfig, *, fallback_time: datetime | None = None) -> dict[str, Any]:
    """Row values for ``forms``.

    Legacy records may lack one or both timestamps. ``created_at`` falls back
    to ``updated_at`` (a record cannot be created after it was last updated),
    then to ``fallback_time``; ``updated_at`` falls back to ``created_at``. If
    both are missing and ``fallback_time`` is ``None`` the database default
    ``now()`` applies.
    """
    created, updated = _resolve_timestamps(form.created_at, form.updated_at, fallback_time)
    return {
        "id": form.id,
        "name": form.name,
        "description": form.description,
        "fields": Jsonb([field.model_dump(by_alias=True, exclude_none=True) for field in form.fields]),
        "created_at": created,
        "updated_at": updated,
        "deleted_at": None,
    }


def row_to_form(row: dict[str, Any]) -> FormConfig:
    return FormConfig(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        fields=[FieldConfig.model_validate(field) for field in row["fields"]],
        created_at=format_timestamp(row["created_at"]),
        updated_at=format_timestamp(row["updated_at"]),
    )


# -- option lists -------------------------------------------------------------

OPTION_LIST_COLUMNS = ("id", "name", "description", "options", "created_at", "updated_at")


def option_list_to_row(option_list: OptionList, *, fallback_time: datetime | None = None) -> dict[str, Any]:
    created, updated = _resolve_timestamps(option_list.created_at, option_list.updated_at, fallback_time)
    return {
        "id": option_list.id,
        "name": option_list.name,
        "description": option_list.description,
        "options": list(option_list.options),
        "created_at": created,
        "updated_at": updated,
    }


def row_to_option_list(row: dict[str, Any]) -> OptionList:
    return OptionList(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        options=list(row["options"]),
        created_at=format_timestamp(row["created_at"]),
        updated_at=format_timestamp(row["updated_at"]),
    )


# -- submissions --------------------------------------------------------------

SUBMISSION_COLUMNS = (
    "id", "form_id", "form_name", "data",
    "submitted_at", "submitted_by", "source", "user_agent", "ip_address",
    "hidden_fields", "metadata",
)

# Declared metadata fields live in columns; everything else is an "extra".
# Both spellings, because ``model_dump(by_alias=True)`` emits camelCase for the
# declared fields while extras keep whatever key the client sent.
_DECLARED_METADATA_KEYS = set(SubmissionMetadata.model_fields) | {
    info.alias for info in SubmissionMetadata.model_fields.values() if info.alias
}


def submission_to_row(submission: Submission) -> dict[str, Any]:
    meta = submission.metadata
    extras = {
        key: value
        for key, value in meta.model_dump(by_alias=True).items()
        if key not in _DECLARED_METADATA_KEYS
    }
    return {
        "id": submission.id,
        "form_id": submission.form_id,
        "form_name": submission.form_name,
        "data": Jsonb(dict(submission.data)),
        "submitted_at": parse_timestamp(meta.submitted_at),
        "submitted_by": meta.submitted_by,
        "source": meta.source,
        "user_agent": meta.user_agent,
        "ip_address": meta.ip_address,
        "hidden_fields": list(meta.hidden_fields),
        "metadata": Jsonb(extras),
    }


def row_to_submission(row: dict[str, Any]) -> Submission:
    metadata = SubmissionMetadata(
        submitted_at=format_timestamp(row["submitted_at"]),
        submitted_by=row["submitted_by"],
        source=row["source"],
        user_agent=row["user_agent"],
        ip_address=row["ip_address"],
        hidden_fields=list(row["hidden_fields"]),
        **dict(row["metadata"]),
    )
    return Submission(
        id=row["id"],
        form_id=row["form_id"],
        form_name=row["form_name"],
        data=dict(row["data"]),
        metadata=metadata,
    )


# -- data tables --------------------------------------------------------------
#
# A DataTable is assembled from one ``data_tables`` row plus its
# ``data_table_columns`` rows (ordered by position). TableRows come straight
# from the user table; PostgreSQL types are converted to API values.

DATA_TABLE_COLUMNS = ("id", "table_name", "name", "description", "created_at", "updated_at")
TABLE_COLUMN_COLUMNS = (
    "table_id", "id", "position", "name", "type", "required", "description", "options", "created_at", "updated_at",
)


def data_table_to_row(table: DataTable, *, fallback_time: datetime | None = None) -> dict[str, Any]:
    """Row values for ``data_tables`` (``table_name`` is generated by the database)."""
    created, updated = _resolve_timestamps(table.created_at, table.updated_at, fallback_time)
    return {
        "id": table.id,
        "name": table.name,
        "description": table.description,
        "created_at": created,
        "updated_at": updated,
    }


def table_column_to_row(table_id: str, position: int, column: TableColumn) -> dict[str, Any]:
    """Row values for ``data_table_columns``."""
    return {
        "table_id": table_id,
        "id": column.id,
        "position": position,
        "name": column.name,
        "type": column.type,
        "required": column.required,
        "description": column.description,
        "options": list(column.options) if column.options is not None else None,
    }


def row_to_table_column(row: dict[str, Any]) -> TableColumn:
    return TableColumn(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        required=row["required"],
        description=row["description"],
        options=list(row["options"]) if row["options"] is not None else None,
    )


def rows_to_data_table(table_row: dict[str, Any], column_rows: list[dict[str, Any]]) -> DataTable:
    """``column_rows`` must already be ordered by ``position``."""
    return DataTable(
        id=table_row["id"],
        name=table_row["name"],
        description=table_row["description"],
        columns=[row_to_table_column(row) for row in column_rows],
        created_at=format_timestamp(table_row["created_at"]),
        updated_at=format_timestamp(table_row["updated_at"]),
    )


# -- rows of a user table -----------------------------------------------------

def cell_from_db(value: Any) -> Any:
    """PostgreSQL value -> API value: numeric -> int/float, date -> ``YYYY-MM-DD``."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def row_to_table_row(row: dict[str, Any], table_id: str, column_ids: list[str]) -> TableRow:
    """A ``SELECT *`` row of a user table -> ``TableRow``. Empty cells (NULL)
    are left out of ``data``."""
    data = {
        column_id: cell_from_db(row[column_id])
        for column_id in column_ids
        if column_id in row and row[column_id] is not None
    }
    return TableRow(
        id=row["id"],
        table_id=table_id,
        data=data,
        created_at=format_timestamp(row["created_at"]),
        updated_at=format_timestamp(row["updated_at"]),
    )
