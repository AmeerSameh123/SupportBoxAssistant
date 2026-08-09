"""Triage endpoints: ad-hoc text, and (re)running a corpus ticket."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AuthDep, TicketDep, TriageServiceDep
from app.api.schemas import TriageResponse, TriageTextRequest
from app.domain.models import Ticket

router = APIRouter(tags=["triage"])


@router.post("/triage", response_model=TriageResponse)
async def triage_text(
    payload: TriageTextRequest,
    service: TriageServiceDep,
    _: AuthDep,
) -> TriageResponse:
    """Triage arbitrary text. The reusable API deliverable.

    Always 200 with a result, including when the LLM is unreachable — in that
    case `degraded` is true, `escalate` is true, and the classification came from
    the heuristic fallback. Returning 503 here would be a worse answer than a
    labelled-degraded one (PRD §9).
    """
    # Built through model_validate so the "from" alias is used explicitly.
    # Constructing with `sender=` relies on populate_by_name and reads as a
    # different field name than the wire format actually uses.
    ticket = Ticket.model_validate(
        {
            "id": "T-000",
            "received_at": datetime.now(UTC),
            "channel": payload.channel,
            "from": "api@localhost",
            "subject": payload.subject,
            "body": payload.body,
        }
    )
    return TriageResponse.from_domain(await service.triage(ticket))


@router.post("/tickets/{ticket_id}/triage", response_model=TriageResponse)
async def triage_ticket(
    ticket: TicketDep,
    service: TriageServiceDep,
    _: AuthDep,
    force: Annotated[bool, Query(description="Bypass the cache and re-run.")] = False,
) -> TriageResponse:
    """(Re)run triage for a corpus ticket. `?force=true` bypasses the cache."""
    return TriageResponse.from_domain(await service.triage(ticket, force=force))
