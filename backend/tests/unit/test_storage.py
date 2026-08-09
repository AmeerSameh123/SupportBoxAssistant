"""The real persistence adapters, not their in-memory doubles.

These exist because tests/fakes.py reimplements optimistic locking, and a suite
that only exercises the fake would prove the fake works. PRD §10.2 makes concrete
claims about atomicity and version conflicts; this is where they are checked
against the code that actually runs.
"""

from __future__ import annotations

import json

import pytest

from app.core.errors import ConfigurationError, OptimisticLockError
from app.domain.enums import Category, Priority, ReviewStatus, TriageStage
from app.domain.models import ReviewRecord, TriageResult, TriageTelemetry
from app.storage.cache import FileTriageCache, NullCache
from app.storage.review_repository import JsonReviewRepository
from app.storage.ticket_repository import JsonTicketRepository


def make_result(confidence: float = 0.9) -> TriageResult:
    return TriageResult(
        category=Category.BILLING,
        priority=Priority.HIGH,
        summary="Duplicate charge",
        suggested_reply="We are looking into it.",
        confidence=confidence,
        escalate=False,
        telemetry=TriageTelemetry(stage=TriageStage.LLM, model="test"),
    )


class TestJsonTicketRepository:
    def test_loads_the_corpus_in_order(self, test_settings):
        repo = JsonTicketRepository(test_settings.tickets_path)
        assert len(repo) == 30
        assert [t.id for t in repo.list_all()][:3] == ["T-001", "T-002", "T-003"]

    def test_get_returns_none_for_unknown(self, test_settings):
        assert JsonTicketRepository(test_settings.tickets_path).get("T-999") is None

    def test_the_from_field_is_aliased(self, test_settings):
        ticket = JsonTicketRepository(test_settings.tickets_path).get("T-001")
        assert ticket is not None
        assert ticket.sender == "marta.kovac@example.com"

    def test_missing_file_fails_with_an_actionable_message(self, tmp_path):
        """A path typo should say what to fix, not raise FileNotFoundError from
        three frames down."""
        with pytest.raises(ConfigurationError, match="DATA_DIR"):
            JsonTicketRepository(tmp_path / "nope.json")

    def test_malformed_json_fails_loudly(self, tmp_path):
        path = tmp_path / "tickets.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            JsonTicketRepository(path)

    def test_wrong_shape_fails_loudly(self, tmp_path):
        path = tmp_path / "tickets.json"
        path.write_text('{"tickets": []}', encoding="utf-8")
        with pytest.raises(ConfigurationError, match="JSON array"):
            JsonTicketRepository(path)


