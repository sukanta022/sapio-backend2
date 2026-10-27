from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    audio,
    auth,
    chapters,
    documents,
    extraction,
    generation,
    health,
    jobs,
    playback,
    popular_summaries,
    popular_media,
    projects,
)

from app.core.config import (
    get_settings,
    validate_production_settings,
)
from app.core.logging import (
    get_logger,
    setup_logging,
)
from app.core.rate_limit import RateLimitMiddleware


def create_app() -> FastAPI:
    settings = get_settings()

    validate_production_settings(settings)
    setup_logging(settings.LOG_LEVEL)

    logger = get_logger("main")

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Backend API for the Sapio AI "
            "document-to-audio summary platform."
        ),
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
    )

    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.RATE_LIMIT_ENABLED,
        api_prefix=settings.API_V1_PREFIX,
        window_seconds=(
            settings.RATE_LIMIT_WINDOW_SECONDS
        ),
        login_limit=settings.RATE_LIMIT_LOGIN,
        register_limit=settings.RATE_LIMIT_REGISTER,
        generation_limit=(
            settings.RATE_LIMIT_GENERATION
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=(
            settings.CORS_ALLOW_CREDENTIALS
        ),
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Range",
        ],
        expose_headers=[
            "Accept-Ranges",
            "Content-Range",
            "Content-Length",
        ],
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next,
    ):
        response = await call_next(request)

        response.headers[
            "X-Content-Type-Options"
        ] = "nosniff"

        response.headers[
            "X-Frame-Options"
        ] = "DENY"

        response.headers[
            "Referrer-Policy"
        ] = "no-referrer"

        response.headers[
            "Permissions-Policy"
        ] = (
            "camera=(), "
            "microphone=(), "
            "geolocation=()"
        )

        if settings.APP_ENV == "production":
            response.headers[
                "Strict-Transport-Security"
            ] = "max-age=31536000"

        if request.url.path.startswith(
            f"{settings.API_V1_PREFIX}/auth/"
        ):
            response.headers[
                "Cache-Control"
            ] = "no-store"

        return response

    routers = [
        health.router,
        auth.router,
        admin.router,
        documents.router,
        extraction.router,
        chapters.router,
        projects.router,
        generation.router,
        audio.router,
        jobs.router,
        playback.router,
        popular_summaries.router,
        popular_media.router,
    ]

    for api_router in routers:
        app.include_router(
            api_router,
            prefix=settings.API_V1_PREFIX,
        )

    @app.get("/", tags=["Root"])
    def root():
        return {
            "message": (
                f"Welcome to {settings.APP_NAME}"
            ),
            "version": settings.APP_VERSION,
            "docs": app.docs_url,
        }

    logger.info(
        "Application initialized | "
        "environment=%s",
        settings.APP_ENV,
    )

    return app


app = create_app()
