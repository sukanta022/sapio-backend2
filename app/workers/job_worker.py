import argparse
import logging
import os
import socket
import time
import uuid

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.generation import (
    get_source_sections,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.models import (
    AudioFormat,
    GeneratedFile,
    Language,
    Narrator,
    ProcessingLog,
    Project,
    SummaryType,
)
from app.db.session import get_db
from app.services.gemini_summarizer import (
    generate_script,
)
from app.services.job_queue import (
    claim_next_job,
    fail_job,
    finish_job,
    update_job_progress,
)
from app.services.tts_service import (
    generate_audio,
    remove_audio_file,
)

from app.workers.heartbeat import JobHeartbeat
from app.services.job_queue import worker_owns_job
from app.services.job_queue import (
    recover_stale_jobs,
    retry_job,
)

logger = logging.getLogger("sapio.worker")


class WorkerJobError(Exception):
    """A job cannot be processed in its current state."""


def make_worker_id() -> str:
    hostname = socket.gethostname()[:40]
    return (
        f"{hostname}-{os.getpid()}-"
        f"{uuid.uuid4().hex[:8]}"
    )


def set_progress(
    db: Session,
    job_id: int,
    worker_id: str,
    progress: int,
) -> None:
    updated = update_job_progress(
        db,
        job_id,
        worker_id,
        progress,
    )

    if not updated:
        raise WorkerJobError(
            "The worker no longer owns this job."
        )

    db.commit()


def require_project(
    db: Session,
    project_id: int,
) -> Project:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
    )

    if project is None:
        raise WorkerJobError(
            "Project does not exist."
        )

    return project


def mark_project_running(
    db: Session,
    job,
) -> None:
    project = require_project(
        db,
        job.project_id,
    )

    if job.job_type == "summary":
        if project.status not in (
            "pending",
            "failed",
        ):
            raise WorkerJobError(
                "Project is not ready for summary generation."
            )

        project.status = "generating"

    elif job.job_type == "audio":
        if (
            project.status != "script_ready"
            or not project.script
        ):
            raise WorkerJobError(
                "Project script is not ready for audio."
            )

        project.status = "audio_generating"

    else:
        raise WorkerJobError(
            "Unsupported background job type."
        )

    db.add(
        ProcessingLog(
            project_id=project.id,
            stage=(
                "summarization"
                if job.job_type == "summary"
                else "tts"
            ),
            status="processing",
            message=(
                f"Background {job.job_type} "
                "processing started."
            ),
        )
    )

    db.commit()


def run_summary_job(
    db: Session,
    job,
    worker_id: str,
    summary_generator: Callable,
) -> None:
    project = db.get(
        Project,
        job.project_id,
    )

    summary_type = db.get(
        SummaryType,
        project.summary_type_id,
    )

    language = db.get(
        Language,
        project.language_id,
    )

    if summary_type is None or language is None:
        raise WorkerJobError(
            "Summary configuration is missing."
        )

    language_code = language.code.lower()

    if language_code not in ("en", "bn"):
        raise WorkerJobError(
            "Unsupported summary language."
        )

    sections = get_source_sections(
        project,
        summary_type.name,
        db,
    )

    set_progress(
        db,
        job.id,
        worker_id,
        10,
    )

    logger.info(
        "Generating summary | job_id=%s | "
        "project_id=%s | language=%s",
        job.id,
        project.id,
        language_code,
    )

    # English keeps the Phase 9 compatible call.
    if language_code == "en":
        result = summary_generator(
            sections,
            summary_type.name,
        )
    else:
        result = summary_generator(
            sections,
            summary_type.name,
            language_code=language_code,
        )

    if not result.script.strip():
        raise WorkerJobError(
            "Summary generator returned empty text."
        )

    set_progress(
        db,
        job.id,
        worker_id,
        90,
    )

    project = require_project(
        db,
        job.project_id,
    )

    if not worker_owns_job(
        db,
        job.id,
        worker_id,
    ):
        raise WorkerJobError(
            "Summary job ownership was lost."
        )

    if project.status != "generating":
        raise WorkerJobError(
            "Project summary state changed."
        )

    project.script = result.script
    project.status = "script_ready"

    db.add(
        ProcessingLog(
            project_id=project.id,
            stage="summarization",
            status="completed",
            message=(
                "Background summary generated "
                f"from {result.chunks_processed} chunks."
            ),
        )
    )

    completed = finish_job(
        db,
        job.id,
        worker_id,
    )

    if not completed:
        raise WorkerJobError(
            "Could not finalize summary job."
        )

    db.commit()


