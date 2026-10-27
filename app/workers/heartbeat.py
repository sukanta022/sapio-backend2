import logging
import threading

from types import TracebackType
from typing import Self

from app.db.session import get_session_factory
from app.services.job_queue import heartbeat_job


logger = logging.getLogger("sapio.worker.heartbeat")


class JobHeartbeat:
    """
    Keep a background job's heartbeat alive while
    Gemini or Piper performs a long-running operation.

    Each heartbeat update uses an independent SQLAlchemy
    session because sessions are not thread-safe.

    The heartbeat only updates a job that:
      - is currently running
      - belongs to the expected worker_id

    Usage:
        with JobHeartbeat(job_id=1, worker_id="worker-1"):
            perform_long_running_operation()
    """

    def __init__(
        self,
        job_id: int,
        worker_id: str,
        interval_seconds: float = 10.0,
    ) -> None:

        if job_id <= 0:
            raise ValueError(
                "job_id must be positive."
            )

        if not worker_id or not worker_id.strip():
            raise ValueError(
                "worker_id must not be empty."
            )

        if interval_seconds <= 0:
            raise ValueError(
                "Heartbeat interval must be positive."
            )

        self.job_id = job_id
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Prevent accidental concurrent reuse.
        self._started = False

    def _run(self) -> None:
        """
        Update the heartbeat periodically until stopped
        or until the worker no longer owns the job.
        """

        while not self._stop.wait(
            self.interval_seconds
        ):
            try:
                session_factory = get_session_factory()

                with session_factory() as db:
                    updated = heartbeat_job(
                        db=db,
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                    )

                    db.commit()

                if not updated:
                    logger.warning(
                        "Heartbeat stopped because job "
                        "ownership was lost | "
                        "job_id=%s | worker_id=%s",
                        self.job_id,
                        self.worker_id,
                    )

                    self._stop.set()
                    break

                logger.debug(
                    "Heartbeat updated | "
                    "job_id=%s | worker_id=%s",
                    self.job_id,
                    self.worker_id,
                )

            except Exception:
                logger.exception(
                    "Heartbeat database update failed | "
                    "job_id=%s | worker_id=%s",
                    self.job_id,
                    self.worker_id,
                )

    def __enter__(self) -> Self:
        """
        Start the heartbeat when entering a with block.
        """

        if self._started:
            raise RuntimeError(
                "This heartbeat has already been started."
            )

        self._started = True
        self._stop.clear()

        self._thread = threading.Thread(
            target=self._run,
            name=f"sapio-heartbeat-{self.job_id}",
            daemon=True,
        )

        try:
            self._thread.start()

        except Exception:
            self._started = False
            self._thread = None
            raise

        logger.debug(
            "Heartbeat thread started | "
            "job_id=%s | interval=%s",
            self.job_id,
            self.interval_seconds,
        )

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Stop the heartbeat when the with block exits,
        including when generation raises an exception.
        """

        self._stop.set()

        if self._thread is not None:
            self._thread.join(
                timeout=self.interval_seconds + 5
            )

            if self._thread.is_alive():
                logger.warning(
                    "Heartbeat thread did not stop "
                    "within the expected timeout | "
                    "job_id=%s",
                    self.job_id,
                )

        logger.debug(
            "Heartbeat context exited | job_id=%s",
            self.job_id,
        )
