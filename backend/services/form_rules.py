"""Business rules for form configurations.

These checks run when a form is *saved* (create/update), never when stored
data is loaded. That way a record written before a rule existed still loads
and renders; the builder shows the problems and blocks saving until fixed.

Mirrors ``frontend/lib/formLogic.ts#findLogicProblems`` and
``frontend/lib/options.ts#validateFieldOptions``.
"""

from __future__ import annotations

from models.forms import (
    OPERATORS_BY_FIELD_TYPE,
    OPTION_VALUE_OPERATORS,
    VALUELESS_OPERATORS,
    Condition,
    FieldConfig,
    FormConfigBase,
)

CHOICE_TYPES = {"select", "radio"}


class FormRuleError(ValueError):
    """Raised when a form breaks one or more business rules."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _label(field: FieldConfig) -> str:
    return field.label or field.id


def option_problems(field: FieldConfig) -> list[str]:
    if field.type not in CHOICE_TYPES:
        return []
    problems: list[str] = []
    options = field.options or []
    if not options:
        problems.append(f"'{_label(field)}' needs at least one option")
    seen: set[str] = set()
    duplicates: list[str] = []
    for option in options:
        key = option.lower()
        if key in seen and option not in duplicates:
            duplicates.append(option)
        seen.add(key)
    if duplicates:
        problems.append(f"'{_label(field)}' has duplicate options: {', '.join(duplicates)}")
    return problems


def stale_option_values(source: FieldConfig, condition: Condition) -> list[str]:
    """Condition values that are not among a choice source's options."""
    if source.type not in CHOICE_TYPES or not source.options:
        return []
    if condition.operator not in OPTION_VALUE_OPERATORS:
        return []
    allowed = {option.lower() for option in source.options}
    values = condition.value if isinstance(condition.value, list) else [condition.value]
    return [
        "" if value is None else str(value)
        for value in values
        if value is None or str(value).strip().lower() not in allowed
    ]


def logic_problems(fields: list[FieldConfig]) -> list[str]:
    problems: list[str] = []
    by_id = {field.id: field for field in fields}

    for index, field in enumerate(fields):
        if field.logic is None:
            continue
        earlier = {f.id for f in fields[:index]}
        where = f"'{_label(field)}'"

        for number, condition in enumerate(field.logic.conditions, start=1):
            prefix = f"{where} condition {number}"
            if condition.field_id == field.id:
                problems.append(f"{prefix}: a field cannot reference itself in its logic")
                continue
            source = by_id.get(condition.field_id)
            if source is None:
                problems.append(
                    f"{prefix}: references '{condition.field_id}', which is not an earlier "
                    "field in the form (it does not exist)"
                )
                continue
            if source.id not in earlier:
                problems.append(
                    f"{prefix}: references '{_label(source)}', which is not an earlier field in "
                    "the form (it appears after this field)"
                )
                continue
            allowed = OPERATORS_BY_FIELD_TYPE.get(source.type, [])
            if condition.operator not in allowed:
                problems.append(
                    f"{prefix}: operator '{condition.operator}' cannot be used with a "
                    f"'{source.type}' field ('{_label(source)}')"
                )
                continue
            if condition.operator not in VALUELESS_OPERATORS and condition.value in (None, "", []):
                problems.append(
                    f"{prefix}: condition on '{_label(source)}' with operator "
                    f"'{condition.operator}' needs a value"
                )
                continue
            stale = stale_option_values(source, condition)
            if stale:
                problems.append(
                    f"{prefix}: uses value '{stale[0]}', which is not one of the options of "
                    f"'{_label(source)}' ({', '.join(source.options or [])})"
                )
    return problems


def form_problems(form: FormConfigBase) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for field in form.fields:
        if field.id in seen:
            problems.append(f"Duplicate field id '{field.id}'")
        seen.add(field.id)
        problems.extend(option_problems(field))
    problems.extend(logic_problems(form.fields))
    return problems


def validate_form_rules(form: FormConfigBase) -> None:
    """Raise :class:`FormRuleError` if the form breaks any rule."""
    problems = form_problems(form)
    if problems:
        raise FormRuleError(problems)
