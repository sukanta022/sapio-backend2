import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import ebooklib
import fitz
from bs4 import BeautifulSoup
from ebooklib import epub

from app.core.config import get_settings


class ExtractionError(Exception):
    """A document could not be processed safely."""


@dataclass
class ExtractionResult:
    extracted_text: str
    cleaned_text: str
    character_count: int


def clean_text(text: str) -> str:
    # Normalize different line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Replace non-breaking spaces.
    text = text.replace("\u00a0", " ")

    # Remove control characters except tabs/newlines.
    text = "".join(
        char
        for char in text
        if char in {"\n", "\t"}
        or ord(char) >= 32
    )

    # Remove standalone page numbers.
    text = re.sub(
        r"(?m)^\s*\d{1,4}\s*$",
        "",
        text,
    )

    # Normalize spaces within each line.
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.split("\n")
    ]

    text = "\n".join(lines)

    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_pdf_text(
    file_path: Path,
    max_characters: int,
    max_pages: int,
) -> str:
    parts: list[str] = []
    total_characters = 0

    try:
        with fitz.open(file_path) as document:
            if document.needs_pass:
                raise ExtractionError(
                    "Password-protected PDFs are not supported."
                )

            if document.page_count == 0:
                raise ExtractionError(
                    "PDF contains no pages."
                )

            if document.page_count > max_pages:
                raise ExtractionError(
                    "PDF exceeds the page limit."
                )

            for page in document:
                page_text = page.get_text(
                    "text",
                    sort=True,
                )

                total_characters += len(page_text)

                if total_characters > max_characters:
                    raise ExtractionError(
                        "Extracted text exceeds the character limit."
                    )

                parts.append(page_text)

    except ExtractionError:
        raise
    except (fitz.FileDataError, RuntimeError, ValueError, OSError):
        raise ExtractionError(
            "PDF could not be read."
        ) from None

    return "\n\n".join(parts)


def validate_epub_archive(
    file_path: Path,
    max_uncompressed_bytes: int,
) -> None:
    try:
        with zipfile.ZipFile(file_path) as archive:
            entries = archive.infolist()

            if len(entries) > 2000:
                raise ExtractionError(
                    "EPUB contains too many archive entries."
                )

            total_size = sum(
                item.file_size for item in entries
            )

            if total_size > max_uncompressed_bytes:
                raise ExtractionError(
                    "EPUB uncompressed content is too large."
                )

            if archive.testzip() is not None:
                raise ExtractionError(
                    "EPUB archive contains a damaged entry."
                )

    except ExtractionError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError):
        raise ExtractionError(
            "EPUB archive could not be read."
        ) from None


def extract_epub_text(
    file_path: Path,
    max_characters: int,
    max_uncompressed_bytes: int,
) -> str:
    validate_epub_archive(
        file_path,
        max_uncompressed_bytes,
    )

    parts: list[str] = []
    total_characters = 0

    try:
        book = epub.read_epub(str(file_path))

        # Use the book's spine to preserve reading order.
        for item_reference in book.spine:
            item_id = item_reference[0]

            item = book.get_item_with_id(item_id)

            if item is None:
                continue

            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            soup = BeautifulSoup(
                item.get_content(),
                "html.parser",
            )

            for unwanted in soup(
                ["script", "style", "nav"]
            ):
                unwanted.decompose()

            body = soup.body or soup

            chapter_text = body.get_text(
                separator="\n",
                strip=True,
            )

            if not chapter_text:
                continue

            total_characters += len(chapter_text)

            if total_characters > max_characters:
                raise ExtractionError(
                    "Extracted text exceeds the character limit."
                )

            parts.append(chapter_text)

    except ExtractionError:
        raise
    except Exception:
        # Do not expose parser internals or book contents.
        raise ExtractionError(
            "EPUB could not be parsed."
        ) from None

    return "\n\n".join(parts)


def extract_and_clean(
    file_path: Path,
    file_type: str,
) -> ExtractionResult:
    settings = get_settings()

    if not file_path.is_file():
        raise ExtractionError(
            "Stored document file is missing."
        )

    max_characters = settings.MAX_EXTRACTED_CHARACTERS

    if file_type == "pdf":
        extracted_text = extract_pdf_text(
            file_path,
            max_characters,
            settings.MAX_PDF_PAGES,
        )

    elif file_type == "epub":
        extracted_text = extract_epub_text(
            file_path,
            max_characters,
            settings.MAX_EPUB_UNCOMPRESSED_MB
            * 1024
            * 1024,
        )

    else:
        raise ExtractionError(
            "Unsupported document type."
        )

    cleaned_text = clean_text(extracted_text)

    if not cleaned_text:
        raise ExtractionError(
            "No readable text was found in the document."
        )

    return ExtractionResult(
        extracted_text=extracted_text,
        cleaned_text=cleaned_text,
        character_count=len(cleaned_text),
    )
