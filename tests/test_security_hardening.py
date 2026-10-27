from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import (
    Settings,
    validate_production_settings,
)
from app.core.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitMiddleware,
)
from app.main import create_app


def test_rate_limiter_allows_requests():
    limiter = FixedWindowRateLimiter()

    for _ in range(3):
        allowed, retry_after = limiter.check(
            key="test-client:login",
            limit=3,
            window_seconds=60,
        )

        assert allowed is True
        assert retry_after == 0


def test_rate_limiter_blocks_excess():
    limiter = FixedWindowRateLimiter()

    for _ in range(2):
        allowed, _ = limiter.check(
            key="test-client:login",
            limit=2,
            window_seconds=60,
        )
        assert allowed is True

    allowed, retry_after = limiter.check(
        key="test-client:login",
        limit=2,
        window_seconds=60,
    )

    assert allowed is False
    assert retry_after >= 1


def test_rate_limiter_separates_clients():
    limiter = FixedWindowRateLimiter()

    assert limiter.check(
        "client-one:login", 1, 60
    )[0] is True

    assert limiter.check(
        "client-one:login", 1, 60
    )[0] is False

    assert limiter.check(
        "client-two:login", 1, 60
    )[0] is True


def test_sensitive_routes_are_classified():
    middleware = RateLimitMiddleware(
        app=lambda scope, receive, send: None,
        enabled=True,
        api_prefix="/api/v1",
        window_seconds=60,
        login_limit=10,
        register_limit=10,
        generation_limit=5,
    )

    assert middleware._category(
        "POST",
        "/api/v1/auth/login",
    ) == ("login", 10)

    assert middleware._category(
        "POST",
        "/api/v1/projects/123/generate",
    ) == ("generation", 5)

    assert middleware._category(
        "POST",
        "/api/v1/projects/123/audio/generate",
    ) == ("generation", 5)

    assert middleware._category(
        "POST",
        "/api/v1/projects/123/jobs",
    ) == ("generation", 5)

    assert middleware._category(
        "GET",
        "/api/v1/projects/123/jobs",
    ) is None


def test_security_headers_present():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200

    assert response.headers[
        "x-content-type-options"
    ] == "nosniff"

    assert response.headers[
        "x-frame-options"
    ] == "DENY"

    assert response.headers[
        "referrer-policy"
    ] == "no-referrer"


def test_production_rejects_debug():
    settings = Settings(
        APP_ENV="production",
        DEBUG=True,
    )

    try:
        validate_production_settings(settings)
    except RuntimeError as exc:
        assert "DEBUG" in str(exc)
    else:
        raise AssertionError(
            "Production DEBUG must be rejected."
        )


def test_production_rejects_wildcard_cors():
    settings = Settings(
        APP_ENV="production",
        DEBUG=False,
        DATABASE_URL=(
            "postgresql+psycopg://"
            "example:password@localhost/example"
        ),
        JWT_SECRET_KEY=SecretStr(
            "a" * 40
        ),
        CORS_ALLOWED_ORIGINS=["*"],
    )

    try:
        validate_production_settings(settings)
    except RuntimeError as exc:
        assert "CORS" in str(exc)
    else:
        raise AssertionError(
            "Wildcard CORS must be rejected."
        )


def test_login_rate_limit_returns_429(
    monkeypatch,
):
    from app.core import config

    settings = config.get_settings()

    monkeypatch.setattr(
        settings,
        "RATE_LIMIT_ENABLED",
        True,
    )
    monkeypatch.setattr(
        settings,
        "RATE_LIMIT_LOGIN",
        1,
    )

    app = create_app()

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            data={
                "username": "invalid@example.com",
                "password": "wrong",
            },
        )

        second = client.post(
            "/api/v1/auth/login",
            data={
                "username": "invalid@example.com",
                "password": "wrong",
            },
        )

    assert second.status_code == 429
    assert "retry-after" in second.headers


def test_login_rate_limit_returns_429():
    from fastapi import FastAPI

    test_app = FastAPI()

    @test_app.post("/api/v1/auth/login")
    def fake_login():
        return {"ok": True}

    test_app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        api_prefix="/api/v1",
        window_seconds=60,
        login_limit=1,
        register_limit=10,
        generation_limit=10,
    )

    with TestClient(test_app) as client:
        first = client.post(
            "/api/v1/auth/login"
        )
        second = client.post(
            "/api/v1/auth/login"
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert "retry-after" in second.headers
