import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import create_access_token
from app.db.models import (
    Chapter,
    Document,
    ExtractionJob,
    Role,
    User,
)
from app.main import app
from app.services.chapter_detection import (
    ChapterDetectionError,
    detect_chapters,
)


SAMPLE_TEXT = (
    "Chapter 1: The Beginning\n"
    "The first chapter starts here.\n\n"
    "Chapter 2: The Journey\n"
    "The second chapter starts here.\n\n"
    "Chapter 3: The Return\n"
    "The final chapter ends here."
)


def test_detect_three_chapters():
    chapters = detect_chapters(SAMPLE_TEXT)

    assert len(chapters) == 3
    assert [c.chapter_number for c in chapters] == [
        1, 2, 3
    ]
    assert chapters[0].title == (
        "Chapter 1: The Beginning"
    )
    assert "second chapter starts" in (
        chapters[1].content
    )


def test_preserves_text_positions():
    chapters = detect_chapters(SAMPLE_TEXT)

    for chapter in chapters:
        assert SAMPLE_TEXT[
            chapter.start_position:chapter.end_position
        ] == chapter.content


def test_roman_and_written_numbers():
    text = (
        "CHAPTER II\n"
        "Some chapter content.\n\n"
        "Chapter Three: The Discovery\n"
        "More chapter content."
    )

    chapters = detect_chapters(text)

    assert len(chapters) == 2
    assert chapters[0].title == "CHAPTER II"
    assert chapters[1].title.startswith(
        "Chapter Three"
    )


def test_introductory_content_preserved():
    text = (
        "Preface\n"
        "An introductory paragraph.\n\n"
        "Chapter 1\n"
        "The story begins."
    )

    chapters = detect_chapters(text)

    assert len(chapters) == 2
    assert chapters[0].title == "Introduction"
    assert "introductory paragraph" in (
        chapters[0].content
    )
    assert chapters[1].title == "Chapter 1"


def test_fallback_when_no_headings():
    chapters = detect_chapters(
        "A book without numbered chapters."
    )

    assert len(chapters) == 1
    assert chapters[0].title == "Full Document"


def test_empty_text_rejected():
    with pytest.raises(ChapterDetectionError):
        detect_chapters("   \n \n")


@pytest.fixture
def chapter_client():
    database_url = os.getenv("TEST_DATABASE_URL")

    if (
        not database_url
        or not database_url.startswith(
            "postgresql+psycopg://"
        )
        or not database_url.endswith("/sapio_test_db")
    ):
        pytest.fail(
            "Set TEST_DATABASE_URL to the separate "
            "PostgreSQL sapio_test_db."
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

                owner = User(
                    name="Chapter Test Owner",
                    email="chapter-owner@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other = User(
                    name="Other Chapter User",
                    email="chapter-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([owner, other])
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="sample.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/sample.pdf",
                    file_size=100,
                    status="extracted",
                )

                db.add(document)
                db.flush()

                job = ExtractionJob(
                    document_id=document.id,
                    extracted_text=SAMPLE_TEXT,
                    cleaned_text=SAMPLE_TEXT,
                    status="completed",
                )

                db.add(job)
                db.commit()

                db.refresh(document)
                db.refresh(job)

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "document_id": document.id,
                        "job_id": job.id,
                        "token": create_access_token(
                            owner.id
                        ),
                        "other_token": create_access_token(
                            other.id
                        ),
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


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
    }


def detection_url(document_id: int) -> str:
    return (
        f"/api/v1/documents/{document_id}"
        "/chapters/detect"
    )


def test_chapter_detection_requires_auth(chapter_client):
    client = chapter_client["client"]

    response = client.post(
        detection_url(chapter_client["document_id"])
    )

    assert response.status_code == 401


def test_cannot_detect_other_users_chapters(
    chapter_client,
):
    response = chapter_client["client"].post(
        detection_url(chapter_client["document_id"]),
        headers=auth_headers(
            chapter_client["other_token"]
        ),
    )

    assert response.status_code == 404


def test_detect_and_persist_chapters(chapter_client):
    client = chapter_client["client"]
    document_id = chapter_client["document_id"]

    response = client.post(
        detection_url(document_id),
        headers=auth_headers(
            chapter_client["token"]
        ),
    )

    assert response.status_code == 201

    data = response.json()

    assert data["total_chapters"] == 3
    assert len(data["chapters"]) == 3

    db = chapter_client["db"]

    chapters = db.scalars(
        select(Chapter)
        .where(
            Chapter.extraction_job_id
            == chapter_client["job_id"]
        )
        .order_by(Chapter.chapter_number)
    ).all()

    assert len(chapters) == 3

    job = db.get(
        ExtractionJob,
        chapter_client["job_id"],
    )

    assert job.chapters_detected["status"] == (
        "completed"
    )
    assert job.chapters_detected["total_chapters"] == 3

    for chapter in chapters:
        assert SAMPLE_TEXT[
            chapter.start_position:chapter.end_position
        ] == chapter.content


def test_reject_duplicate_detection(chapter_client):
    client = chapter_client["client"]
    url = detection_url(chapter_client["document_id"])

    headers = auth_headers(chapter_client["token"])

    first = client.post(url, headers=headers)
    second = client.post(url, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409


def test_list_and_read_chapters(chapter_client):
    client = chapter_client["client"]
    document_id = chapter_client["document_id"]
    headers = auth_headers(chapter_client["token"])

    detected = client.post(
        detection_url(document_id),
        headers=headers,
    )

    assert detected.status_code == 201

    list_response = client.get(
        f"/api/v1/documents/{document_id}/chapters",
        headers=headers,
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 3
    assert "content" not in list_response.json()[0]

    chapter_id = list_response.json()[0]["id"]

    detail_response = client.get(
        (
            f"/api/v1/documents/{document_id}"
            f"/chapters/{chapter_id}"
        ),
        headers=headers,
    )

    assert detail_response.status_code == 200
    assert "The first chapter starts here." in (
        detail_response.json()["content"]
    )


def test_other_user_cannot_read_chapters(
    chapter_client,
):
    client = chapter_client["client"]
    document_id = chapter_client["document_id"]

    detected = client.post(
        detection_url(document_id),
        headers=auth_headers(chapter_client["token"]),
    )

    assert detected.status_code == 201

    chapter_id = detected.json()["chapters"][0]["id"]

    other_headers = auth_headers(
        chapter_client["other_token"]
    )

    list_response = client.get(
        f"/api/v1/documents/{document_id}/chapters",
        headers=other_headers,
    )

    detail_response = client.get(
        (
            f"/api/v1/documents/{document_id}"
            f"/chapters/{chapter_id}"
        ),
        headers=other_headers,
    )

    assert list_response.status_code == 404
    assert detail_response.status_code == 404
