"""Business rules for data table definitions.

Like ``services/form_rules.py`` these run when a definition is *saved*
(create/update). They are stricter than for forms because a column id becomes
a real PostgreSQL column name: lower-case identifier, not a system column.
The registry (``data_table_columns``) repeats the id checks as CHECK
constraints, so nothing unsafe can reach the DDL builder even by accident.
"""

from __future__ import annotations

import re

from models.tables import CHOICE_COLUMN_TYPES, DataTableBase, TableColumn
from services.table_ddl import SYSTEM_COLUMNS

# Plain lower-case identifiers: no quoting surprises for anyone querying the
# table by hand, and they double as JSON keys and query-string sort keys.
COLUMN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# PostgreSQL identifiers are limited to 63 bytes; leave room for the
# ``<id>_options`` constraint name.
MAX_COLUMN_ID_LENGTH = 48
MAX_COLUMN_NAME_LENGTH = 120
MAX_COLUMNS = 200

# Names PostgreSQL reserves for every row, plus our own system columns.
RESERVED_COLUMN_IDS: frozenset[str] = frozenset(
    {*SYSTEM_COLUMNS, "tableoid", "xmin", "cmin", "xmax", "cmax", "ctid"}
)


class TableRuleError(ValueError):
    """Raised when a table definition breaks one or more business rules."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _label(column: TableColumn) -> str:
    return column.name or column.id


def column_problems(column: TableColumn) -> list[str]:
    """Rules that concern a single column."""
    problems: list[str] = []
    where = f"Column '{_label(column)}'"

    if len(column.id) > MAX_COLUMN_ID_LENGTH:
        problems.append(f"{where}: id is longer than {MAX_COLUMN_ID_LENGTH} characters")
    if not COLUMN_ID_PATTERN.match(column.id):
        problems.append(
            f"{where}: id '{column.id}' must start with a lower-case letter and contain only "
            "lower-case letters, digits and '_'"
        )
    elif column.id in RESERVED_COLUMN_IDS:
        problems.append(f"{where}: id '{column.id}' is reserved (every table has it)")

    if not column.name:
        problems.append(f"Column '{column.id}' needs a name")
    elif len(column.name) > MAX_COLUMN_NAME_LENGTH:
        problems.append(f"{where}: name is longer than {MAX_COLUMN_NAME_LENGTH} characters")

    if column.type == "checkbox" and column.required:
        problems.append(f"{where}: a checkbox column is always true or false; 'required' does not apply")

    if column.type in CHOICE_COLUMN_TYPES:
        options = column.options or []
        if not options:
            problems.append(f"{where} needs at least one option")
        # Case-insensitive, because filtering compares option values
        # case-insensitively (same rule as form fields and option lists).
        seen: set[str] = set()
        duplicates: list[str] = []
        for option in options:
            key = option.lower()
            if key in seen and option not in duplicates:
                duplicates.append(option)
            seen.add(key)
        if duplicates:
            problems.append(f"{where} has duplicate options: {', '.join(duplicates)}")
    elif column.options:
        problems.append(f"{where}: options are only allowed on {', '.join(sorted(CHOICE_COLUMN_TYPES))} columns")

    return problems


def table_problems(table: DataTableBase) -> list[str]:
    """Every rule violation in ``table``, in column order."""
    problems: list[str] = []
    if len(table.columns) > MAX_COLUMNS:
        problems.append(f"A table can have at most {MAX_COLUMNS} columns (got {len(table.columns)})")

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for column in table.columns:
        if column.id in seen_ids:
            problems.append(f"Duplicate column id '{column.id}'")
        seen_ids.add(column.id)
        name_key = column.name.lower()
        if name_key and name_key in seen_names:
            problems.append(f"Duplicate column name '{column.name}'")
        seen_names.add(name_key)
        problems.extend(column_problems(column))
    return problems


def validate_table_rules(table: DataTableBase) -> None:
    """Raise :class:`TableRuleError` if the definition breaks any rule."""
    problems = table_problems(table)
    if problems:
        raise TableRuleError(problems)
