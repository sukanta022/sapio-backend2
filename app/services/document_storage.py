import os
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.services.object_storage import (
    cloud_enabled, document_key, upload_path, delete_object,
)


CHUNK_SIZE = 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".epub": "epub",
}

EPUB_MIMETYPE = b"application/epub+zip"


def clean_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A filename is required.",
        )

    # Remove any directory components, including
    # Windows-style paths sent by a client.
    cleaned = filename.replace("\\", "/").split("/")[-1]

    if (
        not cleaned
        or cleaned in {".", ".."}
        or len(cleaned) > 255
        or any(ord(char) < 32 for char in cleaned)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )

    return cleaned


def get_file_type(filename: str) -> str:
    extension = Path(filename).suffix.lower()

    file_type = ALLOWED_EXTENSIONS.get(extension)

    if file_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and EPUB files are supported.",
        )

    return file_type


def validate_file_contents(
    file_path: Path,
    file_type: str,
) -> None:
    if file_type == "pdf":
        with file_path.open("rb") as file:
            header = file.read(5)

        if header != b"%PDF-":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Invalid PDF file signature.",
            )

    elif file_type == "epub":
        if not zipfile.is_zipfile(file_path):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Invalid EPUB file.",
            )

        try:
            with zipfile.ZipFile(file_path, "r") as archive:
                entries = archive.infolist()

                if not entries:
                    raise ValueError("Empty EPUB archive")

                first_entry = entries[0]

                if (
                    first_entry.filename != "mimetype"
                    or first_entry.compress_type
                    != zipfile.ZIP_STORED
                    or first_entry.file_size
                    != len(EPUB_MIMETYPE)
                ):
                    raise ValueError(
                        "Invalid EPUB mimetype entry"
                    )

                mimetype = archive.read(first_entry)

                if mimetype != EPUB_MIMETYPE:
                    raise ValueError(
                        "Invalid EPUB mimetype"
                    )

                if "META-INF/container.xml" not in (
                    archive.namelist()
                ):
                    raise ValueError(
                        "Missing EPUB container"
                    )

        except (
            zipfile.BadZipFile,
            RuntimeError,
            OSError,
            ValueError,
        ):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Invalid EPUB structure.",
            ) from None


async def save_uploaded_document(
    upload: UploadFile,
    user_id: int,
) -> tuple[str, str, str, int]:
    settings = get_settings()

    original_name = clean_filename(upload.filename)
    file_type = get_file_type(original_name)

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    storage_root = settings.STORAGE_ROOT.resolve()
    user_folder = storage_root / str(user_id)

    user_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    stored_filename = f"{uuid.uuid4().hex}.{file_type}"

    relative_path = Path(str(user_id)) / stored_filename
    destination = storage_root / relative_path

    temporary_path = destination.with_suffix(
        destination.suffix + ".part"
    )

    file_size = 0

    try:
        with temporary_path.open("xb") as output:
            while True:
                chunk = await upload.read(CHUNK_SIZE)

                if not chunk:
                    break

                file_size += len(chunk)

                if file_size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File exceeds the "
                            f"{settings.MAX_UPLOAD_SIZE_MB} MiB "
                            "upload limit."
                        ),
                    )

                output.write(chunk)

        if file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file is empty.",
            )

        validate_file_contents(
            temporary_path,
            file_type,
        )

        os.replace(
            temporary_path,
            destination,
        )

        if cloud_enabled():
            content_type = (
                "application/pdf" if file_type == "pdf"
                else "application/epub+zip"
            )
            upload_path(destination, document_key(relative_path.as_posix()), content_type)

        return (
            original_name,
            file_type,
            relative_path.as_posix(),
            file_size,
        )

    except Exception:
        temporary_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise

    finally:
        await upload.close()


def remove_stored_document(relative_path: str) -> None:
    storage_root = get_settings().STORAGE_ROOT.resolve()
    file_path = (storage_root / relative_path).resolve()

    # Only allow removal inside the configured storage root.
    if not file_path.is_relative_to(storage_root):
        raise ValueError("Invalid document storage path")

    if cloud_enabled():
        delete_object(document_key(relative_path))
    file_path.unlink(missing_ok=True)