def run_audio_job(
    db: Session,
    job,
    worker_id: str,
    audio_generator: Callable,
) -> None:
    project = db.get(
        Project,
        job.project_id,
    )

    language = db.get(
        Language,
        project.language_id,
    )

    narrator = db.get(
        Narrator,
        project.narrator_id,
    )

    audio_format = db.get(
        AudioFormat,
        project.audio_format_id,
    )

    if any(
        item is None
        for item in (
            language,
            narrator,
            audio_format,
        )
    ):
        raise WorkerJobError(
            "Audio configuration is missing."
        )

    set_progress(
        db,
        job.id,
        worker_id,
        10,
    )

    logger.info(
        "Generating audio | job_id=%s | "
        "project_id=%s | language=%s",
        job.id,
        project.id,
        language.code,
    )

    result = audio_generator(
        script=project.script,
        user_id=project.user_id,
        project_id=project.id,
        language_code=language.code,
        narrator_name=narrator.name,
        audio_format=audio_format.name,
    )

    try:
        set_progress(
            db,
            job.id,
            worker_id,
            90,
        )

        project = require_project(
            db,
            job.project_id,
        )

        if not worker_owns_job(
            db,
            job.id,
            worker_id,
        ):
            raise WorkerJobError(
                "Audio job ownership was lost."
            )

        if project.status != "audio_generating":
            raise WorkerJobError(
                "Project audio state changed."
            )

        project.audio_file_path = result.relative_path
        project.total_duration = result.duration_seconds
        project.status = "completed"

        db.add(
            GeneratedFile(
                project_id=project.id,
                file_type=audio_format.name.lower(),
                file_path=result.relative_path,
                file_size=result.file_size,
            )
        )

        db.add(
            ProcessingLog(
                project_id=project.id,
                stage="tts",
                status="completed",
                message=(
                    "Background audio generated "
                    f"from {result.chunks_processed} chunks."
                ),
            )
        )

        completed = finish_job(
            db,
            job.id,
            worker_id,
        )

        if not completed:
            raise WorkerJobError(
                "Could not finalize audio job."
            )

        db.commit()

    except Exception:
        db.rollback()

        # The file was generated, but could not be
        # persisted successfully in PostgreSQL.
        try:
            remove_audio_file(
                result.relative_path
            )
        except (OSError, Exception):
            logger.warning(
                "Could not remove audio file for job %s",
                job.id,
            )

        raise



