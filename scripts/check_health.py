#!/usr/bin/env python3
"""Verify a running backend: /health plus one read per resource.

    python scripts/check_health.py [--url http://localhost:8000]

Exit code 0 when the API is up and its database is healthy, 1 otherwise.
Standard library only; works with any Python.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fail, info, ok, step, warn  # noqa: E402


def get(url: str, timeout: float = 5.0) -> tuple[int | None, dict | list | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null"), ""
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body), ""
        except ValueError:
            return exc.code, None, body.decode(errors="replace")[:200]
    except (urllib.error.URLError, OSError) as exc:
        return None, None, str(getattr(exc, "reason", exc))


def check_api(base: str) -> int:
    base = base.rstrip("/")
    problems = 0

    status, body, error = get(f"{base}/health")
    if status is None:
        fail(f"API not reachable at {base}: {error}")
        info("Start it with scripts/start.py (or: cd backend && uvicorn main:app --port 8000)")
        return 1
    db = (body or {}).get("database") or {}
    if status == 200:
        ok(f"/health 200 - PostgreSQL {db.get('serverVersion')}, "
           f"migrations {db.get('appliedMigrations')}")
    else:
        err = db.get("error") or {}
        fail(f"/health {status} - {err.get('message')}: {err.get('error')}")
        if err.get("hint"):
            info(err["hint"])
        if db.get("pendingMigrations"):
            info(f"pending migrations: {db['pendingMigrations']}")
        problems += 1

    for path, describe in (
        ("/forms", lambda b: f"{len(b)} form(s)"),
        ("/option-lists", lambda b: f"{len(b)} option list(s)"),
        ("/submissions?limit=1", lambda b: f"{b.get('total')} submission(s)"),
    ):
        status, body, error = get(f"{base}{path}")
        if status == 200:
            ok(f"GET {path} 200 - {describe(body)}")
        else:
            detail = (body or {}).get("detail") if isinstance(body, dict) else None
            message = f"{detail.get('message')}: {detail.get('error')}" if isinstance(detail, dict) else (detail or error)
            fail(f"GET {path} {status} - {message}")
            problems += 1
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("FORMBUILDER_API_URL", "http://localhost:8000"))
    args = parser.parse_args(argv)
    step(f"API check: {args.url}")
    code = check_api(args.url)
    print("RESULT: " + ("OK" if code == 0 else "PROBLEMS FOUND"))
    return code


if __name__ == "__main__":
    sys.exit(main())
