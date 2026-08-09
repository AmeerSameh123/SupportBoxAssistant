"""The model-backed strategy: generate, salvage, validate, repair-retry.

This owns stages ③ and ④ of the cascade. It does NOT own transport retry — that
is `ResilientChatClient`'s job, one layer down — because the two retries are
answers to different questions:

    transport failure   -> "did the request arrive?"   -> retry the same request
    schema violation    -> "did the model understand?" -> retry a DIFFERENT request

At temperature 0 a blind retry of a schema violation reproduces the same bytes.
The only thing that changes the outcome is telling the model what was wrong, which
is why the repair prompt carries the validation error (PRD §7.1).
"""

from __future__ import annotations

from app.core.errors import LlmError, SchemaViolationError
from app.domain.enums import FailureKind, ResponseFormatMode, TriageStage
from app.domain.models import (
    MAX_REPLY_CHARS,
    MAX_SUMMARY_CHARS,
    ChatRequest,
    Ticket,
    TriageResult,
    TriageSignals,
    TriageTelemetry,
)
from app.domain.policy import TriageAssembler
from app.domain.ports import ChatClient
from app.llm.draft_schema import triage_json_schema
from app.llm.prompt import TriagePromptTemplate
from app.triage.heuristic_strategy import is_multi_intent, looks_non_english
from app.triage.repair import parse_draft
from app.triage.safety import (
    INJECTION_SAFE_REPLY,
    SPAM_SAFE_REPLY,
    SafetyScanner,
    sanitize_output,
)


class LlmTriageStrategy:
    """Implements TriageStrategy. Raises only AppError subclasses (see ports.py)."""

    def __init__(
        self,
        chat_client: ChatClient,
        *,
        template: TriagePromptTemplate,
        assembler: TriageAssembler,
        scanner: SafetyScanner | None = None,
        response_format: ResponseFormatMode = ResponseFormatMode.JSON_SCHEMA,
        temperature: float = 0.0,
        seed: int | None = 42,
        max_tokens: int = 700,
        max_repair_attempts: int = 2,
        model_name: str = "",
    ) -> None:
        self._client = chat_client
        self._template = template
        self._assembler = assembler
        self._scanner = scanner or SafetyScanner()
        self._response_format = response_format
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self._max_repair_attempts = max_repair_attempts
        self._model_name = model_name

    @property
    def name(self) -> str:
        return "llm"

    async def triage(self, ticket: Ticket) -> TriageResult:
        verdict = self._scanner.scan(ticket.text)
        schema = triage_json_schema()

        messages = self._template.render(ticket)
        failures: list[FailureKind] = []
        attempts = 0
        latency_total = 0.0
        last_violation: SchemaViolationError | None = None

        for attempt in range(self._max_repair_attempts + 1):
            attempts += 1
            try:
                response = await self._client.complete(
                    ChatRequest(
                        messages=messages,
                        response_format=self._response_format,
                        json_schema=schema,
                        temperature=self._temperature,
                        seed=self._seed,
                        max_tokens=self._max_tokens,
                    )
                )
            except LlmError as exc:
                # Transport retry already happened below this layer; if it
                # reaches here the endpoint is genuinely unavailable. Let it
                # propagate so the service can fall back — this strategy must not
                # silently substitute a different algorithm.
                failures.append(_failure_for(exc))
                raise

            latency_total += response.latency_ms

            try:
                draft, repair_kinds = parse_draft(response.content)
            except SchemaViolationError as exc:
                last_violation = exc
                failures.append(FailureKind.SCHEMA_INVALID)
                if attempt < self._max_repair_attempts:
                    messages = self._template.render_repair(
                        ticket, bad_output=response.content, error=exc.detail
                    )
                    continue
                raise

            return self._build(
                ticket=ticket,
                draft_category=draft.category,
                draft_priority=draft.priority,
                summary=draft.summary,
                reply=draft.suggested_reply,
                raw_confidence=draft.confidence,
                verdict_injection=verdict.injection_suspected,
                verdict_spam=verdict.spam_suspected,
                repair_kinds=repair_kinds,
                failures=tuple(failures),
                attempts=attempts,
                latency_ms=latency_total,
                retried=attempt > 0,
            )

        raise last_violation or SchemaViolationError("Triage failed")  # pragma: no cover

    # -----------------------------------------------------------------------

    def _build(
        self,
        *,
        ticket: Ticket,
        draft_category: object,
        draft_priority: object,
        summary: str,
        reply: str,
        raw_confidence: float,
        verdict_injection: bool,
        verdict_spam: bool,
        repair_kinds: tuple[object, ...],
        failures: tuple[FailureKind, ...],
        attempts: int,
        latency_ms: float,
        retried: bool,
    ) -> TriageResult:
        from app.domain.enums import Category, Priority, RepairKind  # local: typing only

        assert isinstance(draft_category, Category)
        assert isinstance(draft_priority, Priority)

        safe_summary = sanitize_output(summary, limit=MAX_SUMMARY_CHARS)
        safe_reply = self._safe_reply(reply, verdict_injection, verdict_spam)

        signals = TriageSignals(
            body_length=len(ticket.body.strip()),
            multi_intent=is_multi_intent(ticket.text),
            non_english=looks_non_english(ticket.text),
            repair_applied=bool(repair_kinds),
            retried=retried,
            fallback_used=False,
            injection_suspected=verdict_injection,
            spam_suspected=verdict_spam,
        )
        telemetry = TriageTelemetry(
            stage=TriageStage.LLM,
            attempts=attempts,
            latency_ms=latency_ms,
            repairs=tuple(k for k in repair_kinds if isinstance(k, RepairKind)),
            failures=failures,
            model=self._model_name,
            prompt_version=self._template.version,
        )
        return self._assembler.assemble(
            category=draft_category,
            priority=draft_priority,
            summary=safe_summary,
            suggested_reply=safe_reply,
            raw_confidence=raw_confidence,
            signals=signals,
            telemetry=telemetry,
        )

    def _safe_reply(self, reply: str, injection: bool, spam: bool) -> str:
        """Replace the draft when the ticket is hostile or is not a ticket.

        The model's own reply is discarded here rather than sanitized, because on
        an injection attempt the reply is the one field an attacker was steering.
        The ticket text itself is retained verbatim elsewhere for the human — this
        only governs what gets pre-written into the agent's editor (PRD §8 layer 5).
        """
        if injection:
            return INJECTION_SAFE_REPLY
        if spam:
            # Auto-drafting a courteous response to a phishing email is a
            # real-world harm, so T-015 gets an empty draft and a flag.
            return SPAM_SAFE_REPLY
        return sanitize_output(reply, limit=MAX_REPLY_CHARS)


def _failure_for(exc: LlmError) -> FailureKind:
    from app.core.errors import (
        CircuitOpenError,
        LlmProtocolError,
        LlmTimeoutError,
        LlmTransportError,
    )

    if isinstance(exc, CircuitOpenError):
        return FailureKind.CIRCUIT_OPEN
    if isinstance(exc, LlmTimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(exc, LlmTransportError):
        return FailureKind.TRANSPORT
    if isinstance(exc, LlmProtocolError):
        return FailureKind.EMPTY_COMPLETION
    return FailureKind.TRANSPORT
