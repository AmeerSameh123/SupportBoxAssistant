"""Coercing schema-shaped-but-wrong values into legal ones.

Separate from repair.py on purpose. This module changes when the model finds a new
way to *name* something; repair.py changes when it finds a new way to *mangle
JSON*. Two reasons to change, two modules — the SRP formulation that is actually
operational (PRD §5).

The synonym tables are declarative rather than a chain of `if`s so that adding a
newly observed synonym is a one-line data change with a one-line test, not a
control-flow edit.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.enums import Category, Priority, RepairKind
from app.domain.models import MAX_REPLY_CHARS, MAX_SUMMARY_CHARS

DRAFT_FIELDS = ("category", "priority", "summary", "suggested_reply", "confidence")

# Anything not already a legal value. Keys are lowercased and stripped of
# separators before lookup, so "Feature Request", "feature-request" and
# "FEATURE_REQUEST" all collapse to the same probe.
_CATEGORY_SYNONYMS: dict[str, Category] = {
    "featurerequest": Category.FEATURE_REQUEST,
    "feature": Category.FEATURE_REQUEST,
    "enhancement": Category.FEATURE_REQUEST,
    "improvement": Category.FEATURE_REQUEST,
    "billingissue": Category.BILLING,
    "billingquestion": Category.BILLING,
    "payment": Category.BILLING,
    "invoice": Category.BILLING,
    "refund": Category.BILLING,
    "bugreport": Category.BUG,
    "defect": Category.BUG,
    "issue": Category.BUG,
    "error": Category.BUG,
    "broken": Category.BUG,
    "accountissue": Category.ACCOUNT,
    "login": Category.ACCOUNT,
    "access": Category.ACCOUNT,
    "authentication": Category.ACCOUNT,
    "securityissue": Category.SECURITY,
    "vulnerability": Category.SECURITY,
    "vuln": Category.SECURITY,
    "spam": Category.OTHER,
    "general": Category.OTHER,
    "unknown": Category.OTHER,
    "none": Category.OTHER,
    "misc": Category.OTHER,
}

_PRIORITY_SYNONYMS: dict[str, Priority] = {
    "p0": Priority.URGENT,
    "p1": Priority.URGENT,
    "critical": Priority.URGENT,
    "blocker": Priority.URGENT,
    "emergency": Priority.URGENT,
    "immediate": Priority.URGENT,
    "highest": Priority.URGENT,
    "p2": Priority.HIGH,
    "important": Priority.HIGH,
    "elevated": Priority.HIGH,
    "p3": Priority.MEDIUM,
    "normal": Priority.MEDIUM,
    "moderate": Priority.MEDIUM,
    "standard": Priority.MEDIUM,
    "p4": Priority.LOW,
    "minor": Priority.LOW,
    "trivial": Priority.LOW,
    "lowest": Priority.LOW,
    "none": Priority.LOW,
}

# The model is asked for a number and sometimes answers in English.
_CONFIDENCE_WORDS: dict[str, float] = {
    "certain": 0.95,
    "veryhigh": 0.9,
    "high": 0.8,
    "medium": 0.5,
    "moderate": 0.5,
    "low": 0.2,
    "verylow": 0.1,
    "unsure": 0.1,
    "unknown": 0.0,
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_draft(raw: dict[str, Any]) -> tuple[dict[str, Any], tuple[RepairKind, ...]]:
    """Coerce a parsed object toward the draft schema.

    Returns the coerced dict plus the repairs that were needed, so the eval can
    report *what* had to be fixed rather than only *that* something was
    (PRD §11.4). Never raises: anything it cannot fix it leaves alone for the
    strict validator to reject with a precise, quotable error.
    """
    out: dict[str, Any] = {}
    repairs: list[RepairKind] = []

    # Drop keys the model invented. `extra="forbid"` on the draft would reject
    # the whole response otherwise, and an invented "notes" key is not a reason
    # to throw away a correct classification.
    for key in DRAFT_FIELDS:
        if key in raw:
            out[key] = raw[key]
    if set(raw) - set(DRAFT_FIELDS):
        repairs.append(RepairKind.TYPE_COERCED)

    _normalize_category(out, repairs)
    _normalize_priority(out, repairs)
    _normalize_confidence(out, repairs)
    _normalize_text_field(out, "summary", MAX_SUMMARY_CHARS, repairs)
    _normalize_text_field(out, "suggested_reply", MAX_REPLY_CHARS, repairs)

    return out, tuple(dict.fromkeys(repairs))


def _probe(value: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def coerce_category(value: Any) -> Category | None:
    """Public because the schema-echo recovery in repair.py needs it too."""
    if isinstance(value, Category):
        return value
    if not isinstance(value, str):
        return None
    probe = _probe(value)
    for member in Category:
        if _probe(member.value) == probe:
            return member
    return _CATEGORY_SYNONYMS.get(probe)


def coerce_priority(value: Any) -> Priority | None:
    if isinstance(value, Priority):
        return value
    if not isinstance(value, str):
        return None
    probe = _probe(value)
    for member in Priority:
        if _probe(member.value) == probe:
            return member
    return _PRIORITY_SYNONYMS.get(probe)


def _normalize_category(out: dict[str, Any], repairs: list[RepairKind]) -> None:
    value = out.get("category")
    coerced = coerce_category(value)
    if coerced is None:
        return  # leave it; the strict validator will name the problem
    if coerced.value != value:
        repairs.append(RepairKind.ENUM_SYNONYM)
    out["category"] = coerced.value


def _normalize_priority(out: dict[str, Any], repairs: list[RepairKind]) -> None:
    value = out.get("priority")
    coerced = coerce_priority(value)
    if coerced is None:
        return
    if coerced.value != value:
        repairs.append(RepairKind.ENUM_SYNONYM)
    out["priority"] = coerced.value


def _normalize_confidence(out: dict[str, Any], repairs: list[RepairKind]) -> None:
    value = out.get("confidence")

    if value is None:
        if "confidence" in out:
            out["confidence"] = 0.5
            repairs.append(RepairKind.NULL_DEFAULTED)
        return

    if isinstance(value, bool):
        # bool is an int in Python; treat it as a word, not a number.
        out["confidence"] = 0.9 if value else 0.1
        repairs.append(RepairKind.TYPE_COERCED)
        return

    if isinstance(value, int | float):
        out["confidence"] = _clamp(float(value), repairs)
        return

    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                out["confidence"] = _clamp(float(text[:-1]) / 100.0, repairs)
                repairs.append(RepairKind.TYPE_COERCED)
                return
            except ValueError:
                pass
        try:
            out["confidence"] = _clamp(float(text), repairs)
            repairs.append(RepairKind.TYPE_COERCED)
            return
        except ValueError:
            pass
        word = _CONFIDENCE_WORDS.get(_probe(text))
        if word is not None:
            out["confidence"] = word
            repairs.append(RepairKind.TYPE_COERCED)


def _clamp(value: float, repairs: list[RepairKind]) -> float:
    if value < 0.0 or value > 1.0:
        repairs.append(RepairKind.CLAMPED)
        return max(0.0, min(1.0, value))
    return value


def _normalize_text_field(
    out: dict[str, Any], field: str, limit: int, repairs: list[RepairKind]
) -> None:
    if field not in out:
        return
    value = out[field]

    if value is None:
        out[field] = ""
        repairs.append(RepairKind.NULL_DEFAULTED)
        return

    if not isinstance(value, str):
        # The model occasionally answers with a list of bullet points.
        value = " ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        repairs.append(RepairKind.TYPE_COERCED)

    cleaned = _CONTROL_CHARS.sub("", value).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 3].rstrip() + "..."
        repairs.append(RepairKind.TRUNCATED_FIELD)

    out[field] = cleaned
