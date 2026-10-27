import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.services.job_queue import assert_no_active_job
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import (
    AudioFormat,
    GeneratedFile,
    Language,
    Narrator,
    ProcessingLog,
    Project,
    User,
)
from app.db.session import get_db
from app.schemas.audio import (
    AudioMetadataResponse,
    AudioResponse,
)
from app.services.tts_service import (
    TTSError,
    TTSLimitError,
    generate_audio,
    get_voice,
    remove_audio_file,
    resolve_audio_path,
    split_tts_text,
)


router = APIRouter(
    prefix="/projects",
    tags=["Audio"],
)

logger = logging.getLogger("sapio.audio")


def owned_project(
    project_id: int,
    user_id: int,
    db: Session,
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


def get_audio_record(
    project_id: int,
    db: Session,
) -> GeneratedFile:
    record = db.scalar(
        select(GeneratedFile)
        .where(
            GeneratedFile.project_id == project_id,
            GeneratedFile.file_type.in_(["wav", "mp3"]),
        )
        .order_by(GeneratedFile.id.desc())
    )

    if record is None:
        raise HTTPException(
            status_code=409,
            detail="Project audio is not ready.",
        )

    return record


@router.post(
    "/{project_id}/audio/generate",
    response_model=AudioResponse,
)
def generate_project_audio(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = owned_project(
        project_id,
        current_user.id,
        db,
        lock=True,
    )
    assert_no_active_job(
        db,
        project.id,
    )

    if project.status != "script_ready" or not project.script:
        raise HTTPException(
            status_code=409,
            detail="Project script must be ready first.",
        )

    language = db.get(
        Language,
        project.language_id,
    )
    narrator = db.get(
        Narrator,
        project.narrator_id,
    )
    audio_format = db.get(
        AudioFormat,
        project.audio_format_id,
    )

    if any(
        value is None
        for value in (language, narrator, audio_format)
    ):
        raise HTTPException(
            status_code=409,
            detail="Invalid project audio configuration.",
        )

    settings = get_settings()

    try:
        get_voice(language.code, narrator.name)

        if audio_format.name.upper() not in {"WAV", "MP3"}:
            raise TTSError(
                "Unsupported audio format."
            )

        chunks = split_tts_text(
            project.script,
            settings.PIPER_MAX_CHARS,
        )

        if len(chunks) > settings.PIPER_MAX_CHUNKS:
            raise TTSLimitError(
                "Script exceeds the configured "
                "Piper processing limit."
            )

    except (TTSError, TTSLimitError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from None

    project.status = "audio_generating"

    db.add(
        ProcessingLog(
            project_id=project.id,
            stage="tts",
            status="processing",
            message="Local Piper synthesis started.",
        )
    )
    db.commit()

    result = None

    try:
        result = generate_audio(
            script=project.script,
            user_id=project.user_id,
            project_id=project.id,
            language_code=language.code,
            narrator_name=narrator.name,
            audio_format=audio_format.name,
        )

        project.audio_file_path = result.relative_path
        project.total_duration = result.duration_seconds
        project.status = "completed"

        db.add(
            GeneratedFile(
                project_id=project.id,
                file_type=audio_format.name.lower(),
                file_path=result.relative_path,
                file_size=result.file_size,
            )
        )

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="tts",
                status="completed",
                message=(
                    f"Local audio completed in "
                    f"{result.chunks_processed} chunks."
                ),
            )
        )
        db.commit()

    except (TTSError, TTSLimitError, SQLAlchemyError) as exc:
        db.rollback()

        if result is not None:
            try:
                remove_audio_file(result.relative_path)
            except (TTSError, OSError):
                logger.warning(
                    "Could not clean audio for project %s",
                    project_id,
                )

        project.status = "audio_failed"

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="tts",
                status="failed",
                message="Local audio generation failed.",
            )
        )
        db.commit()

        logger.warning(
            "Piper failed | project_id=%s | type=%s",
            project_id,
            type(exc).__name__,
        )

        if isinstance(exc, TTSLimitError):
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from None

        raise HTTPException(
            status_code=500,
            detail=(
                "Local audio generation failed. "
                "Check Piper models and FFmpeg."
            ),
        ) from None

    return AudioResponse(
        project_id=project.id,
        status=project.status,
        language=language.code,
        narrator=narrator.name,
        audio_format=audio_format.name.upper(),
        duration_seconds=result.duration_seconds,
        file_size=result.file_size,
        chunks_processed=result.chunks_processed,
    )


@router.get(
    "/{project_id}/audio",
    response_model=AudioMetadataResponse,
)
def get_project_audio(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = owned_project(
        project_id,
        current_user.id,
        db,
    )

    if project.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Project audio is not ready.",
        )

    record = get_audio_record(project.id, db)

    return AudioMetadataResponse(
        project_id=project.id,
        status=project.status,
        audio_format=record.file_type.upper(),
        duration_seconds=project.total_duration or 0,
        file_size=record.file_size,
        download_url=(
            f"{get_settings().API_V1_PREFIX}"
            f"/projects/{project.id}/audio/download"
        ),
    )


@router.get("/{project_id}/audio/download")
def download_project_audio(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = owned_project(
        project_id,
        current_user.id,
        db,
    )

    if project.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Project audio is not ready.",
        )

    record = get_audio_record(project.id, db)

    try:
        path = resolve_audio_path(record.file_path)
    except TTSError:
        raise HTTPException(
            status_code=404,
            detail="Stored audio file not found.",
        ) from None

    media_type = (
        "audio/mpeg"
        if record.file_type == "mp3"
        else "audio/wav"
    )

    return FileResponse(
        path=path,
        media_type=media_type,
        filename=(
            f"sapio-project-"
            f"{project.id}.{record.file_type}"
        ),
    )
