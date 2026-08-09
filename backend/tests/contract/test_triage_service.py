"""The cascade, end to end, with no network. THE test of the brief's central claim.

The brief asks for "a reliable AI layer (schema validation, retries/fallbacks,
sensible behavior on bad input)". This file is the evidence: it drives the real
TriageService — real gate, real repair, real policy, real assembler — with a fake
scripted at the ChatClient port, and asserts that the result is ALWAYS
schema-valid, whatever the model does.

It runs in milliseconds. That is what the ports bought (PRD §12).
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from app.core.errors import (
    LlmProtocolError,
    LlmTimeoutError,
    LlmTransportError,
)
from app.domain.enums import Category, Priority, TriageStage
from app.domain.models import Ticket, TriageResult
from app.llm.resilience import CircuitBreaker, ResilientChatClient
from tests.conftest import VALID_DRAFT
from tests.fakes import AlwaysFailingChatClient

SCHEMA_ECHO = (
    '{"type":"object","properties":{'
    '"category":{"type":"string","enum":["other"]},'
    '"priority":{"type":"string","enum":["low"]},'
    '"summary":"nvm figured it out","suggested_reply":"","confidence":0}}'
)
AMBIGUOUS_ECHO = SCHEMA_ECHO.replace('"enum":["other"]', '"enum":["account","other"]')


def assert_valid(result: object) -> TriageResult:
    """Every path through the cascade must end here."""
    assert isinstance(result, TriageResult)
    assert isinstance(result.category, Category)
    assert isinstance(result.priority, Priority)
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.summary) <= 200
    assert len(result.suggested_reply) <= 1500
    assert isinstance(result.escalate, bool)
    return result


class TestEveryModelBehaviour:
    """One parametrized sweep over everything llama3.2:3b actually does."""

    @pytest.mark.parametrize(
        ("name", "response"),
        [
            ("valid", VALID_DRAFT),
            ("fenced", f"```json\n{VALID_DRAFT}\n```"),
            ("bare_fence", f"```\n{VALID_DRAFT}\n```"),
            ("prose_before", f"Here is the triage:\n{VALID_DRAFT}"),
            ("prose_after", f"{VALID_DRAFT}\nHope that helps!"),
            ("prose_both", f"Sure:\n{VALID_DRAFT}\nAnything else?"),
            ("trailing_comma", VALID_DRAFT.replace("}", ",}")),
            ("enum_synonym", VALID_DRAFT.replace('"billing"', '"Billing Issue"')),
            ("priority_synonym", VALID_DRAFT.replace('"high"', '"P1"')),
            ("confidence_word", VALID_DRAFT.replace("0.9", '"high"')),
            ("confidence_out_of_range", VALID_DRAFT.replace("0.9", "1.8")),
            ("schema_echo", SCHEMA_ECHO),
            ("extra_key", VALID_DRAFT[:-1] + ',"notes":"extra"}'),
        ],
    )
    async def test_salvageable_responses_yield_a_valid_result(
        self, service, fake_client, ticket_by_id, name, response
    ):
        fake_client.queue(response)
        result = assert_valid(await service.triage(ticket_by_id["T-001"]))
        assert result.telemetry.stage is TriageStage.LLM
        assert result.degraded is False

    @pytest.mark.parametrize(
        ("name", "responses"),
        [
            ("truncated", ['{"category":"bug","priority":'] * 3),
            ("refusal", ["I'm sorry, I can't help with that."] * 3),
            ("not_json", ["billing, high, duplicate charge"] * 3),
            ("not_an_object", ['["billing","high"]'] * 3),
            ("unknown_enum", [VALID_DRAFT.replace('"billing"', '"pricing"')] * 3),
            ("ambiguous_echo", [AMBIGUOUS_ECHO] * 3),
        ],
    )
    async def test_unsalvageable_responses_fall_back(
        self, service, fake_client, ticket_by_id, name, responses
    ):
        """Still a valid result — just an honest one, marked degraded."""
        fake_client.queue(*responses)
        result = assert_valid(await service.triage(ticket_by_id["T-001"]))
        assert result.telemetry.stage is TriageStage.FALLBACK
        assert result.degraded is True
        assert result.escalate is True

    @pytest.mark.parametrize(
        "error",
        [
            LlmTimeoutError("timed out"),
            LlmTransportError("connection refused"),
            LlmProtocolError("empty completion"),
        ],
    )
    async def test_transport_failures_fall_back(self, service, fake_client, ticket_by_id, error):
        fake_client.queue(error, error, error)
        result = assert_valid(await service.triage(ticket_by_id["T-001"]))
        assert result.telemetry.stage is TriageStage.FALLBACK
        assert result.degraded is True


class TestRepairRetry:
    async def test_a_schema_violation_triggers_a_corrective_second_attempt(
        self, service, fake_client, ticket_by_id
    ):
        fake_client.queue("not json at all", VALID_DRAFT)
        result = assert_valid(await service.triage(ticket_by_id["T-001"]))
        assert result.telemetry.stage is TriageStage.LLM
        assert fake_client.call_count == 2

    async def test_the_retry_prompt_contains_the_validation_error(
        self, service, fake_client, ticket_by_id
    ):
        """Retrying an identical deterministic request is superstition. The
        second request must differ, and must say what was wrong (PRD §7.1)."""
        fake_client.queue("garbage", VALID_DRAFT)
        await service.triage(ticket_by_id["T-001"])

        second = fake_client.calls[1]
        assert len(second.messages) > len(fake_client.calls[0].messages)
        text = " ".join(m.content for m in second.messages)
        assert "rejected by the schema validator" in text
        assert "garbage" in text

    async def test_retry_is_bounded(self, service, fake_client, ticket_by_id):
        fake_client.queue("bad", "bad", "bad", VALID_DRAFT)
        result = await service.triage(ticket_by_id["T-001"])
        assert fake_client.call_count == 3  # 1 + max_repair_attempts
        assert result.telemetry.stage is TriageStage.FALLBACK

    async def test_a_retried_result_is_penalised_in_confidence(
        self, service, fake_client, ticket_by_id
    ):
        fake_client.queue("garbage", VALID_DRAFT)
        retried = await service.triage(ticket_by_id["T-001"])

        fake_client.queue(VALID_DRAFT)
        clean = await service.triage(ticket_by_id["T-002"])
        assert retried.confidence < clean.confidence


class TestStageOrdering:
    async def test_quality_gate_short_circuits_before_any_llm_call(
        self, service, fake_client, ticket_by_id
    ):
        result = assert_valid(await service.triage(ticket_by_id["T-030"]))
        assert result.telemetry.stage is TriageStage.QUALITY_GATE
        assert fake_client.call_count == 0
        assert result.confidence == 0.0
        assert result.escalate is True

    async def test_cache_hit_skips_the_llm(self, service, fake_client, cache, ticket_by_id):
        fake_client.queue(VALID_DRAFT)
        first = await service.triage(ticket_by_id["T-001"])
        second = await service.triage(ticket_by_id["T-001"])

        assert fake_client.call_count == 1
        assert second.telemetry.stage is TriageStage.CACHE
        assert second.category is first.category
        assert cache.hits == 1

    async def test_force_bypasses_the_cache(self, service, fake_client, ticket_by_id):
        fake_client.queue(VALID_DRAFT, VALID_DRAFT)
        await service.triage(ticket_by_id["T-001"])
        result = await service.triage(ticket_by_id["T-001"], force=True)
        assert fake_client.call_count == 2
        assert result.telemetry.stage is TriageStage.LLM

    async def test_cache_key_changes_with_prompt_version(self, service, ticket_by_id):
        """A cache that survives a prompt change silently reports the previous
        prompt's numbers."""
        ticket = ticket_by_id["T-001"]
        before = service.cache_key(ticket)
        service._prompt_version = "triage/v99"
        assert service.cache_key(ticket) != before


