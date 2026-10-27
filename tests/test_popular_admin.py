import os
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.models import (
    Language,
    Narrator,
    PopularSummary,
    Role,
    User,
)
from app.db.session import get_db
from app.main import app


@pytest.fixture
def context():
    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is required."
        )

    parsed = make_url(url)

    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.database != "sapio_test_db"
    ):
        pytest.fail(
            "Tests must use sapio_test_db."
        )

    engine = create_engine(url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            db = Session(
                bind=connection,
                join_transaction_mode=(
                    "create_savepoint"
                ),
                expire_on_commit=False,
            )

            try:
                admin_role = db.scalar(
                    select(Role).where(
                        Role.name == "admin"
                    )
                )

                user_role = db.scalar(
                    select(Role).where(
                        Role.name == "user"
                    )
                )

                if admin_role is None:
                    admin_role = Role(
                        name="admin",
                        description="Administrator",
                    )
                    db.add(admin_role)

                if user_role is None:
                    user_role = Role(
                        name="user",
                        description="Standard user",
                    )
                    db.add(user_role)

                db.flush()

                unique_id = uuid4().hex[:12]

                admin_user = User(
                    name="Test Admin",
                    email=(
                        f"phase-admin-{unique_id}"
                        "@example.com"
                    ),
                    password_hash="not-used-in-test",
                    role_id=admin_role.id,
                    is_active=True,
                )

                standard_user = User(
                    name="Test User",
                    email=(
                        f"phase-user-{unique_id}"
                        "@example.com"
                    ),
                    password_hash="not-used-in-test",
                    role_id=user_role.id,
                    is_active=True,
                )

                language = Language(
                    name=f"Test English {unique_id}",
                    code=f"t{unique_id[:7]}",
                )

                narrator = Narrator(
                    name=f"Test Voice {unique_id}",
                    voice_id=f"test-{unique_id}",
                )

                db.add_all([
                    admin_user,
                    standard_user,
                    language,
                    narrator,
                ])

                db.flush()

                ids = {
                    "admin_id": admin_user.id,
                    "user_id": standard_user.id,
                    "language_id": language.id,
                    "narrator_id": narrator.id,
                }

                db.commit()

                def override_get_db():
                    yield db

                app.dependency_overrides[get_db] = (
                    override_get_db
                )

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        **ids,
                        "admin_headers": {
                            "Authorization": (
                                "Bearer "
                                + create_access_token(
                                    ids["admin_id"]
                                )
                            ),
                        },
                        "user_headers": {
                            "Authorization": (
                                "Bearer "
                                + create_access_token(
                                    ids["user_id"]
                                )
                            ),
                        },
                    }

            finally:
                app.dependency_overrides.pop(
                    get_db, None
                )

                db.close()
                transaction.rollback()

    finally:
        engine.dispose()


def create_summary(context):
    response = context["client"].post(
        "/api/v1/popular-summaries",
        headers=context["admin_headers"],
        json={
            "title": "Introduction to Science",
            "description": "Test popular summary",
            "language_id": context["language_id"],
            "narrator_id": context["narrator_id"],
        },
    )

    assert response.status_code == 201
    return response.json()["id"]


def test_admin_can_create_summary(context):
    summary_id = create_summary(context)

    record = context["db"].get(
        PopularSummary, summary_id
    )

    assert record is not None
    assert record.is_published is False


def test_standard_user_cannot_create(context):
    response = context["client"].post(
        "/api/v1/popular-summaries",
        headers=context["user_headers"],
        json={
            "title": "Forbidden",
            "language_id": context["language_id"],
            "narrator_id": context["narrator_id"],
        },
    )

    assert response.status_code == 403


def test_unpublished_summary_is_hidden(context):
    summary_id = create_summary(context)

    response = context["client"].get(
        f"/api/v1/popular-summaries/{summary_id}"
    )

    assert response.status_code == 404


def test_publish_and_read_summary(context):
    summary_id = create_summary(context)

    published = context["client"].patch(
        (
            f"/api/v1/popular-summaries/"
            f"{summary_id}/publish"
        ),
        headers=context["admin_headers"],
        json={"is_published": True},
    )

    assert published.status_code == 200

    public_response = context["client"].get(
        f"/api/v1/popular-summaries/{summary_id}"
    )

    assert public_response.status_code == 200
    assert public_response.json()["is_published"] is True


def test_translation_upsert(context):
    summary_id = create_summary(context)

    url = (
        f"/api/v1/popular-summaries/"
        f"{summary_id}/translations"
    )

    first = context["client"].put(
        url,
        headers=context["admin_headers"],
        json={
            "language_id": context["language_id"],
            "translated_title": "First translation",
        },
    )

    assert first.status_code == 200

    second = context["client"].put(
        url,
        headers=context["admin_headers"],
        json={
            "language_id": context["language_id"],
            "translated_title": "Updated translation",
        },
    )

    assert second.status_code == 200
    assert (
        second.json()["translated_title"]
        == "Updated translation"
    )


def test_admin_can_disable_standard_user(context):
    response = context["client"].patch(
        (
            f"/api/v1/admin/users/"
            f"{context['user_id']}/status"
        ),
        headers=context["admin_headers"],
        json={"is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_admin_cannot_disable_self(context):
    response = context["client"].patch(
        (
            f"/api/v1/admin/users/"
            f"{context['admin_id']}/status"
        ),
        headers=context["admin_headers"],
        json={"is_active": False},
    )

    assert response.status_code == 409


def test_standard_user_cannot_read_analytics(
    context,
):
    response = context["client"].get(
        "/api/v1/admin/analytics",
        headers=context["user_headers"],
    )

    assert response.status_code == 403


def test_admin_analytics(context):
    response = context["client"].get(
        "/api/v1/admin/analytics",
        headers=context["admin_headers"],
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total_users"] >= 2
    assert "total_projects" in data
    assert "background_job_statuses" in data


def test_admin_user_listing(context):
    response = context["client"].get(
        "/api/v1/admin/users",
        headers=context["admin_headers"],
    )

    assert response.status_code == 200
    assert response.json()["total"] >= 2


def test_delete_summary_removes_translations(
    context,
):
    summary_id = create_summary(context)

    context["client"].put(
        (
            f"/api/v1/popular-summaries/"
            f"{summary_id}/translations"
        ),
        headers=context["admin_headers"],
        json={
            "language_id": context["language_id"],
            "translated_title": "Temporary translation",
        },
    )

    deleted = context["client"].delete(
        f"/api/v1/popular-summaries/{summary_id}",
        headers=context["admin_headers"],
    )

    assert deleted.status_code == 204

    assert context["db"].get(
        PopularSummary, summary_id
    ) is None
