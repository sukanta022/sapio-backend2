from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from sqlalchemy import text

VALID_JOB_TYPES = {"summary", "audio"}


@dataclass(frozen=True)
class JobRecord:
    id: int
    project_id: int
    job_type: str
    status: str
    progress: int
    attempts: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


def to_job_record(row) -> JobRecord:
    data = row._mapping

    return JobRecord(
        id=data["id"],
        project_id=data["project_id"],
        job_type=data["job_type"],
        status=data["status"],
        progress=data["progress"],
        attempts=data["attempts"],
        max_attempts=data["max_attempts"],
        created_at=data["created_at"],
        started_at=data["started_at"],
        finished_at=data["finished_at"],
        error_message=data["error_message"],
    )


def create_job(
    db: Session,
    project_id: int,
    job_type: str,
) -> JobRecord:
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(
            "Unsupported background job type."
        )

    row = db.execute(
        text(
            """
            INSERT INTO background_jobs (
                project_id,
                job_type,
                status,
                progress
            )
            VALUES (
                :project_id,
                :job_type,
                'queued',
                0
            )
            RETURNING *
            """
        ),
        {
            "project_id": project_id,
            "job_type": job_type,
        },
    ).first()

    return to_job_record(row)


def get_job(
    db: Session,
    job_id: int,
    user_id: int,
) -> JobRecord | None:
    row = db.execute(
        text(
            """
            SELECT bj.*
            FROM background_jobs AS bj
            JOIN projects AS p
              ON p.id = bj.project_id
            WHERE bj.id = :job_id
              AND p.user_id = :user_id
            """
        ),
        {
            "job_id": job_id,
            "user_id": user_id,
        },
    ).first()

    if row is None:
        return None

    return to_job_record(row)


def get_project_jobs(
    db: Session,
    project_id: int,
) -> list[JobRecord]:
    rows = db.execute(
        text(
            """
            SELECT *
            FROM background_jobs
            WHERE project_id = :project_id
            ORDER BY id DESC
            """
        ),
        {"project_id": project_id},
    ).all()

    return [
        to_job_record(row)
        for row in rows
    ]


def claim_next_job(
    db: Session,
    worker_id: str,
) -> JobRecord | None:
    """
    Atomically claim one eligible job.

    Call db.commit() immediately after claiming.
    """
    row = db.execute(
        text(
            """
            WITH next_job AS (
                SELECT id
                FROM background_jobs
                WHERE status = 'queued'
                  AND run_after <= NOW()
                  AND attempts < max_attempts
                ORDER BY run_after, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE background_jobs AS bj
            SET
                status = 'running',
                attempts = attempts + 1,
                worker_id = :worker_id,
                started_at = NOW(),
                heartbeat_at = NOW(),
                error_message = NULL
            FROM next_job
            WHERE bj.id = next_job.id
            RETURNING bj.*
            """
        ),
        {"worker_id": worker_id},
    ).first()

    if row is None:
        return None

    return to_job_record(row)


def update_job_progress(
    db: Session,
    job_id: int,
    worker_id: str,
    progress: int,
) -> bool:
    if not 0 <= progress <= 99:
        raise ValueError(
            "Running job progress must be 0-99."
        )

    result = db.execute(
        text(
            """
            UPDATE background_jobs
            SET
                progress = :progress,
                heartbeat_at = NOW()
            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "progress": progress,
        },
    )

    return result.rowcount == 1


def finish_job(
    db: Session,
    job_id: int,
    worker_id: str,
) -> bool:
    result = db.execute(
        text(
            """
            UPDATE background_jobs
            SET
                status = 'completed',
                progress = 100,
                heartbeat_at = NOW(),
                finished_at = NOW()
            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
        },
    )

    return result.rowcount == 1


