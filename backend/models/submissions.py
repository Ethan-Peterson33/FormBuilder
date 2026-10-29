"""Form submission models.

A submission stores the answers (``data``) alongside ``metadata`` describing
when/how it was captured. ``form_id`` + ``id`` make individual submissions easy
to look up; the structure maps 1:1 onto a future ``submissions`` table.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from models.forms import CamelModel

SubmissionValue = str | int | float | bool | list[str] | None


class SubmissionMetadata(CamelModel):
    model_config = ConfigDict(
        alias_generator=CamelModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="allow",  # callers may attach additional context
    )

    submitted_at: str
    submitted_by: str | None = None
    source: str = "web"
    user_agent: str | None = None
    ip_address: str | None = None
    # Fields that were hidden by conditional logic at submit time. Their values
    # are not stored, but knowing they were hidden is useful for reporting.
    hidden_fields: list[str] = Field(default_factory=list)


class SubmissionCreate(CamelModel):
    """Payload accepted by ``POST /forms/{form_id}/submissions``."""

    data: dict[str, SubmissionValue] = Field(default_factory=dict)
    submitted_by: str | None = None
    source: str = "web"
    # Free-form extra metadata the client wants to record (e.g. page URL).
    metadata: dict[str, Any] = Field(default_factory=dict)


class Submission(CamelModel):
    id: str
    form_id: str
    form_name: str
    data: dict[str, SubmissionValue]
    metadata: SubmissionMetadata

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class SubmissionList(CamelModel):
    total: int
    items: list[Submission]
