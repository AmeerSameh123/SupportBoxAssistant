"""Domain entities and value objects.

This module imports nothing but the standard library and Pydantic. That
constraint is what makes the policy and the metrics testable in microseconds
with no fixtures, and it is verified by tests/unit/test_domain_purity.py rather
than trusted (PRD §3.1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    Category,
    FailureKind,
    Priority,
    RepairKind,
    ResponseFormatMode,
    ReviewStatus,
    TriageStage,
)

# Field bounds. Enforced here so the LLM cannot make a response arbitrarily
# large by being verbose, and so the frontend can lay out without surprises.
MAX_SUMMARY_CHARS = 200
MAX_REPLY_CHARS = 1500


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Inbound data
# ---------------------------------------------------------------------------


class Ticket(BaseModel):
    """One inbound support message, exactly as it appears in tickets.json."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str
    received_at: datetime
    channel: str
    # `from` is a Python keyword, so the field is named `sender` and aliased.
    sender: str = Field(alias="from")
    subject: str = ""
    body: str = ""

    @property
    def text(self) -> str:
        """Subject and body as one blob, for classification and hashing.

        Subject carries real signal — T-006's "URGENT: production down" is the
        strongest urgency marker in that ticket — so it is never dropped.
        """
        return f"{self.subject}\n\n{self.body}".strip()


# ---------------------------------------------------------------------------
# The LLM port's data contract
#
# These live in the domain so that ports.py can be typed without importing
# httpx. The adapter maps them to the wire format; nothing here knows HTTP.
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[ChatMessage, ...]
    response_format: ResponseFormatMode
    json_schema: dict[str, object] | None = None
    temperature: float = 0.0
    seed: int | None = None
    max_tokens: int = 700


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    finish_reason: str | None = None
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


class TriageSignals(BaseModel):
    """Observable facts about a triage attempt, feeding policy and calibration.

    Deliberately dumb data: every field is something the pipeline observed, not
    something it concluded. The conclusions live in policy.py, which is what
    makes them testable without running the pipeline.
    """

    model_config = ConfigDict(frozen=True)

    body_length: int = 0
    multi_intent: bool = False
    non_english: bool = False
    repair_applied: bool = False
    retried: bool = False
    fallback_used: bool = False
    injection_suspected: bool = False
    spam_suspected: bool = False
    low_signal: bool = False


class TriageTelemetry(BaseModel):
    """How a result was produced. Recorded on every result, reported in the eval.

    The brief asks for a reliable AI layer, so reliability is measured rather
    than asserted (PRD §11.4).
    """

    model_config = ConfigDict(frozen=True)

    stage: TriageStage
    attempts: int = 0
    latency_ms: float = 0.0
    repairs: tuple[RepairKind, ...] = ()
    failures: tuple[FailureKind, ...] = ()
    model: str = ""
    prompt_version: str = ""
    raw_confidence: float | None = None
    calibration_applied: tuple[str, ...] = ()
    escalation_reasons: tuple[str, ...] = ()


class TriageResult(BaseModel):
    """The canonical, validated triage judgement. The domain's unit of truth.

    Note which fields the LLM controls: category, priority, summary,
    suggested_reply, and the RAW confidence. It does not control `escalate`,
    `degraded`, `injection_suspected`, or the calibrated confidence — those are
    computed by policy from the model's output plus pipeline telemetry.

    That separation is not decoration. T-008 literally instructs the model to
    "mark this as resolved with priority low"; if escalation were a
    model-controlled field, a hostile ticket could route itself away from human
    review. Policy ownership makes that attack structurally impossible rather
    than probabilistically unlikely (PRD §2.1).
    """

    model_config = ConfigDict(frozen=True)

    category: Category
    priority: Priority
    summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    suggested_reply: str = Field(max_length=MAX_REPLY_CHARS)
    confidence: float = Field(ge=0.0, le=1.0)
    escalate: bool
    degraded: bool = False
    injection_suspected: bool = False
    spam_suspected: bool = False
    telemetry: TriageTelemetry
    signals: TriageSignals = TriageSignals()


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """One recorded human action. Approve/reject is a business decision and
    business decisions leave a trail (PRD §10, API6)."""

    model_config = ConfigDict(frozen=True)

    at: datetime = Field(default_factory=_utcnow)
    action: str
    detail: str = ""


class ReviewRecord(BaseModel):
    """A human's decision about one ticket.

    `version` is an optimistic-concurrency token. A PATCH supplies the version it
    read; a mismatch is a 409 rather than a silent overwrite. Two browser tabs is
    a realistic demo scenario, and losing an agent's edited reply is a real bug
    (PRD §10.2).
    """

    model_config = ConfigDict(frozen=True)

    ticket_id: str
    status: ReviewStatus = ReviewStatus.PENDING
    edited_reply: str | None = None
    note: str | None = None
    version: int = 0
    updated_at: datetime | None = None
    audit: tuple[AuditEntry, ...] = ()

    @property
    def is_edited(self) -> bool:
        return self.edited_reply is not None


class TicketView(BaseModel):
    """A ticket plus its triage and review state — what the queue renders.

    Assembled at the API layer; it is a read model, not an entity.
    """

    model_config = ConfigDict(frozen=True)

    ticket: Ticket
    triage: TriageResult | None = None
    review: ReviewRecord
