"""The public API contract. Separate models from both the domain and the LLM.

This is where mass assignment is stopped (OWASP API3). Every request model sets
`extra="forbid"`, so an unknown field is a 422 rather than a silent acceptance —
and the server-owned fields (`confidence`, `escalate`, `degraded`, `version` on
responses) simply do not exist on any request model, so there is no field for a
client to set.

The same boundary contains the LLM: nothing the model produced reaches a client
without being re-serialised through these types (PRD §6.1, §10).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Category, Priority, ReviewStatus, TriageStage
from app.domain.models import MAX_REPLY_CHARS, TicketView, TriageResult

_STRICT = ConfigDict(extra="forbid")


# --- requests --------------------------------------------------------------


class TriageTextRequest(BaseModel):
    """Ad-hoc triage of arbitrary text — the reusable API deliverable.

    Note the absence of `confidence` and `escalate`. They are server-owned, so
    the defence against a client setting them is that there is nowhere to put
    them, not a check that could be forgotten.
    """

    model_config = _STRICT

    subject: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    channel: str = Field(default="api", max_length=50)


class ReviewUpdateRequest(BaseModel):
    """A human decision. `version` is required — see PRD §10.2."""

    model_config = _STRICT

    status: ReviewStatus
    edited_reply: str | None = Field(default=None, max_length=MAX_REPLY_CHARS)
    note: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=0, description="The version you last read. Mismatch -> 409.")


# --- responses -------------------------------------------------------------


class TelemetryResponse(BaseModel):
    stage: TriageStage
    attempts: int
    latency_ms: float
    repairs: list[str]
    failures: list[str]
    model: str
    prompt_version: str
    escalation_reasons: list[str]


class TriageResponse(BaseModel):
    category: Category
    priority: Priority
    summary: str
    suggested_reply: str
    confidence: float
    escalate: bool
    degraded: bool
    injection_suspected: bool
    spam_suspected: bool
    telemetry: TelemetryResponse

    @classmethod
    def from_domain(cls, result: TriageResult) -> TriageResponse:
        return cls(
            category=result.category,
            priority=result.priority,
            summary=result.summary,
            suggested_reply=result.suggested_reply,
            confidence=result.confidence,
            escalate=result.escalate,
            degraded=result.degraded,
            injection_suspected=result.injection_suspected,
            spam_suspected=result.spam_suspected,
            telemetry=TelemetryResponse(
                stage=result.telemetry.stage,
                attempts=result.telemetry.attempts,
                latency_ms=round(result.telemetry.latency_ms, 1),
                repairs=[r.value for r in result.telemetry.repairs],
                failures=[f.value for f in result.telemetry.failures],
                model=result.telemetry.model,
                prompt_version=result.telemetry.prompt_version,
                escalation_reasons=list(result.telemetry.escalation_reasons),
            ),
        )


class TicketResponse(BaseModel):
    id: str
    received_at: datetime
    channel: str
    sender: str
    subject: str
    body: str


class ReviewResponse(BaseModel):
    ticket_id: str
    status: ReviewStatus
    edited_reply: str | None
    note: str | None
    version: int
    updated_at: datetime | None


class TicketViewResponse(BaseModel):
    """What the queue renders: ticket + triage + review state."""

    ticket: TicketResponse
    triage: TriageResponse | None
    review: ReviewResponse

    @classmethod
    def from_domain(cls, view: TicketView) -> TicketViewResponse:
        return cls(
            ticket=TicketResponse(
                id=view.ticket.id,
                received_at=view.ticket.received_at,
                channel=view.ticket.channel,
                sender=view.ticket.sender,
                subject=view.ticket.subject,
                body=view.ticket.body,
            ),
            triage=TriageResponse.from_domain(view.triage) if view.triage else None,
            review=ReviewResponse(
                ticket_id=view.review.ticket_id,
                status=view.review.status,
                edited_reply=view.review.edited_reply,
                note=view.review.note,
                version=view.review.version,
                updated_at=view.review.updated_at,
            ),
        )


class TicketListResponse(BaseModel):
    items: list[TicketViewResponse]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    status: str
    llm_reachable: bool
    tickets_loaded: int
    model: str
    prompt_version: str
