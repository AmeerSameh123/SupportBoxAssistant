"""Test doubles.

These exist because the domain depends on Protocols rather than concretes. Every
fake here is a plain class that structurally satisfies a port — no inheritance
from production code, no mocking library, no patching. That is the practical
payoff of `typing.Protocol` over `abc.ABC` (PRD §5).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.core.errors import OptimisticLockError
from app.domain.models import ChatRequest, ChatResponse, ReviewRecord, TriageResult


class FakeChatClient:
    """A scriptable ChatClient.

    Each script entry is either a string (returned as completion content) or an
    exception instance (raised). This is what lets the contract test drive the
    entire reliability cascade — valid JSON, fenced JSON, schema echo, timeout,
    connection refused — in milliseconds with no network (PRD §12).
    """

    def __init__(self, script: Iterable[str | BaseException] | None = None) -> None:
        self._script: list[str | BaseException] = list(script or [])
        self.calls: list[ChatRequest] = []

    def queue(self, *items: str | BaseException) -> FakeChatClient:
        self._script.extend(items)
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if not self._script:
            raise AssertionError(f"FakeChatClient script exhausted after {len(self.calls)} calls")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResponse(content=item, finish_reason="stop", latency_ms=1.0)


class InMemoryCache:
    """A TriageCache with no disk. Also records hits so tests can assert on them."""

    def __init__(self) -> None:
        self.store: dict[str, TriageResult] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> TriageResult | None:
        result = self.store.get(key)
        if result is None:
            self.misses += 1
        else:
            self.hits += 1
        return result

    async def set(self, key: str, result: TriageResult) -> None:
        self.store[key] = result


class InMemoryReviewRepository:
    """Implements ReviewReader + ReviewWriter, including the version check.

    The optimistic-locking behaviour is duplicated here rather than stubbed out,
    because an in-memory double that silently accepts any version would make the
    409 test pass against a repository that does not actually lock.
    """

    def __init__(self) -> None:
        self.records: dict[str, ReviewRecord] = {}

    async def get(self, ticket_id: str) -> ReviewRecord:
        return self.records.get(ticket_id) or ReviewRecord(ticket_id=ticket_id)

    async def list_all(self) -> dict[str, ReviewRecord]:
        return dict(self.records)

    async def save(self, record: ReviewRecord, expected_version: int) -> ReviewRecord:
        current = self.records.get(record.ticket_id)
        current_version = current.version if current else 0
        if expected_version != current_version:
            raise OptimisticLockError(
                f"Review for {record.ticket_id} is at version {current_version}, "
                f"you supplied {expected_version}"
            )
        saved = record.model_copy(
            update={
                "version": current_version + 1,
                # Mirrors JsonReviewRepository. A fake that diverges from the
                # real implementation on an asserted property is a fake that
                # makes tests pass against broken code.
                "updated_at": datetime.now(UTC),
            }
        )
        self.records[saved.ticket_id] = saved
        return saved


class AlwaysFailingChatClient:
    """Every call raises. Used to prove the eval still produces 30 predictions
    with the model server completely down."""

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.calls = 0

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        raise self._error
