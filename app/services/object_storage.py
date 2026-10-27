
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


class ObjectStorageError(Exception):
    pass


def cloud_enabled() -> bool:
    return get_settings().STORAGE_BACKEND == "s3"


def validate_key(key: str) -> str:
    if not key or "\\" in key or key.startswith("/"):
        raise ValueError("Invalid storage key")

    parts = PurePosixPath(key).parts

    if (
        not parts
        or any(part in ("", ".", "..") for part in parts)
        or any(ord(c) < 32 for c in key)
    ):
        raise ValueError("Invalid storage key")

    return "/".join(parts)


def document_key(relative_path: str) -> str:
    return validate_key(
        f"documents/{validate_key(relative_path)}"
    )


def audio_key(relative_path: str) -> str:
    return validate_key(
        f"generated-audio/{validate_key(relative_path)}"
    )


def popular_key(summary_id: int, filename: str) -> str:
    if summary_id <= 0:
        raise ValueError("Invalid summary ID")

    if Path(filename).name != filename:
        raise ValueError("Invalid media filename")

    return validate_key(
        f"popular-media/{summary_id}/{filename}"
    )


@lru_cache(maxsize=1)
def s3_client():
    settings = get_settings()

    required = {
        "AWS_ENDPOINT_URL_S3": settings.AWS_ENDPOINT_URL_S3,
        "AWS_ACCESS_KEY_ID": settings.AWS_ACCESS_KEY_ID,
        "AWS_SECRET_ACCESS_KEY": settings.AWS_SECRET_ACCESS_KEY,
        "AWS_REGION": settings.AWS_REGION,
    }

    missing = [
        name for name, value in required.items()
        if not value
    ]

    if missing:
        raise ObjectStorageError(
            "Missing S3 configuration: "
            + ", ".join(missing)
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.AWS_ENDPOINT_URL_S3,
        region_name=settings.AWS_REGION,
        aws_access_key_id=(
            settings.AWS_ACCESS_KEY_ID.get_secret_value()
        ),
        aws_secret_access_key=(
            settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
        ),
        config=Config(
            s3={"addressing_style": "path"},
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
            connect_timeout=10,
            read_timeout=60,
        ),
    )


def bucket_name() -> str:
    return get_settings().S3_BUCKET


def upload_path(
    source: Path,
    key: str,
    content_type: str = "application/octet-stream",
) -> None:
    key = validate_key(key)

    if not source.is_file():
        raise ObjectStorageError(
            "Source file does not exist"
        )

    try:
        s3_client().upload_file(
            str(source),
            bucket_name(),
            key,
            ExtraArgs={
                "ContentType": content_type,
            },
        )
    except ClientError as exc:
        raise ObjectStorageError(
            "Cloud upload failed"
        ) from exc


def download_path(key: str, destination: Path) -> Path:
    key = validate_key(key)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = destination.with_name(
        destination.name + ".downloading"
    )

    try:
        s3_client().download_file(
            bucket_name(),
            key,
            str(temporary),
        )
        os.replace(temporary, destination)

        return destination
    except ClientError as exc:
        raise ObjectStorageError(
            "Cloud download failed"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def ensure_local(key: str, destination: Path) -> Path:
    if destination.is_file():
        return destination

    if not cloud_enabled():
        raise FileNotFoundError(destination)

    return download_path(key, destination)


def delete_object(key: str) -> None:
    key = validate_key(key)

    try:
        s3_client().delete_object(
            Bucket=bucket_name(),
            Key=key,
        )
    except ClientError as exc:
        raise ObjectStorageError(
            "Cloud deletion failed"
        ) from exc


def object_exists(key: str) -> bool:
    key = validate_key(key)

    try:
        s3_client().head_object(
            Bucket=bucket_name(),
            Key=key,
        )
        return True
    except ClientError as exc:
        error_code = exc.response.get(
            "Error", {}
        ).get("Code")

        if error_code in ("404", "NoSuchKey", "NotFound"):
            return False

        raise ObjectStorageError(
            "Could not check cloud object"
        ) from exc


def get_object_size(key: str) -> int:
    key = validate_key(key)

    response = s3_client().head_object(
        Bucket=bucket_name(),
        Key=key,
    )

    return int(response["ContentLength"])


def download_to_fileobj(
    key: str,
    fileobj: BinaryIO,
) -> None:
    s3_client().download_fileobj(
        bucket_name(),
        validate_key(key),
        fileobj,
    )
