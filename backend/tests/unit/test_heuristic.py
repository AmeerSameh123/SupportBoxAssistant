"""The deterministic fallback (app.triage.heuristic_strategy).

Held to a real standard rather than treated as dead code, because its accuracy is
a PUBLISHED BASELINE in the eval. If the LLM cannot beat regex, that is the most
useful finding in the report (PRD §11.3).
"""

from __future__ import annotations

import pytest

from app.domain.enums import Category, Priority, TriageStage
from app.domain.models import Ticket
from app.triage.heuristic_strategy import (
    HEURISTIC_CONFIDENCE,
    is_multi_intent,
    looks_non_english,
)


class TestTotality:
    """The property that makes this a safe terminal fallback: it cannot fail."""

    @pytest.mark.parametrize(
        "body",
        ["", "   ", "\x00\x01\x02", "x" * 50_000, "\U0001f642" * 100, "{}[]()<>|\\", "\n" * 100],
        # Explicit ids: a 50,000-character parametrize id makes the whole test
        # report unreadable, which is a real defect in a suite people must read.
        ids=[
            "empty",
            "whitespace",
            "control_chars",
            "very_long",
            "emoji",
            "punctuation",
            "newlines",
        ],
    )
    def test_never_raises_on_hostile_input(self, heuristic, body):
        ticket = Ticket(
            id="T-000",
            received_at="2026-06-22T08:00:00Z",
            channel="test",
            **{"from": "a@b.example"},
            subject="",
            body=body,
        )
        result = heuristic.triage_sync(ticket)
        assert result is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_always_marks_itself_degraded(self, heuristic, ticket_by_id):
        result = heuristic.triage_sync(ticket_by_id["T-001"])
        assert result.degraded is True
        assert result.escalate is True
        assert result.telemetry.stage is TriageStage.FALLBACK

    def test_confidence_never_exceeds_the_ceiling(self, heuristic, real_tickets):
        for ticket in real_tickets:
            assert heuristic.triage_sync(ticket).confidence <= HEURISTIC_CONFIDENCE


class TestClassification:
    @pytest.mark.parametrize(
        ("ticket_id", "expected"),
        [
            ("T-001", Category.BILLING),  # "charged twice", "refund"
            ("T-009", Category.BILLING),  # "invoice", "VAT"
            ("T-002", Category.BUG),  # "crashes", "freezes"
            ("T-006", Category.BUG),  # "503"
            ("T-014", Category.SECURITY),  # "IDOR", "responsibly"
            ("T-021", Category.ACCOUNT),  # "GDPR", "personal data"
            ("T-004", Category.OTHER),  # junk
        ],
    )
    def test_category_routing(self, heuristic, ticket_by_id, ticket_id, expected):
        assert heuristic.triage_sync(ticket_by_id[ticket_id]).category is expected

    def test_security_wins_ties(self, heuristic, ticket_by_id):
        """T-014 contains 'endpoint' and 'report', which also read as bug
        vocabulary. A false negative on a vulnerability report is far more
        expensive than a false positive."""
        assert heuristic.triage_sync(ticket_by_id["T-014"]).category is Category.SECURITY

    def test_spam_is_routed_to_other(self, heuristic, ticket_by_id):
        result = heuristic.triage_sync(ticket_by_id["T-015"])
        assert result.category is Category.OTHER
        assert result.spam_suspected is True


class TestPriority:
    @pytest.mark.parametrize(
        ("ticket_id", "expected"),
        [
            ("T-006", Priority.URGENT),  # "production down", "SLA", "503"
            ("T-014", Priority.URGENT),  # security is always urgent here
            ("T-003", Priority.LOW),  # "No rush"
            ("T-026", Priority.LOW),  # "Tiny thing"
        ],
    )
    def test_priority_markers(self, heuristic, ticket_by_id, ticket_id, expected):
        assert heuristic.triage_sync(ticket_by_id[ticket_id]).priority is expected

    def test_determinism(self, heuristic, ticket_by_id):
        """Same input, same output, every run. A 'deterministic fallback' that is
        not deterministic is just a second source of noise."""
        ticket = ticket_by_id["T-012"]
        results = {heuristic.triage_sync(ticket).priority for _ in range(5)}
        assert len(results) == 1


class TestReplies:
    def test_injection_gets_the_safe_reply(self, heuristic, ticket_by_id):
        result = heuristic.triage_sync(ticket_by_id["T-008"])
        assert result.injection_suspected is True
        assert "not been actioned" in result.suggested_reply

    def test_spam_gets_no_draft_reply(self, heuristic, ticket_by_id):
        """Auto-drafting a courteous response to a phishing email is a
        real-world harm."""
        assert heuristic.triage_sync(ticket_by_id["T-015"]).suggested_reply == ""

    def test_ordinary_reply_commits_to_nothing(self, heuristic, ticket_by_id):
        reply = heuristic.triage_sync(ticket_by_id["T-001"]).suggested_reply.lower()
        for promise in ("refund", "we will fix", "within 24", "guarantee", "sorry for"):
            assert promise not in reply


class TestSignalHelpers:
    def test_multi_intent_detects_t005(self, ticket_by_id):
        """T-005 is genuinely billing AND account; a single label is lossy and
        the confidence should say so."""
        assert is_multi_intent(ticket_by_id["T-005"].text) is True

    def test_single_intent_ticket_is_not_multi(self, ticket_by_id):
        assert is_multi_intent(ticket_by_id["T-026"].text) is False

    def test_spanish_is_detected(self, ticket_by_id):
        assert looks_non_english(ticket_by_id["T-010"].text) is True

    @pytest.mark.parametrize("ticket_id", ["T-001", "T-006", "T-024"])
    def test_english_is_not_flagged(self, ticket_by_id, ticket_id):
        assert looks_non_english(ticket_by_id[ticket_id].text) is False
