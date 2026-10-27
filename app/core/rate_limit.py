import math
import threading
import time

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import (
    BaseHTTPMiddleware,
)


@dataclass
class RateLimitEntry:
    window_started: float
    count: int


class FixedWindowRateLimiter:
    """
    Per-process fixed-window request limiter.

    IMPORTANT:
    This does not synchronize across multiple
    worker processes or backend servers.
    """

    def __init__(self) -> None:
        self._entries: dict[
            str, RateLimitEntry
        ] = {}

        self._lock = threading.Lock()

    def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """
        Return (allowed, retry_after_seconds).
        """
        now = time.monotonic()

        with self._lock:
            entry = self._entries.get(key)

            if (
                entry is None
                or now - entry.window_started
                >= window_seconds
            ):
                self._entries[key] = RateLimitEntry(
                    window_started=now,
                    count=1,
                )

                return True, 0

            if entry.count >= limit:
                remaining = (
                    window_seconds
                    - (now - entry.window_started)
                )

                return (
                    False,
                    max(1, math.ceil(remaining)),
                )

            entry.count += 1
            return True, 0

    def prune(
        self,
        window_seconds: int,
    ) -> None:
        """Remove expired windows from memory."""
        now = time.monotonic()

        with self._lock:
            expired = [
                key
                for key, entry in self._entries.items()
                if now - entry.window_started
                >= window_seconds
            ]

            for key in expired:
                del self._entries[key]


class RateLimitMiddleware(
    BaseHTTPMiddleware
):
    def __init__(
        self,
        app,
        enabled: bool,
        api_prefix: str,
        window_seconds: int,
        login_limit: int,
        register_limit: int,
        generation_limit: int,
    ) -> None:
        super().__init__(app)

        self.enabled = enabled
        self.api_prefix = api_prefix.rstrip("/")
        self.window_seconds = window_seconds

        self.login_limit = login_limit
        self.register_limit = register_limit
        self.generation_limit = generation_limit

        self.limiter = FixedWindowRateLimiter()
        self._requests_seen = 0
        self._counter_lock = threading.Lock()

    def _category(
        self,
        method: str,
        path: str,
    ) -> tuple[str, int] | None:
        if method != "POST":
            return None

        if path == (
            f"{self.api_prefix}/auth/login"
        ):
            return ("login", self.login_limit)

        if path == (
            f"{self.api_prefix}/auth/register"
        ):
            return ("register", self.register_limit)

        prefix = (
            f"{self.api_prefix}/projects/"
        )

        if not path.startswith(prefix):
            return None

        remaining = path[len(prefix):]
        parts = remaining.split("/")

        # Expected:
        # {project_id}/generate
        # {project_id}/audio/generate
        # {project_id}/jobs
        if len(parts) not in (2, 3):
            return None

        project_id = parts[0]

        if (
            not project_id.isascii()
            or not project_id.isdecimal()
        ):
            return None

        suffix = "/".join(parts[1:])

        if suffix in {
            "generate",
            "audio/generate",
            "jobs",
        }:
            return (
                "generation",
                self.generation_limit,
            )

        return None

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        if not self.enabled:
            return await call_next(request)

        category = self._category(
            request.method,
            request.url.path,
        )

        if category is None:
            return await call_next(request)

        category_name, limit = category

        # Use the socket client IP.
        # Never trust user-provided X-Forwarded-For
        # without a separately configured proxy.
        client_ip = (
            request.client.host
            if request.client is not None
            else "unknown"
        )

        key = f"{client_ip}:{category_name}"

        allowed, retry_after = self.limiter.check(
            key=key,
            limit=limit,
            window_seconds=self.window_seconds,
        )

        with self._counter_lock:
            self._requests_seen += 1
            should_prune = (
                self._requests_seen % 1000 == 0
            )

        if should_prune:
            self.limiter.prune(
                self.window_seconds
            )

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Too many requests. "
                        "Please try again later."
                    )
                },
                headers={
                    "Retry-After": str(retry_after),
                    "Cache-Control": "no-store",
                },
            )

        return await call_next(request)
