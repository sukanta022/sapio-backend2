from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    APP_NAME: str = "Sapio AI Backend"
    APP_VERSION: str = "0.1.0"

    APP_ENV: Literal[
        "development",
        "testing",
        "production",
    ] = "development"

    DEBUG: bool = False

    API_V1_PREFIX: str = Field(
        default="/api/v1",
        pattern=r"^/[a-zA-Z0-9/_-]+$",
    )

    LOG_LEVEL: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    DATABASE_URL: str | None = None

    JWT_SECRET_KEY: SecretStr | None = None
    JWT_ALGORITHM: Literal["HS256"] = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        ge=1,
        le=1440,
    )

    STORAGE_ROOT: Path = Path("storage/uploads")

    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    AWS_ENDPOINT_URL_S3: str | None = None
    AWS_REGION: str | None = None
    AWS_ACCESS_KEY_ID: SecretStr | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    S3_BUCKET: str = "uploads"

    MAX_UPLOAD_SIZE_MB: int = Field(
        default=20,
        ge=1,
        le=100,
    )

    MAX_EXTRACTED_CHARACTERS: int = Field(
        default=3_000_000,
        ge=1_000,
        le=20_000_000,
    )

    MAX_PDF_PAGES: int = Field(
        default=1000,
        ge=1,
        le=5000,
    )

    MAX_EPUB_UNCOMPRESSED_MB: int = Field(
        default=50,
        ge=1,
        le=200,
    )

    GEMINI_API_KEY: SecretStr | None = None

    GEMINI_MODEL: str = Field(
        default="gemini-3.5-flash-lite",
        min_length=3,
    )

    GEMINI_CHUNK_SIZE: int = Field(
        default=12000,
        ge=1000,
        le=50000,
    )

    GEMINI_MAX_CHUNKS: int = Field(
        default=60,
        ge=1,
        le=300,
    )

    GEMINI_MAX_OUTPUT_TOKENS: int = Field(
        default=16000,
        ge=1024,
        le=65536,
    )

    AUDIO_STORAGE_ROOT: Path = Path(
        "storage/audio"
    )

    PIPER_MODELS_DIR: Path = Path(
        "models/piper"
    )

    PIPER_MAX_CHARS: int = Field(
        default=900,
        ge=100,
        le=3000,
    )

    PIPER_MAX_CHUNKS: int = Field(
        default=100,
        ge=1,
        le=500,
    )

    FFMPEG_EXECUTABLE: str = "ffmpeg"

    # Phase 14: explicit frontend origins.
    # Environment values must be JSON arrays.
    CORS_ALLOWED_ORIGINS: list[str] = Field(
        default_factory=list
    )

    # Do not enable credentialed CORS unless
    # required for a separately designed cookie flow.
    CORS_ALLOW_CREDENTIALS: bool = False

    # Phase 14: fixed-window limits per client IP.
    RATE_LIMIT_ENABLED: bool = True

    RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        ge=1,
        le=3600,
    )

    RATE_LIMIT_LOGIN: int = Field(
        default=120,
        ge=1,
        le=10000,
    )

    RATE_LIMIT_REGISTER: int = Field(
        default=60,
        ge=1,
        le=10000,
    )

    RATE_LIMIT_GENERATION: int = Field(
        default=40,
        ge=1,
        le=10000,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_database_url() -> str:
    database_url = get_settings().DATABASE_URL

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured."
        )

    if not database_url.startswith(
        "postgresql+psycopg://"
    ):
        raise RuntimeError(
            "DATABASE_URL must use the "
            "postgresql+psycopg driver."
        )

    return database_url


def get_jwt_secret() -> str:
    secret = get_settings().JWT_SECRET_KEY

    if secret is None:
        raise RuntimeError(
            "JWT_SECRET_KEY is not configured."
        )

    value = secret.get_secret_value()

    if len(value.encode("utf-8")) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY must contain "
            "at least 32 UTF-8 bytes."
        )

    return value


def get_gemini_api_key() -> str:
    secret = get_settings().GEMINI_API_KEY

    if secret is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    value = secret.get_secret_value().strip()

    if (
        not value
        or value == "PASTE_YOUR_REAL_GEMINI_API_KEY_HERE"
        or value == "YOUR_GEMINI_API_KEY"
    ):
        raise RuntimeError(
            "Configure a valid GEMINI_API_KEY."
        )

    return value


def validate_production_settings(
    settings: Settings,
) -> None:
    """
    Validate security-critical production settings.

    Called when the FastAPI application is created.
    """
    if settings.APP_ENV != "production":
        return

    if settings.DEBUG:
        raise RuntimeError(
            "DEBUG must be false in production."
        )

    if not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required in production."
        )

    if not settings.DATABASE_URL.startswith(
        "postgresql+psycopg://"
    ):
        raise RuntimeError(
            "Production requires the psycopg "
            "PostgreSQL driver."
        )

    if settings.JWT_SECRET_KEY is None:
        raise RuntimeError(
            "JWT_SECRET_KEY is required in production."
        )

    if len(
        settings.JWT_SECRET_KEY
        .get_secret_value()
        .encode("utf-8")
    ) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY is too short."
        )

    origins = settings.CORS_ALLOWED_ORIGINS

    if any(
        origin == "*"
        or not origin.startswith(
            ("https://", "http://")
        )
        for origin in origins
    ):
        raise RuntimeError(
            "CORS origins must be explicit "
            "HTTP or HTTPS origins."
        )

    if any(
        origin.startswith("http://")
        and not origin.startswith(
            (
                "http://localhost:",
                "http://127.0.0.1:",
            )
        )
        for origin in origins
    ):
        raise RuntimeError(
            "Non-local production CORS origins "
            "must use HTTPS."
        )

    if not settings.RATE_LIMIT_ENABLED:
        raise RuntimeError(
            "Rate limiting must be enabled "
            "in production."
        )
