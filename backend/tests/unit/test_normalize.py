"""Enum synonyms and value coercion (app.triage.normalize)."""

from __future__ import annotations

import pytest

from app.domain.enums import Category, Priority, RepairKind
from app.triage.normalize import (
    coerce_category,
    coerce_priority,
    normalize_draft,
)


class TestCategorySynonyms:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("feature_request", Category.FEATURE_REQUEST),
            ("Feature Request", Category.FEATURE_REQUEST),
            ("feature request", Category.FEATURE_REQUEST),
            ("feature-request", Category.FEATURE_REQUEST),
            ("FEATURE_REQUEST", Category.FEATURE_REQUEST),
            ("enhancement", Category.FEATURE_REQUEST),
            ("Billing Issue", Category.BILLING),
            (" billing ", Category.BILLING),
            ("Bug Report", Category.BUG),
            ("defect", Category.BUG),
            ("vulnerability", Category.SECURITY),
        ],
    )
    def test_synonyms_map_to_canonical(self, raw, expected):
        assert coerce_category(raw) is expected

    def test_unknown_value_is_not_silently_defaulted(self):
        """Returning `other` for an unrecognised label would convert a visible
        model error into an invisible wrong answer."""
        assert coerce_category("pricing_question") is None
        assert coerce_category(42) is None
        assert coerce_category(None) is None

    def test_space_separated_form_is_input_only(self):
        """The brief's prose says "feature request"; labels.json says
        "feature_request". We accept both and emit only the scored one."""
        out, _ = normalize_draft({"category": "feature request"})
        assert out["category"] == "feature_request"


class TestPrioritySynonyms:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("P1", Priority.URGENT),
            ("p0", Priority.URGENT),
            ("critical", Priority.URGENT),
            ("blocker", Priority.URGENT),
            ("High", Priority.HIGH),
            ("P2", Priority.HIGH),
            ("normal", Priority.MEDIUM),
            ("trivial", Priority.LOW),
        ],
    )
    def test_synonyms(self, raw, expected):
        assert coerce_priority(raw) is expected


class TestConfidence:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0.85, 0.85),
            (1, 1.0),
            ("0.85", 0.85),
            ("90%", 0.9),
            ("high", 0.8),
            ("medium", 0.5),
            ("low", 0.2),
            (True, 0.9),
        ],
    )
    def test_coercion(self, raw, expected):
        out, _ = normalize_draft({"confidence": raw})
        assert out["confidence"] == pytest.approx(expected)

    @pytest.mark.parametrize(("raw", "expected"), [(1.7, 1.0), (-0.2, 0.0)])
    def test_out_of_range_is_clamped_not_rejected(self, raw, expected):
        out, repairs = normalize_draft({"confidence": raw})
        assert out["confidence"] == expected
        assert RepairKind.CLAMPED in repairs

    def test_null_gets_a_neutral_default(self):
        out, repairs = normalize_draft({"confidence": None})
        assert out["confidence"] == 0.5
        assert RepairKind.NULL_DEFAULTED in repairs

    def test_unparseable_is_left_for_the_validator(self):
        out, _ = normalize_draft({"confidence": "banana"})
        assert out["confidence"] == "banana"


class TestTextFields:
    def test_summary_is_truncated_to_the_bound(self):
        out, repairs = normalize_draft({"summary": "x" * 500})
        assert len(out["summary"]) <= 200
        assert RepairKind.TRUNCATED_FIELD in repairs

    def test_control_characters_are_stripped(self):
        out, _ = normalize_draft({"summary": "hel\x00lo\x07"})
        assert out["summary"] == "hello"

    def test_null_reply_becomes_empty_string(self):
        out, repairs = normalize_draft({"suggested_reply": None})
        assert out["suggested_reply"] == ""
        assert RepairKind.NULL_DEFAULTED in repairs

    def test_list_valued_field_is_joined(self):
        out, _ = normalize_draft({"suggested_reply": ["line one", "line two"]})
        assert out["suggested_reply"] == "line one line two"


def test_invented_keys_are_dropped_not_fatal():
    """The draft model forbids extra fields, so an invented key would otherwise
    reject an otherwise-correct classification."""
    out, repairs = normalize_draft({"category": "bug", "priority": "low", "notes": "I added this"})
    assert "notes" not in out
    assert RepairKind.TYPE_COERCED in repairs
