"""Filtering, sorting and paging of table rows -> parameterised SQL.

The query model (``RowQuery``) is what ``POST /tables/{ref}/rows/query``
accepts and what the ``GET`` query string is parsed into. Operators are the
form rules engine's (``models.forms.ConditionOperator``) so the frontend can
reuse its condition editor; which ones apply depends on the column's kind.

Every column name is resolved against the catalog description of the table
and rendered with ``sql.Identifier``; every value is a query parameter,
coerced to the column's type first. Invalid queries raise
:class:`RowValidationError` with one message per offending part.
"""

from __future__ import annotations

from typing import Any, Literal

from psycopg import sql
from pydantic import Field

from models.forms import CamelModel, ConditionOperator
from services.table_catalog import ColumnInfo, ColumnKind, TableInfo
from services.table_rows import KINDS_WITH_TEXT_SEARCH, RowValidationError, coerce_cell

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

_TEXT_OPERATORS = [
    "equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with",
    "is_one_of", "is_not_one_of", "is_empty", "is_not_empty",
]
_ORDERED_OPERATORS = [
    "equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal",
    "is_one_of", "is_not_one_of", "is_empty", "is_not_empty",
]
_DATE_OPERATORS = [
    "equals", "not_equals", "before", "after", "on_or_before", "on_or_after",
    "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal",
    "is_empty", "is_not_empty",
]
_EMPTINESS = ["is_empty", "is_not_empty"]

OPERATORS_BY_KIND: dict[ColumnKind, list[str]] = {
    "text": _TEXT_OPERATORS,
    "uuid": ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
    "integer": _ORDERED_OPERATORS,
    "number": _ORDERED_OPERATORS,
    "date": _DATE_OPERATORS,
    "timestamp": _DATE_OPERATORS,
    "boolean": ["is_checked", "is_not_checked", "equals", "not_equals", "is_empty", "is_not_empty"],
    "json": _EMPTINESS,
    "text_array": ["contains", "not_contains", "is_empty", "is_not_empty"],
    "unknown": _EMPTINESS,
}
VALUELESS = {"is_empty", "is_not_empty", "is_checked", "is_not_checked"}
LIST_VALUED = {"is_one_of", "is_not_one_of"}
_COMPARISONS = {
    "greater_than": ">", "after": ">",
    "greater_than_or_equal": ">=", "on_or_after": ">=",
    "less_than": "<", "before": "<",
    "less_than_or_equal": "<=", "on_or_before": "<=",
}


class RowFilter(CamelModel):
    column: str
    operator: ConditionOperator
    value: Any = None


class RowSort(CamelModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class RowQuery(CamelModel):
    filters: list[RowFilter] = Field(default_factory=list)
    match: Literal["all", "any"] = "all"
    sort: list[RowSort] = Field(default_factory=list)
    # Case-insensitive substring search across every text column.
    search: str | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)


# -- query-string form ---------------------------------------------------------

def parse_sort_param(text: str | None) -> list[RowSort]:
    """``"-cost,title"`` -> ``[RowSort(cost desc), RowSort(title asc)]``."""
    sorts: list[RowSort] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            sorts.append(RowSort(column=part[1:].strip(), direction="desc"))
        else:
            sorts.append(RowSort(column=part.lstrip("+").strip(), direction="asc"))
    return sorts


def parse_filter_param(text: str) -> RowFilter:
    """``"status:equals:Open"`` -> ``RowFilter``. The value is everything after
    the second colon (so it may contain colons); list operators take a
    comma-separated list; valueless operators need no third part."""
    column, sep, rest = text.partition(":")
    operator, _, value = rest.partition(":")
    if not column.strip() or not sep or not operator.strip():
        raise RowValidationError({"filter": f"'{text}' is not column:operator[:value]"}, "Invalid query")
    operator = operator.strip()
    raw: Any = None
    if operator in LIST_VALUED:
        raw = [item.strip() for item in value.split(",") if item.strip()]
    elif operator not in VALUELESS:
        raw = value
    try:
        return RowFilter(column=column.strip(), operator=operator, value=raw)  # type: ignore[arg-type]
    except ValueError as exc:
        raise RowValidationError({"filter": f"unknown operator '{operator}'"}, "Invalid query") from exc


