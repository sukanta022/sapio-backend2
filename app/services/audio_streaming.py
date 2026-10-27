from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


STREAM_CHUNK_SIZE = 64 * 1024


def parse_byte_range(
    range_header: str,
    file_size: int,
) -> tuple[int, int]:
    """
    Parse a single HTTP byte range.

    Returns inclusive (start, end).

    Multiple ranges are not supported.
    """
    if file_size <= 0:
        raise ValueError(
            "Cannot stream an empty audio file."
        )

    if not range_header.startswith("bytes="):
        raise ValueError(
            "Unsupported Range unit."
        )

    value = range_header[6:].strip()

    if "," in value or value.count("-") != 1:
        raise ValueError(
            "Only a single byte range is supported."
        )

    first, last = value.split("-", 1)

    if not first and not last:
        raise ValueError(
            "Byte range is empty."
        )

    if first:
        if not first.isascii() or not first.isdigit():
            raise ValueError(
                "Invalid range start."
            )

        start = int(first)

        if last:
            if not last.isascii() or not last.isdigit():
                raise ValueError(
                    "Invalid range end."
                )

            end = int(last)

            if end < start:
                raise ValueError(
                    "Range end precedes range start."
                )
        else:
            end = file_size - 1

        if start >= file_size:
            raise ValueError(
                "Range begins beyond file."
            )

        end = min(end, file_size - 1)

    else:
        if not last.isascii() or not last.isdigit():
            raise ValueError(
                "Invalid suffix length."
            )

        suffix_length = int(last)

        if suffix_length <= 0:
            raise ValueError(
                "Suffix length must be positive."
            )

        start = max(0, file_size - suffix_length)
        end = file_size - 1

    return start, end


def read_file_range(
    path: Path,
    start: int,
    end: int,
) -> Iterator[bytes]:
    """
    Yield the inclusive byte interval from disk.

    Keeps memory use bounded.
    """
    remaining = end - start + 1

    with path.open("rb") as audio_file:
        audio_file.seek(start)

        while remaining > 0:
            chunk = audio_file.read(
                min(STREAM_CHUNK_SIZE, remaining)
            )

            if not chunk:
                break

            remaining -= len(chunk)
            yield chunk


def make_audio_stream(
    path: Path,
    media_type: str,
    range_header: str | None,
) -> StreamingResponse:
    try:
        file_size = path.stat().st_size
    except OSError:
        raise HTTPException(
            status_code=404,
            detail="Stored audio file not found.",
        ) from None

    if file_size <= 0:
        raise HTTPException(
            status_code=404,
            detail="Stored audio file is empty.",
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }

    if range_header is None:
        headers["Content-Length"] = str(file_size)

        return StreamingResponse(
            read_file_range(
                path,
                0,
                file_size - 1,
            ),
            status_code=200,
            media_type=media_type,
            headers=headers,
        )

    try:
        start, end = parse_byte_range(
            range_header,
            file_size,
        )
    except ValueError:
        headers["Content-Range"] = (
            f"bytes */{file_size}"
        )

        raise HTTPException(
            status_code=416,
            detail="Requested byte range is not satisfiable.",
            headers=headers,
        ) from None

    headers["Content-Range"] = (
        f"bytes {start}-{end}/{file_size}"
    )
    headers["Content-Length"] = str(
        end - start + 1
    )

    return StreamingResponse(
        read_file_range(
            path,
            start,
            end,
        ),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )
