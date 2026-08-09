"""FastAPI dependency providers. The bridge from the container to the routes.

Routes ask for a capability and get one. They never construct anything, never
know which implementation they received, and in tests receive a different one via
`app.dependency_overrides` — which is the entire reason the API suite runs with
no disk and no network.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Path, Request

from app.core.config import Settings
from app.core.container import Container
from app.core.errors import NotFoundError
from app.core.security import verify_token
from app.domain.models import Ticket
from app.storage.review_repository import JsonReviewRepository
from app.storage.ticket_repository import TICKET_ID_PATTERN, JsonTicketRepository
from app.triage.service import TriageService


def get_container(request: Request) -> Container:
    container: Container | None = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only if lifespan did not run
        raise RuntimeError("Container is not initialised")
    return container


def get_settings_dep(
    container: Annotated[Container, Depends(get_container)],
) -> Settings:
    return container.settings


def get_ticket_repository(
    container: Annotated[Container, Depends(get_container)],
) -> JsonTicketRepository:
    return container.ticket_repository


def get_review_repository(
    container: Annotated[Container, Depends(get_container)],
) -> JsonReviewRepository:
    return container.review_repository


def get_triage_service(
    container: Annotated[Container, Depends(get_container)],
) -> TriageService:
    return container.triage_service


async def require_auth(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer check on mutating routes (OWASP API2, API5).

    A no-op when API_TOKEN is unset, which is the development default and is
    logged at startup so nobody assumes protection that is not configured.
    """
    verify_token(authorization, settings.api_token)


def get_ticket_or_404(
    ticket_id: Annotated[str, Path(pattern=TICKET_ID_PATTERN.pattern)],
    repository: Annotated[JsonTicketRepository, Depends(get_ticket_repository)],
) -> Ticket:
    """Resolve a ticket ID through the repository.

    The `pattern` on the path parameter rejects anything that is not `T-\\d{3}`
    with a 422 before this function runs, and the lookup itself is a dict access.
    Path traversal is therefore structurally impossible rather than defended
    against: no filesystem operation anywhere takes a ticket ID (OWASP API1).
    """
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise NotFoundError(f"No ticket with id {ticket_id}")
    return ticket


ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
TicketRepoDep = Annotated[JsonTicketRepository, Depends(get_ticket_repository)]
ReviewRepoDep = Annotated[JsonReviewRepository, Depends(get_review_repository)]
TriageServiceDep = Annotated[TriageService, Depends(get_triage_service)]
TicketDep = Annotated[Ticket, Depends(get_ticket_or_404)]
AuthDep = Annotated[None, Depends(require_auth)]
