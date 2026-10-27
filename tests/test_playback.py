import io
import os
import wave

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.security import create_access_token

from app.db.models import (
    AudioFormat,
    Document,
    GeneratedFile,
    Language,
    ListeningProgress,
    Narrator,
    Project,
    Role,
    SummaryType,
    User,
)

from app.main import app

from app.services.audio_streaming import (
    parse_byte_range,
)

from scripts.seed_project_options import seed_options


def make_test_wav() -> bytes:
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(
            b"\x00\x00" * 22050
        )

    return buffer.getvalue()


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
    }


@pytest.fixture
def playback_context(
    tmp_path,
    monkeypatch,
):
    url = os.getenv("TEST_DATABASE_URL")

    if not url or not url.startswith(
        "postgresql+psycopg://"
    ) or not url.endswith(
        "/sapio_test_db"
    ):
        pytest.fail(
            "TEST_DATABASE_URL must point to "
            "the separate sapio_test_db database."
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

                user = User(
                    name="Playback Test User",
                    email="playback-test@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                other = User(
                    name="Different Listener",
                    email="playback-other@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add_all([user, other])
                db.flush()

                document = Document(
                    user_id=user.id,
                    file_name="playback-book.pdf",
                    file_type="pdf",
                    file_path=f"{user.id}/playback-book.pdf",
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
                    user_id=user.id,
                    document_id=document.id,
                    summary_type_id=summary.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="completed",
                    script="Playback test narration.",
                    total_duration=60,
                )

                pending = Project(
                    user_id=user.id,
                    document_id=document.id,
                    summary_type_id=summary.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="script_ready",
                    script="Not generated yet.",
                )

                db.add_all([project, pending])
                db.flush()

                relative_path = (
                    f"{user.id}/{project.id}/sample.wav"
                )

                absolute_path = (
                    settings.AUDIO_STORAGE_ROOT
                    / relative_path
                )

                absolute_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                audio_bytes = make_test_wav()
                absolute_path.write_bytes(
                    audio_bytes
                )

                project.audio_file_path = relative_path

                db.add(
                    GeneratedFile(
                        project_id=project.id,
                        file_type="wav",
                        file_path=relative_path,
                        file_size=len(audio_bytes),
                    )
                )

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
                        "project_id": project.id,
                        "pending_id": pending.id,
                        "user_id": user.id,
                        "token": create_access_token(
                            user.id
                        ),
                        "other_token": create_access_token(
                            other.id
                        ),
                        "audio_bytes": audio_bytes,
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


def test_parse_byte_range():
    assert parse_byte_range(
        "bytes=0-9", 100
    ) == (0, 9)

    assert parse_byte_range(
        "bytes=90-", 100
    ) == (90, 99)

    assert parse_byte_range(
        "bytes=-10", 100
    ) == (90, 99)


def test_stream_requires_auth(playback_context):
    ctx = playback_context

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/audio/stream"
    )

    assert response.status_code == 401


def test_stream_complete_audio(playback_context):
    ctx = playback_context

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/audio/stream",
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 200
    assert response.content == ctx["audio_bytes"]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"].startswith(
        "audio/wav"
    )


def test_stream_partial_content(playback_context):
    ctx = playback_context

    request_headers = headers(ctx["token"])
    request_headers["Range"] = "bytes=0-99"

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/audio/stream",
        headers=request_headers,
    )

    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.content == (
        ctx["audio_bytes"][:100]
    )
    assert response.headers["content-range"].startswith(
        "bytes 0-99/"
    )


def test_stream_invalid_range(playback_context):
    ctx = playback_context

    request_headers = headers(ctx["token"])
    request_headers["Range"] = (
        "bytes=999999999-"
    )

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/audio/stream",
        headers=request_headers,
    )

    assert response.status_code == 416
    assert response.headers[
        "content-range"
    ].startswith("bytes */")


def test_other_user_cannot_stream(
    playback_context,
):
    ctx = playback_context

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/audio/stream",
        headers=headers(ctx["other_token"]),
    )

    assert response.status_code == 404


def test_playback_initial_position(
    playback_context,
):
    ctx = playback_context

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback",
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 200
    assert response.json()["position_seconds"] == 0
    assert response.json()["is_completed"] is False
    assert (
        response.json()["total_duration_seconds"]
        == 60
    )


def test_save_playback_position(
    playback_context,
):
    ctx = playback_context

    url = (
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback"
    )

    updated = ctx["client"].put(
        url,
        json={"position_seconds": 25},
        headers=headers(ctx["token"]),
    )

    assert updated.status_code == 200
    assert updated.json()["position_seconds"] == 25

    retrieved = ctx["client"].get(
        url,
        headers=headers(ctx["token"]),
    )

    assert retrieved.status_code == 200
    assert retrieved.json()["position_seconds"] == 25

    rows = ctx["db"].scalars(
        select(ListeningProgress).where(
            ListeningProgress.project_id
            == ctx["project_id"]
        )
    ).all()

    assert len(rows) == 1


def test_reject_position_beyond_duration(
    playback_context,
):
    ctx = playback_context

    response = ctx["client"].put(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback",
        json={"position_seconds": 100},
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 422


def test_mark_playback_completed(
    playback_context,
):
    ctx = playback_context

    response = ctx["client"].post(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback/complete",
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 200
    assert response.json()["is_completed"] is True
    assert response.json()["position_seconds"] == 60
    assert response.json()["completed_at"] is not None


def test_listening_history(playback_context):
    ctx = playback_context

    ctx["client"].put(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback",
        json={"position_seconds": 12},
        headers=headers(ctx["token"]),
    )

    response = ctx["client"].get(
        "/api/v1/me/listening-history",
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 200

    items = response.json()["items"]

    assert len(items) == 1
    assert items[0]["project_id"] == (
        ctx["project_id"]
    )
    assert items[0]["document_name"] == (
        "playback-book.pdf"
    )


def test_other_user_history_is_empty(
    playback_context,
):
    ctx = playback_context

    ctx["client"].put(
        f"/api/v1/projects/"
        f"{ctx['project_id']}/playback",
        json={"position_seconds": 15},
        headers=headers(ctx["token"]),
    )

    response = ctx["client"].get(
        "/api/v1/me/listening-history",
        headers=headers(ctx["other_token"]),
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_unfinished_audio_rejected(
    playback_context,
):
    ctx = playback_context

    response = ctx["client"].get(
        f"/api/v1/projects/"
        f"{ctx['pending_id']}/playback",
        headers=headers(ctx["token"]),
    )

    assert response.status_code == 409
