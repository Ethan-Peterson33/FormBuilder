"""Reusable option lists.

A named list of choices (e.g. "Contact Methods") that can be loaded into any
select or radio field. Loading *copies* the options into the field, so editing
a list later never changes forms that already used it.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from models.forms import CamelModel


def validate_option_values(options: list[str]) -> list[str]:
    """Trim, drop empties, and reject duplicates (case-insensitive, because
    conditional logic compares option values case-insensitively)."""
    cleaned = [option.strip() for option in options if option and option.strip()]
    seen: set[str] = set()
    duplicates: list[str] = []
    for option in cleaned:
        key = option.lower()
        if key in seen and option not in duplicates:
            duplicates.append(option)
        seen.add(key)
    if duplicates:
        raise ValueError(f"Duplicate options: {', '.join(duplicates)}")
    return cleaned


class OptionListBase(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    options: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("List name is required")
        return name

    @field_validator("options")
    @classmethod
    def _validate_options(cls, options: list[str]) -> list[str]:
        cleaned = validate_option_values(options)
        if not cleaned:
            raise ValueError("An option list needs at least one option")
        return cleaned


class OptionListCreate(OptionListBase):
    """``id`` optional; ``createdAt``/``updatedAt`` accepted and ignored."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OptionListUpdate(OptionListBase):
    """``id``, ``createdAt`` and ``updatedAt`` in the body are ignored."""

    id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OptionList(OptionListBase):
    id: str
    created_at: str | None = None
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = self.model_dump(by_alias=True, exclude_none=True)
        return {"id": record.pop("id"), **record}
