import os
from pathlib import Path

import fitz
import pytest
from ebooklib import epub
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.models import Document, ExtractionJob, Role, User
from app.main import app
from app.services.text_extraction import (
    ExtractionError,
    clean_text,
    extract_and_clean,
)


def make_pdf(path: Path, text: str) -> None:
    document = fitz.open()

    page = document.new_page()
    page.insert_text(
        (72, 72),
        text,
        fontsize=12,
    )

    document.save(str(path))
    document.close()


def make_epub(path: Path) -> None:
    book = epub.EpubBook()

    book.set_identifier("sapio-phase6-test")
    book.set_title("Test EPUB")
    book.set_language("en")
    book.add_author("Test Author")

    chapter = epub.EpubHtml(
        title="Chapter 1",
        file_name="chapter1.xhtml",
        lang="en",
    )

    chapter.content = (
        "<html><body>"
        "<h1>Chapter 1</h1>"
        "<p>The journey begins here.</p>"
        "</body></html>"
    )

    book.add_item(chapter)
    book.spine = ["nav", chapter]

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(path), book)


def test_clean_text():
    original = (
        "Chapter 1\r\n"
        "\r\n"
        "  The   journey    begins. \n"
        "12\n"
        "\n\n\n"
        "The next paragraph."
    )

    cleaned = clean_text(original)

    assert "Chapter 1" in cleaned
    assert "The journey begins." in cleaned
    assert "The next paragraph." in cleaned
    assert "\n12\n" not in cleaned
    assert "\n\n\n" not in cleaned


def test_pdf_extraction(tmp_path):
    file_path = tmp_path / "test.pdf"

    make_pdf(
        file_path,
        "Chapter One: The journey begins.",
    )

    result = extract_and_clean(
        file_path,
        "pdf",
    )

    assert "Chapter One" in result.cleaned_text
    assert "journey begins" in result.cleaned_text
    assert result.character_count > 0


def test_epub_extraction(tmp_path):
    file_path = tmp_path / "test.epub"

    make_epub(file_path)

    result = extract_and_clean(
        file_path,
        "epub",
    )

    assert "Chapter 1" in result.cleaned_text
    assert "The journey begins here." in (
        result.cleaned_text
    )


def test_corrupted_pdf(tmp_path):
    file_path = tmp_path / "invalid.pdf"

    file_path.write_bytes(
        b"This is not a real PDF"
    )

    with pytest.raises(ExtractionError):
        extract_and_clean(file_path, "pdf")


def test_invalid_epub(tmp_path):
    file_path = tmp_path / "invalid.epub"

    file_path.write_bytes(
        b"Invalid EPUB data"
    )

    with pytest.raises(ExtractionError):
        extract_and_clean(file_path, "epub")


def test_missing_document(tmp_path):
    file_path = tmp_path / "missing.pdf"

    with pytest.raises(
        ExtractionError,
        match="missing",
    ):
        extract_and_clean(file_path, "pdf")


def test_unsupported_document_type(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("Hello world")

    with pytest.raises(
        ExtractionError,
        match="Unsupported",
    ):
        extract_and_clean(file_path, "txt")


@pytest.fixture
def extraction_client(tmp_path, monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")

    if (
        not database_url
        or not database_url.startswith(
            "postgresql+psycopg://"
        )
        or not database_url.endswith("/sapio_test_db")
    ):
        pytest.fail(
            "Set TEST_DATABASE_URL to the "
            "separate PostgreSQL sapio_test_db."
        )

    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "STORAGE_ROOT",
        tmp_path / "uploads",
    )

    engine = create_engine(database_url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            db = Session(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )

            def override_get_db():
                yield db

            app.dependency_overrides[get_db] = (
                override_get_db
            )

            try:
                role = db.scalar(
                    select(Role).where(
                        Role.name == "user"
                    )
                )

                if role is None:
                    role = Role(
                        name="user",
                        description="Standard user",
                    )
                    db.add(role)
                    db.flush()

                first_user = User(
                    name="Extraction Test User",
                    email="extraction@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                second_user = User(
                    name="Other Extraction User",
                    email="other-extraction@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([
                    first_user,
                    second_user,
                ])
                db.flush()

                storage_dir = (
                    settings.STORAGE_ROOT
                    / str(first_user.id)
                )

                storage_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                pdf_path = storage_dir / "test.pdf"

                make_pdf(
                    pdf_path,
                    "A real PDF with readable text.",
                )

                document = Document(
                    user_id=first_user.id,
                    file_name="test.pdf",
                    file_type="pdf",
                    file_path=(
                        f"{first_user.id}/test.pdf"
                    ),
                    file_size=pdf_path.stat().st_size,
                    status="uploaded",
                )

                db.add(document)
                db.commit()

                db.refresh(document)

                first_token = create_access_token(
                    first_user.id
                )

                second_token = create_access_token(
                    second_user.id
                )

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "document_id": document.id,
                        "token": first_token,
                        "other_token": second_token,
                    }

            finally:
                app.dependency_overrides.pop(
                    get_db,
                    None,
                )
                db.close()
                transaction.rollback()

    finally:
        engine.dispose()


def authorization_header(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}"
    }


def test_extraction_requires_authentication(
    extraction_client,
):
    client = extraction_client["client"]
    document_id = extraction_client["document_id"]

    response = client.post(
        f"/api/v1/documents/{document_id}/extract"
    )

    assert response.status_code == 401


def test_cannot_extract_other_users_document(
    extraction_client,
):
    client = extraction_client["client"]
    document_id = extraction_client["document_id"]

    response = client.post(
        f"/api/v1/documents/{document_id}/extract",
        headers=authorization_header(
            extraction_client["other_token"]
        ),
    )

    assert response.status_code == 404


def test_extraction_api_success(
    extraction_client,
):
    client = extraction_client["client"]
    db = extraction_client["db"]

    document_id = extraction_client["document_id"]

    response = client.post(
        f"/api/v1/documents/{document_id}/extract",
        headers=authorization_header(
            extraction_client["token"]
        ),
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "completed"
    assert response_data["character_count"] > 0

    job = db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.document_id == document_id
        )
    )

    assert job is not None
    assert "readable text" in job.cleaned_text

    stored_document = db.get(
        Document,
        document_id,
    )

    assert stored_document.status == "extracted"

    status_response = client.get(
        f"/api/v1/documents/{document_id}/extraction",
        headers=authorization_header(
            extraction_client["token"]
        ),
    )

    assert status_response.status_code == 200
    assert (
        status_response.json()["has_cleaned_text"]
        is True
    )

    repeated = client.post(
        f"/api/v1/documents/{document_id}/extract",
        headers=authorization_header(
            extraction_client["token"]
        ),
    )

    assert repeated.status_code == 409
