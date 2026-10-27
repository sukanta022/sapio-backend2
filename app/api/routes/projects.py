from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import (
    AudioFormat,
    Chapter,
    Document,
    ExtractionJob,
    Language,
    Narrator,
    Project,
    ProjectChapter,
    SummaryType,
    User,
)
from app.db.session import get_db
from app.schemas.project import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectOptionsResponse,
    ProjectResponse,
)


router = APIRouter(
    prefix="/projects",
    tags=["Projects"],
)


@router.get(
    "/options",
    response_model=ProjectOptionsResponse,
)
def get_project_options(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ProjectOptionsResponse(
        summary_types=db.scalars(
            select(SummaryType).order_by(SummaryType.id)
        ).all(),
        narrators=db.scalars(
            select(Narrator).order_by(Narrator.id)
        ).all(),
        languages=db.scalars(
            select(Language).order_by(Language.id)
        ).all(),
        audio_formats=db.scalars(
            select(AudioFormat).order_by(AudioFormat.id)
        ).all(),
    )


@router.post(
    "",
    response_model=ProjectDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.scalar(
        select(Document).where(
            Document.id == payload.document_id,
            Document.user_id == current_user.id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    extraction = db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.document_id == document.id
        )
    )

    if (
        extraction is None
        or extraction.status != "completed"
        or not extraction.cleaned_text
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Complete document text extraction "
                "before creating a project."
            ),
        )

    summary_type = db.get(
        SummaryType,
        payload.summary_type_id,
    )
    narrator = db.get(
        Narrator,
        payload.narrator_id,
    )
    language = db.get(
        Language,
        payload.language_id,
    )
    audio_format = db.get(
        AudioFormat,
        payload.audio_format_id,
    )

    selected_options = {
        "summary_type_id": summary_type,
        "narrator_id": narrator,
        "language_id": language,
        "audio_format_id": audio_format,
    }

    for field_name, selected in selected_options.items():
        if selected is None:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {field_name}.",
            )

    chapter_records = []

    if summary_type.name == "Chapter Breakdown":
        chapter_records = db.scalars(
            select(Chapter)
            .where(
                Chapter.extraction_job_id == extraction.id
            )
            .order_by(
                Chapter.chapter_number,
                Chapter.id,
            )
        ).all()

        if not chapter_records:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Run chapter detection before "
                    "creating a Chapter Breakdown project."
                ),
            )

    project = Project(
        user_id=current_user.id,
        document_id=document.id,
        summary_type_id=summary_type.id,
        narrator_id=narrator.id,
        language_id=language.id,
        audio_format_id=audio_format.id,
        status="pending",
        script=None,
        completed_at=None,
        audio_file_path=None,
        total_duration=None,
    )

    try:
        db.add(project)
        db.flush()

        project_chapters = [
            ProjectChapter(
                project_id=project.id,
                chapter_id=chapter.id,
                chapter_order=position,
                chapter_title=(
                    chapter.title
                    or f"Section {position}"
                ),
            )
            for position, chapter in enumerate(
                chapter_records,
                start=1,
            )
        ]

        db.add_all(project_chapters)
        db.commit()
        db.refresh(project)

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not create project.",
        ) from None

    return ProjectDetailResponse(
        **ProjectResponse.model_validate(
            project
        ).model_dump(),
        chapters=project_chapters,
    )


@router.get(
    "",
    response_model=list[ProjectResponse],
)
def list_my_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Project)
        .where(Project.user_id == current_user.id)
        .order_by(
            Project.created_at.desc(),
            Project.id.desc(),
        )
    ).all()


@router.get(
    "/{project_id}",
    response_model=ProjectDetailResponse,
)
def get_project_details(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found.",
        )

    chapters = db.scalars(
        select(ProjectChapter)
        .where(
            ProjectChapter.project_id == project.id
        )
        .order_by(
            ProjectChapter.chapter_order,
            ProjectChapter.id,
        )
    ).all()

    return ProjectDetailResponse(
        **ProjectResponse.model_validate(
            project
        ).model_dump(),
        chapters=chapters,
    )
