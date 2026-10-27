
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging


def test_settings_defaults():
    settings = Settings(
        _env_file=None,
        APP_NAME="Sapio AI Backend",
        APP_VERSION="0.1.0",
        APP_ENV="development",
        DEBUG=False,
        API_V1_PREFIX="/api/v1",
        LOG_LEVEL="INFO",
    )

    assert settings.APP_NAME == "Sapio AI Backend"
    assert settings.APP_VERSION == "0.1.0"
    assert settings.APP_ENV == "development"
    assert settings.DEBUG is False
    assert settings.API_V1_PREFIX == "/api/v1"
    assert settings.LOG_LEVEL == "INFO"



def test_settings_load_from_env_file(
    tmp_path,
    monkeypatch,
):
    env_file = tmp_path / ".env"

    env_file.write_text(
        "APP_NAME=Sapio Test Backend\n"
        "APP_VERSION=0.2.0\n"
        "APP_ENV=testing\n"
        "DEBUG=true\n"
        "API_V1_PREFIX=/api/test\n"
        "LOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )

    # Remove environment variables that could
    # override this temporary .env test file.
    fields = [
        "APP_NAME",
        "APP_VERSION",
        "APP_ENV",
        "DEBUG",
        "API_V1_PREFIX",
        "LOG_LEVEL",
    ]

    for field in fields:
        monkeypatch.delenv(
            field,
            raising=False,
        )

    settings = Settings(_env_file=env_file)

    assert settings.APP_NAME == "Sapio Test Backend"
    assert settings.APP_VERSION == "0.2.0"
    assert settings.APP_ENV == "testing"
    assert settings.DEBUG is True
    assert settings.API_V1_PREFIX == "/api/test"
    assert settings.LOG_LEVEL == "DEBUG"


def test_environment_variable_overrides_env_file(
    tmp_path,
    monkeypatch,
):
    env_file = tmp_path / ".env"

    env_file.write_text(
        "APP_NAME=Name From File\n",
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "APP_NAME",
        "Name From Environment",
    )

    settings = Settings(_env_file=env_file)

    assert settings.APP_NAME == "Name From Environment"


def test_invalid_environment_raises_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="invalid-environment",
        )


def test_invalid_log_level_raises_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            LOG_LEVEL="VERBOSE",
        )


def test_invalid_api_prefix_raises_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            API_V1_PREFIX="api/v1",
        )


def test_settings_are_cached():
    first = get_settings()
    second = get_settings()

    assert first is second



def test_logger_configuration():
    setup_logging("DEBUG")
    setup_logging("DEBUG")

    logger = get_logger("test")

    assert logger.name == "sapio.test"

    parent_logger = logging.getLogger("sapio")

    assert parent_logger.level == logging.DEBUG

    sapio_handlers = [
        handler
        for handler in parent_logger.handlers
        if handler.get_name() == "sapio_console_handler"
    ]

    assert len(sapio_handlers) == 1

