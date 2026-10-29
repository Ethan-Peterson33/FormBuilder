"""Row endpoints - for user tables and for any other allowed table.

``{ref}`` is a user table's slug (``work-orders``) or ``schema.table``
(``public.option_lists``); see ``services/table_catalog.py`` for what is
reachable. Cell types are the PostgreSQL column types.

* ``GET    /tables/{ref}/schema``        - the columns as the catalog describes them
* ``GET    /tables/{ref}/rows``          - page of rows; ``?limit&offset&sort=-col,col&filter=col:op:value&search=``
* ``POST   /tables/{ref}/rows/query``    - same, with a JSON ``RowQuery`` body
* ``GET    /tables/{ref}/rows/{rowId}``
* ``POST   /tables/{ref}/rows``          - ``{data}``
* ``PATCH  /tables/{ref}/rows/{rowId}``  - ``{data}`` partial; ``null`` clears
* ``DELETE /tables/{ref}/rows/{rowId}``

Errors:

| status | when | ``detail`` |
| --- | --- | --- |
| 404 | unknown table / row | ``"Table not found"`` / ``"Row not found"`` |
| 400 | the table has no single-column primary key (row lookup impossible) | text |
| 409 | a constraint or the column type refused the write | ``{message, table, column, constraint, error, sqlstate, hint}`` |
| 422 | a cell or query parameter is invalid | ``{message, errors: {cell-or-part: message}}`` |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from core.dependencies import get_table_row_repository
from models.tables import TableRow, TableRowCreate, TableRowList, TableRowUpdate
from repositories.base import TableRowRepository
from services.row_query import DEFAULT_LIMIT, MAX_LIMIT, RowQuery, parse_filter_param, parse_sort_param
from services.table_catalog import TableInfo, TableNotAllowedError
from services.table_rows import RowValidationError, coerce_key, validate_row

router = APIRouter(prefix="/tables/{ref}", tags=["table rows"])


def _table(ref: str, repo: TableRowRepository) -> TableInfo:
    try:
        table = repo.describe(ref)
    except TableNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return table


def _keyed(table: TableInfo, row_id: str) -> Any:
    if table.primary_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{table.ref}' has no single-column primary key, so rows cannot be addressed by id",
        )
    key = coerce_key(table, row_id)
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    return key


def _invalid(exc: RowValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": exc.message, "errors": exc.errors})


def _page(repo: TableRowRepository, table: TableInfo, query: RowQuery) -> TableRowList:
    try:
        items, total = repo.list_rows(table, query)
    except RowValidationError as exc:
        raise _invalid(exc) from exc
    return TableRowList(total=total, limit=query.limit, offset=query.offset, items=items)


@router.get("/schema")
def get_schema(ref: str, repo: TableRowRepository = Depends(get_table_row_repository)) -> JSONResponse:
    """The table as PostgreSQL describes it: columns with SQL type, kind,
    nullability, defaults, writability, primary key and option constraints."""
    return JSONResponse(content=_table(ref, repo).as_dict())


@router.get("/rows", response_model=TableRowList)
def list_rows(
    ref: str,
    limit: int = Query(default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    sort: str | None = Query(default=None, description="comma-separated columns, '-' prefix for descending"),
    filter: list[str] = Query(default=[], description="column:operator[:value], repeatable"),
    match: str = Query(default="all", pattern="^(all|any)$"),
    search: str | None = Query(default=None),
    repo: TableRowRepository = Depends(get_table_row_repository),
) -> TableRowList:
    table = _table(ref, repo)
    try:
        query = RowQuery(
            filters=[parse_filter_param(item) for item in filter],
            match=match,  # type: ignore[arg-type]
            sort=parse_sort_param(sort),
            search=search,
            limit=limit,
            offset=offset,
        )
    except RowValidationError as exc:
        raise _invalid(exc) from exc
    return _page(repo, table, query)


@router.post("/rows/query", response_model=TableRowList)
def query_rows(ref: str, query: RowQuery, repo: TableRowRepository = Depends(get_table_row_repository)) -> TableRowList:
    return _page(repo, _table(ref, repo), query)


@router.get("/rows/{row_id}", response_model=TableRow)
def get_row(ref: str, row_id: str, repo: TableRowRepository = Depends(get_table_row_repository)) -> TableRow:
    table = _table(ref, repo)
    row = repo.get_row(table, _keyed(table, row_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    return row


@router.post("/rows", response_model=TableRow, status_code=status.HTTP_201_CREATED)
def create_row(
    ref: str, payload: TableRowCreate, repo: TableRowRepository = Depends(get_table_row_repository)
) -> TableRow:
    table = _table(ref, repo)
    try:
        values = validate_row(table, payload.data, partial=False)
    except RowValidationError as exc:
        raise _invalid(exc) from exc
    return repo.create_row(table, values)  # RowConstraintError -> 409 (main.py)


@router.patch("/rows/{row_id}", response_model=TableRow)
def patch_row(
    ref: str, row_id: str, payload: TableRowUpdate, repo: TableRowRepository = Depends(get_table_row_repository)
) -> TableRow:
    table = _table(ref, repo)
    key = _keyed(table, row_id)
    try:
        changes = validate_row(table, payload.data, partial=True)
    except RowValidationError as exc:
        raise _invalid(exc) from exc
    row = repo.patch_row(table, key, changes)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    return row


@router.delete("/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(ref: str, row_id: str, repo: TableRowRepository = Depends(get_table_row_repository)) -> None:
    table = _table(ref, repo)
    if not repo.delete_row(table, _keyed(table, row_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
