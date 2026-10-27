from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import (
    Chapter,
    Document,
    ExtractionJob,
    User,
)
from app.db.session import get_db
from app.schemas.chapter import (
    ChapterDetailResponse,
    ChapterDetectionResponse,
    ChapterResponse,
)
from app.services.chapter_detection import (
    ChapterDetectionError,
    detect_chapters,
)


router = APIRouter(
    prefix="/documents",
    tags=["Chapters"],
)

logger = get_logger("chapters")


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


@router.post(
    "/{document_id}/chapters/detect",
    response_model=ChapterDetectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def detect_document_chapters(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_document(
        document_id,
        current_user.id,
        db,
    )

    # Lock the extraction record for the duration
    # of chapter generation and database insertion.
    job = db.scalar(
        select(ExtractionJob)
        .where(
            ExtractionJob.document_id == document_id
        )
        .with_for_update()
    )

    if job is None:
        raise HTTPException(
            status_code=409,
            detail="Extract the document first.",
        )

    if job.status != "completed" or not job.cleaned_text:
        raise HTTPException(
            status_code=409,
            detail=(
                "Document extraction must be "
                "completed before chapter detection."
            ),
        )

    existing_chapter = db.scalar(
        select(Chapter.id).where(
            Chapter.extraction_job_id == job.id
        ).limit(1)
    )

    if existing_chapter is not None:
        raise HTTPException(
            status_code=409,
            detail="Chapters have already been detected.",
        )

    try:
        detected = detect_chapters(job.cleaned_text)
    except ChapterDetectionError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from None

    chapter_records = [
        Chapter(
            extraction_job_id=job.id,
            chapter_number=item.chapter_number,
            title=item.title,
            content=item.content,
            start_position=item.start_position,
            end_position=item.end_position,
        )
        for item in detected
    ]

    db.add_all(chapter_records)

    job.chapters_detected = {
        "status": "completed",
        "method": "rule_based_v1",
        "total_chapters": len(chapter_records),
    }

    try:
        db.commit()
        for chapter in chapter_records:
            db.refresh(chapter)
    except Exception:
        db.rollback()
        logger.exception(
            "Could not save chapters for document_id=%s",
            document_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not save detected chapters.",
        ) from None

    logger.info(
        "Detected %s chapters for document_id=%s",
        len(chapter_records),
        document_id,
    )

    return ChapterDetectionResponse(
        document_id=document_id,
        extraction_job_id=job.id,
        total_chapters=len(chapter_records),
        chapters=[
            ChapterResponse.model_validate(chapter)
            for chapter in chapter_records
        ],
    )


@router.get(
    "/{document_id}/chapters",
    response_model=list[ChapterResponse],
)
def list_document_chapters(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_document(
        document_id,
        current_user.id,
        db,
    )

    chapters = db.scalars(
        select(Chapter)
        .join(
            ExtractionJob,
            Chapter.extraction_job_id == ExtractionJob.id,
        )
        .where(
            ExtractionJob.document_id == document_id
        )
        .order_by(
            Chapter.chapter_number,
            Chapter.id,
        )
    ).all()

    return chapters


@router.get(
    "/{document_id}/chapters/{chapter_id}",
    response_model=ChapterDetailResponse,
)
def get_document_chapter(
    document_id: int,
    chapter_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_document(
        document_id,
        current_user.id,
        db,
    )

    chapter = db.scalar(
        select(Chapter)
        .join(
            ExtractionJob,
            Chapter.extraction_job_id == ExtractionJob.id,
        )
        .where(
            ExtractionJob.document_id == document_id,
            Chapter.id == chapter_id,
        )
    )

    if chapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chapter not found.",
        )

    return chapter
