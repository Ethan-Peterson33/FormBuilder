"""Reusable option list endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_option_list_repository
from models.forms import SERVER_MANAGED_KEYS
from models.option_lists import OptionList, OptionListCreate, OptionListUpdate
from repositories.base import OptionListRepository
from services.ids import slugify, unique_slug_id, utc_now_iso

router = APIRouter(prefix="/option-lists", tags=["option-lists"])

ID_ALLOCATION_ATTEMPTS = 10


def _get_or_404(repo: OptionListRepository, list_id: str) -> OptionList:
    option_list = repo.get_option_list(list_id)
    if option_list is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option list not found")
    return option_list


def _ensure_name_free(repo: OptionListRepository, name: str, exclude_id: str | None = None) -> None:
    existing = repo.find_by_name(name)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An option list named '{existing.name}' already exists",
        )


@router.get("", response_model=list[OptionList], response_model_exclude_none=True)
def list_option_lists(repo: OptionListRepository = Depends(get_option_list_repository)) -> list[OptionList]:
    return repo.list_option_lists()


@router.get("/{list_id}", response_model=OptionList, response_model_exclude_none=True)
def get_option_list(list_id: str, repo: OptionListRepository = Depends(get_option_list_repository)) -> OptionList:
    return _get_or_404(repo, list_id)


@router.post(
    "",
    response_model=OptionList,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_option_list(
    payload: OptionListCreate,
    repo: OptionListRepository = Depends(get_option_list_repository),
) -> OptionList:
    _ensure_name_free(repo, payload.name)
    body = payload.model_dump(exclude=SERVER_MANAGED_KEYS)

    def build(list_id: str) -> OptionList:
        now = utc_now_iso()
        return OptionList(id=list_id, created_at=now, updated_at=now, **body)

    requested_id = slugify(payload.id or "")
    if requested_id:
        if repo.exists(requested_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An option list with this ID already exists",
            )
        try:
            return repo.create_option_list(build(requested_id))
        except ValueError as exc:  # lost a race for the id or the name
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Generated id: retry on an id clash (see routers/forms.py). A clash on the
    # *name* is a real conflict and is reported as 409.
    for _ in range(ID_ALLOCATION_ATTEMPTS):
        try:
            return repo.create_option_list(build(unique_slug_id(payload.name, repo.exists, fallback="options")))
        except ValueError as exc:
            if "named" in str(exc):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            continue
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Could not allocate a unique option list id (too many concurrent creates); please retry",
    )


@router.put("/{list_id}", response_model=OptionList, response_model_exclude_none=True)
def update_option_list(
    list_id: str,
    payload: OptionListUpdate,
    repo: OptionListRepository = Depends(get_option_list_repository),
) -> OptionList:
    existing = _get_or_404(repo, list_id)
    _ensure_name_free(repo, payload.name, exclude_id=existing.id)
    option_list = OptionList(
        id=existing.id,
        created_at=existing.created_at,
        updated_at=utc_now_iso(),
        **payload.model_dump(exclude=SERVER_MANAGED_KEYS),
    )
    try:
        return repo.update_option_list(option_list)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option list not found") from exc


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_option_list(list_id: str, repo: OptionListRepository = Depends(get_option_list_repository)) -> None:
    if not repo.delete_option_list(list_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option list not found")
