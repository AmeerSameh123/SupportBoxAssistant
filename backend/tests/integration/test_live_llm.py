"""The one test that talks to a real model.

    uv run pytest -m llm

Deselected by default (`addopts = -m 'not llm'`), because a suite that needs a
running Ollama is not a suite — CI must be green on a machine that has never
heard of llama3.2 (PRD §12).

So what is it for? The offline tests prove the pipeline handles every response
shape I have *seen*. This one proves the wire format is still right: that the
adapter, the prompt and the parser agree with what the real endpoint does today.
Those are different claims, and only this test can make the second one.

It skips rather than fails when Ollama is absent, so `pytest -m llm` is safe to
run anywhere.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.domain.enums import Category, Priority, TriageStage
from app.domain.policy import ConfidenceCalibrator, EscalationPolicy, TriageAssembler
from app.llm.client import OpenAICompatibleChatClient
from app.llm.prompt import TriagePromptTemplate
from app.llm.resilience import ResilientChatClient
from app.storage.cache import NullCache
from app.triage.heuristic_strategy import HeuristicTriageStrategy
from app.triage.llm_strategy import LlmTriageStrategy
from app.triage.quality_gate import QualityGate
from app.triage.service import TriageService

pytestmark = pytest.mark.llm


def _ollama_available(settings: Settings) -> bool:
    try:
        response = httpx.get(f"{settings.llm_base_url}/models", timeout=3.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


@pytest.fixture
def live_settings() -> Settings:
    settings = Settings(cache_enabled=False)
    if not _ollama_available(settings):
        pytest.skip(
            f"No LLM at {settings.llm_base_url}. Run `ollama serve` and `ollama pull llama3.2:3b`."
        )
    return settings


@pytest.fixture
async def live_service(live_settings: Settings):
    assembler = TriageAssembler(
        EscalationPolicy(live_settings.escalate_confidence_threshold),
        ConfidenceCalibrator(live_settings.confidence_mode),
    )
    async with httpx.AsyncClient(timeout=live_settings.llm_timeout_seconds) as http:
        raw = OpenAICompatibleChatClient(
            base_url=live_settings.llm_base_url,
            api_key=live_settings.llm_api_key,
            model=live_settings.llm_model,
            max_response_bytes=live_settings.llm_max_response_bytes,
            http_client=http,
        )
        template = TriagePromptTemplate()
        yield TriageService(
            primary=LlmTriageStrategy(
                ResilientChatClient(raw, max_retries=live_settings.llm_max_retries),
                template=template,
                assembler=assembler,
                response_format=live_settings.llm_response_format,
                temperature=live_settings.llm_temperature,
                seed=live_settings.llm_seed,
                max_tokens=live_settings.llm_max_tokens,
                model_name=live_settings.llm_model,
            ),
            fallback=HeuristicTriageStrategy(assembler),
            gate=QualityGate(live_settings.min_signal_chars),
            assembler=assembler,
            cache=NullCache(),
            prompt_version=template.version,
            model_name=live_settings.llm_model,
        )


async def test_real_model_returns_a_valid_result(live_service, ticket_by_id):
    """The wire format still works: adapter, prompt and parser agree with the
    endpoint as it behaves today."""
    result = await live_service.triage(ticket_by_id["T-001"])

    assert result.telemetry.stage is TriageStage.LLM, (
        "fell back to the heuristic - the LLM path is broken, not merely inaccurate"
    )
    assert result.degraded is False
    assert isinstance(result.category, Category)
    assert isinstance(result.priority, Priority)
    assert 0.0 <= result.confidence <= 1.0
    assert result.summary
    assert result.telemetry.latency_ms > 0


async def test_real_model_classifies_an_unambiguous_ticket_correctly(live_service, ticket_by_id):
    """T-001 is a duplicate charge with an invoice number. If the model cannot
    get this one, something is wrong with the prompt rather than with the model."""
    result = await live_service.triage(ticket_by_id["T-001"])
    assert result.category is Category.BILLING


async def test_real_model_cannot_de_escalate_an_injection(live_service, ticket_by_id):
    """T-008 against the live model. The offline contract test proves the policy
    holds when the model obeys the injection; this proves it end to end with
    whatever the real model actually does."""
    result = await live_service.triage(ticket_by_id["T-008"])

    assert result.injection_suspected is True
    assert result.escalate is True
    for leak in ("system prompt", "api key", "LLM_API_KEY", "Bearer"):
        assert leak.lower() not in result.suggested_reply.lower()


async def test_real_model_output_survives_the_schema(live_service, real_tickets):
    """Five varied tickets, including the ones that produced malformed output in
    the Day-0 spike. Every result must be schema-valid whatever comes back."""
    by_id = {t.id: t for t in real_tickets}
    for ticket_id in ("T-004", "T-014", "T-023", "T-028", "T-030"):
        result = await live_service.triage(by_id[ticket_id])
        assert len(result.summary) <= 200
        assert len(result.suggested_reply) <= 1500
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.escalate, bool)
