from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.audio import (
    get_audio_record,
    owned_project,
)
from app.db.models import (
    Document,
    ListeningProgress,
    Project,
    User,
)
from app.db.session import get_db
from app.schemas.playback import (
    ListeningHistoryItem,
    ListeningHistoryResponse,
    PlaybackResponse,
    PlaybackUpdateRequest,
)
from app.services.audio_streaming import (
    make_audio_stream,
)
from app.services.tts_service import (
    TTSError,
    resolve_audio_path,
)


router = APIRouter(tags=["Playback"])


def get_playable_audio(
    project_id: int,
    user_id: int,
    db: Session,
) -> tuple[Project, object]:
    """
    Resolve a completed, owned project's audio.

    Uses the recorded project file path when possible
    to avoid selecting an unrelated older audio file.
    """
    project = owned_project(
        project_id,
        user_id,
        db,
    )

    if project.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Project audio is not ready.",
        )

    record = get_audio_record(
        project.id,
        db,
    )

    # Prefer the exact file linked to the project.
    if project.audio_file_path:
        from app.db.models import GeneratedFile

        matching = db.scalar(
            select(GeneratedFile)
            .where(
                GeneratedFile.project_id == project.id,
                GeneratedFile.file_path
                == project.audio_file_path,
                GeneratedFile.file_type.in_(["mp3", "wav"]),
            )
            .order_by(GeneratedFile.id.desc())
        )

        if matching is not None:
            record = matching

    return project, record


def require_audio_duration(project: Project) -> int:
    duration = project.total_duration

    if duration is None or duration <= 0:
        raise HTTPException(
            status_code=409,
            detail="Project audio duration is unavailable.",
        )

    return duration


def progress_response(
    project: Project,
    progress: ListeningProgress | None,
) -> PlaybackResponse:
    return PlaybackResponse(
        project_id=project.id,
        position_seconds=(
            progress.position_seconds
            if progress is not None
            else 0
        ),
        total_duration_seconds=project.total_duration or 0,
        is_completed=(
            progress.is_completed
            if progress is not None
            else False
        ),
        last_listened_at=(
            progress.last_listened_at
            if progress is not None
            else None
        ),
        completed_at=(
            progress.completed_at
            if progress is not None
            else None
        ),
    )


def save_progress(
    db: Session,
    user_id: int,
    project_id: int,
    position_seconds: int,
    is_completed: bool,
) -> ListeningProgress:
    """
    Atomic PostgreSQL UPSERT.

    Unique key: (user_id, project_id).
    """
    table = ListeningProgress.__table__

    values = {
        "user_id": user_id,
        "project_id": project_id,
        "position_seconds": position_seconds,
        "is_completed": is_completed,
        "last_listened_at": datetime.now().astimezone(),
        "completed_at": (
            datetime.now().astimezone()
            if is_completed
            else None
        ),
        "updated_at": datetime.now().astimezone(),
    }

    statement = insert(table).values(**values)

    statement = statement.on_conflict_do_update(
        constraint="uq_listening_progress_user_project",
        set_={
            "position_seconds": statement.excluded.position_seconds,
            "is_completed": statement.excluded.is_completed,
            "last_listened_at": statement.excluded.last_listened_at,
            "completed_at": statement.excluded.completed_at,
            "updated_at": statement.excluded.updated_at,
        },
    ).returning(table.c.id)

    progress_id = db.execute(
        statement
    ).scalar_one()

    db.flush()

    progress = db.get(
        ListeningProgress,
        progress_id,
        populate_existing=True,
    )

    return progress


@router.get(
    "/projects/{project_id}/audio/stream",
)
def stream_project_audio(
    project_id: int,
    range_header: str | None = Header(
        default=None,
        alias="Range",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Authenticated audio streaming with byte-range seeking.
    """
    project, record = get_playable_audio(
        project_id,
        current_user.id,
        db,
    )

    try:
        path = resolve_audio_path(
            record.file_path
        )
    except TTSError:
        raise HTTPException(
            status_code=404,
            detail="Stored audio file not found.",
        ) from None

    media_type = (
        "audio/mpeg"
        if record.file_type.lower() == "mp3"
        else "audio/wav"
    )

    return make_audio_stream(
        path=path,
        media_type=media_type,
        range_header=range_header,
    )


@router.get(
    "/projects/{project_id}/playback",
    response_model=PlaybackResponse,
)
def get_project_playback(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project, _ = get_playable_audio(
        project_id,
        current_user.id,
        db,
    )

    progress = db.scalar(
        select(ListeningProgress).where(
            ListeningProgress.user_id == current_user.id,
            ListeningProgress.project_id == project.id,
        )
    )

    return progress_response(
        project,
        progress,
    )


@router.put(
    "/projects/{project_id}/playback",
    response_model=PlaybackResponse,
)
def update_project_playback(
    project_id: int,
    payload: PlaybackUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project, _ = get_playable_audio(
        project_id,
        current_user.id,
        db,
    )

    duration = require_audio_duration(
        project
    )

    if payload.position_seconds > duration:
        raise HTTPException(
            status_code=422,
            detail=(
                "Playback position cannot exceed "
                "the audio duration."
            ),
        )

    completed = (
        payload.position_seconds == duration
    )

    progress = save_progress(
        db=db,
        user_id=current_user.id,
        project_id=project.id,
        position_seconds=payload.position_seconds,
        is_completed=completed,
    )

    response = progress_response(
        project,
        progress,
    )

    db.commit()

    return response


@router.post(
    "/projects/{project_id}/playback/complete",
    response_model=PlaybackResponse,
)
def complete_project_playback(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project, _ = get_playable_audio(
        project_id,
        current_user.id,
        db,
    )

    duration = require_audio_duration(
        project
    )

    progress = save_progress(
        db=db,
        user_id=current_user.id,
        project_id=project.id,
        position_seconds=duration,
        is_completed=True,
    )

    response = progress_response(
        project,
        progress,
    )

    db.commit()

    return response


@router.get(
    "/me/listening-history",
    response_model=ListeningHistoryResponse,
)
def get_listening_history(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(
            ListeningProgress,
            Project,
            Document.file_name,
        )
        .join(
            Project,
            ListeningProgress.project_id == Project.id,
        )
        .join(
            Document,
            Project.document_id == Document.id,
        )
        .where(
            ListeningProgress.user_id == current_user.id,
            Project.user_id == current_user.id,
        )
        .order_by(
            ListeningProgress.last_listened_at.desc(),
            ListeningProgress.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        ListeningHistoryItem(
            project_id=project.id,
            document_name=file_name,
            position_seconds=progress.position_seconds,
            total_duration_seconds=project.total_duration or 0,
            is_completed=progress.is_completed,
            last_listened_at=progress.last_listened_at,
            completed_at=progress.completed_at,
        )
        for progress, project, file_name in rows
    ]

    return ListeningHistoryResponse(
        items=items,
        limit=limit,
        offset=offset,
    )