def record_job_failure(
    db: Session,
    job,
    worker_id: str,
    error: Exception,
) -> None:
    db.rollback()

    logger.error(
        "Background job error | job_id=%s | "
        "type=%s | exception=%s",
        job.id,
        job.job_type,
        type(error).__name__,
    )

    # Only known temporary infrastructure failures
    # are automatically retried.
    retryable = isinstance(
        error,
        (TimeoutError, ConnectionError),
    )

    project = db.scalar(
        select(Project)
        .where(Project.id == job.project_id)
        .with_for_update()
    )

    if project is None:
        failed = fail_job(
            db,
            job.id,
            worker_id,
            "Associated project no longer exists.",
        )

        if not failed:
            raise WorkerJobError(
                "Could not finalize missing-project job."
            )

        db.commit()
        return

    if job.job_type == "summary":
        running_status = "generating"
        pending_status = "pending"
        failed_status = "failed"
        stage = "summarization"
    else:
        running_status = "audio_generating"
        pending_status = "script_ready"
        failed_status = "audio_failed"
        stage = "tts"

    # Do not corrupt a project that has already
    # moved to a different state.
    if project.status != running_status:
        failed = fail_job(
            db,
            job.id,
            worker_id,
            "Project state changed during processing.",
        )

        if not failed:
            raise WorkerJobError(
                "Job ownership was lost."
            )

        db.commit()
        return

    if retryable:
        scheduled = retry_job(
            db,
            job.id,
            worker_id,
            type(error).__name__,
        )

        if scheduled:
            project.status = pending_status

            db.add(
                ProcessingLog(
                    project_id=project.id,
                    stage=stage,
                    status="failed",
                    message=(
                        "Temporary error; background job "
                        "scheduled for retry."
                    ),
                )
            )

            db.commit()

            logger.warning(
                "Background job scheduled for retry | "
                "job_id=%s",
                job.id,
            )
            return

    # This is either a permanent failure or
    # the configured retry limit was exhausted.
    failed = fail_job(
        db,
        job.id,
        worker_id,
        (
            "Background processing failed: "
            f"{type(error).__name__}"
        ),
    )

    if not failed:
        raise WorkerJobError(
            "Job ownership was lost."
        )

    project.status = failed_status

    db.add(
        ProcessingLog(
            project_id=project.id,
            stage=stage,
            status="failed",
            message=(
                "Background processing failed."
            ),
        )
    )

    db.commit()




def process_one_job(
    db: Session,
    worker_id: str,
    summary_generator: Callable | None = None,
    audio_generator: Callable | None = None,
) -> bool:
    """
    Process one queued job with a separate heartbeat.

    True: one job was claimed.
    False: queue was empty.
    """
    summary_generator = (
        summary_generator or generate_script
    )
    audio_generator = (
        audio_generator or generate_audio
    )

    job = claim_next_job(
        db,
        worker_id,
    )

    if job is None:
        db.rollback()
        return False

    db.commit()

    logger.info(
        "Claimed job | job_id=%s | "
        "project_id=%s | type=%s",
        job.id,
        job.project_id,
        job.job_type,
    )

    try:
        with JobHeartbeat(
            job_id=job.id,
            worker_id=worker_id,
            interval_seconds=10.0,
        ):
            mark_project_running(
                db,
                job,
            )

            if job.job_type == "summary":
                run_summary_job(
                    db,
                    job,
                    worker_id,
                    summary_generator,
                )

            elif job.job_type == "audio":
                run_audio_job(
                    db,
                    job,
                    worker_id,
                    audio_generator,
                )

            else:
                raise WorkerJobError(
                    "Unsupported background job type."
                )

        logger.info(
            "Background job completed | job_id=%s",
            job.id,
        )

    except Exception as exc:
        try:
            record_job_failure(
                db,
                job,
                worker_id,
                exc,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Could not persist failure "
                "for job %s",
                job.id,
            )

    return True





def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sapio AI background worker"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one queued job and exit.",
    )

    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=3.0,
    )

    args = parser.parse_args()

    if args.poll_seconds < 0.1:
        parser.error(
            "--poll-seconds must be at least 0.1."
        )

    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    worker_id = make_worker_id()

    logger.info(
        "Sapio worker started | worker_id=%s",
        worker_id,
    )

    try:
        while True:
            db_generator = get_db()
            db = next(db_generator)

            try:
                # Recover jobs abandoned by a crashed worker.
                recovered = recover_stale_jobs(
                    db,
                    stale_seconds=120,
                )
                db.commit()

                if recovered:
                    logger.warning(
                        "Recovered %s abandoned background jobs.",
                        recovered,
                    )

                # Claim and process at most one queued job.
                found = process_one_job(
                    db,
                    worker_id,
                )

            finally:
                db_generator.close()

            # Exit after one iteration when --once is used.
            if args.once:
                break

            # Wait when there are no ready jobs.
            if not found:
                time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        logger.info(
            "Worker stopped by keyboard interrupt."
        )




if __name__ == "__main__":
    main()
