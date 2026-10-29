"""Data table definition endpoints.

* ``GET    /tables``            - every table, creation order
* ``GET    /tables/{id}``       - one definition
* ``POST   /tables``            - register + ``CREATE TABLE``
* ``PUT    /tables/{id}``       - desired definition -> ``ALTER TABLE`` steps
* ``DELETE /tables/{id}``       - ``DROP TABLE`` + unregister
* ``GET    /tables/{id}/check`` - registry vs. catalog for this table

Error responses (see ``main.py`` for the handlers of the last two):

| status | when | body ``detail`` |
| --- | --- | --- |
| 404 | unknown table | ``"Table not found"`` |
| 409 | id or name taken | text |
| 409 | the table's data refuses a change | ``{message, table, column, error, sqlstate, hint}`` |
| 422 | structure or rule problems | list of ``{type, loc, msg}`` (like forms) |
| 500 | physical table differs from the registry | ``{message, table, column, error, hint}`` |
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_data_table_repository
from models.tables import TABLE_SERVER_MANAGED_KEYS, DataTable, DataTableCreate, DataTableUpdate
from repositories.base import DataTableRepository
from services.ids import slugify, unique_table_id
from services.table_rules import TableRuleError, validate_table_rules

router = APIRouter(prefix="/tables", tags=["tables"])

# Retries when concurrent creates with the same name race for a generated slug.
ID_ALLOCATION_ATTEMPTS = 10

# PostgreSQL identifiers are limited to 63 bytes (the slug becomes the table name).
MAX_TABLE_ID_LENGTH = 63


def _enforce_rules(payload: DataTableCreate | DataTableUpdate) -> None:
    try:
        validate_table_rules(payload)
    except TableRuleError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"type": "value_error", "loc": ["body", "columns"], "msg": problem} for problem in exc.problems],
        ) from exc


def _get_or_404(repo: DataTableRepository, table_id: str) -> DataTable:
    table = repo.get_table(table_id)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return table


def _ensure_name_free(repo: DataTableRepository, name: str, exclude_id: str | None = None) -> None:
    existing = repo.find_by_name(name)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"A table named '{existing.name}' already exists"
        )


@router.get("", response_model=list[DataTable], response_model_exclude_none=True)
def list_tables(repo: DataTableRepository = Depends(get_data_table_repository)) -> list[DataTable]:
    return repo.list_tables()


@router.get("/{table_id}", response_model=DataTable, response_model_exclude_none=True)
def get_table(table_id: str, repo: DataTableRepository = Depends(get_data_table_repository)) -> DataTable:
    return _get_or_404(repo, table_id)


@router.post("", response_model=DataTable, response_model_exclude_none=True, status_code=status.HTTP_201_CREATED)
def create_table(
    payload: DataTableCreate, repo: DataTableRepository = Depends(get_data_table_repository)
) -> DataTable:
    _enforce_rules(payload)
    _ensure_name_free(repo, payload.name)
    body = payload.model_dump(exclude=TABLE_SERVER_MANAGED_KEYS)

    requested_id = slugify(payload.id or "")[:MAX_TABLE_ID_LENGTH]
    if requested_id:
        if repo.exists(requested_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A table with this ID already exists")
        try:
            return repo.create_table(DataTable(id=requested_id, **body))
        except ValueError as exc:  # lost a race for the id or the name
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Generated id: a clash on the id is our race (retry with the next slug);
    # a clash on the name is a real conflict.
    for _ in range(ID_ALLOCATION_ATTEMPTS):
        table_id = unique_table_id(payload.name, repo.exists)[:MAX_TABLE_ID_LENGTH]
        try:
            return repo.create_table(DataTable(id=table_id, **body))
        except ValueError as exc:
            if "named" in str(exc):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            continue
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Could not allocate a unique table id (too many concurrent creates); please retry",
    )


@router.put("/{table_id}", response_model=DataTable, response_model_exclude_none=True)
def update_table(
    table_id: str, payload: DataTableUpdate, repo: DataTableRepository = Depends(get_data_table_repository)
) -> DataTable:
    existing = _get_or_404(repo, table_id)
    _enforce_rules(payload)
    _ensure_name_free(repo, payload.name, exclude_id=existing.id)
    table = DataTable(id=existing.id, **payload.model_dump(exclude=TABLE_SERVER_MANAGED_KEYS))
    try:
        return repo.update_table(table)
    except KeyError as exc:  # dropped between the lookup and the write
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # DefinitionConflictError / TableSchemaMismatchError: handled app-wide (main.py)


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_table(table_id: str, repo: DataTableRepository = Depends(get_data_table_repository)) -> None:
    if not repo.delete_table(table_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")


@router.get("/{table_id}/check")
def check_table(table_id: str, repo: DataTableRepository = Depends(get_data_table_repository)) -> dict:
    """Does the physical table match the registered definition? ``problems``
    is empty when it does."""
    _get_or_404(repo, table_id)
    problems = repo.verify(table_id)
    return {"tableId": table_id, "ok": not problems, "problems": problems}
