"""The human decision endpoint.

This is the half of the brief that makes the system a review queue rather than a
classifier. Note what is NOT here: any way to send anything to a customer.
Approving records a decision; it transmits nothing. That is the brief's core
product constraint, enforced by the absence of the capability (PRD §10, API6).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AuthDep, ReviewRepoDep, TicketDep
from app.api.schemas import ReviewResponse, ReviewUpdateRequest

router = APIRouter(tags=["reviews"])


@router.patch("/reviews/{ticket_id}", response_model=ReviewResponse)
async def update_review(
    ticket: TicketDep,
    payload: ReviewUpdateRequest,
    reviews: ReviewRepoDep,
    _: AuthDep,
) -> ReviewResponse:
    """Approve, reject, or edit the draft reply.

    `version` must match what the client last read, or this returns 409 rather
    than overwriting. The losing write in that race is an agent's edited reply,
    which is a real bug and not a theoretical one — two browser tabs is a
    realistic demo (PRD §10.2).

    The ticket is resolved through `TicketDep` first, so a review cannot be
    written for an id that does not exist.
    """
    current = await reviews.get(ticket.id)
    saved = await reviews.save(
        current.model_copy(
            update={
                "status": payload.status,
                "edited_reply": payload.edited_reply,
                "note": payload.note,
            }
        ),
        expected_version=payload.version,
    )
    return ReviewResponse(
        ticket_id=saved.ticket_id,
        status=saved.status,
        edited_reply=saved.edited_reply,
        note=saved.note,
        version=saved.version,
        updated_at=saved.updated_at,
    )
