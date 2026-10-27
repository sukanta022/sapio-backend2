from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import get_logger

from app.db.models import (
    Chapter,
    ExtractionJob,
    Language,
    ProcessingLog,
    Project,
    ProjectChapter,
    SummaryType,
    User,
)

from app.db.session import get_db

from app.schemas.generation import (
    GenerationResponse,
    ScriptResponse,
)

from app.services.gemini_summarizer import (
    SUPPORTED_LANGUAGES,
    SourceSection,
    SummarizationError,
    generate_script,
)

from app.services.job_queue import (
    assert_no_active_job,
)


router = APIRouter(
    prefix="/projects",
    tags=["AI Generation"],
)

logger = get_logger("generation")

CHUNK_LIMIT_ERROR = (
    "Document exceeds the configured "
    "Gemini processing limit."
)


def get_owned_project(
    project_id: int,
    user_id: int,
    db: Session,
    lock: bool = False,
) -> Project:
    """
    Retrieve a project owned by the current user.

    When lock=True, PostgreSQL locks the project row
    until the current transaction is committed or
    rolled back.
    """

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


def get_source_sections(
    project: Project,
    summary_type: str,
    db: Session,
) -> list[SourceSection]:
    """
    Retrieve the extracted source text or project chapters.

    This helper is shared by synchronous generation
    and the Phase 12 background worker.
    """

    extraction = db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.document_id
            == project.document_id
        )
    )

    if (
        extraction is None
        or extraction.status != "completed"
        or not extraction.cleaned_text
    ):
        raise HTTPException(
            status_code=409,
            detail="Document extraction is not ready.",
        )

    if summary_type != "Chapter Breakdown":
        return [
            SourceSection(
                title="Full Document",
                text=extraction.cleaned_text,
            )
        ]

    rows = db.execute(
        select(ProjectChapter, Chapter)
        .join(
            Chapter,
            ProjectChapter.chapter_id == Chapter.id,
        )
        .where(
            ProjectChapter.project_id == project.id,
            Chapter.extraction_job_id == extraction.id,
        )
        .order_by(
            ProjectChapter.chapter_order,
            ProjectChapter.id,
        )
    ).all()

    if not rows:
        raise HTTPException(
            status_code=409,
            detail="Project chapters are not available.",
        )

    sections = [
        SourceSection(
            title=association.chapter_title,
            text=chapter.content or "",
        )
        for association, chapter in rows
        if chapter.content and chapter.content.strip()
    ]

    if not sections:
        raise HTTPException(
            status_code=409,
            detail="No readable project chapters exist.",
        )

    return sections


@router.post(
    "/{project_id}/generate",
    response_model=GenerationResponse,
)
def generate_project_summary(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate a narration script synchronously.

    Phase 12 protection:
    Reject generation when a background job for
    the same project is queued or running.
    """

    # IMPORTANT:
    # Lock the project row before checking for
    # active background jobs or changing status.
    project = get_owned_project(
        project_id,
        current_user.id,
        db,
        lock=True,
    )

    # PHASE 12:
    # Prevent this synchronous endpoint from
    # conflicting with queued/running jobs.
    #
    # Job submission must also obtain a FOR UPDATE
    # lock on the same project row.
    assert_no_active_job(
        db,
        project.id,
    )

    if project.status not in ("pending", "failed"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Project is not available for generation."
            ),
        )

    summary_type = db.get(
        SummaryType,
        project.summary_type_id,
    )

    language = db.get(
        Language,
        project.language_id,
    )

    if summary_type is None or language is None:
        raise HTTPException(
            status_code=409,
            detail="Project configuration is invalid.",
        )

    language_code = language.code.lower()

    if language_code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail="Unsupported output language.",
        )

    settings = get_settings()

    if (
        settings.GEMINI_API_KEY is None
        or not settings.GEMINI_API_KEY
        .get_secret_value()
        .strip()
    ):
        raise HTTPException(
            status_code=503,
            detail="Gemini API key is not configured.",
        )

    sections = get_source_sections(
        project,
        summary_type.name,
        db,
    )

    try:
        project.status = "generating"
        project.script = None

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="summarization",
                status="processing",
                message=(
                    "Generation started for "
                    f"language={language_code}."
                ),
            )
        )

        # Commit the generating state.
        #
        # This also releases the project's
        # PostgreSQL row lock.
        db.commit()

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Could not start generation.",
        ) from None

    try:
        # Preserve the existing two-argument call
        # for English and Phase 9 compatibility.
        if language_code == "en":
            result = generate_script(
                sections,
                summary_type.name,
            )
        else:
            result = generate_script(
                sections,
                summary_type.name,
                language_code=language_code,
            )

    except SummarizationError as exc:
        db.rollback()

        error_message = str(exc)

        is_chunk_limit_error = (
            error_message == CHUNK_LIMIT_ERROR
        )

        project.status = "failed"

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="summarization",
                status="failed",
                message=(
                    CHUNK_LIMIT_ERROR
                    if is_chunk_limit_error
                    else "Summary generation failed."
                ),
            )
        )

        db.commit()

        logger.warning(
            "Summarization failed | project_id=%s | "
            "language=%s | chunk_limit=%s",
            project.id,
            language_code,
            is_chunk_limit_error,
        )

        if is_chunk_limit_error:
            raise HTTPException(
                status_code=422,
                detail=CHUNK_LIMIT_ERROR,
            ) from None

        raise HTTPException(
            status_code=502,
            detail=(
                "Summary generation failed. "
                "Check Gemini configuration, "
                "quota, and service availability."
            ),
        ) from None

    try:
        project.script = result.script
        project.status = "script_ready"

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="summarization",
                status="completed",
                message=(
                    f"Processed {result.chunks_processed} "
                    f"chunks in language={language_code}."
                ),
            )
        )

        db.commit()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Could not save script for project_id=%s",
            project.id,
        )

        raise HTTPException(
            status_code=500,
            detail="Could not save generated script.",
        ) from None

    return GenerationResponse(
        project_id=project.id,
        status="script_ready",
        summary_type=summary_type.name,
        language=language_code,
        chunks_processed=result.chunks_processed,
        script_length=len(result.script),
    )


@router.get(
    "/{project_id}/script",
    response_model=ScriptResponse,
)
def get_project_script(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retrieve a generated narration script.

    This endpoint works with scripts generated
    either synchronously or by the Phase 12 worker.
    """

    project = get_owned_project(
        project_id,
        current_user.id,
        db,
    )

    if not project.script:
        raise HTTPException(
            status_code=409,
            detail="Project script is not ready.",
        )

    language = db.get(
        Language,
        project.language_id,
    )

    if (
        language is None
        or language.code.lower()
        not in SUPPORTED_LANGUAGES
    ):
        raise HTTPException(
            status_code=409,
            detail="Project language is not supported.",
        )

    return ScriptResponse(
        project_id=project.id,
        status=project.status,
        language=language.code.lower(),
        script=project.script,
        character_count=len(project.script),
    )
