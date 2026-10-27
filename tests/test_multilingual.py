import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes import generation
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.models import (
    AudioFormat,
    Document,
    ExtractionJob,
    Language,
    Narrator,
    Project,
    Role,
    SummaryType,
    User,
)
from app.main import app
from app.services.gemini_summarizer import (
    SourceSection,
    SummarizationError,
    SummaryResult,
    generate_script,
    get_language_instructions,
    validate_script_language,
)
from scripts.seed_project_options import seed_options


BANGLA_SCRIPT = (
    "বই পড়া আমাদের জ্ঞান বৃদ্ধি করে। "
    "এটি নতুন ধারণা বুঝতে সাহায্য করে।"
)


def test_english_language_instructions():
    instructions = get_language_instructions("en")

    assert "English" in instructions


def test_bangla_language_instructions():
    instructions = get_language_instructions("bn")

    assert "Bangla" in instructions
    assert "Bengali script" in instructions


def test_unsupported_language_rejected():
    with pytest.raises(
        SummarizationError,
        match="Unsupported output language",
    ):
        get_language_instructions("fr")


def test_bangla_script_validation():
    validate_script_language(
        BANGLA_SCRIPT,
        "bn",
    )

    with pytest.raises(
        SummarizationError,
        match="did not return a Bangla script",
    ):
        validate_script_language(
            "This is entirely in English.",
            "bn",
        )


def test_bangla_pipeline_without_api():
    prompts = []

    def fake_generator(prompt, max_output_tokens):
        prompts.append(prompt)

        if "TASK: FINAL_SCRIPT" in prompt:
            return BANGLA_SCRIPT

        return "The source explains why reading matters."

    result = generate_script(
        sections=[
            SourceSection(
                title="Chapter 1",
                text=(
                    "Reading promotes lifelong learning "
                    "and deeper understanding."
                ),
            )
        ],
        summary_type="10-Min Express",
        generator=fake_generator,
        language_code="bn",
    )

    assert result.script == BANGLA_SCRIPT
    assert result.chunks_processed == 1

    final_prompt = prompts[-1]

    assert "OUTPUT LANGUAGE: bn" in final_prompt
    assert "Bengali script" in final_prompt


@pytest.fixture
def multilingual_client(monkeypatch):
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

    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "GEMINI_API_KEY",
        SecretStr("test-key-not-used"),
    )

    def fake_summarizer(
        sections,
        summary_type,
        language_code="en",
    ):
        if language_code == "bn":
            text = BANGLA_SCRIPT
        else:
            text = "Reading is important for learning."

        return SummaryResult(
            script=text,
            chunks_processed=1,
        )

    monkeypatch.setattr(
        generation,
        "generate_script",
        fake_summarizer,
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

                seed_options(db)

                owner = User(
                    name="Multilingual Owner",
                    email="multilingual-owner@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other_user = User(
                    name="Multilingual Other",
                    email="multilingual-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([owner, other_user])
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="language-test.pdf",
                    file_type="pdf",
                    file_path=(
                        f"{owner.id}/language-test.pdf"
                    ),
                    file_size=100,
                    status="extracted",
                )

                db.add(document)
                db.flush()

                db.add(
                    ExtractionJob(
                        document_id=document.id,
                        extracted_text=(
                            "Reading helps us learn."
                        ),
                        cleaned_text=(
                            "Reading helps us learn."
                        ),
                        status="completed",
                    )
                )

                summary_type = db.scalar(
                    select(SummaryType).where(
                        SummaryType.name == "10-Min Express"
                    )
                )

                narrator = db.scalar(
                    select(Narrator).where(
                        Narrator.name == "James"
                    )
                )

                audio_format = db.scalar(
                    select(AudioFormat).where(
                        AudioFormat.name == "MP3"
                    )
                )

                languages = {
                    language.code: language
                    for language in db.scalars(
                        select(Language)
                    ).all()
                }

                projects = {}

                for code in ("en", "bn"):
                    project = Project(
                        user_id=owner.id,
                        document_id=document.id,
                        summary_type_id=summary_type.id,
                        narrator_id=narrator.id,
                        language_id=languages[code].id,
                        audio_format_id=audio_format.id,
                        status="pending",
                    )

                    db.add(project)
                    db.flush()

                    projects[code] = project.id

                db.commit()

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "projects": projects,
                        "token": create_access_token(
                            owner.id
                        ),
                        "other_token": create_access_token(
                            other_user.id
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


def auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
    }


def test_generate_bangla_project(
    multilingual_client,
):
    context = multilingual_client
    project_id = context["projects"]["bn"]

    response = context["client"].post(
        f"/api/v1/projects/{project_id}/generate",
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["language"] == "bn"
    assert data["status"] == "script_ready"

    stored = context["db"].get(
        Project,
        project_id,
    )

    context["db"].refresh(stored)

    assert stored.script == BANGLA_SCRIPT


def test_retrieve_bangla_script(
    multilingual_client,
):
    context = multilingual_client
    project_id = context["projects"]["bn"]

    context["client"].post(
        f"/api/v1/projects/{project_id}/generate",
        headers=auth_headers(context["token"]),
    )

    response = context["client"].get(
        f"/api/v1/projects/{project_id}/script",
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200
    assert response.json()["language"] == "bn"
    assert response.json()["script"] == BANGLA_SCRIPT


def test_english_generation_still_works(
    multilingual_client,
):
    context = multilingual_client
    project_id = context["projects"]["en"]

    response = context["client"].post(
        f"/api/v1/projects/{project_id}/generate",
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200
    assert response.json()["language"] == "en"


def test_other_user_cannot_read_bangla_script(
    multilingual_client,
):
    context = multilingual_client
    project_id = context["projects"]["bn"]

    response = context["client"].get(
        f"/api/v1/projects/{project_id}/script",
        headers=auth_headers(context["other_token"]),
    )

    assert response.status_code == 404
