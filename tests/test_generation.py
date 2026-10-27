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
    ProcessingLog,
    Project,
    Role,
    SummaryType,
    User,
)
from app.main import app
from app.services.gemini_summarizer import (
    SourceSection,
    SummarizationError,
    generate_script,
    split_text,
)
from scripts.seed_project_options import seed_options


def test_split_text():
    text = "A" * 2500

    chunks = split_text(text, 1000)

    assert len(chunks) == 3
    assert "".join(chunks) == text
    assert all(len(item) <= 1000 for item in chunks)


def test_empty_source_rejected():
    with pytest.raises(SummarizationError):
        split_text("   ", 1000)


def test_unsupported_summary_mode():
    with pytest.raises(SummarizationError):
        generate_script(
            [SourceSection("Book", "Example content")],
            "Unsupported Mode",
            generator=lambda prompt, limit: "text",
        )


def test_summary_pipeline_without_gemini():
    calls = []

    def fake_generator(prompt, max_output_tokens):
        calls.append(prompt)

        if "TASK: FINAL_SCRIPT" in prompt:
            return "This is the final generated narration."

        return "The section explains an important idea."

    result = generate_script(
        [
            SourceSection(
                title="Introduction",
                text="Sample book content " * 1000,
            )
        ],
        "10-Min Express",
        generator=fake_generator,
    )

    assert result.script == (
        "This is the final generated narration."
    )
    assert result.chunks_processed >= 2
    assert len(calls) == result.chunks_processed + 1


@pytest.fixture
def generation_client(monkeypatch):
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

    def fake_summarizer(sections, summary_type):
        return generate_script(
            sections,
            summary_type,
            generator=lambda prompt, limit: (
                "Generated final narration script."
                if "TASK: FINAL_SCRIPT" in prompt
                else "Important source facts and ideas."
            ),
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
                    name="Generation Owner",
                    email="generation-owner@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other = User(
                    name="Another User",
                    email="generation-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([owner, other])
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="generation-test.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/generation-test.pdf",
                    file_size=100,
                    status="extracted",
                )

                db.add(document)
                db.flush()

                extraction = ExtractionJob(
                    document_id=document.id,
                    extracted_text="A useful book about learning.",
                    cleaned_text="A useful book about learning.",
                    status="completed",
                )

                db.add(extraction)

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

                project = Project(
                    user_id=owner.id,
                    document_id=document.id,
                    summary_type_id=summary_type.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="pending",
                )

                db.add(project)
                db.commit()
                db.refresh(project)

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "project_id": project.id,
                        "project": project,
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


def auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
    }


def generate_url(project_id):
    return f"/api/v1/projects/{project_id}/generate"


def test_generation_requires_authentication(
    generation_client,
):
    response = generation_client["client"].post(
        generate_url(generation_client["project_id"])
    )

    assert response.status_code == 401


def test_other_user_cannot_generate(
    generation_client,
):
    response = generation_client["client"].post(
        generate_url(generation_client["project_id"]),
        headers=auth_headers(
            generation_client["other_token"]
        ),
    )

    assert response.status_code == 404


def test_generate_and_store_script(generation_client):
    context = generation_client

    response = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "script_ready"
    assert data["script_length"] > 0
    assert data["chunks_processed"] == 1

    context["db"].refresh(context["project"])

    assert context["project"].status == "script_ready"
    assert context["project"].script == (
        "Generated final narration script."
    )

    logs = context["db"].scalars(
        select(ProcessingLog).where(
            ProcessingLog.project_id
            == context["project_id"]
        )
    ).all()

    assert len(logs) == 2


def test_get_generated_script(generation_client):
    context = generation_client

    generated = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    assert generated.status_code == 200

    response = context["client"].get(
        (
            f"/api/v1/projects/"
            f"{context['project_id']}/script"
        ),
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200
    assert response.json()["script"] == (
        "Generated final narration script."
    )


def test_script_not_ready(generation_client):
    context = generation_client

    response = context["client"].get(
        (
            f"/api/v1/projects/"
            f"{context['project_id']}/script"
        ),
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 409


def test_duplicate_generation_rejected(
    generation_client,
):
    context = generation_client

    first = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    second = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_generation_failure_is_recorded(
    generation_client,
    monkeypatch,
):
    context = generation_client

    def broken_generator(sections, summary_type):
        raise SummarizationError(
            "Simulated provider failure"
        )

    monkeypatch.setattr(
        generation,
        "generate_script",
        broken_generator,
    )

    response = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 502

    context["db"].refresh(context["project"])

    assert context["project"].status == "failed"
    assert context["project"].script is None


def test_document_chunk_limit_returns_422(
    generation_client,
    monkeypatch,
):
    context = generation_client

    def oversized_document(sections, summary_type):
        raise SummarizationError(
            "Document exceeds the configured "
            "Gemini processing limit."
        )

    monkeypatch.setattr(
        generation,
        "generate_script",
        oversized_document,
    )

    response = context["client"].post(
        generate_url(context["project_id"]),
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 422

    assert response.json() == {
        "detail": (
            "Document exceeds the configured "
            "Gemini processing limit."
        )
    }

    context["db"].refresh(context["project"])

    assert context["project"].status == "failed"

