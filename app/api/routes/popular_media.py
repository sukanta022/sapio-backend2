from pathlib import Path
from uuid import uuid4
import wave

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.api.routes.popular_summaries import require_summary, serialize_summary
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.schemas.popular_summaries import PopularSummaryResponse
from app.services.audio_streaming import make_audio_stream

router = APIRouter(prefix="/popular-summaries", tags=["Popular Summaries Media"])
MEDIA_TYPES = {
    "audio": {".mp3": "audio/mpeg", ".wav": "audio/wav"},
    "cover": {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"},
    "document": {".pdf": "application/pdf", ".epub": "application/epub+zip"},
}
MAX_BYTES = {"audio": 100 * 1024 * 1024, "cover": 5 * 1024 * 1024, "document": 20 * 1024 * 1024}


def media_root() -> Path:
    return (get_settings().AUDIO_STORAGE_ROOT.parent / "popular_summaries").resolve()


def file_kind_valid(kind: str, suffix: str, header: bytes) -> bool:
    if kind == "audio":
        return (suffix == ".mp3" and (header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 255 and (header[1] & 224) == 224))) or (suffix == ".wav" and header[:4] == b"RIFF" and header[8:12] == b"WAVE")
    if kind == "cover":
        return (suffix in {".jpg", ".jpeg"} and header[:3] == b"\xff\xd8\xff") or (suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
    return (suffix == ".pdf" and header.startswith(b"%PDF-")) or (suffix == ".epub" and header.startswith(b"PK"))


async def save_upload(file: UploadFile, kind: str, summary_id: int) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MEDIA_TYPES[kind]:
        raise HTTPException(415, f"Unsupported {kind} extension.")
    folder = media_root() / str(summary_id)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{kind}-{uuid4().hex}{suffix}"
    size = 0
    header = b""
    try:
        with destination.open("xb") as stream:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES[kind]:
                    raise HTTPException(413, f"{kind} exceeds upload limit.")
                if len(header) < 16:
                    header = (header + chunk)[:16]
                stream.write(chunk)
        if not size or not file_kind_valid(kind, suffix, header):
            raise HTTPException(415, f"Invalid {kind} file signature.")
        if kind == "audio" and suffix == ".wav":
            try:
                with wave.open(str(destination), "rb") as audio:
                    if audio.getnframes() <= 0:
                        raise ValueError("Empty WAV")
            except (wave.Error, EOFError, ValueError):
                raise HTTPException(415, "Invalid WAV file.") from None
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def safe_path(stored: str | None, summary_id: int) -> Path:
    if not stored:
        raise HTTPException(404, "Media is unavailable.")
    root = media_root()
    path = Path(stored).resolve()
    if not path.is_relative_to(root / str(summary_id)) or not path.is_file():
        raise HTTPException(404, "Media is unavailable.")
    return path


@router.post("/{summary_id}/media", response_model=PopularSummaryResponse)
async def upload_popular_media(
    summary_id: int,
    audio: UploadFile | None = File(default=None),
    cover: UploadFile | None = File(default=None),
    document: UploadFile | None = File(default=None),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    summary = require_summary(db, summary_id)
    if audio is None and cover is None and document is None:
        raise HTTPException(422, "Select at least one media file.")
    saved = []
    old = []
    try:
        for kind, upload, attr in [
            ("audio", audio, "audio_file_path"),
            ("cover", cover, "thumbnail_path"),
            ("document", document, "file_path"),
        ]:
            if upload is not None:
                new_path = await save_upload(upload, kind, summary_id)
                saved.append(new_path)
                old.append((attr, getattr(summary, attr)))
                setattr(summary, attr, str(new_path))
        db.commit()
        db.refresh(summary)
    except Exception:
        db.rollback()
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    # Remove only prior files owned by the same summary and storage root.
    for _attr, previous in old:
        try:
            previous_path = safe_path(previous, summary_id)
        except HTTPException:
            continue
        if previous_path not in saved:
            previous_path.unlink(missing_ok=True)
    return serialize_summary(db, summary)


@router.get("/{summary_id}/cover")
def get_popular_cover(summary_id: int, db: Session = Depends(get_db)):
    summary = require_summary(db, summary_id, published_only=True)
    path = safe_path(summary.thumbnail_path, summary_id)
    media_type = MEDIA_TYPES["cover"].get(path.suffix.lower())
    if not media_type:
        raise HTTPException(404, "Cover is unavailable.")
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300"})


@router.get("/{summary_id}/audio/stream")
def stream_popular_audio(summary_id: int, request: Request, db: Session = Depends(get_db)):
    summary = require_summary(db, summary_id, published_only=True)
    path = safe_path(summary.audio_file_path, summary_id)
    media_type = MEDIA_TYPES["audio"].get(path.suffix.lower())
    if not media_type:
        raise HTTPException(404, "Audio is unavailable.")
    return make_audio_stream(path, media_type, request.headers.get("range"))
