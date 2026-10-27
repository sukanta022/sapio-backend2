import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

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
from app.services.gemini_summarizer import (
    SummarizationError,
    SummaryResult,
)
from app.services.job_queue import (
    create_job,
    get_job,
    worker_owns_job,
    heartbeat_job,
)

from app.services.tts_service import AudioResult
from app.workers.job_worker import process_one_job
from scripts.seed_project_options import seed_options


@pytest.fixture
def worker_context():
    url = os.getenv("TEST_DATABASE_URL")

    if (
        not url
        or not url.startswith("postgresql+psycopg://")
        or not url.endswith("/sapio_test_db")
    ):
        pytest.fail(
            "TEST_DATABASE_URL must point to "
            "the separate sapio_test_db database."
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

                owner = User(
                    name="Worker Test User",
                    email="worker-test@example.com",
                    password_hash="test-only-hash",
                    role_id=role.id,
                    is_active=True,
                )

                db.add(owner)
                db.flush()

                document = Document(
                    user_id=owner.id,
                    file_name="worker-test.pdf",
                    file_type="pdf",
                    file_path=f"{owner.id}/worker-test.pdf",
                    file_size=100,
                    status="extracted",
                )

                db.add(document)
                db.flush()

                db.add(
                    ExtractionJob(
                        document_id=document.id,
                        extracted_text=(
                            "Books help us learn."
                        ),
                        cleaned_text=(
                            "Books help us learn."
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

                summary_project = Project(
                    user_id=owner.id,
                    document_id=document.id,
                    summary_type_id=summary_type.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="pending",
                )

                audio_project = Project(
                    user_id=owner.id,
                    document_id=document.id,
                    summary_type_id=summary_type.id,
                    narrator_id=narrator.id,
                    language_id=language.id,
                    audio_format_id=audio_format.id,
                    status="script_ready",
                    script="Welcome to Sapio AI.",
                )

                db.add_all([
                    summary_project,
                    audio_project,
                ])

                db.commit()

                yield {
                    "db": db,
                    "user_id": owner.id,
                    "summary_project_id": (
                        summary_project.id
                    ),
                    "audio_project_id": (
                        audio_project.id
                    ),
                }

            finally:
                db.close()
                transaction.rollback()

    finally:
        engine.dispose()


def fake_summary(
    sections,
    summary_type,
):
    assert sections
    assert summary_type == "10-Min Express"

    return SummaryResult(
        script="Reading helps people learn.",
        chunks_processed=1,
    )


def fake_audio(**kwargs):
    assert kwargs["script"]

    return AudioResult(
        relative_path="worker-test/audio.wav",
        file_size=12345,
        duration_seconds=12,
        chunks_processed=1,
    )


def read_job_status(db, job_id):
    return db.execute(
        text(
            """
            SELECT status, progress, attempts
            FROM background_jobs
            WHERE id = :job_id
            """
        ),
        {"job_id": job_id},
    ).one()


def test_worker_empty_queue(worker_context):
    db = worker_context["db"]

    processed = process_one_job(
        db,
        worker_id="test-empty-worker",
        summary_generator=fake_summary,
        audio_generator=fake_audio,
    )

    assert processed is False


def test_worker_generates_summary(worker_context):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(db, project_id, "summary")
    db.commit()

    processed = process_one_job(
        db,
        worker_id="summary-worker",
        summary_generator=fake_summary,
        audio_generator=fake_audio,
    )

    assert processed is True

    status = read_job_status(db, job.id)

    assert status.status == "completed"
    assert status.progress == 100
    assert status.attempts == 1

    project = db.get(Project, project_id)
    db.refresh(project)

    assert project.status == "script_ready"
    assert project.script == (
        "Reading helps people learn."
    )


def test_worker_generates_audio(worker_context):
    db = worker_context["db"]
    project_id = worker_context["audio_project_id"]

    job = create_job(db, project_id, "audio")
    db.commit()

    processed = process_one_job(
        db,
        worker_id="audio-worker",
        summary_generator=fake_summary,
        audio_generator=fake_audio,
    )

    assert processed is True

    status = read_job_status(db, job.id)

    assert status.status == "completed"
    assert status.progress == 100

    project = db.get(Project, project_id)
    db.refresh(project)

    assert project.status == "completed"
    assert project.audio_file_path == (
        "worker-test/audio.wav"
    )
    assert project.total_duration == 12


def test_worker_records_summary_failure(
    worker_context,
):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(db, project_id, "summary")
    db.commit()

    def failing_summary(sections, summary_type):
        raise SummarizationError(
            "Simulated Gemini failure."
        )

    processed = process_one_job(
        db,
        worker_id="failure-worker",
        summary_generator=failing_summary,
        audio_generator=fake_audio,
    )

    assert processed is True

    status = read_job_status(db, job.id)

    assert status.status == "failed"
    assert status.attempts == 1

    project = db.get(Project, project_id)
    db.refresh(project)

    assert project.status == "failed"


def test_worker_job_ownership(worker_context):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(db, project_id, "summary")
    db.commit()

    owner_job = get_job(
        db,
        job.id,
        worker_context["user_id"],
    )

    other_user_job = get_job(
        db,
        job.id,
        999999999,
    )

    assert owner_job is not None
    assert other_user_job is None


from app.services.job_queue import (
    cancel_queued_job,
    claim_next_job,
    recover_stale_jobs,
)


def test_cancel_queued_job(worker_context):
    db = worker_context["db"]
    project_id = worker_context["audio_project_id"]

    job = create_job(
        db,
        project_id,
        "audio",
    )
    db.commit()

    cancelled = cancel_queued_job(
        db,
        job.id,
        worker_context["user_id"],
    )
    db.commit()

    assert cancelled is True

    status = read_job_status(db, job.id)
    assert status.status == "cancelled"


def test_cannot_cancel_another_users_job(
    worker_context,
):
    db = worker_context["db"]
    project_id = worker_context["audio_project_id"]

    job = create_job(
        db,
        project_id,
        "audio",
    )
    db.commit()

    cancelled = cancel_queued_job(
        db,
        job.id,
        999999999,
    )
    db.commit()

    assert cancelled is False

    status = read_job_status(db, job.id)
    assert status.status == "queued"


def test_temporary_failure_schedules_retry(
    worker_context,
):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(
        db,
        project_id,
        "summary",
    )
    db.commit()

    def temporary_failure(
        sections,
        summary_type,
    ):
        raise TimeoutError(
            "Simulated temporary timeout."
        )

    processed = process_one_job(
        db,
        worker_id="retry-worker",
        summary_generator=temporary_failure,
        audio_generator=fake_audio,
    )

    assert processed is True

    status = read_job_status(db, job.id)

    assert status.status == "queued"
    assert status.attempts == 1
    assert status.progress == 0

    project = db.get(Project, project_id)
    db.refresh(project)
    assert project.status == "pending"


def test_recover_stale_summary_job(
    worker_context,
):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(
        db,
        project_id,
        "summary",
    )
    db.commit()

    claimed = claim_next_job(
        db,
        "crashed-worker",
    )
    db.commit()

    assert claimed is not None

    project = db.get(Project, project_id)
    project.status = "generating"

    db.execute(
        text("""
            UPDATE background_jobs
            SET heartbeat_at =
                NOW() - INTERVAL '10 minutes'
            WHERE id = :job_id
        """),
        {"job_id": job.id},
    )
    db.commit()

    count = recover_stale_jobs(
        db,
        stale_seconds=120,
    )
    db.commit()

    assert count == 1

    status = read_job_status(db, job.id)
    assert status.status == "queued"

    db.refresh(project)
    assert project.status == "pending"


def test_recovery_does_not_touch_fresh_job(
    worker_context,
):
    db = worker_context["db"]
    project_id = worker_context["summary_project_id"]

    job = create_job(
        db,
        project_id,
        "summary",
    )
    db.commit()

    claimed = claim_next_job(
        db,
        "active-worker",
    )
    db.commit()

    assert claimed is not None

    count = recover_stale_jobs(
        db,
        stale_seconds=120,
    )
    db.commit()

    assert count == 0

    status = read_job_status(db, job.id)
    assert status.status == "running"



def test_running_worker_owns_job(worker_context):
    db = worker_context["db"]

    job = create_job(
        db,
        worker_context["summary_project_id"],
        "summary",
    )
    db.commit()

    claimed = claim_next_job(
        db,
        "worker-owner-1",
    )
    db.commit()

    assert claimed is not None

    assert worker_owns_job(
        db,
        job.id,
        "worker-owner-1",
    )

    assert not worker_owns_job(
        db,
        job.id,
        "another-worker",
    )


def test_heartbeat_rejects_wrong_worker(
    worker_context,
):
    db = worker_context["db"]

    job = create_job(
        db,
        worker_context["summary_project_id"],
        "summary",
    )
    db.commit()

    claim_next_job(
        db,
        "real-worker",
    )
    db.commit()

    updated = heartbeat_job(
        db,
        job.id,
        "wrong-worker",
    )
    db.commit()

    assert updated is False


def test_exhausted_job_cannot_retry(
    worker_context,
):
    db = worker_context["db"]

    job = create_job(
        db,
        worker_context["summary_project_id"],
        "summary",
    )

    db.execute(
        text("""
            UPDATE background_jobs
            SET max_attempts = 1
            WHERE id = :job_id
        """),
        {"job_id": job.id},
    )
    db.commit()

    def temporary_failure(
        sections,
        summary_type,
    ):
        raise TimeoutError(
            "Temporary failure with no retries left."
        )

    processed = process_one_job(
        db,
        worker_id="exhausted-worker",
        summary_generator=temporary_failure,
        audio_generator=fake_audio,
    )

    assert processed is True

    status = read_job_status(db, job.id)
    assert status.status == "failed"
    assert status.attempts == 1

    project = db.get(
        Project,
        worker_context["summary_project_id"],
    )
    db.refresh(project)

    assert project.status == "failed"


def test_exhausted_stale_job_fails(
    worker_context,
):
    db = worker_context["db"]

    job = create_job(
        db,
        worker_context["summary_project_id"],
        "summary",
    )

    db.execute(
        text("""
            UPDATE background_jobs
            SET max_attempts = 1
            WHERE id = :job_id
        """),
        {"job_id": job.id},
    )
    db.commit()

    claimed = claim_next_job(
        db,
        "worker-that-crashed",
    )
    db.commit()

    assert claimed is not None

    project = db.get(
        Project,
        worker_context["summary_project_id"],
    )
    project.status = "generating"

    db.execute(
        text("""
            UPDATE background_jobs
            SET heartbeat_at =
                NOW() - INTERVAL '10 minutes'
            WHERE id = :job_id
        """),
        {"job_id": job.id},
    )
    db.commit()

    recovered = recover_stale_jobs(
        db,
        stale_seconds=120,
    )
    db.commit()

    assert recovered == 1

    status = read_job_status(db, job.id)
    assert status.status == "failed"

    db.refresh(project)
    assert project.status == "failed"


def test_cancelled_job_is_not_claimed(
    worker_context,
):
    db = worker_context["db"]

    job = create_job(
        db,
        worker_context["audio_project_id"],
        "audio",
    )
    db.commit()

    assert cancel_queued_job(
        db,
        job.id,
        worker_context["user_id"],
    )
    db.commit()

    claimed = claim_next_job(
        db,
        "should-not-claim",
    )

    assert claimed is None
    db.rollback()
