
import io
import os
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.services.job_queue import create_job
from app.api.deps import get_db
from app.api.routes import audio
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.models import (
    AudioFormat,
    Document,
    GeneratedFile,
    Language,
    Narrator,
    Project,
    Role,
    SummaryType,
    User,
)
from app.main import app
from app.services.tts_service import (
    TTSLimitError,
    TTSError,
    combine_wav_segments,
    generate_audio,
    get_voice,
    split_tts_text,
)
from scripts.seed_project_options import seed_options


def fake_piper(
    text,
    model_path,
    speaker_id,
):
    """Produce valid WAV audio without loading Piper."""
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(
            b"\x00\x00" * 2205
        )

    return buffer.getvalue()


def test_english_chunking():
    chunks = split_tts_text(
        "Reading helps us learn. " * 100,
        100,
    )

    assert len(chunks) > 1
    assert all(len(item) <= 100 for item in chunks)


def test_bangla_chunking():
    chunks = split_tts_text(
        "বাংলা ভাষায় বই পড়া আনন্দের। " * 40,
        80,
    )

    assert len(chunks) > 1
    assert all(len(item) <= 80 for item in chunks)


def test_narrator_mapping():
    assert get_voice("en", "James") == (
        "en_US-lessac-medium",
        0,
    )

    assert get_voice("bn", "Elena") == (
        "bn_BD-google-medium",
        1,
    )


def test_invalid_narrator_rejected():
    with pytest.raises(
        TTSError,
        match="Unsupported narrator",
    ):
        get_voice("en", "Unknown")


def test_combine_wav(tmp_path):
    destination = tmp_path / "combined.wav"

    duration = combine_wav_segments(
        [
            fake_piper("one", None, 0),
            fake_piper("two", None, 0),
        ],
        destination,
    )

    assert destination.is_file()
    assert duration == pytest.approx(0.2)


def test_generate_wav_offline(
    tmp_path,
    monkeypatch,
):
    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "AUDIO_STORAGE_ROOT",
        tmp_path / "audio",
    )

    result = generate_audio(
        script="Welcome to Sapio AI.",
        user_id=1,
        project_id=2,
        language_code="en",
        narrator_name="James",
        audio_format="WAV",
        synthesizer=fake_piper,
        validate_voice=False,
    )

    path = (
        settings.AUDIO_STORAGE_ROOT
        / result.relative_path
    )

    assert path.is_file()
    assert result.file_size > 0
    assert result.chunks_processed == 1

    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1


def test_generate_bangla_wav_offline(
    tmp_path,
    monkeypatch,
):
    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "AUDIO_STORAGE_ROOT",
        tmp_path / "audio",
    )

    result = generate_audio(
        script="বাংলা ভাষায় একটি বইয়ের সারাংশ।",
        user_id=1,
        project_id=3,
        language_code="bn",
        narrator_name="James",
        audio_format="WAV",
        synthesizer=fake_piper,
        validate_voice=False,
    )

    assert result.file_size > 0


def test_tts_limit(
    tmp_path,
    monkeypatch,
):
    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "PIPER_MAX_CHARS",
        50,
    )
    monkeypatch.setattr(
        settings,
        "PIPER_MAX_CHUNKS",
        1,
    )

    with pytest.raises(
        TTSLimitError,
        match="Script exceeds",
    ):
        generate_audio(
            script="Reading is important. " * 40,
            user_id=1,
            project_id=1,
            language_code="en",
            narrator_name="James",
            audio_format="WAV",
            synthesizer=fake_piper,
            validate_voice=False,
        )


