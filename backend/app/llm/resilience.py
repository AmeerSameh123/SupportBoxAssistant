"""Retry, bounded concurrency, and a circuit breaker — as a Decorator.

`ResilientChatClient` wraps any `ChatClient` and is itself a `ChatClient`. That
shape keeps `client.py` free of retry logic (it changes when the wire format
changes; this changes when the failure policy changes — two reasons, two modules)
and lets every retry semantic be tested against a fake with no HTTP at all.

Both mechanisms here are hand-written rather than pulled from `tenacity` and
`pybreaker`. Fifty lines, no transitive dependencies, and every state transition
is mine to explain in the interview (PRD §6.2).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from enum import StrEnum

from app.core.errors import (
    CircuitOpenError,
    LlmProtocolError,
    LlmTransportError,
)
from app.domain.models import ChatRequest, ChatResponse
from app.domain.ports import ChatClient

BASE_BACKOFF_SECONDS = 0.5


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after N consecutive transport failures; recovers through half-open.

    Why this earns its lines: without it, "Ollama isn't running" turns a
    30-ticket eval into 30 x (1 + retries) timeouts — roughly fifteen minutes of
    waiting to produce a result the breaker delivers in forty seconds. The eval
    must complete and produce 30 predictions even with the model server down
    (PRD §7.1), and that is a hard requirement, not a nicety.

    Only TRANSPORT failures count. A schema violation means the server answered
    perfectly well and the model wrote nonsense — that is not a reason to stop
    calling the server.
    """

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._clock = clock or asyncio.get_event_loop().time
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self._cooldown:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    @property
    def failures(self) -> int:
        return self._failures

    def allows_call(self) -> bool:
        """Half-open lets exactly one trial through. If it succeeds the circuit
        closes; if it fails the cooldown restarts."""
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


class ResilientChatClient:
    """Adds retry, jittered backoff, bounded concurrency and breaking.

    Retry semantics are TYPED, not uniform — that distinction is the point of the
    whole design (PRD §7.1):

      transport / timeout  -> retry with exponential backoff + full jitter
      protocol (4xx, empty completion, oversized) -> no retry, straight through
      circuit open         -> no call at all

    Schema violations are not visible here: they happen after parsing, so
    `LlmTriageStrategy` owns the repair-retry. Retrying the same deterministic
    request at temperature 0 without telling the model what was wrong would
    reproduce the same bytes.
    """

    def __init__(
        self,
        inner: ChatClient,
        *,
        max_retries: int = 2,
        concurrency: int = 2,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        # Bounded concurrency is FASTER than unbounded against one CPU-bound
        # Ollama: 30 simultaneous requests make every request slower and push
        # some past the timeout (PRD §10, API4).
        self._semaphore = asyncio.Semaphore(concurrency)
        self._breaker = breaker or CircuitBreaker()
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not cryptography

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not self._breaker.allows_call():
            raise CircuitOpenError(
                f"LLM circuit is open after {self._breaker.failures} "
                "consecutive transport failures; skipping the call"
            )

        last_error: LlmTransportError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._inner.complete(request)
            except LlmTransportError as exc:
                last_error = exc
                self._breaker.record_failure()
                if attempt < self._max_retries:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise
            except LlmProtocolError:
                # The server answered. Transport is healthy, so the breaker must
                # not count this — otherwise a model that refuses politely would
                # trip the circuit and disable a working endpoint.
                self._breaker.record_success()
                raise
            else:
                self._breaker.record_success()
                return response

        # Unreachable: the loop either returns or raises.
        raise last_error or LlmTransportError("LLM call failed")  # pragma: no cover

    def _backoff(self, attempt: int) -> float:
        """Exponential with FULL jitter.

        Full jitter rather than fixed backoff because thirty tickets retrying in
        lockstep is a thundering herd against a single Ollama process — the
        synchronised retry is itself the second outage.
        """
        ceiling = BASE_BACKOFF_SECONDS * (2**attempt)
        return self._rng.uniform(0, ceiling)
