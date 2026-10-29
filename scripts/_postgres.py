"""Find the PostgreSQL instance to use.

Candidates, in order of preference:

1. ``--database-url`` / ``DATABASE_URL`` in the environment
2. ``DATABASE_URL`` in ``.env``
3. every *running* Docker container based on a ``postgres`` image - whatever
   directory its compose project lives in - using the credentials and the
   published host port read from ``docker inspect``
4. ``localhost`` on the usual ports with the default credentials

Each candidate is tested with a real connection. The first one that answers
is used; failures are reported with the PostgreSQL message so a wrong port or
password is obvious.

Containers that exist but are *stopped* are discovered too (``docker ps -a``);
``start.py`` starts the best one with ``docker start`` and waits for
PostgreSQL inside it to accept connections before falling back to this
repository's compose service.

Requires psycopg (runs under the backend virtualenv).
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

import psycopg

from _common import build_url, mask_url, run, split_url

DEFAULT_USER = "formbuilder"
DEFAULT_PASSWORD = "formbuilder"
DEFAULT_DB = "formbuilder"
FALLBACK_PORTS = (55432, 5432)
OUR_CONTAINER_NAME = "formbuilder-postgres"


@dataclass
class Candidate:
    url: str
    source: str                      # where it came from, for the report
    container: str | None = None     # docker container name, if any

    @property
    def display(self) -> str:
        return f"{mask_url(self.url)}  ({self.source})"


@dataclass
class ConnectResult:
    candidate: Candidate
    ok: bool
    message: str                     # server version or the error
    server_version: str | None = None


@dataclass
class ContainerInfo:
    name: str
    image: str
    status: str                      # running | exited | created | paused | ...
    host_port: int | None
    user: str
    password: str
    dbname: str
    ports_raw: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def summary(self) -> str:
        port = f"host port {self.host_port}" if self.host_port else "no published port"
        return f"{self.name} ({self.image}, {self.status}, {port}, db {self.dbname}, user {self.user})"


# -- docker --------------------------------------------------------------------

def docker_postgres_containers() -> list[ContainerInfo]:
    """All containers (running or not) whose image or name looks like PostgreSQL."""
    try:
        listing = run(["docker", "ps", "-a", "--format", "{{json .}}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if listing.returncode != 0:
        return []

    names: list[str] = []
    for line in listing.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        image = str(entry.get("Image", "")).lower()
        name = str(entry.get("Names", ""))
        if "postgres" in image or "postgres" in name.lower():
            names.append(name.split(",")[0])
    if not names:
        return []

    try:
        inspect = run(["docker", "inspect", *names], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if inspect.returncode != 0:
        return []
    try:
        details = json.loads(inspect.stdout)
    except json.JSONDecodeError:
        return []

    containers: list[ContainerInfo] = []
    for item in details:
        env = {}
        for pair in item.get("Config", {}).get("Env", []) or []:
            key, _, value = pair.partition("=")
            env[key] = value
        ports = item.get("NetworkSettings", {}).get("Ports", {}) or {}
        host_port = None
        for container_port, bindings in ports.items():
            if container_port.startswith("5432") and bindings:
                for binding in bindings:
                    try:
                        host_port = int(binding.get("HostPort"))
                        break
                    except (TypeError, ValueError):
                        continue
            if host_port:
                break
        containers.append(
            ContainerInfo(
                name=item.get("Name", "").lstrip("/"),
                image=item.get("Config", {}).get("Image", ""),
                status=item.get("State", {}).get("Status", ""),
                host_port=host_port,
                user=env.get("POSTGRES_USER", "postgres"),
                password=env.get("POSTGRES_PASSWORD", ""),
                dbname=env.get("POSTGRES_DB") or env.get("POSTGRES_USER", "postgres"),
                ports_raw=ports,
            )
        )

    # Running first; then our own container name, then the ones whose database is ours.
    def rank(c: ContainerInfo) -> tuple[int, int, int, str]:
        return (0 if c.running else 1, 0 if c.name == OUR_CONTAINER_NAME else 1, 0 if c.dbname == DEFAULT_DB else 1, c.name)

    return sorted(containers, key=rank)


def start_container(name: str) -> tuple[bool, str]:
    """``docker start NAME``. Returns ``(ok, message)``."""
    try:
        result = run(["docker", "start", name], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, result.stdout.strip()


def container_candidate(container: ContainerInfo) -> Candidate | None:
    if container.host_port is None:
        return None
    return Candidate(
        url=build_url(container.user, container.password, "localhost", container.host_port, container.dbname),
        source=f"docker container {container.name}, image {container.image}, host port {container.host_port}",
        container=container.name,
    )


# -- connection test -------------------------------------------------------------

def try_connect(candidate: Candidate, timeout: int = 3) -> ConnectResult:
    try:
        with psycopg.connect(candidate.url, connect_timeout=timeout) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
        return ConnectResult(candidate, True, f"PostgreSQL {version}", version)
    except psycopg.OperationalError as exc:
        return ConnectResult(candidate, False, " ".join(str(exc).split()))
    except psycopg.Error as exc:
        return ConnectResult(candidate, False, f"{type(exc).__name__}: {' '.join(str(exc).split())}")


_TRANSIENT = ("connection refused", "starting up", "connection reset", "server closed the connection",
              "could not connect", "timeout expired", "shutting down")


def looks_transient(message: str) -> bool:
    """Errors that a container which was just started will stop producing."""
    lowered = message.lower()
    return any(marker in lowered for marker in _TRANSIENT)


def wait_for_connection(candidate: Candidate, seconds: float, *, poll: float = 1.0) -> ConnectResult:
    """Retry until the server answers, the error stops looking transient, or time runs out."""
    deadline = time.monotonic() + seconds
    while True:
        result = try_connect(candidate, timeout=3)
        if result.ok or not looks_transient(result.message) or time.monotonic() >= deadline:
            return result
        time.sleep(poll)


# -- discovery ---------------------------------------------------------------------

def gather_candidates(
    explicit_url: str | None,
    env_url: str | None,
    dotenv_url: str | None,
    *,
    use_docker: bool,
    only_container: str | None = None,
) -> tuple[list[Candidate], list[ContainerInfo]]:
    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add(candidate: Candidate | None) -> None:
        if candidate and candidate.url not in seen:
            seen.add(candidate.url)
            candidates.append(candidate)

    if explicit_url:
        add(Candidate(explicit_url, "--database-url"))
    if env_url:
        add(Candidate(env_url, "DATABASE_URL in the environment"))
    if dotenv_url:
        add(Candidate(dotenv_url, "DATABASE_URL in .env"))

    containers: list[ContainerInfo] = []
    if use_docker:
        containers = docker_postgres_containers()
        if only_container:
            containers = [c for c in containers if c.name == only_container]
        for container in containers:
            if container.running:  # stopped ones are started by start.py if nothing answers
                add(container_candidate(container))

    # Last resort: the defaults on the usual ports. Reuse credentials from the
    # best URL we were given, if any, so a custom password still works.
    creds = split_url(explicit_url or env_url or dotenv_url) if (explicit_url or env_url or dotenv_url) else None
    user = (creds or {}).get("user") or DEFAULT_USER
    password = (creds or {}).get("password") or DEFAULT_PASSWORD
    dbname = (creds or {}).get("dbname") or DEFAULT_DB
    for port in FALLBACK_PORTS:
        add(Candidate(build_url(user, password, "localhost", port, dbname), f"default credentials on localhost:{port}"))

    # A URL from .env/the environment may address a running container without
    # saying so; tag it so a container that is still starting gets waited for.
    by_port = {c.host_port: c.name for c in containers if c.running and c.host_port}
    for candidate in candidates:
        if candidate.container is None:
            parts = split_url(candidate.url)
            if parts["host"] in ("localhost", "127.0.0.1") and int(parts["port"]) in by_port:
                candidate.container = by_port[int(parts["port"])]

    return candidates, containers
