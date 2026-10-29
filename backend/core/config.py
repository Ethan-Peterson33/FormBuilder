"""Application configuration.

Values come from environment variables (and ``.env`` files) so the same code
runs locally, in tests (against a scratch database), and in deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# ``.env`` files consulted at import time, first match wins per key. Values
# already present in the environment always take precedence.
DOTENV_FILES: tuple[Path, ...] = (BACKEND_DIR / ".env", REPO_ROOT / ".env")


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` parser: comments, blank lines, ``export`` prefix
    and single/double quotes are handled; no interpolation."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_dotenv(paths: tuple[Path, ...] = DOTENV_FILES) -> list[Path]:
    """Load ``.env`` files into ``os.environ`` without overriding existing
    variables. Returns the files that were read."""
    loaded: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
            os.environ.setdefault(key, value)
        loaded.append(path)
    return loaded


load_dotenv()


class StorageConfigurationError(RuntimeError):
    """The database settings are missing or inconsistent; raised at start-up, never per request."""


@dataclass(frozen=True)
class Settings:
    """How the API reaches its PostgreSQL database.

    ``database_url`` is required: the API does not start without it. There is
    no other storage backend.
    """

    database_url: str | None = None
    # Seconds a request waits for a database connection before failing with 503.
    database_timeout: float = 5.0

    def __post_init__(self) -> None:
        if not self.database_url:
            raise StorageConfigurationError(
                "DATABASE_URL is not set. "
                "Set DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME (see docs/database.md), "
                "or run ./start.sh which finds the database and writes .env."
            )

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=get_database_url(),
            database_timeout=float(os.environ.get("FORMBUILDER_DB_TIMEOUT", "5")),
        )


def get_data_dir() -> Path:
    """Directory with the JSON data files that ``python -m db.import_json``
    loads (seed data / file backups). Override with ``FORMBUILDER_DATA_DIR``.
    The directory is created on demand.
    """
    data_dir = Path(os.environ.get("FORMBUILDER_DATA_DIR", BACKEND_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_image_dir() -> Path:
    image_dir = Path(os.environ.get("FORMBUILDER_IMAGE_DIR", BACKEND_DIR / "images"))
    image_dir.mkdir(parents=True, exist_ok=True)
    return image_dir


def get_database_url() -> str | None:
    """PostgreSQL connection URL, e.g. ``postgresql://user:pass@host:5432/db``.

    Read from ``DATABASE_URL``. ``None`` means "not configured", which stops
    the backend at start-up. See ``docs/database.md``.
    """
    return os.environ.get("DATABASE_URL") or None