@pytest.fixture
def audio_client(monkeypatch, tmp_path):
    url = os.getenv("TEST_DATABASE_URL")

    if (
        not url
        or not url.startswith(
            "postgresql+psycopg://"
        )
        or not url.endswith("/sapio_test_db")
    ):
        pytest.fail(
            "TEST_DATABASE_URL must point to "
            "sapio_test_db."
        )

    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "AUDIO_STORAGE_ROOT",
        tmp_path / "audio",
    )

    engine = create_engine(url)

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
                    name="Audio Test Owner",
                    email="piper-owner@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other = User(
                    name="Audio Other User",
                    email="piper-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([owner, other])
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="test.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/test.pdf",
                    file_size=100,
                    status="extracted",
                )

                db.add(document)
                db.flush()

                summary = db.scalar(
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
                        AudioFormat.name == "WAV"
                    )
                )

                project = Project(
                    user_id=owner.id,
                    document_id=document.id,
                    summary_type_id=summary.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="script_ready",
                    script="Welcome to Sapio AI.",
                )

                db.add(project)
                db.commit()
                db.refresh(project)

                real_generate_audio = generate_audio

                def fake_generate_audio(**kwargs):
                    return real_generate_audio(
                        **kwargs,
                        synthesizer=fake_piper,
                        validate_voice=False,
                    )

                monkeypatch.setattr(
                    audio,
                    "generate_audio",
                    fake_generate_audio,
                )

                with TestClient(app) as client:
                    yield {
                        "client": client,
                        "db": db,
                        "project_id": project.id,
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
        "Authorization": f"Bearer {token}"
    }


def test_audio_auth_required(audio_client):
    project_id = audio_client["project_id"]

    response = audio_client["client"].post(
        f"/api/v1/projects/{project_id}/audio/generate"
    )

    assert response.status_code == 401


def test_audio_ownership(audio_client):
    project_id = audio_client["project_id"]

    response = audio_client["client"].post(
        f"/api/v1/projects/{project_id}/audio/generate",
        headers=auth_headers(
            audio_client["other_token"]
        ),
    )

    assert response.status_code == 404


def test_generate_store_and_download_audio(
    audio_client,
):
    context = audio_client
    project_id = context["project_id"]
    headers = auth_headers(context["token"])

    response = context["client"].post(
        f"/api/v1/projects/{project_id}/audio/generate",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["audio_format"] == "WAV"

    record = context["db"].scalar(
        select(GeneratedFile).where(
            GeneratedFile.project_id == project_id
        )
    )

    assert record is not None
    assert record.file_type == "wav"

    metadata = context["client"].get(
        f"/api/v1/projects/{project_id}/audio",
        headers=headers,
    )

    assert metadata.status_code == 200

    downloaded = context["client"].get(
        f"/api/v1/projects/{project_id}/audio/download",
        headers=headers,
    )

    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"RIFF")

    duplicate = context["client"].post(
        f"/api/v1/projects/{project_id}/audio/generate",
        headers=headers,
    )

    assert duplicate.status_code == 409


def test_other_user_cannot_download(
    audio_client,
):
    context = audio_client
    project_id = context["project_id"]

    response = context["client"].post(
        f"/api/v1/projects/{project_id}/audio/generate",
        headers=auth_headers(context["token"]),
    )

    assert response.status_code == 200

    other_response = context["client"].get(
        f"/api/v1/projects/{project_id}/audio/download",
        headers=auth_headers(
            context["other_token"]
        ),
    )

    assert other_response.status_code == 404


def test_audio_endpoint_rejects_active_job(
    audio_client,
):
    context = audio_client
    db = context["db"]
    project_id = context["project_id"]

    job = create_job(
        db,
        project_id,
        "audio",
    )
    db.commit()

    response = context["client"].post(
        (
            f"/api/v1/projects/{project_id}"
            "/audio/generate"
        ),
        headers=auth_headers(
            context["token"]
        ),
    )

    assert response.status_code == 409
    assert "background job" in (
        response.json()["detail"].lower()
    )


def test_cancel_job_through_api(
    audio_client,
):
    context = audio_client
    db = context["db"]
    project_id = context["project_id"]

    job = create_job(
        db,
        project_id,
        "audio",
    )
    db.commit()

    response = context["client"].post(
        f"/api/v1/jobs/{job.id}/cancel",
        headers=auth_headers(
            context["token"]
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    second_response = context["client"].post(
        f"/api/v1/jobs/{job.id}/cancel",
        headers=auth_headers(
            context["token"]
        ),
    )

    assert second_response.status_code == 409
