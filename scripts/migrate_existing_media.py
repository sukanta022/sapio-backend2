import argparse
import mimetypes
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Document, GeneratedFile, PopularSummary
from app.db.session import get_session_factory
from app.services.object_storage import (
    document_key, audio_key, popular_key, upload_path, object_exists,
)


def _upload_if_needed(path: Path, key: str, execute: bool) -> str:
    if not path.is_file():
        print(f"MISSING  {path}")
        return "missing"
    if object_exists(key):
        print(f"EXISTS   {key}")
        return "existing"
    if not execute:
        print(f"PREVIEW  {path} -> {key}")
        return "preview"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_path(path, key, mime)
    print(f"UPLOADED {key}")
    return "uploaded"


def main(execute: bool = False):
    settings = get_settings()
    summary = {}

    def track(result):
        summary[result] = summary.get(result, 0) + 1

    session_factory = get_session_factory()
    with session_factory() as db:
        for doc in db.scalars(select(Document)).all():
            relative = doc.file_path
            if relative:
                track(_upload_if_needed(
                    settings.STORAGE_ROOT / relative,
                    document_key(relative), execute,
                ))

        for audio in db.scalars(select(GeneratedFile)).all():
            if audio.file_type not in ("mp3", "wav"):
                continue
            relative = audio.file_path
            if relative:
                track(_upload_if_needed(
                    settings.AUDIO_STORAGE_ROOT / relative,
                    audio_key(relative), execute,
                ))

        for item in db.scalars(select(PopularSummary)).all():
            for stored in (item.audio_file_path, item.thumbnail_path, item.file_path):
                if not stored:
                    continue
                basename = stored.replace("\\", "/").rsplit("/", 1)[-1]
                # On Windows, a legacy absolute path is usable locally.
                # New Linux/relative S3-style paths are NOT local files.
                original = Path(stored)
                if original.is_file():
                    path = original
                else:
                    path = settings.AUDIO_STORAGE_ROOT.parent / "popular_summaries" / str(item.id) / basename
                track(_upload_if_needed(path, popular_key(item.id, basename), execute))

    print("Summary:", summary)
    if summary.get("missing"):
        print("WARNING: Some database records have no corresponding local files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    main(args.execute)
