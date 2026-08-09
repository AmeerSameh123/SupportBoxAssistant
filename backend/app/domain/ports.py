"""The ports. Every dependency the domain has on the outside world, as Protocols.

Why `typing.Protocol` and not `abc.ABC`: structural typing gives the same
mypy-checked contract without forcing test doubles to inherit from production
base classes and without a runtime inheritance graph. It is the idiomatic modern
Python answer, and mypy enforces it statically (PRD §5).

Interface segregation is real here, not cosmetic. `TicketReader` and
`ReviewWriter` are separate protocols so the queue's read path is *physically
incapable* of mutating review state — the object it holds has no write method.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import (
    ChatRequest,
    ChatResponse,
    ReviewRecord,
    Ticket,
    TriageResult,
)


@runtime_checkable
class ChatClient(Protocol):
    """One method, because one method is all anything needs (ISP).

    Implementations must raise only the exception types declared in
    app.core.errors — the triage layer's retry policy dispatches on them, so an
    adapter leaking `httpx.ConnectError` would silently defeat the classification
    in PRD §7.1 and turn a retryable transport blip into an unretried failure.
    """

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Send a completion request. Raises LlmTransportError / LlmProtocolError."""
        ...


@runtime_checkable
class TriageStrategy(Protocol):
    """An interchangeable way to produce a triage judgement.

    THE BEHAVIOURAL CONTRACT — this is what makes substitution safe, and it is
    asserted by a shared parametrized test over every implementation (PRD §5, L):

        For ANY input, including an empty string or 100KB of binary noise:
          - it returns a valid TriageResult, or raises an app.core.errors.AppError
            subclass;
          - it never returns None;
          - it never lets any OTHER exception type escape.

    HeuristicTriageStrategy STRENGTHENS this: it never raises at all. Strengthening
    a postcondition is exactly what LSP permits, and it is the property that makes
    the heuristic a safe terminal fallback — the cascade has to end somewhere that
    cannot fail.

    Note this contract is stated more precisely than PRD §5's "never raises". The
    LLM strategy genuinely can fail, and the service must be able to tell — so the
    honest contract bounds the exception TYPE rather than pretending failure is
    impossible. A violation here is not a type error; it is a crashed 30-ticket
    eval run at ticket 17.
    """

    @property
    def name(self) -> str:
        """Stable identifier, recorded in telemetry."""
        ...

    async def triage(self, ticket: Ticket) -> TriageResult: ...


@runtime_checkable
class TicketReader(Protocol):
    """Read-only access to the inbound corpus. No write method exists, by design."""

    def list_all(self) -> tuple[Ticket, ...]: ...

    def get(self, ticket_id: str) -> Ticket | None: ...


@runtime_checkable
class ReviewReader(Protocol):
    async def get(self, ticket_id: str) -> ReviewRecord: ...

    async def list_all(self) -> dict[str, ReviewRecord]: ...


@runtime_checkable
class ReviewWriter(Protocol):
    async def save(self, record: ReviewRecord, expected_version: int) -> ReviewRecord:
        """Persist a review decision.

        Raises OptimisticLockError when `expected_version` does not match the
        stored version — the caller must re-read rather than overwrite.
        """
        ...


@runtime_checkable
class TriageCache(Protocol):
    """Keyed on prompt version + model + normalized ticket text.

    Including the prompt version in the key means changing the prompt correctly
    invalidates everything, which is what makes cached eval runs trustworthy
    rather than stale (PRD §7.1 stage 2).
    """

    async def get(self, key: str) -> TriageResult | None: ...

    async def set(self, key: str, result: TriageResult) -> None: ...
