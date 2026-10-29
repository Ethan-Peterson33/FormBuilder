"""Helpers shared by start.py, check_db.py and check_health.py.

Deliberately dependency-free (standard library only) so the bootstrap can run
under whatever Python is on the machine before the virtualenv exists.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
VENV_DIR = BACKEND_DIR / ".venv"
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"

# Interpreters the backend is known to work with. 3.14 is excluded until the
# compiled dependencies (psycopg-binary, pydantic-core, uvicorn extras) are
# verified on it; override with --python / FORMBUILDER_PYTHON.
MIN_PYTHON = (3, 11)
MAX_PYTHON_EXCLUSIVE = (3, 14)

IS_WINDOWS = os.name == "nt"


# -- output ------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty() and not IS_WINDOWS or bool(os.environ.get("WT_SESSION"))


_COLORS = {"ok": "32", "warn": "33", "fail": "31", "step": "36"}


def _paint(kind: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{_COLORS[kind]}m{text}\033[0m"


def step(text: str) -> None:
    print(_paint("step", f"==> {text}"))


def ok(text: str) -> None:
    print(_paint("ok", "    ok  ") + text)


def warn(text: str) -> None:
    print(_paint("warn", "    !!  ") + text)


def fail(text: str) -> None:
    print(_paint("fail", "    XX  ") + text, file=sys.stderr)


def info(text: str) -> None:
    print("        " + text)


# -- virtualenv --------------------------------------------------------------

def venv_python(venv: Path = VENV_DIR) -> Path:
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def running_in_venv(venv: Path = VENV_DIR) -> bool:
    try:
        return Path(sys.prefix).resolve() == venv.resolve()
    except OSError:
        return False


def reexec_in_venv_if_needed(venv: Path = VENV_DIR) -> None:
    """Check scripts want the backend's dependencies. If the venv exists and
    we are not inside it, re-run the current script with its interpreter."""
    python = venv_python(venv)
    if running_in_venv(venv) or not python.exists():
        return
    os.execv(str(python), [str(python), *sys.argv])


def python_version_of(executable: str | Path) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            [str(executable), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()
        major, minor, micro = (int(part) for part in out.split("."))
        return major, minor, micro
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def is_supported_python(version: tuple[int, int, int] | None) -> bool:
    return version is not None and MIN_PYTHON <= version[:2] < MAX_PYTHON_EXCLUSIVE


def find_python(preferred: str | None = None) -> tuple[str, tuple[int, int, int]] | None:
    """Locate a supported interpreter. ``preferred`` (path or command) wins if
    it exists, whatever its version."""
    if preferred:
        version = python_version_of(preferred)
        if version is None:
            raise SystemExit(f"--python {preferred!r} is not a working Python interpreter")
        return preferred, version

    candidates: list[list[str]] = []
    for minor in range(MAX_PYTHON_EXCLUSIVE[1] - 1, MIN_PYTHON[1] - 1, -1):
        if IS_WINDOWS:
            candidates.append(["py", f"-3.{minor}"])
        candidates.append([f"python3.{minor}"])
    candidates += [["python3"], ["python"], [sys.executable]]

    for command in candidates:
        exe = shutil.which(command[0]) if len(command) == 1 else shutil.which("py")
        if not exe:
            continue
        if len(command) == 2:  # Windows launcher: resolve to the real interpreter
            try:
                real = subprocess.run(
                    [exe, command[1], "-c", "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=20, check=True,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            exe = real
        version = python_version_of(exe)
        if is_supported_python(version):
            return exe, version  # type: ignore[return-value]
    return None


# -- .env files ----------------------------------------------------------------

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    return parse_env_text(path.read_text(encoding="utf-8")) if path.is_file() else {}


def update_env_file(updates: dict[str, str], path: Path = ENV_FILE, template: Path = ENV_EXAMPLE) -> list[str]:
    """Set keys in ``path`` (created from ``template`` if missing), keeping
    every other line and comment. Returns the keys whose value changed."""
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif template.is_file():
        lines = template.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    current = parse_env_text("\n".join(lines))
    changed = [key for key, value in updates.items() if current.get(key) != value]
    if not changed:
        return []

    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        match = _ENV_LINE.match(line.strip())
        if match and match.group(1) in remaining:
            key = match.group(1)
            new_lines.append(f"{key}={remaining.pop(key)}")
        elif line.strip().startswith("#") and (m := _ENV_LINE.match(line.strip().lstrip("#").strip())) and m.group(1) in remaining:
            # A commented-out placeholder like "# FORMBUILDER_DB_TIMEOUT=5"
            key = m.group(1)
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)
    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# Added by scripts/start.py")
        new_lines.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return changed


def load_env_file_into_environ(path: Path = ENV_FILE) -> None:
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


# -- URLs --------------------------------------------------------------------

def mask_url(url: str) -> str:
    """Hide the password for display."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def build_url(user: str, password: str, host: str, port: int | str, dbname: str) -> str:
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def split_url(url: str) -> dict[str, str]:
    """Parts of a ``postgresql://`` URL or a libpq ``key=value`` conninfo string."""
    if "://" not in url:
        pairs = dict(item.split("=", 1) for item in url.split() if "=" in item)
        return {
            "user": pairs.get("user", ""),
            "password": pairs.get("password", ""),
            "host": pairs.get("host", "localhost"),
            "port": pairs.get("port", "5432"),
            "dbname": pairs.get("dbname", ""),
        }
    parts = urlsplit(url)
    return {
        "user": parts.username or "",
        "password": parts.password or "",
        "host": parts.hostname or "localhost",
        "port": str(parts.port or 5432),
        "dbname": parts.path.lstrip("/"),
    }


# -- processes ---------------------------------------------------------------

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)


def docker_state() -> str:
    """``"ok"`` (CLI and daemon usable), ``"daemon-down"`` (CLI present but the
    daemon does not answer - Docker Desktop not started), or ``"absent"``."""
    if not shutil.which("docker"):
        return "absent"
    try:
        result = run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "daemon-down"
    return "ok" if result.returncode == 0 else "daemon-down"


def docker_available() -> bool:
    return docker_state() == "ok"
