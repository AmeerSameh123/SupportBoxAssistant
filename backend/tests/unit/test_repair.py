"""Salvaging a valid draft from malformed model output (app.triage.repair).

Table-driven over tests/data/malformed_outputs.json. Six of those cases are REAL
llama3.2:3b responses captured in the Day-0 spike; the rest are labelled
`constructed` and cover classic malformations the 30-ticket spike did not happen
to produce. Fixtures I invented alone would only prove I can defeat my own
imagination, which is why the captured ones exist and are marked.
"""

from __future__ import annotations

import pytest

from app.core.errors import SchemaViolationError
from app.domain.enums import RepairKind
from app.triage.repair import parse_draft


def _cases(all_cases, expect):
    return [c for c in all_cases if c["expect"] == expect]


def test_fixture_file_contains_real_captures(malformed_cases):
    """Guards the honesty claim itself: if the captured cases were ever silently
    replaced with invented ones, this fails."""
    captured = [c for c in malformed_cases if c["source"] == "captured"]
    assert len(captured) >= 6
    assert all(c.get("ticket_id", "").startswith("T-") for c in captured)


class TestRecoverable:
    def test_every_recoverable_case_parses(self, malformed_cases):
        failures = []
        for case in _cases(malformed_cases, "recovered") + _cases(malformed_cases, "clean"):
            try:
                parse_draft(case["raw"])
            except SchemaViolationError as exc:
                failures.append(f"{case['name']}: {exc.detail}")
        assert not failures, "cases that should have been salvaged:\n" + "\n".join(failures)

    def test_recovered_fields_match_expectations(self, malformed_cases):
        for case in malformed_cases:
            expected = case.get("expect_fields")
            if not expected:
                continue
            draft, _ = parse_draft(case["raw"])
            for field, value in expected.items():
                actual = getattr(draft, field)
                actual = actual.value if hasattr(actual, "value") else actual
                assert actual == value, f"{case['name']}.{field}: {actual!r} != {value!r}"

    def test_clean_output_reports_no_repairs(self, malformed_cases):
        clean = _cases(malformed_cases, "clean")[0]
        _, repairs = parse_draft(clean["raw"])
        assert repairs == ()


class TestUnrecoverable:
    def test_every_unrecoverable_case_raises(self, malformed_cases):
        survived = []
        for case in _cases(malformed_cases, "unrecoverable"):
            try:
                parse_draft(case["raw"])
                survived.append(case["name"])
            except SchemaViolationError:
                pass
        assert not survived, (
            "these should NOT have parsed - silently inventing data is worse "
            f"than failing: {survived}"
        )

    def test_violation_carries_a_usable_message(self):
        """The repair-retry prompt pastes this back to the model. An empty or
        generic message makes the retry a second roll of the same dice."""
        with pytest.raises(SchemaViolationError) as excinfo:
            parse_draft(
                '{"category":"nonsense","priority":"high","summary":"x",'
                '"suggested_reply":"y","confidence":0.5}'
            )
        assert excinfo.value.detail
        assert "category" in excinfo.value.detail


class TestExtraction:
    """The balanced-brace scanner. A greedy regex fails all of these."""

    def test_nested_objects_survive(self):
        raw = (
            '{"category":"bug","priority":"high","summary":"x",'
            '"suggested_reply":"y","confidence":0.5,"meta":{"a":{"b":1}}}'
        )
        draft, _ = parse_draft(raw)
        assert draft.category.value == "bug"

    def test_braces_inside_strings_do_not_confuse_the_scanner(self):
        raw = (
            '{"category":"bug","priority":"low","summary":"use {placeholder} here",'
            '"suggested_reply":"a } brace","confidence":0.5}'
        )
        draft, _ = parse_draft(raw)
        assert "{placeholder}" in draft.summary

    def test_escaped_quote_inside_string(self):
        raw = (
            '{"category":"bug","priority":"low","summary":"he said \\"hi\\"",'
            '"suggested_reply":"y","confidence":0.5}'
        )
        draft, _ = parse_draft(raw)
        assert '"hi"' in draft.summary

    @pytest.mark.parametrize(
        ("raw", "kind"),
        [
            (
                '```json\n{"category":"bug","priority":"low","summary":"x",'
                '"suggested_reply":"y","confidence":0.5}\n```',
                RepairKind.FENCED,
            ),
            (
                'Here you go: {"category":"bug","priority":"low","summary":"x",'
                '"suggested_reply":"y","confidence":0.5}',
                RepairKind.PROSE_WRAPPED,
            ),
            (
                '{"category":"bug","priority":"low","summary":"x",'
                '"suggested_reply":"y","confidence":0.5,}',
                RepairKind.TRAILING_COMMA,
            ),
        ],
    )
    def test_repair_kind_is_reported(self, raw, kind):
        """The taxonomy is evidence in the eval report, so it has to be accurate,
        not merely present (PRD §11.4)."""
        _, repairs = parse_draft(raw)
        assert kind in repairs


class TestSchemaEcho:
    """The Day-0 finding: the model returns the SCHEMA, with its answer hidden
    inside single-value enum arrays."""

    ECHO = (
        '{"type":"object","properties":{'
        '"category":{"type":"string","enum":["other"]},'
        '"priority":{"type":"string","enum":["low"]},'
        '"summary":"empty message","suggested_reply":"","confidence":0}}'
    )

    def test_single_value_enum_is_treated_as_a_commitment(self):
        draft, repairs = parse_draft(self.ECHO)
        assert draft.category.value == "other"
        assert draft.priority.value == "low"
        assert RepairKind.SCHEMA_ECHO in repairs

    def test_ambiguous_enum_is_not_guessed_at(self):
        """Two legal values surviving means the model chose nothing. Resolving
        that by coin-flip would be inventing an answer and calling it a repair."""
        ambiguous = self.ECHO.replace('"enum":["other"]', '"enum":["account","other"]')
        with pytest.raises(SchemaViolationError):
            parse_draft(ambiguous)

    def test_full_enum_echo_is_rejected(self):
        full = self.ECHO.replace(
            '"enum":["other"]',
            '"enum":["billing","bug","feature_request","account","security","other"]',
        )
        with pytest.raises(SchemaViolationError):
            parse_draft(full)

    def test_recovered_confidence_is_capped(self):
        """A malformed response is not a confident one, whatever number it
        contains."""
        optimistic = self.ECHO.replace('"confidence":0', '"confidence":0.99')
        draft, _ = parse_draft(optimistic)
        assert draft.confidence <= 0.2
