#!/usr/bin/env python3
"""One-command start-up for the backend after pulling new code.

    python scripts/start.py            (or ./start.sh / start.cmd)

What it does, in order:

1. Finds a supported Python (3.11-3.13; ``--python`` / ``FORMBUILDER_PYTHON``
   to override), creates ``backend/.venv`` with it if needed, and installs
   ``backend/requirements.txt`` when they changed.
2. Re-runs itself inside that virtualenv.
3. Finds PostgreSQL: ``DATABASE_URL`` (flag, environment, ``.env``), then any
   running Docker container with a postgres image (from any directory), then
   localhost on the usual ports. Every candidate is tested with a real
   connection. If nothing answers and Docker is available, starts this
   repository's ``docker compose`` service and waits for it.
4. Updates ``.env`` so ``DATABASE_URL`` (and ``POSTGRES_*``) point at the
   instance that works. The backend, the ``db.*`` tools and pytest read
   ``.env`` themselves, so everything afterwards agrees.
5. Applies pending migrations (``python -m db.migrate``), refusing on drift.
6. Imports ``backend/data/*.json`` once, if the database is empty.
7. Verifies the database (the same checks as ``scripts/check_db.py``).
8. Starts the API with uvicorn, waits for ``/health``, prints the result and
   keeps serving until Ctrl+C.

Flags turn individual steps off (``--no-serve`` for CI, ``--no-docker`` if
Docker must not be touched, ``--no-import``, ``--no-migrate``).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import venv as venv_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    BACKEND_DIR,
    ENV_FILE,
    MAX_PYTHON_EXCLUSIVE,
    MIN_PYTHON,
    REPO_ROOT,
    REQUIREMENTS,
    VENV_DIR,
    docker_state,
    fail,
    find_python,
    info,
    is_supported_python,
    mask_url,
    ok,
    python_version_of,
    read_env_file,
    run,
    running_in_venv,
    split_url,
    step,
    update_env_file,
    venv_python,
    warn,
)

STAMP = VENV_DIR / ".requirements.sha256"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=os.environ.get("FORMBUILDER_PYTHON"), help="interpreter to build the venv with")
    parser.add_argument("--recreate-venv", action="store_true", help="delete and rebuild backend/.venv")
    parser.add_argument("--reinstall", action="store_true", help="pip install even if requirements.txt is unchanged")
    parser.add_argument("--database-url", default=None, help="use this PostgreSQL URL (tested first)")
    parser.add_argument("--container", default=None, help="only consider this Docker container")
    parser.add_argument("--no-docker", action="store_true", help="do not look at or start Docker containers")
    parser.add_argument("--no-start-containers", action="store_true",
                        help="never `docker start` a stopped container or `docker compose up` (only use what is running)")
    parser.add_argument("--no-migrate", action="store_true", help="skip applying migrations")
    parser.add_argument("--no-import", action="store_true", help="never import backend/data/*.json")
    parser.add_argument("--no-serve", action="store_true", help="set everything up but do not start the API")
    parser.add_argument("--no-env-update", action="store_true", help="do not write .env")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORMBUILDER_API_PORT", "8000")))
    parser.add_argument("--no-reload", action="store_true", help="run uvicorn without --reload")
    parser.add_argument("--stage2", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.passthrough = list(sys.argv[1:] if argv is None else argv)
    return args


# =============================================================================
# Stage 1: interpreter, virtualenv, dependencies (standard library only)
# =============================================================================

def stage1(args: argparse.Namespace) -> int:
    step("Python")
    found = find_python(args.python)
    if found is None:
        fail(
            f"No Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}-{MAX_PYTHON_EXCLUSIVE[0]}.{MAX_PYTHON_EXCLUSIVE[1] - 1} found. "
            "Install one (https://www.python.org/downloads/) or pass --python /path/to/python3.13"
        )
        return 1
    executable, version = found
    label = f"Python {version[0]}.{version[1]}.{version[2]} at {executable}"
    if not is_supported_python(version):
        warn(f"{label} is outside the tested range (chosen explicitly; continuing)")
    else:
        ok(label)

    step("Virtualenv")
    python = venv_python(VENV_DIR)
    if VENV_DIR.exists():
        current = python_version_of(python) if python.exists() else None
        if args.recreate_venv or current is None or current[:2] != version[:2]:
            reason = "requested" if args.recreate_venv else (
                "broken" if current is None else f"built with Python {current[0]}.{current[1]}, want {version[0]}.{version[1]}"
            )
            warn(f"Rebuilding {VENV_DIR} ({reason})")
            shutil.rmtree(VENV_DIR)
    if not VENV_DIR.exists():
        info(f"Creating {VENV_DIR} with {executable}")
        result = run([str(executable), "-m", "venv", str(VENV_DIR)])
        if result.returncode != 0:
            fail("python -m venv failed")
            return 1
    ok(f"{VENV_DIR} (Python {'.'.join(map(str, python_version_of(python) or ()))})")

    step("Dependencies")
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if args.reinstall or not STAMP.exists() or STAMP.read_text().strip() != digest:
        info(f"pip install -r {REQUIREMENTS}")
        result = run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)])
        if result.returncode != 0:
            fail("pip install failed (see output above)")
            return 1
        STAMP.write_text(digest)
        ok("requirements installed")
    else:
        ok("requirements unchanged, nothing to install")

    # Hand over to the virtualenv's interpreter for everything that needs psycopg.
    cmd = [str(python), str(Path(__file__).resolve()), "--stage2", *args.passthrough]
    child = subprocess.Popen(cmd)
    try:
        return child.wait()
    except KeyboardInterrupt:
        # Ctrl+C reaches the child as well; let it shut the API down cleanly.
        try:
            return child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            return 130


# =============================================================================
# Stage 2: PostgreSQL, .env, migrations, data, API (runs inside the venv)
# =============================================================================

def stage2(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        import psycopg  # noqa: F401
    except ImportError:
        fail(f"psycopg is not installed in {sys.prefix}; run scripts/start.py --reinstall")
        return 1

    from _postgres import (  # noqa: E402
        Candidate,
        container_candidate,
        gather_candidates,
        looks_transient,
        start_container,
        try_connect,
        wait_for_connection,
    )

    step("PostgreSQL")
    dotenv = read_env_file(ENV_FILE)
    env_url = os.environ.get("DATABASE_URL")
    docker = "disabled" if args.no_docker else docker_state()
    use_docker = docker == "ok"
    if docker == "absent":
        info("Docker is not installed; only URLs and localhost ports are considered")
    elif docker == "daemon-down":
        warn("Docker CLI found but the daemon is not running (start Docker Desktop / dockerd); "
             "containers cannot be inspected or started")

    candidates, containers = gather_candidates(
        args.database_url, env_url, dotenv.get("DATABASE_URL"),
        use_docker=use_docker, only_container=args.container,
    )
    for c in containers:
        info(f"docker: {c.summary}")
    if args.container and use_docker and not containers:
        fail(f"no container named {args.container!r} (docker ps -a)")
        return 1

    chosen = None
    for candidate in candidates:
        result = try_connect(candidate)
        if not result.ok and candidate.container and looks_transient(result.message):
            # A container that is up but whose PostgreSQL is still starting.
            info(f"{candidate.container} is running but not answering yet ({result.message}); waiting up to 30 s")
            result = wait_for_connection(candidate, 30)
        if result.ok:
            ok(f"{candidate.display}: {result.message}")
            chosen = candidate
            break
        warn(f"{candidate.display}: {result.message}")

    stopped = [c for c in containers if not c.running]
    if chosen is None and stopped and use_docker:
        if args.no_start_containers:
            warn("stopped container(s) found but --no-start-containers given: " + ", ".join(c.name for c in stopped))
        else:
            chosen = start_stopped_container(stopped, container_candidate, start_container, wait_for_connection)

    if chosen is None and use_docker and not args.container and not args.no_start_containers and not containers:
        chosen = start_compose_postgres(dotenv, wait_for_connection, Candidate)

    if chosen is None:
        fail("No PostgreSQL instance answered.")
        if containers:
            info("Containers seen (docker ps -a): " + "; ".join(c.summary for c in containers))
            info("Start one with `docker start NAME`, or pick it with --container NAME.")
        elif docker == "daemon-down":
            info("Start Docker, then run this script again.")
        else:
            info("Start one with `docker compose up -d` (POSTGRES_PORT in .env, default 55432),")
        info("or point at any server with --database-url postgresql://user:pass@host:port/db")
        return 1

    step("Environment (.env)")
    updates = {"DATABASE_URL": chosen.url}
    parts = split_url(chosen.url)
    updates.update({"POSTGRES_USER": parts["user"], "POSTGRES_PASSWORD": parts["password"],
                    "POSTGRES_DB": parts["dbname"], "POSTGRES_PORT": parts["port"]})
    if args.no_env_update:
        info(f"not writing .env (--no-env-update); DATABASE_URL={mask_url(chosen.url)}")
    else:
        changed = update_env_file(updates, ENV_FILE)
        if changed:
            ok(f"{ENV_FILE} updated: {', '.join(changed)}")
        else:
            ok(f"{ENV_FILE} already points at this database")
    os.environ["DATABASE_URL"] = chosen.url

    from db import migrate  # noqa: E402

    step("Migrations")
    try:
        with psycopg.connect(chosen.url, autocommit=True) as conn:
            states = migrate.migration_states(conn)
            for state in states:
                info(f"{state.label:32} {state.version}")
            if any(s.modified for s in states):
                fail("An applied migration file was modified. Restore it (git checkout backend/db/migrations) "
                     "and put the change in a new migration. See docs/database.md.")
                return 2
            pending = [s.version for s in states if not s.applied]
            if pending and args.no_migrate:
                warn(f"pending but --no-migrate given: {', '.join(pending)}")
            elif pending:
                applied = migrate.apply_migrations(conn)
                ok(f"applied {', '.join(applied)}")
            else:
                ok("schema up to date")
    except migrate.MigrationDriftError as exc:
        fail(str(exc))
        return 2
    except psycopg.Error as exc:
        fail(f"migration failed: {' '.join(str(exc).split())}")
        return 2

    step("Data")
    if args.no_import:
        info("skipped (--no-import)")
    else:
        import_if_empty(chosen.url)

    step("Database check")
    from check_db import check_database  # noqa: E402
    if check_database(chosen.url, verbose=False) != 0:
        fail("database check failed (run `python scripts/check_db.py` for details)")
        return 1
    ok("connection, migrations and schema verified")

    if args.no_serve:
        step("Done (--no-serve)")
        info(f"Start the API with: cd backend && {venv_python()} -m uvicorn main:app --reload --port {args.port}")
        return 0

    return serve(args)


def start_stopped_container(stopped, container_candidate, start_container, wait_for_connection):
    """Nothing is answering but a stopped postgres container exists: start the
    best-ranked one and wait for PostgreSQL inside it."""
    container = stopped[0]
    if len(stopped) > 1:
        info("several stopped containers; starting " + container.name + " (use --container NAME to pick another)")
    candidate = container_candidate(container)
    if candidate is None:
        warn(f"{container.name} publishes no port; cannot be reached from the host (check its compose 'ports:')")
        return None
    info(f"docker start {container.name}")
    started, message = start_container(container.name)
    if not started:
        fail(f"docker start {container.name} failed: {message}")
        return None
    info("waiting up to 60 s for PostgreSQL in the container to accept connections")
    result = wait_for_connection(candidate, 60)
    if result.ok:
        ok(f"{candidate.display}: {result.message}")
        return candidate
    fail(f"{container.name} started but PostgreSQL did not answer: {result.message}")
    info("Check its log with: docker logs " + container.name)
    return None


def start_compose_postgres(dotenv: dict[str, str], wait_for_connection, Candidate):
    """No container exists at all: bring up this repository's compose service."""
    compose_file = REPO_ROOT / "docker-compose.yml"
    if not compose_file.exists():
        return None
    port = dotenv.get("POSTGRES_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
    user = dotenv.get("POSTGRES_USER", "formbuilder")
    password = dotenv.get("POSTGRES_PASSWORD", "formbuilder")
    dbname = dotenv.get("POSTGRES_DB", "formbuilder")
    info(f"starting docker compose service 'postgres' on port {port} (POSTGRES_PORT in .env)")
    if not ENV_FILE.exists():
        update_env_file({"POSTGRES_PORT": port}, ENV_FILE)
    result = run(["docker", "compose", "-f", str(compose_file), "up", "-d", "postgres"], cwd=REPO_ROOT)
    if result.returncode != 0:
        fail("docker compose up failed. If the port is in use, set POSTGRES_PORT in .env (e.g. 55432) and retry.")
        return None
    candidate = Candidate(f"postgresql://{user}:{password}@localhost:{port}/{dbname}", "docker compose (this repository)",
                          container="formbuilder-postgres")
    info("waiting up to 90 s for the new container to initialise")
    result = wait_for_connection(candidate, 90, poll=2)
    if result.ok:
        ok(f"{candidate.display}: {result.message}")
        return candidate
    fail(f"container started but did not answer within 90 s: {result.message}")
    info("Check its log with: docker compose logs postgres")
    return None


def import_if_empty(url: str) -> None:
    import psycopg
    from core.config import get_data_dir
    from db import import_json

    data_dir = get_data_dir()
    with psycopg.connect(url) as conn:
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("forms", "option_lists", "submissions")}
        if any(counts.values()):
            ok(f"database has data (forms {counts['forms']}, option lists {counts['option_lists']}, "
               f"submissions {counts['submissions']}); not importing")
            return
        if not any((data_dir / name).exists() for name in ("forms.json", "option_lists.json", "submissions.json")):
            ok(f"empty database and no JSON files in {data_dir}; nothing to import")
            return
        try:
            report = import_json.import_data_dir(conn, data_dir)
        except import_json.OrphanSubmissionsError as exc:
            warn(f"import skipped: {exc}")
            return
    ok(f"imported {data_dir}: forms {report.forms}, option lists {report.option_lists}, submissions {report.submissions}")