# -- SQL ---------------------------------------------------------------------------

def _like_pattern(text: str, *, prefix: bool = True, suffix: bool = True) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return ("%" if prefix else "") + escaped + ("%" if suffix else "")


def _filter_sql(column: ColumnInfo, operator: str, value: Any, where: str) -> tuple[sql.Composable, list[Any]]:
    """One filter -> (SQL fragment, params). Raises RowValidationError."""
    ident = sql.Identifier(column.name)
    if operator not in OPERATORS_BY_KIND[column.kind]:
        raise RowValidationError(
            {where: f"operator '{operator}' cannot be used with a {column.sql_type} column"}, "Invalid query"
        )

    # Emptiness: NULL (and blank text / empty array).
    if operator in ("is_empty", "is_not_empty"):
        if column.kind == "text":
            empty = sql.SQL("({c} IS NULL OR btrim({c}) = '')").format(c=ident)
        elif column.kind == "text_array":
            empty = sql.SQL("({c} IS NULL OR cardinality({c}) = 0)").format(c=ident)
        else:
            empty = sql.SQL("{c} IS NULL").format(c=ident)
        return (empty if operator == "is_empty" else sql.SQL("NOT ") + empty), []
    if operator == "is_checked":
        return sql.SQL("{c} IS TRUE").format(c=ident), []
    if operator == "is_not_checked":
        return sql.SQL("{c} IS NOT TRUE").format(c=ident), []

    # Valued operators: coerce to the column type first.
    if operator in LIST_VALUED:
        raw_items = value if isinstance(value, list) else [v for v in str(value or "").split(",")]
        items = []
        for item in raw_items:
            coerced, error = coerce_cell(column, item, check_options=False)
            if error or coerced is None:
                raise RowValidationError({where: f"value '{item}' {error or 'is empty'}"}, "Invalid query")
            items.append(coerced)
        if not items:
            raise RowValidationError({where: "needs at least one value"}, "Invalid query")
        if column.kind == "text":
            match = sql.SQL("lower({c}) = ANY(%s)").format(c=ident)
            params: list[Any] = [[item.lower() for item in items]]
        else:
            match = sql.SQL("{c} = ANY(%s)").format(c=ident)
            params = [items]
        if operator == "is_one_of":
            return match, params
        return sql.SQL("({c} IS NULL OR NOT ").format(c=ident) + match + sql.SQL(")"), params

    if column.kind == "text_array" and operator in ("contains", "not_contains"):
        if not isinstance(value, str) or not value:
            raise RowValidationError({where: "needs a text value"}, "Invalid query")
        match = sql.SQL("%s = ANY({c})").format(c=ident)
        if operator == "contains":
            return match, [value]
        return sql.SQL("({c} IS NULL OR NOT ").format(c=ident) + match + sql.SQL(")"), [value]

    coerced, error = coerce_cell(column, value, check_options=False)
    if error:
        raise RowValidationError({where: f"value {error}"}, "Invalid query")
    if coerced is None or coerced == "":
        raise RowValidationError({where: f"operator '{operator}' needs a value"}, "Invalid query")

    if column.kind == "text":
        if operator in ("contains", "not_contains", "starts_with", "ends_with"):
            pattern = _like_pattern(
                coerced, prefix=operator != "starts_with", suffix=operator != "ends_with"
            )
            like = sql.SQL("{c} ILIKE %s").format(c=ident)
            if operator == "not_contains":
                return sql.SQL("({c} IS NULL OR NOT ").format(c=ident) + like + sql.SQL(")"), [pattern]
            return like, [pattern]
        if operator == "equals":
            return sql.SQL("lower({c}) = lower(%s)").format(c=ident), [coerced]
        if operator == "not_equals":
            return sql.SQL("({c} IS NULL OR lower({c}) <> lower(%s))").format(c=ident), [coerced]

    if operator == "equals":
        return sql.SQL("{c} = %s").format(c=ident), [coerced]
    if operator == "not_equals":
        return sql.SQL("({c} IS NULL OR {c} <> %s)").format(c=ident), [coerced]
    comparison = _COMPARISONS.get(operator)
    if comparison:
        return sql.SQL("{c} {op} %s").format(c=ident, op=sql.SQL(comparison)), [coerced]
    raise RowValidationError({where: f"operator '{operator}' is not supported"}, "Invalid query")  # pragma: no cover


