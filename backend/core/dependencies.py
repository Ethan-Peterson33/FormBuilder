"""FastAPI dependency providers.

This is the single place where routers get their repositories. Every
repository works on the PostgreSQL pool opened by the lifespan
(``app.state.database``). Tests override these with ``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import Request

from core.config import Settings
from core.database import Database
from repositories.base import (
    DataTableRepository,
    FormRepository,
    OptionListRepository,
    SubmissionRepository,
    TableRowRepository,
)
from repositories.pg_forms import PgFormRepository
from repositories.pg_option_lists import PgOptionListRepository
from repositories.pg_sql import PgSqlRepository
from repositories.pg_submissions import PgSubmissionRepository
from repositories.pg_table_rows import PgTableRowRepository
from repositories.pg_tables import PgDataTableRepository


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    """The pool opened by the lifespan."""
    return request.app.state.database


def get_form_repository(request: Request) -> FormRepository:
    return PgFormRepository(get_database(request))


def get_submission_repository(request: Request) -> SubmissionRepository:
    return PgSubmissionRepository(get_database(request))


def get_option_list_repository(request: Request) -> OptionListRepository:
    return PgOptionListRepository(get_database(request))


def get_data_table_repository(request: Request) -> DataTableRepository:
    return PgDataTableRepository(get_database(request))


def get_table_row_repository(request: Request) -> TableRowRepository:
    return PgTableRowRepository(get_database(request))


def get_sql_repository(request: Request) -> PgSqlRepository:
    return PgSqlRepository(get_database(request))
