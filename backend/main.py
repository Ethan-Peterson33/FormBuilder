"""FastAPI entry point.

Run from this directory with::

    export DATABASE_URL=postgresql://formbuilder:formbuilder@localhost:5432/formbuilder
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Storage is PostgreSQL, always. Database problems are reported, never worked
around: a missing ``DATABASE_URL`` stops start-up, an unreachable database
answers ``503`` and a missing/outdated schema ``500`` - each with the real
PostgreSQL message.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import BACKEND_DIR, Settings, get_image_dir
from core.database import DATABASE_ERRORS, Database, describe_database_error
from repositories import pg_forms, pg_option_lists, pg_submissions, pg_tables
from repositories.base import DefinitionConflictError, RowConstraintError, TableSchemaMismatchError
from routers import forms, images, option_lists, sql, submissions, table_rows, tables

SCHEMA_PROBES = (
    pg_forms.SCHEMA_PROBE, pg_option_lists.SCHEMA_PROBE, pg_submissions.SCHEMA_PROBE, *pg_tables.SCHEMA_PROBES,
)
# Registry vs. catalog for every data table; /health fails with the list when they differ.
CONSISTENCY_CHECKS = (pg_tables.verify_registry,)

CHECK_DB_HINT = "Run `python scripts/check_db.py` to see every discrepancy; repair the table or the registry by hand."

# Make the application's own log lines (database status, request errors)
# visible under uvicorn, which configures only its own loggers. No-op when the
# host process already configured logging.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    assert settings.database_url  # guaranteed by Settings.__post_init__
    database = Database(
        settings.database_url,
        connect_timeout=settings.database_timeout,
        schema_probes=SCHEMA_PROBES,
        consistency_checks=CONSISTENCY_CHECKS,
    )
    database.open()
    app.state.database = database
    # Report problems loudly, but keep serving: every request will return a
    # clear database error until the problem is fixed.
    database.log_startup_status()
    try:
        yield
    finally:
        database.close()


def _database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    status, detail = describe_database_error(exc)
    logger.error(
        "%s %s -> %s %s: %s", request.method, request.url.path, status, detail["message"], detail["error"]
    )
    return JSONResponse(status_code=status, content={"detail": detail})


def _definition_conflict_handler(request: Request, exc: DefinitionConflictError) -> JSONResponse:
    detail = {
        "message": "The table's data does not allow this change",
        "table": exc.table_id,
        "column": exc.column_id,
        "error": str(exc),
        "sqlstate": exc.sqlstate,
        "hint": exc.hint,
    }
    logger.warning("%s %s -> 409 %s: %s", request.method, request.url.path, exc.column_id, exc)
    return JSONResponse(status_code=409, content={"detail": detail})


def _row_constraint_handler(request: Request, exc: RowConstraintError) -> JSONResponse:
    detail = {
        "message": "The row was refused by a table constraint",
        "table": exc.table_ref,
        "column": exc.column,
        "constraint": exc.constraint,
        "error": str(exc),
        "sqlstate": exc.sqlstate,
        "hint": exc.hint,
    }
    logger.warning("%s %s -> 409 %s: %s", request.method, request.url.path, exc.constraint or exc.column, exc)
    return JSONResponse(status_code=409, content={"detail": detail})


def _schema_mismatch_handler(request: Request, exc: TableSchemaMismatchError) -> JSONResponse:
    detail = {
        "message": "Data table does not match its registry",
        "table": exc.table_id,
        "column": exc.column_id,
        "error": str(exc),
        "hint": CHECK_DB_HINT,
    }
    logger.error("%s %s -> 500 table %s: %s", request.method, request.url.path, exc.table_id, exc)
    return JSONResponse(status_code=500, content={"detail": detail})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. ``settings`` defaults to the environment."""
    settings = settings or Settings.from_env()

    app = FastAPI(title="Form Builder API", version="0.4.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for error_type in DATABASE_ERRORS:
        app.add_exception_handler(error_type, _database_error_handler)
    app.add_exception_handler(DefinitionConflictError, _definition_conflict_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RowConstraintError, _row_constraint_handler)  # type: ignore[arg-type]
    app.add_exception_handler(TableSchemaMismatchError, _schema_mismatch_handler)  # type: ignore[arg-type]

    app.mount("/images/files", StaticFiles(directory=get_image_dir()), name="images")
    # A small dependency-free console over every endpoint (backend/ui).
    app.mount("/ui", StaticFiles(directory=BACKEND_DIR / "ui", html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.get("/health", tags=["system"])
    def health(request: Request) -> JSONResponse:
        """``200`` when the API can serve requests, ``503`` with the database
        error otherwise (unreachable server, pending migrations)."""
        status = request.app.state.database.check()
        body: dict[str, Any] = {"status": "ok" if status.ok else "error", "database": status.as_dict()}
        return JSONResponse(status_code=200 if status.ok else 503, content=body)

    app.include_router(images.router)
    app.include_router(forms.router)
    app.include_router(submissions.router)
    app.include_router(option_lists.router)
    app.include_router(tables.router)
    app.include_router(table_rows.router)
    app.include_router(sql.router)

    return app


app = create_app()
