from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Project, User
from app.db.session import get_db
from app.schemas.job import (
    JobCreateRequest,
    JobResponse,
)
from app.services.job_queue import (
    cancel_queued_job,
    create_job,
    get_job,
    get_project_jobs,
)


router = APIRouter(tags=["Background Jobs"])



def get_owned_project(
    db: Session,
    project_id: int,
    user_id: int,
    lock: bool = False,
) -> Project:
    statement = select(Project).where(
        Project.id == project_id,
        Project.user_id == user_id,
    )

    if lock:
        statement = statement.with_for_update()

    project = db.scalar(statement)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found.",
        )

    return project



@router.post(
    "/projects/{project_id}/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_project_job(
    project_id: int,
    payload: JobCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = get_owned_project(
        db,
        project_id,
        current_user.id,
        lock=True,
    )

    if payload.job_type == "summary":
        if project.status not in ("pending", "failed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Project is not ready for "
                    "summary generation."
                ),
            )

    elif payload.job_type == "audio":
        if (
            project.status != "script_ready"
            or not project.script
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Project script must be ready "
                    "before audio generation."
                ),
            )

    try:
        job = create_job(
            db,
            project_id,
            payload.job_type,
        )
        db.commit()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "An active background job already "
                "exists for this project."
            ),
        ) from None

    return JobResponse.model_validate(job)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
)
def read_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_job(
        db,
        job_id,
        current_user.id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found.",
        )

    return JobResponse.model_validate(job)


@router.get(
    "/projects/{project_id}/jobs",
    response_model=list[JobResponse],
)
def list_project_jobs(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_project(
        db,
        project_id,
        current_user.id,
    )

    jobs = get_project_jobs(
        db,
        project_id,
    )

    return [
        JobResponse.model_validate(job)
        for job in jobs
    ]


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
)
def cancel_project_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_job(
        db,
        job_id,
        current_user.id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found.",
        )

    if job.status != "queued":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only queued jobs can be cancelled."
            ),
        )

    cancelled = cancel_queued_job(
        db,
        job_id,
        current_user.id,
    )

    if not cancelled:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Job is no longer queued.",
        )

    db.commit()

    updated = get_job(
        db,
        job_id,
        current_user.id,
    )

    return JobResponse.model_validate(updated)