def fail_job(
    db: Session,
    job_id: int,
    worker_id: str,
    message: str,
) -> bool:
    result = db.execute(
        text(
            """
            UPDATE background_jobs
            
            SET status = 'failed',
                error_message = :message,
                heartbeat_at = NOW(),
                finished_at = NOW()

            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "message": message[:1000],
        },
    )

    return result.rowcount == 1


def heartbeat_job(
    db: Session,
    job_id: int,
    worker_id: str,
) -> bool:
    """Refresh the lease only if this worker owns the job."""
    result = db.execute(
        text("""
            UPDATE background_jobs
            SET heartbeat_at = NOW()
            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
        """),
        {
            "job_id": job_id,
            "worker_id": worker_id,
        },
    )
    return result.rowcount == 1


def cancel_queued_job(
    db: Session,
    job_id: int,
    user_id: int,
) -> bool:
    """Cancel only a queued job owned by this user."""
    result = db.execute(
        text("""
            UPDATE background_jobs AS bj
            SET status = 'cancelled',
                finished_at = NOW(),
                worker_id = NULL
            FROM projects AS p
            WHERE bj.project_id = p.id
              AND bj.id = :job_id
              AND p.user_id = :user_id
              AND bj.status = 'queued'
        """),
        {
            "job_id": job_id,
            "user_id": user_id,
        },
    )
    return result.rowcount == 1


def retry_job(
    db: Session,
    job_id: int,
    worker_id: str,
    error_type: str,
) -> bool:
    """
    Retry a running job with exponential backoff.

    Attempt 1: 10 seconds
    Attempt 2: 20 seconds
    Attempt 3: no retry (default max_attempts=3)
    """
    row = db.execute(
        text("""
            SELECT attempts, max_attempts
            FROM background_jobs
            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
            FOR UPDATE
        """),
        {
            "job_id": job_id,
            "worker_id": worker_id,
        },
    ).first()

    if row is None:
        return False

    if row.attempts >= row.max_attempts:
        return False

    delay = min(
        300,
        10 * (2 ** max(0, row.attempts - 1)),
    )

    result = db.execute(
        text("""
            UPDATE background_jobs
            SET status = 'queued',
                progress = 0,
                worker_id = NULL,
                heartbeat_at = NULL,
                started_at = NULL,
                run_after = NOW()
                    + (:delay * INTERVAL '1 second'),
                error_message = :message
            WHERE id = :job_id
              AND worker_id = :worker_id
              AND status = 'running'
        """),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "delay": delay,
            "message": (
                f"Temporary processing error: "
                f"{error_type}"
            )[:1000],
        },
    )

    return result.rowcount == 1


def recover_stale_jobs(
    db: Session,
    stale_seconds: int = 120,
    batch_size: int = 20,
) -> int:
    """
    Recover abandoned jobs.

    Claim and project-state repair happen in
    the same PostgreSQL transaction.
    """
    if stale_seconds < 30:
        raise ValueError(
            "stale_seconds must be at least 30."
        )

    if not 1 <= batch_size <= 100:
        raise ValueError(
            "batch_size must be between 1 and 100."
        )

    rows = db.execute(
        text("""
            SELECT
                bj.id,
                bj.project_id,
                bj.job_type,
                bj.attempts,
                bj.max_attempts
            FROM background_jobs AS bj
            WHERE bj.status = 'running'
              AND bj.heartbeat_at <
                  NOW() - (
                      :stale_seconds *
                      INTERVAL '1 second'
                  )
            ORDER BY bj.heartbeat_at, bj.id
            FOR UPDATE OF bj SKIP LOCKED
            LIMIT :batch_size
        """),
        {
            "stale_seconds": stale_seconds,
            "batch_size": batch_size,
        },
    ).all()

    recovered = 0

    for row in rows:
        # Lock the project too, so a late worker
        # cannot commit conflicting project changes.
        project = db.execute(
            text("""
                SELECT status
                FROM projects
                WHERE id = :project_id
                FOR UPDATE
            """),
            {"project_id": row.project_id},
        ).first()

        if project is None:
            continue

        if row.attempts < row.max_attempts:
            new_status = "queued"
            project_status = (
                "pending"
                if row.job_type == "summary"
                else "script_ready"
            )
        else:
            new_status = "failed"
            project_status = (
                "failed"
                if row.job_type == "summary"
                else "audio_failed"
            )

        expected_project_status = (
            "generating"
            if row.job_type == "summary"
            else "audio_generating"
        )

        # Don't overwrite a project already completed
        # through another operation.
        if project.status != expected_project_status:
            new_status = "failed"
            project_status = None

        
        db.execute(
            text("""
                UPDATE background_jobs
                SET status = CAST(:new_status AS VARCHAR),
                    progress = CASE
                        WHEN CAST(:new_status AS VARCHAR) = 'queued'
                        THEN 0
                        ELSE progress
                    END,
                    worker_id = NULL,
                    heartbeat_at = NULL,
                    started_at = CASE
                        WHEN CAST(:new_status AS VARCHAR) = 'queued'
                        THEN NULL
                        ELSE started_at
                    END,
                    run_after = NOW(),
                    finished_at = CASE
                        WHEN CAST(:new_status AS VARCHAR) = 'failed'
                        THEN NOW()
                        ELSE NULL
                    END,
                    error_message =
                        'Worker heartbeat expired.'
                WHERE id = :job_id
                  AND status = 'running'
            """),
            {
                "job_id": row.id,
                "new_status": new_status,
            },
        )


        if project_status is not None:
            db.execute(
                text("""
                    UPDATE projects
                    SET status = :new_status
                    WHERE id = :project_id
                      AND status = :expected_status
                """),
                {
                    "project_id": row.project_id,
                    "new_status": project_status,
                    "expected_status": expected_project_status,
                },
            )

        recovered += 1

    return recovered


def project_has_active_job(
    db: Session,
    project_id: int,
) -> bool:
    """Check for a queued or running job for this project."""
    return bool(
        db.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM background_jobs
                    WHERE project_id = :project_id
                      AND status IN ('queued', 'running')
                )
            """),
            {"project_id": project_id},
        ).scalar_one()
    )


def assert_no_active_job(
    db: Session,
    project_id: int,
) -> None:
    """
    Protect legacy synchronous endpoints.

    The caller should already hold a FOR UPDATE
    lock on the corresponding project row.
    """
    if project_has_active_job(db, project_id):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail=(
                "A background job is already active "
                "for this project."
            ),
        )


def worker_owns_job(
    db: Session,
    job_id: int,
    worker_id: str,
) -> bool:
    """Check whether a running job still belongs to a worker."""
    return bool(
        db.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM background_jobs
                    WHERE id = :job_id
                      AND status = 'running'
                      AND worker_id = :worker_id
                )
            """),
            {
                "job_id": job_id,
                "worker_id": worker_id,
            },
        ).scalar_one()
    )
