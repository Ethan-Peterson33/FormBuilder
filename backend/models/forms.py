"""Form configuration models.

These mirror the TypeScript types in ``frontend/lib/types.ts``. JSON uses
camelCase (``fieldId``) while Python code uses snake_case (``field_id``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


FieldType = Literal["text", "textarea", "number", "date", "select", "radio", "checkbox"]

# Field types whose values are compared as plain strings.
TEXT_LIKE_TYPES: set[str] = {"text", "textarea", "select", "radio"}

ConditionOperator = Literal[
    # Generic
    "equals",
    "not_equals",
    "is_empty",
    "is_not_empty",
    # Strings
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is_one_of",
    "is_not_one_of",
    # Numbers
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    # Dates
    "before",
    "after",
    "on_or_before",
    "on_or_after",
    # Checkboxes
    "is_checked",
    "is_not_checked",
]

# Which operators make sense for which source field types. Shared with the
# frontend (see ``OPERATORS_BY_FIELD_TYPE`` in ``frontend/lib/formLogic.ts``).
OPERATORS_BY_FIELD_TYPE: dict[str, list[str]] = {
    "text": [
        "equals", "not_equals", "contains", "not_contains", "starts_with",
        "ends_with", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty",
    ],
    "textarea": [
        "equals", "not_equals", "contains", "not_contains", "starts_with",
        "ends_with", "is_empty", "is_not_empty",
    ],
    "select": ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
    "radio": ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
    "number": [
        "equals", "not_equals", "greater_than", "greater_than_or_equal",
        "less_than", "less_than_or_equal", "is_empty", "is_not_empty",
    ],
    "date": [
        "equals", "not_equals", "before", "after", "on_or_before", "on_or_after",
        "is_empty", "is_not_empty",
    ],
    "checkbox": ["is_checked", "is_not_checked"],
}

# Operators that do not take a comparison value.
VALUELESS_OPERATORS: set[str] = {"is_empty", "is_not_empty", "is_checked", "is_not_checked"}


# Operators whose value is compared against a choice field's options.
OPTION_VALUE_OPERATORS: set[str] = {"equals", "not_equals", "is_one_of", "is_not_one_of"}


class Condition(CamelModel):
    """A single comparison against the value of an earlier field."""

    field_id: str = Field(min_length=1)
    operator: ConditionOperator
    value: str | int | float | bool | list[str] | None = None


class FieldLogic(CamelModel):
    """Visibility rules attached to a field."""

    action: Literal["show", "hide"] = "show"
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list)


class FieldConfig(CamelModel):
    id: str = Field(min_length=1)
    type: FieldType
    label: str = ""
    required: bool = False
    placeholder: str | None = None
    options: list[str] | None = None
    logic: FieldLogic | None = None

    @field_validator("options")
    @classmethod
    def _strip_options(cls, options: list[str] | None) -> list[str] | None:
        """Structural clean-up only. Duplicate/empty checks live in
        ``services.form_rules`` so that stored data always loads."""
        if options is None:
            return None
        return [option.strip() for option in options if option and option.strip()]


class FormConfigBase(CamelModel):
    name: str = Field(min_length=1)
    description: str = ""
    fields: list[FieldConfig] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Form name is required")
        return name


# Keys the server owns. Clients may echo them back (a GET response is valid
# PUT input) but they are never taken from the payload.
SERVER_MANAGED_KEYS: frozenset[str] = frozenset({"id", "created_at", "updated_at"})


class FormConfigCreate(FormConfigBase):
    """Payload accepted when creating a form. ``id`` is optional; one is generated.
    ``createdAt``/``updatedAt`` are accepted and ignored."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FormConfigUpdate(FormConfigBase):
    """Payload accepted when replacing a form. ``id``, ``createdAt`` and
    ``updatedAt`` in the body are ignored (the stored values are kept)."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FormConfig(FormConfigBase):
    """A stored form."""

    id: str
    created_at: str | None = None
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = self.model_dump(by_alias=True, exclude_none=True)
        # Keep ``id`` first so the JSON file stays easy to scan by eye.
        return {"id": record.pop("id"), **record}
