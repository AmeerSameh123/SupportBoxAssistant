"""The cascade orchestrator.

Deliberately a LINEAR method with four named private helpers rather than a Chain
of Responsibility. The cascade genuinely is a chain — but it has exactly four
fixed stages with no runtime reconfiguration, so CoR would add a framework to
express a straight line, and would replace a readable stack trace with a walk
through handler objects. Restraint is a design decision and this is one
(PRD §6.2).

    gate -> cache -> primary strategy -> fallback strategy
"""

from __future__ import annotations

import hashlib
import logging

from app.core.errors import AppError
from app.domain.enums import Category, Priority, TriageStage
from app.domain.models import Ticket, TriageResult, TriageSignals, TriageTelemetry
from app.domain.policy import TriageAssembler
from app.domain.ports import TriageCache, TriageStrategy
from app.triage.quality_gate import INSUFFICIENT_REPLY, QualityGate
from app.triage.safety import INJECTION_SAFE_REPLY, SafetyScanner

logger = logging.getLogger(__name__)


class TriageService:
    """Produces exactly one TriageResult per ticket, whatever happens."""

    def __init__(
        self,
        *,
        primary: TriageStrategy,
        fallback: TriageStrategy,
        gate: QualityGate,
        assembler: TriageAssembler,
        cache: TriageCache,
        scanner: SafetyScanner | None = None,
        prompt_version: str = "",
        model_name: str = "",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._gate = gate
        self._assembler = assembler
        self._cache = cache
        self._scanner = scanner or SafetyScanner()
        self._prompt_version = prompt_version
        self._model_name = model_name

    async def triage(self, ticket: Ticket, *, force: bool = False) -> TriageResult:
        """The whole cascade. Returns a valid result under every failure mode."""
        gated = self._gate_result(ticket)
        if gated is not None:
            return gated

        key = self.cache_key(ticket)
        if not force:
            cached = await self._cache.get(key)
            if cached is not None:
                return cached.model_copy(
                    update={
                        "telemetry": cached.telemetry.model_copy(
                            update={"stage": TriageStage.CACHE}
                        )
                    }
                )

        result = await self._run_strategies(ticket)
        await self._cache.set(key, result)
        return result

    async def get_cached(self, ticket: Ticket) -> TriageResult | None:
        """Cache lookup with no LLM call, ever.

        This is what makes the queue render instantly. Listing 30 tickets must
        never trigger 30 model calls — the frontend renders what is cached and
        requests the rest per ticket, with a visible loading state. A queue that
        blocks for a minute on first paint is a queue nobody uses (PRD §16).
        """
        gated = self._gate_result(ticket)
        if gated is not None:
            return gated
        cached = await self._cache.get(self.cache_key(ticket))
        if cached is None:
            return None
        return cached.model_copy(
            update={"telemetry": cached.telemetry.model_copy(update={"stage": TriageStage.CACHE})}
        )

    def cache_key(self, ticket: Ticket) -> str:
        """sha256(prompt_version || model || normalized text).

        The prompt version is in the key so that editing the prompt correctly
        invalidates every cached answer. A cache that survives a prompt change is
        worse than no cache: it makes an eval run silently report the previous
        prompt's numbers.
        """
        normalized = " ".join(ticket.text.split()).lower()
        material = f"{self._prompt_version}\x1f{self._model_name}\x1f{normalized}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    # -----------------------------------------------------------------------

    def _gate_result(self, ticket: Ticket) -> TriageResult | None:
        """Stage 1. Refuse to guess when there is nothing to go on."""
        verdict = self._gate.assess(ticket)
        if verdict is None:
            return None

        safety = self._scanner.scan(ticket.text)
        signals = TriageSignals(
            body_length=len(ticket.body.strip()),
            low_signal=True,
            injection_suspected=safety.injection_suspected,
            spam_suspected=safety.spam_suspected,
        )
        return self._assembler.assemble(
            category=Category.OTHER,
            priority=Priority.LOW,
            summary=verdict.summary,
            suggested_reply=(
                INJECTION_SAFE_REPLY if safety.injection_suspected else INSUFFICIENT_REPLY
            ),
            raw_confidence=0.0,
            signals=signals,
            telemetry=TriageTelemetry(
                stage=TriageStage.QUALITY_GATE,
                model=self._model_name,
                prompt_version=self._prompt_version,
                escalation_reasons=(verdict.reason,),
            ),
        )

    async def _run_strategies(self, ticket: Ticket) -> TriageResult:
        """Stages 3-5. The primary may fail; the fallback may not."""
        try:
            return await self._primary.triage(ticket)
        except AppError as exc:
            # Ticket ID, never ticket text: the corpus contains a VAT number and
            # a GDPR erasure request, and INFO-level bodies would be a
            # data-protection incident (PRD §10.3).
            logger.warning(
                "triage.primary_failed",
                extra={
                    "ticket_id": ticket.id,
                    "error_type": type(exc).__name__,
                    "stage": "primary",
                },
            )
            return await self._fallback.triage(ticket)
