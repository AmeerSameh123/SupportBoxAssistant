"""The queue: list and detail. Read-only by construction."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ReviewRepoDep, TicketDep, TicketRepoDep, TriageServiceDep
from app.api.schemas import TicketListResponse, TicketViewResponse
from app.domain.enums import Category, ReviewStatus
from app.domain.models import TicketView

router = APIRouter(tags=["tickets"])


@router.get("/tickets", response_model=TicketListResponse)
async def list_tickets(
    tickets: TicketRepoDep,
    reviews: ReviewRepoDep,
    service: TriageServiceDep,
    status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    category: Annotated[Category | None, Query()] = None,
    escalated: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketListResponse:
    """The review queue.

    Triage is served FROM CACHE ONLY — listing never triggers a model call. A
    ticket with no cached triage returns `triage: null` and the frontend requests
    it per row with a visible loading state. Thirty synchronous model calls on
    first paint would make the queue unusable (PRD §16).

    Filters are typed enums, so an unknown value is a 422 rather than a silently
    empty list — an API that answers "no results" to a typo trains people to
    distrust it.
    """
    all_reviews = await reviews.list_all()
    views: list[TicketView] = []

    for ticket in tickets.list_all():
        review = all_reviews.get(ticket.id)
        if review is None:
            from app.domain.models import ReviewRecord

            review = ReviewRecord(ticket_id=ticket.id)
        triage = await service.get_cached(ticket)

        if status_filter is not None and review.status is not status_filter:
            continue
        if category is not None and (triage is None or triage.category is not category):
            continue
        if escalated is not None and (triage is None or triage.escalate is not escalated):
            continue

        views.append(TicketView(ticket=ticket, triage=triage, review=review))

    window = views[offset : offset + limit]
    return TicketListResponse(
        items=[TicketViewResponse.from_domain(v) for v in window],
        total=len(views),
        limit=limit,
        offset=offset,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketViewResponse)
async def get_ticket(
    ticket: TicketDep,
    reviews: ReviewRepoDep,
    service: TriageServiceDep,
) -> TicketViewResponse:
    """One ticket with its triage (cached only) and review state."""
    return TicketViewResponse.from_domain(
        TicketView(
            ticket=ticket,
            triage=await service.get_cached(ticket),
            review=await reviews.get(ticket.id),
        )
    )
