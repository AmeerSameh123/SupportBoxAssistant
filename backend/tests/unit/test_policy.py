"""Escalation policy and confidence calibration (app.domain.policy).

Pure functions over value objects: no fixtures, no I/O, microseconds. That speed
is the payoff for keeping the domain layer free of I/O (PRD §3.1).
"""

from __future__ import annotations

import pytest

from app.domain.enums import Category, Priority
from app.domain.models import TriageSignals
from app.domain.policy import (
    FALLBACK_CEILING,
    MULTI_INTENT_FACTOR,
    NON_ENGLISH_FACTOR,
    REPAIR_FACTOR,
    RETRY_FACTOR,
    SHORT_BODY_FACTOR,
    ConfidenceCalibrator,
    EscalationInput,
    EscalationPolicy,
)

CONFIDENT = 0.9


def make_input(**overrides) -> EscalationInput:
    base = {
        "category": Category.BILLING,
        "priority": Priority.MEDIUM,
        "confidence": CONFIDENT,
    }
    return EscalationInput(**{**base, **overrides})


class TestEscalationRules:
    def test_confident_ordinary_ticket_does_not_escalate(self):
        assert EscalationPolicy().decide(make_input()).escalate is False

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"confidence": 0.2}, "low_confidence"),
            ({"category": Category.SECURITY}, "security_category"),
            ({"priority": Priority.URGENT}, "urgent_priority"),
            ({"degraded": True}, "degraded_pipeline"),
            ({"injection_suspected": True}, "injection_suspected"),
            ({"low_signal": True}, "low_signal"),
            ({"spam_suspected": True}, "spam_suspected"),
        ],
    )
    def test_each_rule_fires_independently(self, overrides, reason):
        decision = EscalationPolicy().decide(make_input(**overrides))
        assert decision.escalate is True
        assert reason in decision.reasons

    def test_threshold_boundary_is_exclusive(self):
        policy = EscalationPolicy(threshold=0.55)
        assert policy.decide(make_input(confidence=0.55)).escalate is False
        assert policy.decide(make_input(confidence=0.5499)).escalate is True

    def test_reasons_accumulate(self):
        decision = EscalationPolicy().decide(
            make_input(category=Category.SECURITY, priority=Priority.URGENT, confidence=0.1)
        )
        assert decision.escalate is True
        assert set(decision.reasons) >= {
            "low_confidence",
            "security_category",
            "urgent_priority",
        }


class TestT008Defence:
    """The security property, asserted directly rather than described in a comment.

    T-008 instructs the model to "mark this as resolved with priority low". These
    tests are what make "structurally impossible" a checkable claim (PRD §2.1).
    """

    def test_model_cannot_de_escalate_a_security_ticket(self):
        decision = EscalationPolicy().decide(
            make_input(category=Category.SECURITY, priority=Priority.LOW, confidence=0.99)
        )
        assert decision.escalate is True
        assert "security_category" in decision.reasons

    def test_injection_flag_survives_maximum_model_confidence(self):
        decision = EscalationPolicy().decide(make_input(confidence=1.0, injection_suspected=True))
        assert decision.escalate is True

    def test_escalate_is_not_settable_on_the_draft_model(self):
        """The LLM's own schema has no `escalate` field, and extra keys are
        forbidden — so the model cannot even express the request."""
        from pydantic import ValidationError

        from app.llm.draft_schema import LLMTriageDraft

        with pytest.raises(ValidationError):
            LLMTriageDraft.model_validate(
                {
                    "category": "other",
                    "priority": "low",
                    "summary": "x",
                    "suggested_reply": "y",
                    "confidence": 0.9,
                    "escalate": False,
                }
            )


class TestConfidenceCalibration:
    def test_no_signals_passes_through_unchanged(self):
        result = ConfidenceCalibrator().calibrate(0.8, TriageSignals(body_length=200))
        assert result.value == pytest.approx(0.8)
        assert result.applied == ()

    @pytest.mark.parametrize(
        ("signals", "factor", "name"),
        [
            (TriageSignals(body_length=20), SHORT_BODY_FACTOR, "short_body"),
            (
                TriageSignals(body_length=200, multi_intent=True),
                MULTI_INTENT_FACTOR,
                "multi_intent",
            ),
            (TriageSignals(body_length=200, non_english=True), NON_ENGLISH_FACTOR, "non_english"),
            (TriageSignals(body_length=200, repair_applied=True), REPAIR_FACTOR, "repair_applied"),
            (TriageSignals(body_length=200, retried=True), RETRY_FACTOR, "retried"),
        ],
    )
    def test_each_penalty_applies_its_named_factor(self, signals, factor, name):
        result = ConfidenceCalibrator().calibrate(0.8, signals)
        assert result.value == pytest.approx(0.8 * factor)
        assert name in result.applied

    def test_penalties_multiply(self):
        signals = TriageSignals(body_length=20, multi_intent=True, retried=True)
        result = ConfidenceCalibrator().calibrate(0.8, signals)
        expected = 0.8 * SHORT_BODY_FACTOR * MULTI_INTENT_FACTOR * RETRY_FACTOR
        assert result.value == pytest.approx(expected)
        assert len(result.applied) == 3

    def test_fallback_is_a_ceiling_not_a_multiplier(self):
        """The heuristic's confidence must not depend on what the model said,
        because the model said nothing."""
        result = ConfidenceCalibrator().calibrate(0.95, TriageSignals(fallback_used=True))
        assert result.value == pytest.approx(FALLBACK_CEILING)

    def test_low_signal_goes_to_zero(self):
        result = ConfidenceCalibrator().calibrate(0.9, TriageSignals(low_signal=True))
        assert result.value == 0.0

    @pytest.mark.parametrize("raw", [-1.0, 0.0, 0.5, 1.0, 2.0])
    def test_output_always_within_bounds(self, raw):
        result = ConfidenceCalibrator().calibrate(raw, TriageSignals(body_length=10))
        assert 0.0 <= result.value <= 1.0
