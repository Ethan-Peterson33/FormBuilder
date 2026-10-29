"""The SQL page's endpoints.

* ``POST /sql/query``  - run one statement and return a grid-shaped result
* ``GET  /sql/tables`` - every table the SQL and row APIs expose (sidebar)
* ``GET  /sql/rules``  - what the guard allows and blocks (to show in the UI)

What may run is decided by ``services/sql_guard.py`` (single statement;
SELECT / WITH / VALUES / TABLE / EXPLAIN / SHOW / INSERT / UPDATE; no
ALTER / DROP / DELETE / TRUNCATE / CREATE / ... anywhere; writes only to
exposed tables) and enforced again by the database (``repositories/pg_sql.py``:
read-only transaction, row cap, timeout).

| status | when | ``detail`` |
| --- | --- | --- |
| 400 | the guard refuses the statement | ``{message: "Statement not allowed", error}`` |
| 400 | PostgreSQL refuses or fails it | ``{message: "SQL error", error, sqlstate, position, detail, hint, timedOut}`` |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from core.dependencies import get_sql_repository
from models.forms import CamelModel
from repositories.pg_sql import (
    DEFAULT_LIMIT,
    DEFAULT_TIMEOUT_MS,
    MAX_LIMIT,
    MAX_TIMEOUT_MS,
    PgSqlRepository,
    SqlExecutionError,
)
from services import sql_guard

router = APIRouter(prefix="/sql", tags=["sql"])


class SqlRequest(CamelModel):
    sql: str = Field(min_length=1, max_length=100_000)
    # Bound to ``%s`` placeholders in the statement (a literal ``%`` is then ``%%``).
    params: list[Any] = Field(default_factory=list)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, ge=100, le=MAX_TIMEOUT_MS)


@router.post("/query")
def run_query(payload: SqlRequest, repo: PgSqlRepository = Depends(get_sql_repository)) -> JSONResponse:
    try:
        verdict = sql_guard.check(payload.sql)
    except sql_guard.SqlGuardError as exc:
        raise HTTPException(status_code=400, detail={"message": "Statement not allowed", "error": str(exc)}) from exc
    try:
        result = repo.run(verdict, payload.params, limit=payload.limit, timeout_ms=payload.timeout_ms)
    except SqlExecutionError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "SQL error",
                "error": str(exc),
                "sqlstate": exc.sqlstate,
                "position": exc.position,
                "detail": exc.detail,
                "hint": exc.hint,
                "timedOut": exc.timed_out,
            },
        ) from exc
    return JSONResponse(content=result.as_dict())


@router.get("/tables")
def list_tables(repo: PgSqlRepository = Depends(get_sql_repository)) -> JSONResponse:
    return JSONResponse(content={"tables": repo.list_tables()})


@router.get("/rules")
def rules() -> dict[str, Any]:
    return {
        "allowedStatements": sorted(sql_guard.ALLOWED_STARTERS),
        "readOnlyStatements": sorted(sql_guard.READ_STARTERS | sql_guard.EXPLAIN_STARTERS),
        "writeStatements": sorted(sql_guard.WRITE_STARTERS),
        "blockedKeywords": sorted(sql_guard.BLOCKED_KEYWORDS),
        "blockedFunctions": sorted(sql_guard.BLOCKED_FUNCTIONS),
        "singleStatement": True,
        "defaultLimit": DEFAULT_LIMIT,
        "maxLimit": MAX_LIMIT,
        "defaultTimeoutMs": DEFAULT_TIMEOUT_MS,
        "maxTimeoutMs": MAX_TIMEOUT_MS,
    }
