import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
)
from app.db.models import Document, Role, User
from app.main import app


def create_epub_bytes() -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            "<container></container>",
        )

    return buffer.getvalue()


@pytest.fixture
def document_client(tmp_path, monkeypatch):
    test_database_url = os.getenv("TEST_DATABASE_URL")

    if not test_database_url:
        pytest.fail(
            "TEST_DATABASE_URL is required."
        )

    if not test_database_url.startswith(
        "postgresql+psycopg://"
    ):
        pytest.fail(
            "Invalid test database driver."
        )

    if not test_database_url.endswith(
        "/sapio_test_db"
    ):
        pytest.fail(
            "Tests must use sapio_test_db."
        )

    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "STORAGE_ROOT",
        tmp_path / "uploads",
    )

    engine = create_engine(test_database_url)

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

                user = User(
                    name="Upload Test User",
                    email="uploadtest@example.com",
                    password_hash=hash_password(
                        "StrongPassword123!"
                    ),
                    role_id=role.id,
                    is_active=True,
                )

                other_user = User(
                    name="Other Test User",
                    email="otherupload@example.com",
                    password_hash=hash_password(
                        "StrongPassword123!"
                    ),
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([user, other_user])
                db.commit()

                db.refresh(user)
                db.refresh(other_user)

                token = create_access_token(user.id)
                other_token = create_access_token(
                    other_user.id
                )

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "user_id": user.id,
                        "token": token,
                        "other_token": other_token,
                        "storage_root": settings.STORAGE_ROOT,
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


def upload_file(
    client: TestClient,
    token: str,
    filename: str,
    contents: bytes,
):
    return client.post(
        "/api/v1/documents/upload",
        headers=auth_headers(token),
        files={
            "file": (
                filename,
                contents,
                "application/octet-stream",
            )
        },
    )


def test_upload_requires_authentication(
    document_client,
):
    client = document_client["client"]

    response = client.post(
        "/api/v1/documents/upload",
        files={
            "file": (
                "book.pdf",
                b"%PDF-1.4\nTest",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 401


def test_upload_pdf_success(document_client):
    client = document_client["client"]
    token = document_client["token"]

    response = upload_file(
        client,
        token,
        "example.pdf",
        b"%PDF-1.4\nSample PDF content",
    )

    assert response.status_code == 201

    data = response.json()

    assert data["file_name"] == "example.pdf"
    assert data["file_type"] == "pdf"
    assert data["status"] == "uploaded"
    assert data["file_size"] > 0
    assert "file_path" not in data

    db = document_client["db"]

    document = db.get(Document, data["id"])

    assert document is not None

    stored_file = (
        document_client["storage_root"]
        / document.file_path
    )

    assert stored_file.exists()
    assert stored_file.read_bytes().startswith(
        b"%PDF-"
    )


def test_upload_epub_success(document_client):
    response = upload_file(
        document_client["client"],
        document_client["token"],
        "example.epub",
        create_epub_bytes(),
    )

    assert response.status_code == 201
    assert response.json()["file_type"] == "epub"


def test_reject_unsupported_extension(
    document_client,
):
    response = upload_file(
        document_client["client"],
        document_client["token"],
        "malware.exe",
        b"Not a book",
    )

    assert response.status_code == 415


def test_reject_fake_pdf(document_client):
    response = upload_file(
        document_client["client"],
        document_client["token"],
        "fake.pdf",
        b"This is not a PDF",
    )

    assert response.status_code == 415

    storage_root = document_client["storage_root"]

    assert not list(
        storage_root.rglob("*.part")
    )


def test_reject_empty_file(document_client):
    response = upload_file(
        document_client["client"],
        document_client["token"],
        "empty.pdf",
        b"",
    )

    assert response.status_code == 400


def test_reject_oversized_file(
    document_client,
    monkeypatch,
):
    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "MAX_UPLOAD_SIZE_MB",
        1,
    )

    oversized_content = (
        b"%PDF-1.4\n"
        + b"x" * (1024 * 1024)
    )

    response = upload_file(
        document_client["client"],
        document_client["token"],
        "large.pdf",
        oversized_content,
    )

    assert response.status_code == 413


def test_list_own_documents(document_client):
    client = document_client["client"]
    token = document_client["token"]

    uploaded = upload_file(
        client,
        token,
        "listed.pdf",
        b"%PDF-1.4\nDocument",
    )

    assert uploaded.status_code == 201

    response = client.get(
        "/api/v1/documents",
        headers=auth_headers(token),
    )

    assert response.status_code == 200

    document_ids = {
        item["id"]
        for item in response.json()
    }

    assert uploaded.json()["id"] in document_ids


def test_get_own_document(document_client):
    client = document_client["client"]
    token = document_client["token"]

    uploaded = upload_file(
        client,
        token,
        "details.pdf",
        b"%PDF-1.4\nDetails",
    )

    assert uploaded.status_code == 201

    document_id = uploaded.json()["id"]

    response = client.get(
        f"/api/v1/documents/{document_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["id"] == document_id


def test_cannot_access_another_users_document(
    document_client,
):
    client = document_client["client"]

    uploaded = upload_file(
        client,
        document_client["token"],
        "private.pdf",
        b"%PDF-1.4\nPrivate",
    )

    assert uploaded.status_code == 201

    document_id = uploaded.json()["id"]

    response = client.get(
        f"/api/v1/documents/{document_id}",
        headers=auth_headers(
            document_client["other_token"]
        ),
    )

    assert response.status_code == 404

    other_list = client.get(
        "/api/v1/documents",
        headers=auth_headers(
            document_client["other_token"]
        ),
    )

    assert other_list.status_code == 200
    assert all(
        item["id"] != document_id
        for item in other_list.json()
    )
