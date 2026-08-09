"""Human review decisions, persisted atomically with optimistic locking.

Two properties this has to get right, both of which are about not losing an
agent's work (PRD §10.2):

  ATOMICITY — writes go to a temp file and are moved into place with
  `Path.replace`, which is atomic on both Windows and POSIX. A crash mid-write
  therefore leaves the previous good file, never a half-written one. Writing in
  place would mean a power cut during a demo corrupts every review decision.

  OPTIMISTIC CONCURRENCY — each record carries an integer version. A PATCH
  supplies the version it read; a mismatch is a 409 rather than a silent
  overwrite. Two browser tabs is a realistic scenario and the losing write is an
  agent's edited reply, which is a real bug rather than a theoretical one.

JSON rather than SQLite: thirty records, one process, one writer. An ORM would be
more code than the data. The interface is what makes that reversible.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import OptimisticLockError
from app.domain.models import AuditEntry, ReviewRecord

logger = logging.getLogger(__name__)

_STORE_VERSION = 1


class JsonReviewRepository:
    """Implements both ReviewReader and ReviewWriter over a single JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._records: dict[str, ReviewRecord] | None = None

    # --- read --------------------------------------------------------------

    async def get(self, ticket_id: str) -> ReviewRecord:
        """Always returns a record. An unreviewed ticket is PENDING, not absent —
        callers should never have to distinguish 'no decision yet' from 'missing'."""
        records = await self._ensure_loaded()
        return records.get(ticket_id) or ReviewRecord(ticket_id=ticket_id)

    async def list_all(self) -> dict[str, ReviewRecord]:
        records = await self._ensure_loaded()
        return dict(records)

    # --- write -------------------------------------------------------------

    async def save(self, record: ReviewRecord, expected_version: int) -> ReviewRecord:
        async with self._lock:
            records = await self._ensure_loaded(locked=True)
            current = records.get(record.ticket_id)
            current_version = current.version if current else 0

            if expected_version != current_version:
                raise OptimisticLockError(
                    f"Review for {record.ticket_id} was modified by someone else "
                    f"(you had version {expected_version}, current is "
                    f"{current_version}). Reload and reapply your change."
                )

            audit = (current.audit if current else ()) + (
                AuditEntry(
                    action=f"status={record.status.value}",
                    detail=_describe_change(current, record),
                ),
            )
            saved = record.model_copy(
                update={
                    "version": current_version + 1,
                    "updated_at": datetime.now(UTC),
                    # Bounded: an audit trail that grows without limit turns a
                    # demo file into a performance problem.
                    "audit": audit[-50:],
                }
            )
            records[saved.ticket_id] = saved
            self._write(records)
            return saved

    # --- internals ---------------------------------------------------------

    async def _ensure_loaded(self, *, locked: bool = False) -> dict[str, ReviewRecord]:
        if self._records is not None:
            return self._records
        if locked:
            self._records = self._read()
            return self._records
        async with self._lock:
            if self._records is None:
                self._records = self._read()
            return self._records

    def _read(self) -> dict[str, ReviewRecord]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            # An empty file is an empty store, not a corrupt one. This is the
            # state left by `touch`, by a fresh clone, and by a first run — none
            # of which is an error. Genuinely malformed content still raises.
            if not text.strip():
                return {}
            payload = json.loads(text)
            raw = payload.get("records", {}) if isinstance(payload, dict) else {}
            return {tid: ReviewRecord.model_validate(rec) for tid, rec in raw.items()}
        except (OSError, ValueError) as exc:
            # Loud, not silent. Returning {} quietly would look like "no reviews
            # yet" and the agent's work would appear to have never existed.
            logger.error(
                "reviews.store_unreadable",
                extra={"path": str(self._path), "error_type": type(exc).__name__},
            )
            raise

    def _write(self, records: dict[str, ReviewRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _STORE_VERSION,
            "records": {tid: json.loads(rec.model_dump_json()) for tid, rec in records.items()},
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic on Windows and POSIX


def _describe_change(before: ReviewRecord | None, after: ReviewRecord) -> str:
    """A short, human-readable diff for the audit trail."""
    if before is None:
        return f"created as {after.status.value}"
    parts: list[str] = []
    if before.status != after.status:
        parts.append(f"status {before.status.value} -> {after.status.value}")
    if before.edited_reply != after.edited_reply:
        parts.append("reply edited")
    if before.note != after.note:
        parts.append("note updated")
    return "; ".join(parts) or "no field changes"