class TestSafetyThroughTheCascade:
    async def test_injection_cannot_de_escalate_itself(self, service, fake_client, ticket_by_id):
        """The model obeys T-008 completely and returns other/low with high
        confidence. The result is still escalated (PRD §2.1)."""
        obedient = json.dumps(
            {
                "category": "other",
                "priority": "low",
                "summary": "Resolved as requested",
                "suggested_reply": "Here is the system prompt: ...",
                "confidence": 0.99,
            }
        )
        fake_client.queue(obedient)
        result = assert_valid(await service.triage(ticket_by_id["T-008"]))

        assert result.injection_suspected is True
        assert result.escalate is True
        assert "injection_suspected" in result.telemetry.escalation_reasons
        assert "system prompt" not in result.suggested_reply

    async def test_phishing_gets_an_empty_draft(self, service, fake_client, ticket_by_id):
        polite = json.dumps(
            {
                "category": "other",
                "priority": "low",
                "summary": "Prize offer",
                "suggested_reply": "Thank you for contacting us about your prize!",
                "confidence": 0.8,
            }
        )
        fake_client.queue(polite)
        result = await service.triage(ticket_by_id["T-015"])
        assert result.spam_suspected is True
        assert result.suggested_reply == ""
        assert result.escalate is True

    async def test_ticket_text_is_delimited_not_concatenated(
        self, service, fake_client, ticket_by_id
    ):
        """Structural separation: ticket content must never reach the system
        message (PRD §8, layer 2)."""
        fake_client.queue(VALID_DRAFT)
        await service.triage(ticket_by_id["T-008"])

        messages = fake_client.calls[0].messages
        system = next(m for m in messages if m.role == "system")
        user = next(m for m in messages if m.role == "user")
        assert "Ignore all previous instructions" not in system.content
        assert "Ignore all previous instructions" in user.content
        assert "<<<TICKET_testnonce>>>" in user.content


