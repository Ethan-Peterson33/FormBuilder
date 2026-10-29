"""Submission endpoints.

* ``POST /forms/{form_id}/submissions`` - save answers for a form
* ``GET  /forms/{form_id}/submissions`` - list submissions for a form
* ``GET  /submissions``                 - list all submissions (optionally filtered)
* ``GET  /submissions/{submission_id}`` - fetch one submission
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.dependencies import get_form_repository, get_submission_repository
from models.submissions import Submission, SubmissionCreate, SubmissionList
from repositories.base import FormRepository, SubmissionRepository
from services.submissions import SubmissionValidationError, build_submission

router = APIRouter(tags=["submissions"])


def _client_details(request: Request) -> tuple[str | None, str | None]:
    """User agent / IP, preferring values forwarded by the Next.js proxy."""
    user_agent = request.headers.get("x-client-user-agent") or request.headers.get("user-agent")
    ip_address = request.headers.get("x-client-ip")
    if not ip_address:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
    return user_agent, ip_address


@router.post(
    "/forms/{form_id}/submissions",
    response_model=Submission,
    status_code=status.HTTP_201_CREATED,
)
def create_submission(
    form_id: str,
    payload: SubmissionCreate,
    request: Request,
    forms: FormRepository = Depends(get_form_repository),
    submissions: SubmissionRepository = Depends(get_submission_repository),
) -> Submission:
    form = forms.get_form(form_id)
    if form is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")

    user_agent, ip_address = _client_details(request)
    try:
        submission = build_submission(form, payload, user_agent=user_agent, ip_address=ip_address)
    except SubmissionValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Submission failed validation", "errors": exc.errors},
        ) from exc

    try:
        return submissions.create_submission(submission)
    except KeyError as exc:  # form deleted between the lookup and the insert
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found") from exc


@router.get("/forms/{form_id}/submissions", response_model=SubmissionList)
def list_form_submissions(
    form_id: str,
    limit: int | None = Query(default=None, ge=0, le=1000),
    offset: int = Query(default=0, ge=0),
    forms: FormRepository = Depends(get_form_repository),
    submissions: SubmissionRepository = Depends(get_submission_repository),
) -> SubmissionList:
    if not forms.exists(form_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return SubmissionList(
        total=submissions.count_submissions(form_id),
        items=submissions.list_submissions(form_id=form_id, limit=limit, offset=offset),
    )


@router.get("/submissions", response_model=SubmissionList)
def list_submissions(
    form_id: str | None = Query(default=None, alias="formId"),
    limit: int | None = Query(default=None, ge=0, le=1000),
    offset: int = Query(default=0, ge=0),
    submissions: SubmissionRepository = Depends(get_submission_repository),
) -> SubmissionList:
    return SubmissionList(
        total=submissions.count_submissions(form_id),
        items=submissions.list_submissions(form_id=form_id, limit=limit, offset=offset),
    )


@router.get("/submissions/{submission_id}", response_model=Submission)
def get_submission(
    submission_id: str,
    submissions: SubmissionRepository = Depends(get_submission_repository),
) -> Submission:
    submission = submissions.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    return submission