def build_where(table: TableInfo, query: RowQuery) -> tuple[sql.Composable | None, list[Any]]:
    """``WHERE`` body (without the keyword) and its parameters, or ``(None, [])``."""
    errors: dict[str, str] = {}
    parts: list[sql.Composable] = []
    params: list[Any] = []
    for index, item in enumerate(query.filters):
        where = f"filters[{index}]"
        column = table.column(item.column)
        if column is None:
            errors[where] = f"unknown column '{item.column}'"
            continue
        try:
            fragment, fragment_params = _filter_sql(column, item.operator, item.value, where)
        except RowValidationError as exc:
            errors.update(exc.errors)
            continue
        parts.append(fragment)
        params.extend(fragment_params)
    if errors:
        raise RowValidationError(errors, "Invalid query")

    clauses: list[sql.Composable] = []
    if parts:
        joiner = sql.SQL(" AND ") if query.match == "all" else sql.SQL(" OR ")
        clauses.append(sql.SQL("(") + joiner.join(parts) + sql.SQL(")"))
    if query.search and query.search.strip():
        searchable = [c for c in table.columns if c.kind in KINDS_WITH_TEXT_SEARCH]
        if searchable:
            pattern = _like_pattern(query.search.strip())
            likes = [sql.SQL("{c}::text ILIKE %s").format(c=sql.Identifier(c.name)) for c in searchable]
            clauses.append(sql.SQL("(") + sql.SQL(" OR ").join(likes) + sql.SQL(")"))
            params.extend([pattern] * len(searchable))
        else:
            clauses.append(sql.SQL("false"))
    if not clauses:
        return None, params
    return sql.SQL(" AND ").join(clauses), params


def build_query(table: TableInfo, query: RowQuery) -> tuple[sql.Composable | None, list[Any], sql.Composable]:
    """``(where, params, order_by)``; problems in filters and sort are reported together."""
    errors: dict[str, str] = {}
    where, params, order_by = None, [], None
    try:
        where, params = build_where(table, query)
    except RowValidationError as exc:
        errors.update(exc.errors)
    try:
        order_by = build_order_by(table, query)
    except RowValidationError as exc:
        errors.update(exc.errors)
    if errors:
        raise RowValidationError(errors, "Invalid query")
    assert order_by is not None
    return where, params, order_by


def build_order_by(table: TableInfo, query: RowQuery) -> sql.Composable:
    """``ORDER BY`` body: the requested columns (NULLs last), then the primary
    key so paging is stable."""
    errors: dict[str, str] = {}
    parts: list[sql.Composable] = []
    seen: set[str] = set()
    for index, item in enumerate(query.sort):
        column = table.column(item.column)
        if column is None:
            errors[f"sort[{index}]"] = f"unknown column '{item.column}'"
            continue
        if column.kind in ("json", "unknown"):
            errors[f"sort[{index}]"] = f"cannot sort by a {column.sql_type} column"
            continue
        if column.name in seen:
            continue
        seen.add(column.name)
        direction = sql.SQL("DESC") if item.direction == "desc" else sql.SQL("ASC")
        parts.append(sql.SQL("{c} {d} NULLS LAST").format(c=sql.Identifier(column.name), d=direction))
    if errors:
        raise RowValidationError(errors, "Invalid query")
    key = table.primary_key
    if key is not None and key.name not in seen:
        parts.append(sql.SQL("{c} ASC").format(c=sql.Identifier(key.name)))
    if not parts:
        parts.append(sql.SQL("1"))
    return sql.SQL(", ").join(parts)