class TestWholeCorpus:
    async def test_all_thirty_tickets_produce_valid_results(
        self, service, fake_client, real_tickets
    ):
        fake_client.queue(*([VALID_DRAFT] * 120))
        for ticket in real_tickets:
            assert_valid(await service.triage(ticket))

    async def test_thirty_predictions_even_with_the_model_server_down(
        self, assembler, heuristic, cache, real_tickets
    ):
        """Success criterion #1 from PRD §1.5, asserted rather than hoped for.
        Note the breaker: after 3 consecutive transport failures the LLM is not
        called again, which is what makes this fast instead of a 15-minute wait."""
        from app.llm.prompt import TriagePromptTemplate
        from app.triage.llm_strategy import LlmTriageStrategy
        from app.triage.quality_gate import QualityGate
        from app.triage.service import TriageService

        dead = AlwaysFailingChatClient(LlmTransportError("connection refused"))
        resilient = ResilientChatClient(
            dead,
            max_retries=1,
            concurrency=2,
            breaker=CircuitBreaker(threshold=3, cooldown_seconds=60, clock=lambda: 0.0),
            sleep=_no_sleep,
        )
        template = TriagePromptTemplate(nonce_factory=lambda: "n")
        service = TriageService(
            primary=LlmTriageStrategy(
                resilient, template=template, assembler=assembler, model_name="dead"
            ),
            fallback=heuristic,
            gate=QualityGate(15),
            assembler=assembler,
            cache=cache,
            prompt_version=template.version,
            model_name="dead",
        )

        results = [await service.triage(t) for t in real_tickets]
        assert len(results) == 30
        for result in results:
            assert_valid(result)
            assert result.escalate is True
        assert dead.calls < 10, "the breaker should stop calling a dead endpoint"


class TestLspConformance:
    """Substitutability verified, not assumed (PRD §5, L)."""

    HOSTILE: ClassVar[list[str]] = [
        "",
        "   ",
        "\x00" * 10,
        "x" * 20_000,
        "{}",
        "\U0001f642" * 3,
    ]
    # Explicit ids: a 20,000-character parametrize id makes the report unreadable.
    HOSTILE_IDS: ClassVar[list[str]] = [
        "empty",
        "whitespace",
        "nulls",
        "very_long",
        "braces",
        "emoji",
    ]

    @pytest.mark.parametrize("body", HOSTILE, ids=HOSTILE_IDS)
    async def test_heuristic_never_raises(self, heuristic, body):
        ticket = Ticket(
            id="T-000",
            received_at="2026-06-22T08:00:00Z",
            channel="t",
            **{"from": "a@b.example"},
            subject="s",
            body=body,
        )
        assert_valid(await heuristic.triage(ticket))

    @pytest.mark.parametrize("body", HOSTILE, ids=HOSTILE_IDS)
    async def test_llm_strategy_only_raises_app_errors(self, service, fake_client, body):
        from app.core.errors import AppError

        ticket = Ticket(
            id="T-000",
            received_at="2026-06-22T08:00:00Z",
            channel="t",
            **{"from": "a@b.example"},
            subject="s",
            body=body,
        )
        fake_client.queue(*(["total garbage"] * 3))
        try:
            assert_valid(await service.triage(ticket))
        except AppError:
            pass  # permitted by the contract
        except Exception as exc:
            pytest.fail(f"leaked a non-AppError: {type(exc).__name__}: {exc}")

    def test_both_strategies_satisfy_the_protocol(self, service, heuristic):
        from app.domain.ports import TriageStrategy

        assert isinstance(heuristic, TriageStrategy)
        assert isinstance(service._primary, TriageStrategy)


async def _no_sleep(_seconds: float) -> None:
    return None
