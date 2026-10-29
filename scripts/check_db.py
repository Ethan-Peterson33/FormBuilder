#!/usr/bin/env python3
"""Verify the PostgreSQL connection and schema the backend will use.

    python scripts/check_db.py [--database-url URL]

Reports, with the real PostgreSQL messages:

* which DATABASE_URL is in effect and where it came from (flag, environment, .env)
* whether the server answers, and its version
* every migration file: applied / pending / MODIFIED
* whether the tables have the columns the backend uses (schema probes)
* whether every data table matches its registry (columns, types, NOT NULL, options)
* row counts

Exit code 0 when everything is fine, 1 otherwise. Runs inside backend/.venv
automatically when it exists.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BACKEND_DIR, ENV_FILE, fail, info, mask_url, ok, read_env_file, reexec_in_venv_if_needed, step, warn  # noqa: E402

sys.path.insert(0, str(BACKEND_DIR))


def resolve_url(flag: str | None) -> tuple[str | None, str]:
    if flag:
        return flag, "--database-url"
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"], "DATABASE_URL in the environment"
    dotenv = read_env_file(ENV_FILE)
    if dotenv.get("DATABASE_URL"):
        return dotenv["DATABASE_URL"], f"DATABASE_URL in {ENV_FILE}"
    return None, "not configured"


def _explain_containers() -> None:
    """After a failed connection: say what Docker knows, stopped containers included."""
    from _common import docker_state
    from _postgres import docker_postgres_containers

    state = docker_state()
    if state == "daemon-down":
        info("Docker CLI found but the daemon is not running - start Docker, then `docker start <container>` or ./start.sh")
        return
    if state != "ok":
        info("Check the host/port/user/password in DATABASE_URL and that the server is running.")
        return
    containers = docker_postgres_containers()
    if not containers:
        info("No postgres container exists (docker ps -a). Start one with ./start.sh or `docker compose up -d`.")
        return
    for c in containers:
        info(f"docker: {c.summary}")
    stopped = [c.name for c in containers if not c.running]
    if stopped:
        info("Stopped container(s): " + ", ".join(stopped) + " - start with `docker start NAME` or ./start.sh")
    else:
        info("A container is running; check that DATABASE_URL uses its host port and credentials (./start.sh fixes .env).")


def check_database(url: str, *, verbose: bool = True) -> int:
    try:
        import psycopg
    except ImportError:
        fail("psycopg is not installed; run scripts/start.py first (or pip install -r backend/requirements.txt)")
        return 1
    from core.database import Database
    from db import migrate
    from main import CONSISTENCY_CHECKS, SCHEMA_PROBES

    problems = 0

    # 1. connection
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            dbname = conn.execute("SELECT current_database()").fetchone()[0]
            user = conn.execute("SELECT current_user").fetchone()[0]
        if verbose:
            ok(f"connected: PostgreSQL {version}, database {dbname!r}, user {user!r}")
    except psycopg.OperationalError as exc:
        fail(f"cannot connect: {' '.join(str(exc).split())}")
        _explain_containers()
        return 1

    # 2. migrations
    with psycopg.connect(url, autocommit=True) as conn:
        states = migrate.migration_states(conn)
    for state in states:
        line = f"{state.label:32} {state.version}"
        if state.modified:
            fail(line); problems += 1
        elif not state.applied:
            warn(line); problems += 1
        elif verbose:
            ok(line)
    if any(not s.applied for s in states):
        info("apply with: cd backend && python -m db.migrate")
    if any(s.modified for s in states):
        info("an applied migration file was edited; restore it and add a new migration (docs/database.md)")

    # 3. schema probes + consistency checks + health check as the API performs it
    db = Database(url, schema_probes=SCHEMA_PROBES, consistency_checks=CONSISTENCY_CHECKS)
    db.open()
    try:
        status = db.check()
    finally:
        db.close()
    if status.ok:
        if verbose:
            ok("schema matches what the backend expects")
    else:
        err = status.error or {}
        if err.get("problems"):
            fail(err["message"])
            for problem in err["problems"]:
                fail(f"  {problem}")
            info("a user table was changed outside the API (or the registry was); repair the table or the "
                 "registry by hand so they agree (docs/data-tables.md, section 5.3)")
        else:
            fail(f"{err.get('message')}: {err.get('error')}")
            if err.get("hint"):
                info(err["hint"])
        problems += 1

    # 4. counts
    if status.ok:
        with psycopg.connect(url) as conn:
            counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("forms", "option_lists", "submissions", "data_tables")}
            deleted = conn.execute("SELECT count(*) FROM forms WHERE deleted_at IS NOT NULL").fetchone()[0]
        if verbose:
            ok(f"rows: forms {counts['forms']} ({deleted} deleted), option lists {counts['option_lists']}, "
               f"submissions {counts['submissions']}, data tables {counts['data_tables']} (all match their registry)")

    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    reexec_in_venv_if_needed()  # only when run as a script, never on import
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    step("Database check")
    url, source = resolve_url(args.database_url)
    if not url:
        fail("DATABASE_URL is not set (environment or .env). Run scripts/start.py, or pass --database-url.")
        return 1
    info(f"{mask_url(url)}  ({source})")
    code = check_database(url)
    print("RESULT: " + ("OK" if code == 0 else "PROBLEMS FOUND"))
    return code


if __name__ == "__main__":
    sys.exit(main())
