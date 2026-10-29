"""Conditional logic evaluation.

A faithful port of ``frontend/lib/formLogic.ts``. The server uses it to work
out which fields were visible for a submission so it can enforce "required"
only on visible fields and discard answers to hidden ones. Keep both
implementations in sync (``tests/test_form_logic.py`` holds shared cases).
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from models.forms import FieldConfig, FieldLogic

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_NEGATIVE_OPERATORS = {"not_equals", "not_contains", "is_not_one_of"}
_NUMBER_OPERATORS = {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}
_DATE_OPERATORS = {"before", "after", "on_or_before", "on_or_after"}


# -- value helpers ------------------------------------------------------------

def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and not math.isfinite(value)) else float(value)
    if isinstance(value, str) and value.strip() != "":
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str) or value.strip() == "":
        return None
    match = _DATE_RE.match(value.strip())
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    if value is None:
        return []
    return [_to_text(value)]


def _norm(text: str) -> str:
    return text.strip().lower()


# -- evaluation ---------------------------------------------------------------

def evaluate_condition(operator: str, expected: Any, actual: Any, source_type: str | None = None) -> bool:
    if operator == "is_empty":
        return is_empty_value(actual)
    if operator == "is_not_empty":
        return not is_empty_value(actual)
    if operator == "is_checked":
        return actual is True or actual == "true"
    if operator == "is_not_checked":
        return not (actual is True or actual == "true")

    negative = operator in _NEGATIVE_OPERATORS
    if is_empty_value(actual):
        return negative

    if source_type == "number" or operator in _NUMBER_OPERATORS:
        a, b = _to_number(actual), _to_number(expected)
        if a is None or b is None:
            if operator == "equals":
                return False
            if operator == "not_equals":
                return True
            if source_type == "number":
                return False
        else:
            if operator == "equals":
                return a == b
            if operator == "not_equals":
                return a != b
            if operator == "greater_than":
                return a > b
            if operator == "greater_than_or_equal":
                return a >= b
            if operator == "less_than":
                return a < b
            if operator == "less_than_or_equal":
                return a <= b

    if source_type == "date" or operator in _DATE_OPERATORS:
        a, b = _to_date(actual), _to_date(expected)
        if a is None or b is None:
            return operator == "not_equals"
        if operator == "equals":
            return a == b
        if operator == "not_equals":
            return a != b
        if operator == "before":
            return a < b
        if operator == "after":
            return a > b
        if operator == "on_or_before":
            return a <= b
        if operator == "on_or_after":
            return a >= b

    actual_text = _norm(_to_text(actual))
    expected_text = _norm(_to_text(expected))

    if operator == "equals":
        return actual_text == expected_text
    if operator == "not_equals":
        return actual_text != expected_text
    if operator == "contains":
        return expected_text in actual_text
    if operator == "not_contains":
        return expected_text not in actual_text
    if operator == "starts_with":
        return actual_text.startswith(expected_text)
    if operator == "ends_with":
        return actual_text.endswith(expected_text)
    if operator == "is_one_of":
        return any(_norm(option) == actual_text for option in _to_list(expected))
    if operator == "is_not_one_of":
        return not any(_norm(option) == actual_text for option in _to_list(expected))
    return False


def evaluate_field_logic(
    logic: FieldLogic | None,
    values: dict[str, Any],
    field_types: dict[str, str] | None = None,
) -> bool:
    """Return True when the field should be visible."""
    if logic is None or not logic.conditions:
        return True
    field_types = field_types or {}

    results = [
        evaluate_condition(
            condition.operator,
            condition.value,
            values.get(condition.field_id),
            field_types.get(condition.field_id),
        )
        for condition in logic.conditions
    ]
    matched = any(results) if logic.match == "any" else all(results)
    return (not matched) if logic.action == "hide" else matched


def get_visible_field_ids(fields: list[FieldConfig], values: dict[str, Any]) -> list[str]:
    """Ids of visible fields, in form order. Hidden fields count as unanswered
    for any later conditions that depend on them."""
    field_types = {field.id: field.type for field in fields}
    effective = dict(values)
    visible: list[str] = []

    for field in fields:
        if evaluate_field_logic(field.logic, effective, field_types):
            visible.append(field.id)
        else:
            effective[field.id] = None

    return visible