def launch_api(host: str, port: int, *, reload: bool = True, python: Path | None = None,
               wait_seconds: float = 30.0) -> tuple[subprocess.Popen, tuple[int, dict] | None]:
    """Start uvicorn and wait for /health. Returns the process (still running
    unless it died) and the first health response ``(status, body)`` or
    ``None`` if it did not answer in time."""
    import json
    import urllib.error
    import urllib.request

    cmd = [str(python or venv_python(VENV_DIR)), "-m", "uvicorn", "main:app", "--host", host, "--port", str(port)]
    if reload:
        cmd.append("--reload")
    info(" ".join(cmd[1:]))
    process = subprocess.Popen(cmd, cwd=BACKEND_DIR)
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + wait_seconds
    status = None
    while time.time() < deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                status = (response.status, json.loads(response.read()))
                break
        except urllib.error.HTTPError as exc:
            status = (exc.code, json.loads(exc.read() or b"{}"))
            break
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    return process, status


def report_health(status: tuple[int, dict] | None, wait_seconds: float = 30.0) -> None:
    if status is None:
        warn(f"API did not answer /health within {wait_seconds:.0f} s (still running; check the log above)")
    elif status[0] == 200:
        db = status[1].get("database") or {}
        ok(f"healthy - PostgreSQL {db.get('serverVersion')}, migrations {db.get('appliedMigrations')}")
    else:
        err = (status[1].get("database") or {}).get("error") or {}
        fail(f"/health -> {status[0]}: {err.get('message')}: {err.get('error')}")
        info(err.get("hint", ""))


def serve(args: argparse.Namespace) -> int:
    step(f"API on http://localhost:{args.port}")
    process, status = launch_api(args.host, args.port, reload=not args.no_reload)
    if process.poll() is not None:
        fail(f"uvicorn exited with code {process.returncode}")
        return process.returncode or 1
    report_health(status)
    info("Ctrl+C stops the API.")
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(REPO_ROOT)
    if args.stage2 or running_in_venv():
        return stage2(args)
    return stage1(args)


if __name__ == "__main__":
    sys.exit(main())
