"""Identifier helpers."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:64]


def unique_slug_id(name: str, exists: Callable[[str], bool], fallback: str = "item") -> str:
    """Generate a URL-safe id from ``name``, suffixing ``-2``, ``-3``... until
    the id is unused."""
    base = slugify(name) or fallback
    candidate = base
    counter = 2
    while exists(candidate):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def unique_form_id(name: str, exists: Callable[[str], bool]) -> str:
    return unique_slug_id(name, exists, fallback="form")


def new_submission_id() -> str:
    return f"sub-{uuid.uuid4()}"


def unique_table_id(name: str, exists: Callable[[str], bool]) -> str:
    return unique_slug_id(name, exists, fallback="table")


def new_row_id() -> str:
    return f"row-{uuid.uuid4()}"


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
