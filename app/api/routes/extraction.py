from pathlib import Path

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
from app.core.config import get_settings
from app.services.object_storage import cloud_enabled, document_key, ensure_local, ObjectStorageError
from app.core.logging import get_logger
from app.db.models import Document, ExtractionJob, User
from app.db.session import get_db
from app.schemas.extraction import (
    ExtractionResponse,
    ExtractionStatusResponse,
)
from app.services.text_extraction import (
    ExtractionError,
    extract_and_clean,
)


router = APIRouter(
    prefix="/documents",
    tags=["Text Extraction"],
)

logger = get_logger("extraction")


def get_owned_document(
    document_id: int,
    user_id: int,
    db: Session,
) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user_id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    return document


def resolve_document_path(
    relative_path: str,
) -> Path:
    storage_root = get_settings().STORAGE_ROOT.resolve()
    full_path = (storage_root / relative_path).resolve()

    if not full_path.is_relative_to(storage_root):
        raise HTTPException(
            status_code=400,
            detail="Invalid stored document path.",
        )

    if cloud_enabled():
        try:
            ensure_local(document_key(relative_path), full_path)
        except (ObjectStorageError, FileNotFoundError):
            raise HTTPException(404, "Stored document not found in cloud storage.") from None
    elif not full_path.is_file():
        raise HTTPException(404, "Stored document file not found.")

    return full_path


@router.post(
    "/{document_id}/extract",
    response_model=ExtractionResponse,
)
def process_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = get_owned_document(
        document_id,
        current_user.id,
        db,
    )

    existing_job = db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.document_id == document.id
        )
    )

    if existing_job is not None:
        if existing_job.status == "completed":
            raise HTTPException(
                status_code=409,
                detail="Document has already been extracted.",
            )

        if existing_job.status == "processing":
            raise HTTPException(
                status_code=409,
                detail="Document extraction is already running.",
            )

        # Reuse a previously failed extraction job.
        job = existing_job
    else:
        job = ExtractionJob(
            document_id=document.id,
            status="processing",
        )

        db.add(job)

    job.status = "processing"
    job.extracted_text = None
    job.cleaned_text = None
    job.chapters_detected = None

    document.status = "processing"

    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An extraction job already exists.",
        ) from None

    try:
        file_path = resolve_document_path(document.file_path)
        result = extract_and_clean(
            file_path,
            document.file_type,
        )

    except (ExtractionError, HTTPException) as exc:
        logger.warning(
            "Extraction failed for document_id=%s: %s",
            document.id,
            str(exc),
        )

        job.status = "failed"
        document.status = "failed"
        db.commit()

        raise HTTPException(
            status_code=422,
            detail=(exc.detail if isinstance(exc, HTTPException) else str(exc)),
        ) from None

    job.extracted_text = result.extracted_text
    job.cleaned_text = result.cleaned_text
    job.status = "completed"

    document.status = "extracted"

    db.commit()
    db.refresh(job)

    logger.info(
        "Extraction completed for document_id=%s",
        document.id,
    )

    return ExtractionResponse(
        id=job.id,
        document_id=document.id,
        status=job.status,
        character_count=result.character_count,
        created_at=job.created_at,
    )


@router.get(
    "/{document_id}/extraction",
    response_model=ExtractionStatusResponse,
)
def get_extraction_status(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = get_owned_document(
        document_id,
        current_user.id,
        db,
    )

    job = db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.document_id == document.id
        )
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Extraction job not found.",
        )

    return ExtractionStatusResponse(
        id=job.id,
        document_id=job.document_id,
        status=job.status,
        has_extracted_text=bool(job.extracted_text),
        has_cleaned_text=bool(job.cleaned_text),
        character_count=len(job.cleaned_text or ""),
    )