class TestJsonReviewRepository:
    @pytest.fixture
    def repo(self, tmp_path) -> JsonReviewRepository:
        return JsonReviewRepository(tmp_path / "reviews.json")

    async def test_unreviewed_ticket_is_pending_not_missing(self, repo):
        """Callers should never distinguish 'no decision yet' from 'absent'."""
        record = await repo.get("T-001")
        assert record.status is ReviewStatus.PENDING
        assert record.version == 0

    async def test_save_increments_version_and_stamps_time(self, repo):
        saved = await repo.save(
            ReviewRecord(ticket_id="T-001", status=ReviewStatus.APPROVED),
            expected_version=0,
        )
        assert saved.version == 1
        assert saved.updated_at is not None

    async def test_stale_version_raises(self, repo):
        await repo.save(ReviewRecord(ticket_id="T-001"), expected_version=0)
        with pytest.raises(OptimisticLockError, match="modified by someone else"):
            await repo.save(ReviewRecord(ticket_id="T-001"), expected_version=0)

    async def test_conflict_does_not_mutate_the_store(self, repo):
        await repo.save(ReviewRecord(ticket_id="T-001", edited_reply="first"), expected_version=0)
        with pytest.raises(OptimisticLockError):
            await repo.save(
                ReviewRecord(ticket_id="T-001", edited_reply="second"),
                expected_version=0,
            )
        assert (await repo.get("T-001")).edited_reply == "first"

    async def test_data_survives_a_fresh_repository(self, tmp_path):
        """Proves the write reached disk, not just an in-memory dict — which is
        the whole point of persisting review decisions."""
        path = tmp_path / "reviews.json"
        first = JsonReviewRepository(path)
        await first.save(ReviewRecord(ticket_id="T-001", edited_reply="kept"), expected_version=0)

        second = JsonReviewRepository(path)
        reloaded = await second.get("T-001")
        assert reloaded.edited_reply == "kept"
        assert reloaded.version == 1

    async def test_write_is_atomic_and_leaves_no_temp_file(self, tmp_path):
        """Temp-file + replace. A crash mid-write leaves the previous good file
        rather than a half-written one (PRD §10.2)."""
        path = tmp_path / "reviews.json"
        repo = JsonReviewRepository(path)
        await repo.save(ReviewRecord(ticket_id="T-001"), expected_version=0)

        assert path.exists()
        assert not path.with_suffix(".tmp").exists()
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1

    async def test_empty_file_is_an_empty_store_not_a_corrupt_one(self, tmp_path):
        """The state left by `touch`, by a fresh clone, and by a first run."""
        path = tmp_path / "reviews.json"
        path.write_text("", encoding="utf-8")
        assert (await JsonReviewRepository(path).get("T-001")).version == 0

    async def test_corrupt_file_raises_rather_than_looking_empty(self, tmp_path):
        """Returning {} quietly would look like 'no reviews yet', and an agent's
        work would appear never to have existed."""
        path = tmp_path / "reviews.json"
        path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ValueError):
            await JsonReviewRepository(path).get("T-001")

    async def test_audit_trail_records_each_change(self, repo):
        await repo.save(
            ReviewRecord(ticket_id="T-001", status=ReviewStatus.APPROVED),
            expected_version=0,
        )
        saved = await repo.save(
            ReviewRecord(
                ticket_id="T-001",
                status=ReviewStatus.REJECTED,
                edited_reply="changed",
            ),
            expected_version=1,
        )
        assert len(saved.audit) == 2
        assert "approved -> rejected" in saved.audit[-1].detail
        assert "reply edited" in saved.audit[-1].detail


class TestFileTriageCache:
    async def test_roundtrip(self, tmp_path):
        cache = FileTriageCache(tmp_path)
        await cache.set("a" * 64, make_result())
        loaded = await cache.get("a" * 64)
        assert loaded is not None
        assert loaded.category is Category.BILLING
        assert loaded.confidence == 0.9

    async def test_miss_returns_none(self, tmp_path):
        assert await FileTriageCache(tmp_path).get("b" * 64) is None

    async def test_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path):
        """The worst case for an unreadable cache entry is one recomputation."""
        cache = FileTriageCache(tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{'c' * 64}.json").write_text("{broken", encoding="utf-8")
        assert await cache.get("c" * 64) is None

    async def test_stale_schema_entry_is_a_miss(self, tmp_path):
        cache = FileTriageCache(tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{'d' * 64}.json").write_text('{"category":"gone"}', encoding="utf-8")
        assert await cache.get("d" * 64) is None

    @pytest.mark.parametrize("key", ["../escape", "a/b", "a\\b", "a.b", ""])
    def test_non_alphanumeric_keys_are_rejected(self, tmp_path, key):
        """Keys are internally generated hex digests, never user input — but
        'it cannot be attacker-controlled' is a property that outlives nobody's
        refactor."""
        with pytest.raises(ValueError, match="alphanumeric"):
            FileTriageCache(tmp_path)._path(key)

    async def test_write_failure_degrades_rather_than_raising(self, tmp_path, monkeypatch):
        """A cache that cannot write is a slow app, not a broken one."""
        cache = FileTriageCache(tmp_path / "sub")

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("pathlib.Path.write_text", boom)
        await cache.set("e" * 64, make_result())  # must not raise


class TestNullCache:
    async def test_always_misses_and_accepts_writes(self):
        cache = NullCache()
        await cache.set("k", make_result())
        assert await cache.get("k") is None
