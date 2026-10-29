"""Submission processing: validate answers against the form definition, apply
conditional-logic visibility, and build the stored record with metadata."""

from __future__ import annotations

import re
from typing import Any

from models.forms import FieldConfig, FormConfig
from models.submissions import Submission, SubmissionCreate, SubmissionMetadata
from services.form_logic import get_visible_field_ids, is_empty_value
from services.ids import new_submission_id, utc_now_iso
from services.values import parse_bool, parse_number

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SubmissionValidationError(ValueError):
    """Raised when submitted answers do not satisfy the form definition."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Submission failed validation")
        self.errors = errors


def _coerce_value(field: FieldConfig, value: Any) -> tuple[Any, str | None]:
    """Normalise ``value`` for ``field``. Returns ``(value, error)``."""
    if is_empty_value(value):
        return None, None

    if field.type == "number":
        return parse_number(value)

    if field.type == "checkbox":
        return parse_bool(value)

    if field.type == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
            return None, "must be a date in YYYY-MM-DD format"
        return value.strip(), None

    if field.type in {"select", "radio"}:
        text = str(value).strip()
        if field.options and text not in field.options:
            return None, f"must be one of: {', '.join(field.options)}"
        return text, None

    # text / textarea
    if isinstance(value, (list, dict)):
        return None, "must be text"
    return str(value), None


def build_submission(
    form: FormConfig,
    payload: SubmissionCreate,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> Submission:
    """Validate ``payload`` against ``form`` and return a ready-to-store record.

    Raises :class:`SubmissionValidationError` listing every problem found.
    """
    fields_by_id = {field.id: field for field in form.fields}
    errors: dict[str, str] = {}

    unknown = [key for key in payload.data if key not in fields_by_id]
    for key in unknown:
        errors[key] = "is not a field on this form"

    # Coerce first so logic is evaluated with clean values.
    cleaned: dict[str, Any] = {}
    for field_id, raw in payload.data.items():
        field = fields_by_id.get(field_id)
        if field is None:
            continue
        value, error = _coerce_value(field, raw)
        if error:
            errors[field_id] = error
        elif value is not None:
            cleaned[field_id] = value

    visible_ids = get_visible_field_ids(form.fields, cleaned)
    visible = set(visible_ids)
    hidden_fields = [field.id for field in form.fields if field.id not in visible]

    for field in form.fields:
        if field.id not in visible:
            continue
        if field.required and field.id not in cleaned and field.id not in errors:
            errors[field.id] = "is required"
        if field.type == "checkbox" and field.required and cleaned.get(field.id) is False:
            errors[field.id] = "must be checked"

    if errors:
        raise SubmissionValidationError(errors)

    data = {field.id: cleaned[field.id] for field in form.fields if field.id in visible and field.id in cleaned}

    # Client-supplied extras may not override the server-controlled keys.
    reserved = set(SubmissionMetadata.model_fields) | {
        info.alias for info in SubmissionMetadata.model_fields.values() if info.alias
    }
    extras = {key: value for key, value in payload.metadata.items() if key not in reserved}

    metadata = SubmissionMetadata(
        submitted_at=utc_now_iso(),
        submitted_by=payload.submitted_by,
        source=payload.source,
        user_agent=user_agent,
        ip_address=ip_address,
        hidden_fields=hidden_fields,
        **extras,
    )

    return Submission(
        id=new_submission_id(),
        form_id=form.id,
        form_name=form.name,
        data=data,
        metadata=metadata,
    )
