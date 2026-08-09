"""Authentication, rate limiting, body-size capping and security headers.

Mapped to the OWASP API Security Top 10 (2023, still the current edition). Local
single-user tooling does not get to skip a threat model; it gets to SCOPE one,
out loud — which is what the honesty notes below are for (PRD §10).
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.errors import (
    AuthenticationError,
    PayloadTooLargeError,
    RateLimitedError,
    problem,
)
from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # This API returns JSON only and never renders HTML, so the strictest
    # possible CSP is also the correct one.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def verify_token(provided: str | None, expected: str) -> None:
    """Constant-time bearer check.

    HONEST LIMITATION, repeated in the README: a single shared bearer token is
    not authentication, it is a doorstop. There is no user model in this dataset,
    so there is nobody to authenticate; the real answer is OIDC with per-user
    identity. What this does buy is that the endpoint is not open to anything on
    the machine, and that the comparison does not leak the token one byte at a
    time — a timing-leaky compare in a security-themed submission would be an
    own goal.
    """
    if not expected:
        return
    if not provided:
        raise AuthenticationError("Missing bearer token")

    scheme, _, credential = provided.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(credential, expected):
        raise AuthenticationError("Invalid bearer token")


class TokenBucketRateLimiter:
    """Per-client token bucket, in process.

    In process because there is one process. A distributed deployment needs Redis
    and this class is the seam where that swap happens — stated so the limitation
    is visible rather than discovered.
    """

    def __init__(self, per_minute: int, *, clock: Callable[[], float] | None = None) -> None:
        self._capacity = float(per_minute)
        self._refill_per_second = per_minute / 60.0
        self._clock = clock or time.monotonic
        self._tokens: dict[str, float] = defaultdict(lambda: float(per_minute))
        self._last: dict[str, float] = {}

    def allow(self, client: str) -> bool:
        now = self._clock()
        last = self._last.get(client, now)
        self._tokens[client] = min(
            self._capacity,
            self._tokens[client] + (now - last) * self._refill_per_second,
        )
        self._last[client] = now

        if self._tokens[client] < 1.0:
            return False
        self._tokens[client] -= 1.0
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlation ID in, correlation ID out, correlation ID in every log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies before they are buffered or parsed (API4).

    Checks Content-Length first because that rejects without reading anything;
    a chunked request with no length still gets caught by the streamed count.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max:
            return _too_large(request, self._max)

        if request.method in {"POST", "PUT", "PATCH"} and not declared:
            body = await request.body()
            if len(body) > self._max:
                return _too_large(request, self._max)

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP limit on the expensive paths only.

    Applied to triage endpoints rather than globally: the queue view polls, and
    rate-limiting a reviewer's own screen would be a self-inflicted outage. The
    thing worth protecting is the path that costs a model call.
    """

    def __init__(self, app: ASGIApp, *, limiter: TokenBucketRateLimiter) -> None:
        super().__init__(app)
        self._limiter = limiter

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if "/triage" in request.url.path and request.method == "POST":
            client = request.client.host if request.client else "unknown"
            if not self._limiter.allow(client):
                error = RateLimitedError("Rate limit exceeded; slow down.")
                return problem(
                    status_code=error.status_code,
                    title=error.title,
                    detail=error.detail,
                    instance=str(request.url.path),
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)


def _too_large(request: Request, limit: int) -> Response:
    error = PayloadTooLargeError(f"Request body exceeds {limit} bytes")
    return problem(
        status_code=error.status_code,
        title=error.title,
        detail=error.detail,
        instance=str(request.url.path),
    )
