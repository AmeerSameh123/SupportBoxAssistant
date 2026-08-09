"""Liveness and readiness. Two endpoints because they answer two questions."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import ContainerDep, TicketRepoDep
from app.api.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: is the process up? Touches no dependency, so it can never fail
    for a reason the orchestrator would be wrong to restart on."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(
    container: ContainerDep,
    tickets: TicketRepoDep,
    response: Response,
) -> ReadinessResponse:
    """Readiness: can this instance actually do its job?

    Returns 503 when the LLM is unreachable — but note this is a *readiness*
    signal, not the behaviour of the triage endpoints. Those still return 200
    with `degraded=true`, because a degraded answer beats no answer (PRD §9).
    """
    reachable = await container.llm_reachable()
    if not reachable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if reachable else "degraded",
        llm_reachable=reachable,
        tickets_loaded=len(tickets),
        model=container.settings.llm_model,
        prompt_version=container.template.version,
    )
