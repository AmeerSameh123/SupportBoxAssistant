"""Business rules, extracted from I/O into pure functions.

Consequence of the extraction: the escalation logic is verified by table-driven
assertions that run in under a millisecond with no fixtures, and the rule set is
readable by someone who does not write Python. That is the whole argument for
keeping the domain layer free of I/O (PRD §6.1, Policy/Specification).

Nothing in this module performs I/O, reads configuration, or raises.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.enums import Category, ConfidenceMode, Priority
from app.domain.models import TriageResult, TriageSignals, TriageTelemetry

# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

DEFAULT_ESCALATE_THRESHOLD = 0.55


@dataclass(frozen=True, slots=True)
class EscalationInput:
    """Everything the escalation decision is allowed to depend on.

    Note what is *not* here: the ticket text, and any field the model could use
    to argue for its own de-escalation.
    """

    category: Category
    priority: Priority
    confidence: float
    degraded: bool = False
    injection_suspected: bool = False
    low_signal: bool = False
    spam_suspected: bool = False


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    escalate: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EscalationRule:
    """A named predicate. Adding a rule is appending to a list — no caller changes
    anywhere, which is the open/closed principle doing actual work (PRD §5, O)."""

    name: str
    applies: Callable[[EscalationInput], bool]


class EscalationPolicy:
    """Decides whether a triage result needs a human before anything else happens.

    `escalate` is a POLICY output, not a model output. The brief's results.json
    shape contains `escalate` but its per-message field list does not; that gap is
    resolved deliberately here (PRD §2.1).

    T-008 instructs the model to "mark this as resolved with priority low". Because
    escalation is computed here — from the model's output plus the pipeline's own
    telemetry — that instruction cannot succeed even if the model obeys it
    completely. The attack is structurally impossible, not merely unlikely.
    """

    def __init__(self, threshold: float = DEFAULT_ESCALATE_THRESHOLD) -> None:
        self._threshold = threshold
        self._rules: tuple[EscalationRule, ...] = (
            EscalationRule(
                "low_confidence",
                lambda i: i.confidence < self._threshold,
            ),
            EscalationRule(
                "security_category",
                lambda i: i.category is Category.SECURITY,
            ),
            EscalationRule(
                "urgent_priority",
                lambda i: i.priority is Priority.URGENT,
            ),
            EscalationRule(
                "degraded_pipeline",
                lambda i: i.degraded,
            ),
            EscalationRule(
                "injection_suspected",
                lambda i: i.injection_suspected,
            ),
            EscalationRule(
                "low_signal",
                lambda i: i.low_signal,
            ),
            EscalationRule(
                "spam_suspected",
                lambda i: i.spam_suspected,
            ),
        )

    @property
    def threshold(self) -> float:
        return self._threshold

    def decide(self, value: EscalationInput) -> EscalationDecision:
        """Return the decision plus every reason that fired.

        Reasons are surfaced in the UI and the eval report. "Escalated" tells a
        reviewer nothing; "escalated: security_category, urgent_priority" tells
        them what to look at first.
        """
        reasons = tuple(rule.name for rule in self._rules if rule.applies(value))
        return EscalationDecision(escalate=bool(reasons), reasons=reasons)


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------

SHORT_BODY_CHARS = 40

# Named, multiplicative, individually tested. Every factor is a constant in this
# one table so the whole calibration is auditable at a glance (PRD §7.2).
SHORT_BODY_FACTOR = 0.5
MULTI_INTENT_FACTOR = 0.7
NON_ENGLISH_FACTOR = 0.8
REPAIR_FACTOR = 0.85
RETRY_FACTOR = 0.8
FALLBACK_CEILING = 0.3


@dataclass(frozen=True, slots=True)
class CalibratedConfidence:
    value: float
    applied: tuple[str, ...]


class ConfidenceCalibrator:
    """Turns a 3B model's self-report into a number that means something.

    A 3B model's self-reported confidence is close to noise: it will happily say
    0.95 for T-018 ("doesnt work anymore. please fix asap"), which is genuinely
    un-triageable. Shipping that raw makes the field decorative, and the brief
    specifically wants confidence "so low-confidence items can be escalated".

    This is a heuristic and is labelled as one. PRD §11.5 measures whether it
    actually helped — mean confidence on correct vs incorrect predictions, raw and
    calibrated — and the eval reports the answer either way. That measurement is
    the difference between a heuristic and an unjustified one.
    """

    def __init__(self, mode: ConfidenceMode = ConfidenceMode.SELF_REPORTED) -> None:
        self._mode = mode

    @property
    def mode(self) -> ConfidenceMode:
        return self._mode

    def calibrate(self, raw: float, signals: TriageSignals) -> CalibratedConfidence:
        """Apply penalties in a fixed order and report which ones fired."""
        value = _clamp01(raw)
        applied: list[str] = []

        if signals.fallback_used:
            # Not a model judgement at all. A ceiling, not a multiplier — the
            # heuristic's confidence should not depend on what the model said,
            # because the model said nothing.
            value = min(value, FALLBACK_CEILING)
            applied.append("fallback_ceiling")
            return CalibratedConfidence(_clamp01(value), tuple(applied))

        if signals.low_signal:
            # The quality gate already decided there is nothing to go on.
            return CalibratedConfidence(0.0, ("low_signal_zero",))

        if 0 < signals.body_length < SHORT_BODY_CHARS:
            value *= SHORT_BODY_FACTOR
            applied.append("short_body")
        if signals.multi_intent:
            value *= MULTI_INTENT_FACTOR
            applied.append("multi_intent")
        if signals.non_english:
            value *= NON_ENGLISH_FACTOR
            applied.append("non_english")
        if signals.repair_applied:
            value *= REPAIR_FACTOR
            applied.append("repair_applied")
        if signals.retried:
            value *= RETRY_FACTOR
            applied.append("retried")

        return CalibratedConfidence(_clamp01(value), tuple(applied))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


class TriageAssembler:
    """Turns a raw classification into a TriageResult by applying both policies.

    Every TriageResult in the system is built here — by both strategies, by the
    quality gate, and by the cache-miss path. That single funnel is what makes
    the guarantee in PRD §2.1 checkable rather than aspirational: there is
    exactly one place where `escalate` and the final `confidence` are set, and
    the model's output is an *input* to it, never a source of it.
    """

    def __init__(
        self,
        policy: EscalationPolicy,
        calibrator: ConfidenceCalibrator,
    ) -> None:
        self._policy = policy
        self._calibrator = calibrator

    def assemble(
        self,
        *,
        category: Category,
        priority: Priority,
        summary: str,
        suggested_reply: str,
        raw_confidence: float,
        signals: TriageSignals,
        telemetry: TriageTelemetry,
    ) -> TriageResult:
        calibrated = self._calibrator.calibrate(raw_confidence, signals)
        decision = self._policy.decide(
            EscalationInput(
                category=category,
                priority=priority,
                confidence=calibrated.value,
                degraded=signals.fallback_used,
                injection_suspected=signals.injection_suspected,
                low_signal=signals.low_signal,
                spam_suspected=signals.spam_suspected,
            )
        )
        return TriageResult(
            category=category,
            priority=priority,
            summary=summary,
            suggested_reply=suggested_reply,
            confidence=calibrated.value,
            escalate=decision.escalate,
            degraded=signals.fallback_used,
            injection_suspected=signals.injection_suspected,
            spam_suspected=signals.spam_suspected,
            signals=signals,
            telemetry=telemetry.model_copy(
                update={
                    "raw_confidence": raw_confidence,
                    "calibration_applied": calibrated.applied,
                    "escalation_reasons": decision.reasons,
                }
            ),
        )
