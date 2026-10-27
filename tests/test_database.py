
from sqlalchemy import inspect, text

from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import get_db, get_engine


EXPECTED_TABLES = {
    "roles",
    "users",
    "documents",
    "extraction_jobs",
    "chapters",
    "summary_types",
    "narrators",
    "languages",
    "audio_formats",
    "projects",
    "files",
    "project_chapters",
    "popular_summaries",
    "translations",
    "processing_logs",
    "listening_progress",
}


def test_all_models_registered():
    actual_tables = set(Base.metadata.tables.keys())

    assert actual_tables == EXPECTED_TABLES


def test_primary_keys_defined():
    for table in Base.metadata.tables.values():
        primary_key_columns = list(
            table.primary_key.columns
        )

        assert len(primary_key_columns) == 1
        assert primary_key_columns[0].name == "id"


def test_important_foreign_keys():
    users = Base.metadata.tables["users"]
    projects = Base.metadata.tables["projects"]

    user_fks = {
        fk.target_fullname
        for fk in users.foreign_keys
    }

    assert "roles.id" in user_fks

    project_fks = {
        fk.target_fullname
        for fk in projects.foreign_keys
    }

    assert project_fks == {
        "users.id",
        "documents.id",
        "summary_types.id",
        "narrators.id",
        "languages.id",
        "audio_formats.id",
    }


def test_postgresql_connection():
    engine = get_engine()

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT current_database()")
        )

        assert result.scalar_one() == "sapio_db"


def test_database_tables_created():
    engine = get_engine()

    inspector = inspect(engine)

    actual_tables = set(
        inspector.get_table_names()
    )

    assert EXPECTED_TABLES.issubset(actual_tables)
    assert "alembic_version" in actual_tables


def test_database_session():
    session_generator = get_db()

    try:
        db = next(session_generator)

        result = db.execute(text("SELECT 1"))

        assert result.scalar_one() == 1
    finally:
        session_generator.close()
