"""The pre-LLM screen (app.triage.quality_gate).

More negative cases than positive ones, deliberately. A false negative costs one
wasted 1.5s model call; a false positive silently drops a real customer's ticket.
Those are not symmetric (PRD §7.1 stage 1).
"""

from __future__ import annotations

import pytest

from app.triage.quality_gate import QualityGate


@pytest.fixture
def gate() -> QualityGate:
    return QualityGate(min_signal_chars=15)


class TestGateFires:
    def test_empty_body_and_subject(self, gate, ticket_by_id):
        verdict = gate.assess(ticket_by_id["T-030"].model_copy(update={"subject": ""}))
        assert verdict is not None
        assert verdict.reason == "empty_message"

    def test_whitespace_only(self, gate, ticket_by_id):
        ticket = ticket_by_id["T-030"].model_copy(update={"subject": "   ", "body": "\n\t  "})
        assert gate.assess(ticket) is not None

    def test_keyboard_mash_t004(self, gate, ticket_by_id):
        """T-004 is 'asdkjhasd test test ignore' - 26 chars, so length alone does
        not catch it. The mash detector does."""
        verdict = gate.assess(ticket_by_id["T-004"])
        assert verdict is not None
        assert verdict.reason == "no_lexical_content"

    def test_below_min_signal_chars(self, gate, ticket_by_id):
        ticket = ticket_by_id["T-001"].model_copy(update={"subject": "", "body": "hi"})
        verdict = gate.assess(ticket)
        assert verdict is not None
        assert verdict.reason == "below_min_signal_chars"


class TestGateDoesNotFire:
    """The conservative half. Every one of these is a real ticket."""

    @pytest.mark.parametrize(
        "ticket_id",
        [
            "T-001",  # ordinary billing
            "T-018",  # "doesnt work anymore. please fix asap" - low info, real
            "T-023",  # "nvm figured it out, thanks anyway" - short, real
            "T-028",  # short billing question with numbers
            "T-010",  # Spanish: non-English is not low-signal
            "T-008",  # injection is a real message and must reach classification
            "T-026",  # a typo report is trivial but genuine
        ],
    )
    def test_real_tickets_pass_through(self, gate, ticket_by_id, ticket_id):
        assert gate.assess(ticket_by_id[ticket_id]) is None

    def test_word_test_in_a_real_sentence_is_not_junk(self, gate, ticket_by_id):
        ticket = ticket_by_id["T-001"].model_copy(
            update={"subject": "", "body": "Our test environment is returning 503 errors."}
        )
        assert gate.assess(ticket) is None


class TestGateProperties:
    def test_gate_performs_no_io(self, gate, ticket_by_id):
        """Asserted structurally: the gate takes only a Ticket and returns a
        verdict. If it grew a dependency this signature would have to change."""
        import inspect

        signature = inspect.signature(gate.assess)
        assert list(signature.parameters) == ["ticket"]

    def test_verdict_carries_a_reason_for_the_reviewer(self, gate, ticket_by_id):
        verdict = gate.assess(ticket_by_id["T-004"])
        assert verdict is not None
        assert verdict.reason and verdict.summary
