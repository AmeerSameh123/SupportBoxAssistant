"""The exception hierarchy, and its mapping to RFC 9457 problem+json.

Two jobs, one module, because they are the same decision seen from two sides:
what can go wrong, and what the client is told about it.

The hierarchy is shaped by RETRY POLICY, not by tidiness. `LlmTransportError` and
`SchemaViolationError` are siblings in the tree but opposites in handling — one is
retried with backoff, the other is retried with a repair prompt, and a refusal is
not retried at all. That distinction is the point of PRD §7.1, so the types make
it explicit rather than leaving it to a string comparison on an error message.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base for everything this application raises deliberately."""

    title = "Application error"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class ConfigurationError(AppError):
    """Invalid configuration. Raised at startup so a typo fails loudly at boot
    rather than silently at ticket 17."""

    title = "Configuration error"


# --- LLM boundary ----------------------------------------------------------


class LlmError(AppError):
    title = "LLM error"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class LlmTransportError(LlmError):
    """Connection refused, 5xx, DNS failure. RETRYABLE with backoff — transient
    by nature, and the thing a backoff schedule was invented for."""

    title = "LLM transport error"


class LlmTimeoutError(LlmTransportError):
    """The endpoint took longer than LLM_TIMEOUT_SECONDS. Retryable, but counts
    toward the circuit breaker: a consistently slow endpoint is a down endpoint."""

    title = "LLM timeout"


class LlmProtocolError(LlmError):
    """The endpoint answered, but not with something usable — an oversized body,
    a malformed envelope, an empty completion.

    NOT retried at temperature 0: the request is deterministic, so a blind retry
    reproduces the same response. Retrying an identical deterministic request is
    superstition, and it costs 20 seconds to fail identically (PRD §7.1).
    """

    title = "LLM protocol error"


class CircuitOpenError(LlmError):
    """The breaker is open; the call was not attempted.

    This is what turns "Ollama isn't running" from a 15-minute eval into a
    40-second one (PRD §7.1).
    """

    title = "LLM circuit open"


class SchemaViolationError(AppError):
    """Model output could not be salvaged into a valid draft.

    Carries the field-level validation error so the repair-retry prompt can tell
    the model exactly what it got wrong. A retry that does not say what was wrong
    is just a second roll of the same dice.
    """

    title = "Schema violation"

    def __init__(self, detail: str = "", *, raw: str = "") -> None:
        super().__init__(detail)
        # Truncated: this ends up in logs, and the raw body is model output of
        # unbounded length.
        self.raw = raw[:2000]


# --- Application / HTTP ----------------------------------------------------


class NotFoundError(AppError):
    title = "Not found"
    status_code = status.HTTP_404_NOT_FOUND


class OptimisticLockError(AppError):
    """The record changed since the client read it.

    Returned as 409 rather than silently overwriting, because the losing write is
    an agent's edited reply (PRD §10.2).
    """

    title = "Version conflict"
    status_code = status.HTTP_409_CONFLICT


class RateLimitedError(AppError):
    title = "Too many requests"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class PayloadTooLargeError(AppError):
    title = "Payload too large"
    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class AuthenticationError(AppError):
    title = "Unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# RFC 9457 problem+json
# ---------------------------------------------------------------------------


def problem(
    *,
    status_code: int,
    title: str,
    detail: str = "",
    instance: str = "",
    type_: str = "about:blank",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build one problem+json response. No route ever constructs an error body by
    hand — there is exactly one shape and this function owns it."""
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def register_exception_handlers(app: FastAPI, *, debug: bool) -> None:
    """Map every exception class to a problem+json response.

    `debug` controls one thing only: whether an unexpected 500 leaks its detail.
    In production it does not. Stack traces to the client are an information leak
    and the tidiest way to fail a security review (PRD §10, API8).
    """

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem(
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            instance=str(request.url.path),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's error list is genuinely useful to an API client and contains
        # no server internals — it describes the client's own request.
        return problem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Validation error",
            detail="The request body or parameters failed validation.",
            instance=str(request.url.path),
            extra={"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(
            status_code=exc.status_code,
            title=str(exc.detail),
            instance=str(request.url.path),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return problem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            detail=str(exc) if debug else "An unexpected error occurred.",
            instance=str(request.url.path),
        )
