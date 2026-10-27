
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import (
    create_access_token,
    verify_password,
)
from app.db.models import Role, User
from app.main import app


@pytest.fixture
def client():
    test_database_url = os.getenv(
        "TEST_DATABASE_URL"
    )

    if not test_database_url:
        pytest.fail(
            "TEST_DATABASE_URL is required. "
            "Configure a separate PostgreSQL "
            "test database before running tests."
        )

    if not test_database_url.startswith(
        "postgresql+psycopg://"
    ):
        pytest.fail(
            "TEST_DATABASE_URL must use psycopg."
        )

    if not test_database_url.endswith(
        "/sapio_test_db"
    ):
        pytest.fail(
            "Tests must target sapio_test_db."
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
                user_role = db.scalar(
                    select(Role).where(
                        Role.name == "user"
                    )
                )

                admin_role = db.scalar(
                    select(Role).where(
                        Role.name == "admin"
                    )
                )

                if user_role is None:
                    db.add(
                        Role(
                            name="user",
                            description="Standard user",
                        )
                    )

                if admin_role is None:
                    db.add(
                        Role(
                            name="admin",
                            description="Administrator",
                        )
                    )

                db.commit()

                with TestClient(app) as test_client:
                    yield test_client

            finally:
                app.dependency_overrides.pop(
                    get_db,
                    None,
                )
                db.close()
                transaction.rollback()
    finally:
        engine.dispose()


def register_test_user(client):
    return client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test User",
            "email": "test@example.com",
            "password": "StrongPassword123!",
        },
    )


def login_test_user(client):
    return client.post(
        "/api/v1/auth/login",
        data={
            "username": "test@example.com",
            "password": "StrongPassword123!",
        },
    )


def test_register_user(client):
    response = register_test_user(client)

    assert response.status_code == 201

    data = response.json()

    assert data["email"] == "test@example.com"
    assert data["role"] == "user"
    assert data["is_active"] is True
    assert "password_hash" not in data


def test_duplicate_registration(client):
    first = register_test_user(client)
    second = register_test_user(client)

    assert first.status_code == 201
    assert second.status_code == 409


def test_login_success(client):
    register_test_user(client)

    response = login_test_user(client)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


def test_login_invalid_password(client):
    register_test_user(client)

    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": "test@example.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401


def test_me_without_token(client):
    response = client.get(
        "/api/v1/auth/me"
    )

    assert response.status_code == 401


def test_me_with_token(client):
    register_test_user(client)

    token = login_test_user(
        client
    ).json()["access_token"]

    response = client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200
    assert response.json()["email"] == (
        "test@example.com"
    )


def test_standard_user_denied_admin(client):
    register_test_user(client)

    token = login_test_user(
        client
    ).json()["access_token"]

    response = client.get(
        "/api/v1/admin/ping",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 403


def test_admin_access(client):
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)

    try:
        role = db.scalar(
            select(Role).where(
                Role.name == "admin"
            )
        )

        admin = User(
            name="Test Admin",
            email="admin@example.com",
            password_hash="not-used-in-this-test",
            role_id=role.id,
            is_active=True,
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        token = create_access_token(admin.id)

        response = client.get(
            "/api/v1/admin/ping",
            headers={
                "Authorization": f"Bearer {token}",
            },
        )

        assert response.status_code == 200
        assert response.json()["admin_id"] == admin.id
    finally:
        db_generator.close()


def test_disabled_user_denied(client):
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)

    try:
        role = db.scalar(
            select(Role).where(
                Role.name == "user"
            )
        )

        user = User(
            name="Disabled User",
            email="disabled@example.com",
            password_hash="not-used-in-this-test",
            role_id=role.id,
            is_active=False,
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user.id)

        response = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
            },
        )

        assert response.status_code == 403
    finally:
        db_generator.close()


def test_password_hashing():
    from app.core.security import hash_password

    original = "StrongPassword123!"
    hashed = hash_password(original)

    assert hashed != original
    assert verify_password(original, hashed)
    assert not verify_password(
        "WrongPassword123!",
        hashed,
    )
