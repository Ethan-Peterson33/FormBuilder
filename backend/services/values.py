"""Parsing of incoming values into typed Python values.

Shared by form submissions (``services/submissions.py``, where the target
type is the field type) and data table rows (``services/table_rows.py``,
where the target type is the PostgreSQL column type). Every parser returns
``(value, error)``: exactly one of them is ``None``.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_number(value: Any) -> tuple[int | float | None, str | None]:
    """int/float or numeric string -> number (ints stay ints, ``"2.0"`` -> 2)."""
    if isinstance(value, bool):
        return None, "must be a number"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None, "must be a number"
        return value, None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None, "must be a number"
        if not math.isfinite(parsed):
            return None, "must be a number"
        return int(parsed) if parsed.is_integer() else parsed, None
    return None, "must be a number"


def parse_integer(value: Any) -> tuple[int | None, str | None]:
    number, error = parse_number(value)
    if error:
        return None, "must be a whole number"
    if isinstance(number, float):
        if not number.is_integer():
            return None, "must be a whole number"
        number = int(number)
    return number, None


def parse_bool(value: Any) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true", None
    return None, "must be true or false"


def parse_date_string(value: Any) -> tuple[str | None, str | None]:
    """``YYYY-MM-DD`` (trimmed) -> the same string, validated as a real date."""
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        return None, "must be a date in YYYY-MM-DD format"
    text = value.strip()
    try:
        date.fromisoformat(text)
    except ValueError:
        return None, "must be a valid date in YYYY-MM-DD format"
    return text, None


def parse_timestamp(value: Any) -> tuple[datetime | None, str | None]:
    """ISO-8601 string (``Z`` or offset; date-only allowed) -> aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None, "must be an ISO-8601 timestamp"
    else:
        return None, "must be an ISO-8601 timestamp"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), None


def parse_text(value: Any) -> tuple[str | None, str | None]:
    """Scalars become text; structures are rejected."""
    if isinstance(value, bool):
        return ("true" if value else "false"), None
    if isinstance(value, (list, dict)):
        return None, "must be text"
    return str(value), None


def parse_uuid(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, uuid.UUID):
        return str(value), None
    if isinstance(value, str):
        try:
            return str(uuid.UUID(value.strip())), None
        except ValueError:
            pass
    return None, "must be a UUID"


def parse_text_list(value: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(value, list) or any(isinstance(item, (list, dict)) for item in value):
        return None, "must be a list of text values"
    return [str(item) for item in value], None


def parse_json(value: Any) -> tuple[Any, str | None]:
    """Anything JSON can carry (the request body already guarantees that)."""
    return value, None
