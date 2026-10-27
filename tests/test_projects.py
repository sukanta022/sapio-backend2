import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import create_access_token
from app.db.models import (
    AudioFormat,
    Chapter,
    Document,
    ExtractionJob,
    Language,
    Narrator,
    Project,
    ProjectChapter,
    Role,
    SummaryType,
    User,
)
from app.main import app
from scripts.seed_project_options import seed_options


@pytest.fixture
def project_client():
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
            "separate sapio_test_db database."
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
                    select(Role).where(Role.name == "user")
                )

                if role is None:
                    role = Role(
                        name="user",
                        description="Standard user",
                    )
                    db.add(role)
                    db.flush()

                seed_options(db)

                owner = User(
                    name="Project Test Owner",
                    email="project-owner@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other = User(
                    name="Other Project User",
                    email="project-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([owner, other])
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="test-book.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/test-book.pdf",
                    file_size=100,
                    status="extracted",
                )

                unprocessed_document = Document(
                    user_id=owner.id,
                    file_name="unprocessed.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/unprocessed.pdf",
                    file_size=100,
                    status="uploaded",
                )

                db.add_all([
                    document,
                    unprocessed_document,
                ])
                db.flush()

                cleaned_text = (
                    "Chapter 1\n"
                    "The beginning of the book.\n\n"
                    "Chapter 2\n"
                    "The second part of the book."
                )

                extraction = ExtractionJob(
                    document_id=document.id,
                    extracted_text=cleaned_text,
                    cleaned_text=cleaned_text,
                    status="completed",
                )

                db.add(extraction)
                db.flush()

                first_chapter = Chapter(
                    extraction_job_id=extraction.id,
                    chapter_number=1,
                    title="Chapter 1",
                    content="Chapter 1\nThe beginning of the book.",
                    start_position=0,
                    end_position=37,
                )

                second_chapter = Chapter(
                    extraction_job_id=extraction.id,
                    chapter_number=2,
                    title="Chapter 2",
                    content="Chapter 2\nThe second part of the book.",
                    start_position=39,
                    end_position=len(cleaned_text),
                )

                db.add_all([
                    first_chapter,
                    second_chapter,
                ])
                db.commit()

                summary_types = {
                    item.name: item.id
                    for item in db.scalars(
                        select(SummaryType)
                    ).all()
                }

                narrator = db.scalar(
                    select(Narrator).where(
                        Narrator.name == "James"
                    )
                )

                language = db.scalar(
                    select(Language).where(
                        Language.code == "en"
                    )
                )

                audio_format = db.scalar(
                    select(AudioFormat).where(
                        AudioFormat.name == "MP3"
                    )
                )

                payload = {
                    "document_id": document.id,
                    "summary_type_id": summary_types[
                        "10-Min Express"
                    ],
                    "narrator_id": narrator.id,
                    "language_id": language.id,
                    "audio_format_id": audio_format.id,
                }

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "payload": payload,
                        "summary_types": summary_types,
                        "unprocessed_id": (
                            unprocessed_document.id
                        ),
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


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
    }


def create_project_request(
    context,
    payload=None,
    token=None,
):
    return context["client"].post(
        "/api/v1/projects",
        headers=headers(token or context["token"]),
        json=payload or context["payload"],
    )


def test_options_require_authentication(project_client):
    response = project_client["client"].get(
        "/api/v1/projects/options"
    )

    assert response.status_code == 401


def test_get_project_options(project_client):
    response = project_client["client"].get(
        "/api/v1/projects/options",
        headers=headers(project_client["token"]),
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["summary_types"]) >= 3
    assert len(data["narrators"]) >= 4
    assert len(data["languages"]) >= 2
    assert len(data["audio_formats"]) >= 2


def test_create_project_success(project_client):
    response = create_project_request(project_client)

    assert response.status_code == 201

    data = response.json()

    assert data["status"] == "pending"
    assert data["chapters"] == []
    assert data["document_id"] == (
        project_client["payload"]["document_id"]
    )

    project = project_client["db"].get(
        Project,
        data["id"],
    )

    assert project is not None
    assert project.status == "pending"
    assert project.script is None


def test_reject_unprocessed_document(project_client):
    payload = project_client["payload"].copy()

    payload["document_id"] = (
        project_client["unprocessed_id"]
    )

    response = create_project_request(
        project_client,
        payload=payload,
    )

    assert response.status_code == 409


def test_reject_invalid_option(project_client):
    payload = project_client["payload"].copy()

    payload["narrator_id"] = 999999999

    response = create_project_request(
        project_client,
        payload=payload,
    )

    assert response.status_code == 422


def test_other_user_cannot_create_project(
    project_client,
):
    response = create_project_request(
        project_client,
        token=project_client["other_token"],
    )

    assert response.status_code == 404


def test_chapter_breakdown_creates_associations(
    project_client,
):
    payload = project_client["payload"].copy()

    payload["summary_type_id"] = (
        project_client["summary_types"][
            "Chapter Breakdown"
        ]
    )

    response = create_project_request(
        project_client,
        payload=payload,
    )

    assert response.status_code == 201

    data = response.json()

    assert len(data["chapters"]) == 2

    assert [
        chapter["chapter_order"]
        for chapter in data["chapters"]
    ] == [1, 2]

    associations = project_client["db"].scalars(
        select(ProjectChapter)
        .where(
            ProjectChapter.project_id == data["id"]
        )
        .order_by(ProjectChapter.chapter_order)
    ).all()

    assert len(associations) == 2


def test_list_and_get_own_project(project_client):
    created = create_project_request(project_client)

    assert created.status_code == 201

    project_id = created.json()["id"]

    list_response = project_client["client"].get(
        "/api/v1/projects",
        headers=headers(project_client["token"]),
    )

    assert list_response.status_code == 200

    project_ids = {
        item["id"]
        for item in list_response.json()
    }

    assert project_id in project_ids

    detail_response = project_client["client"].get(
        f"/api/v1/projects/{project_id}",
        headers=headers(project_client["token"]),
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == project_id

    forbidden_response = project_client["client"].get(
        f"/api/v1/projects/{project_id}",
        headers=headers(
            project_client["other_token"]
        ),
    )

    assert forbidden_response.status_code == 404
