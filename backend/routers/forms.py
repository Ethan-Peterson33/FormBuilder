"""Form configuration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_form_repository
from models.forms import SERVER_MANAGED_KEYS, FormConfig, FormConfigCreate, FormConfigUpdate
from repositories.base import FormRepository
from services.form_rules import FormRuleError, validate_form_rules
from services.ids import slugify, unique_form_id, utc_now_iso

router = APIRouter(prefix="/forms", tags=["forms"])

# Retries when concurrent creates with the same name race for a generated slug.
ID_ALLOCATION_ATTEMPTS = 10


def _enforce_rules(payload: FormConfigCreate | FormConfigUpdate) -> None:
    """Business rules are checked on write so invalid configs are never stored,
    while previously stored data always loads (see ``services.form_rules``)."""
    try:
        validate_form_rules(payload)
    except FormRuleError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {"type": "value_error", "loc": ["body", "fields"], "msg": problem}
                for problem in exc.problems
            ],
        ) from exc


def _get_or_404(repo: FormRepository, form_id: str) -> FormConfig:
    form = repo.get_form(form_id)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form


@router.get("", response_model=list[FormConfig], response_model_exclude_none=True)
def list_forms(repo: FormRepository = Depends(get_form_repository)) -> list[FormConfig]:
    return repo.list_forms()


@router.get("/{form_id}", response_model=FormConfig, response_model_exclude_none=True)
def get_form(form_id: str, repo: FormRepository = Depends(get_form_repository)) -> FormConfig:
    return _get_or_404(repo, form_id)


@router.post(
    "",
    response_model=FormConfig,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_form(
    payload: FormConfigCreate,
    repo: FormRepository = Depends(get_form_repository),
) -> FormConfig:
    _enforce_rules(payload)
    body = payload.model_dump(exclude=SERVER_MANAGED_KEYS)

    def build(form_id: str) -> FormConfig:
        now = utc_now_iso()
        return FormConfig(id=form_id, created_at=now, updated_at=now, **body)

    requested_id = slugify(payload.id or "")
    if requested_id:
        if repo.exists(requested_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A form with this ID already exists",
            )
        try:
            return repo.create_form(build(requested_id))
        except ValueError as exc:  # lost a race for the requested id
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Generated id: the slug is chosen before the insert, so two concurrent
    # requests with the same name can pick the same one. That is our race, not
    # the client's problem - take the next free slug and try again.
    for _ in range(ID_ALLOCATION_ATTEMPTS):
        try:
            return repo.create_form(build(unique_form_id(payload.name, repo.exists)))
        except ValueError:
            continue
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Could not allocate a unique form id (too many concurrent creates); please retry",
    )


@router.put("/{form_id}", response_model=FormConfig, response_model_exclude_none=True)
def update_form(
    form_id: str,
    payload: FormConfigUpdate,
    repo: FormRepository = Depends(get_form_repository),
) -> FormConfig:
    existing = _get_or_404(repo, form_id)
    _enforce_rules(payload)
    form = FormConfig(
        id=existing.id,
        created_at=existing.created_at,
        updated_at=utc_now_iso(),
        **payload.model_dump(exclude=SERVER_MANAGED_KEYS),
    )
    try:
        return repo.update_form(form)
    except KeyError as exc:  # deleted between the lookup and the write
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found") from exc


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_form(form_id: str, repo: FormRepository = Depends(get_form_repository)) -> None:
    if not repo.delete_form(form_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
