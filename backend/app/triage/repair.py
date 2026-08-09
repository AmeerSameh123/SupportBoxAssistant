"""Salvaging a valid draft from whatever the model actually said.

This is stage ④ of the cascade (PRD §7.1). It exists because the premise of the
whole design is that the LLM is an untrusted network service returning a string,
not a function returning an object.

Everything here is driven by responses observed in the Day-0 spike against
llama3.2:3b, recorded in tests/data/malformed_outputs.json. The most valuable
finding was one I would not have invented: on short or ambiguous tickets the
model returns the JSON *Schema* instead of an instance — but commits its real
answer inside single-value `enum` arrays. See `_recover_schema_echo`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.errors import SchemaViolationError
from app.domain.enums import Category, Priority, RepairKind
from app.llm.draft_schema import LLMTriageDraft
from app.triage.normalize import (
    DRAFT_FIELDS,
    coerce_category,
    coerce_priority,
    normalize_draft,
)

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")

# Confidence assigned when a schema echo yields a classification but no
# self-report. Deliberately low: the model produced a malformed response, which
# is itself evidence that it was struggling with this input.
SCHEMA_ECHO_CONFIDENCE = 0.2
SCHEMA_ECHO_SUMMARY = "Model returned a malformed response; classification recovered."


def parse_draft(text: str) -> tuple[LLMTriageDraft, tuple[RepairKind, ...]]:
    """Extract → normalize → validate. The whole of stage ④ in one call.

    Raises SchemaViolationError when the response cannot be salvaged. The exception
    carries the field-level error so the repair-retry prompt can tell the model
    exactly what was wrong — a retry that does not say what was wrong is just a
    second roll of the same dice (PRD §7.1).
    """
    obj, repairs = extract_json_object(text)
    normalized, norm_repairs = normalize_draft(obj)
    repairs = tuple(dict.fromkeys(repairs + norm_repairs))

    try:
        draft = LLMTriageDraft.model_validate(normalized)
    except Exception as exc:
        raise SchemaViolationError(_summarize_validation_error(exc), raw=text) from exc

    return draft, repairs


def extract_json_object(text: str) -> tuple[dict[str, Any], tuple[RepairKind, ...]]:
    """Find and parse the first complete JSON object in a model response."""
    repairs: list[RepairKind] = []

    if not text or not text.strip():
        raise SchemaViolationError("Model returned an empty response", raw=text)

    working = text
    if "```" in working:
        match = _FENCE.search(working)
        if match:
            working = match.group(1)
            repairs.append(RepairKind.FENCED)
        else:
            # An opening fence with no closing one — a truncated response.
            working = working.replace("```json", "").replace("```", "")
            repairs.append(RepairKind.FENCED)

    blob, had_surrounding_prose = _find_balanced_object(working)
    if blob is None:
        raise SchemaViolationError("No JSON object found in the model response", raw=text)
    if had_surrounding_prose:
        repairs.append(RepairKind.PROSE_WRAPPED)

    obj = _loads_with_repairs(blob, repairs, original=text)

    if not isinstance(obj, dict):
        raise SchemaViolationError(f"Expected a JSON object, got {type(obj).__name__}", raw=text)

    recovered = _recover_schema_echo(obj)
    if recovered is not None:
        repairs.append(RepairKind.SCHEMA_ECHO)
        return recovered, tuple(dict.fromkeys(repairs))

    return obj, tuple(dict.fromkeys(repairs))


def _find_balanced_object(text: str) -> tuple[str | None, bool]:
    """Depth-scan for the first balanced `{...}`, ignoring braces inside strings.

    A greedy regex would either stop at the first `}` (breaking nested objects)
    or run to the last one (swallowing trailing prose). Neither survives real
    model output, so this is a small state machine instead — it tracks string
    context and backslash escapes, which is the only way `{"a":"}"}` parses
    correctly.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                continue  # stray closer before any opener
            depth -= 1
            if depth == 0 and start is not None:
                blob = text[start : index + 1]
                prose = bool(text[:start].strip()) or bool(text[index + 1 :].strip())
                return blob, prose

    if start is not None:
        # Opened but never closed: a max_tokens truncation. Returned as-is so
        # _loads_with_repairs can attempt to close it.
        return text[start:], bool(text[:start].strip())

    return None, False


def _loads_with_repairs(blob: str, repairs: list[RepairKind], *, original: str) -> Any:
    """json.loads, then a bounded ladder of structural repairs.

    Bounded on purpose. Each rung is a specific, observed malformation; there is
    no general "try to fix arbitrary broken JSON" step, because that silently
    invents data and the failure mode is a confident wrong answer rather than a
    visible error.
    """
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass

    candidate = _TRAILING_COMMA.sub(r"\1", blob)
    if candidate != blob:
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            blob = candidate
        else:
            repairs.append(RepairKind.TRAILING_COMMA)
            return result

    if "'" in blob and '"' not in blob:
        candidate = blob.replace("'", '"')
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            repairs.append(RepairKind.SINGLE_QUOTES)
            return result

    closed = _close_truncated(blob)
    if closed is not None:
        try:
            result = json.loads(closed)
        except json.JSONDecodeError:
            pass
        else:
            repairs.append(RepairKind.UNTERMINATED_STRING)
            return result

    raise SchemaViolationError(
        "Model response could not be parsed as JSON after repair", raw=original
    )


def _close_truncated(blob: str) -> str | None:
    """Close an unterminated string and any open braces/brackets.

    Recovers the fields the model *did* finish. The unfinished field is usually
    missing afterwards, so the strict validator still rejects it — which is
    correct. This rung exists so a response truncated after `suggested_reply`
    is not thrown away when everything needed is already present.
    """
    in_string = False
    escaped = False
    stack: list[str] = []

    for char in blob:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    if not in_string and not stack:
        return None

    repaired = blob
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _recover_schema_echo(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Recover an instance from a response that is shaped like the schema.

    THE DAY-0 FINDING. On short or ambiguous tickets llama3.2:3b returns the JSON
    Schema rather than an instance — but it narrows the `enum` arrays to the
    answer it wanted to give:

        {"type":"object","properties":{
            "category":{"type":"string","enum":["other"]},
            "priority":{"type":"string","enum":["low"]},
            "summary":"Empty message","suggested_reply":"","confidence":0}}

    The rule: intersect each echoed enum with the legal values. Exactly one
    survivor is a commitment and is used. More than one is NOT a commitment and
    must not be guessed at — that response goes to repair-retry.

    The distinction is load-bearing and both sides are fixtures:
      T-008 echoes ["other","low"]; "low" is not a legal category, so exactly one
            legal value survives -> recovered as "other".
      T-024 echoes ["account","other"]; both are legal, the model chose nothing,
            so it is rejected rather than resolved by coin-flip.
      T-009 echoes all six -> rejected.

    Returns None when the object is not a schema echo at all, which is the
    overwhelmingly common case.
    """
    looks_like_schema = obj.get("type") == "object" or "properties" in obj
    if not looks_like_schema:
        return None

    properties = obj.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    category = _commitment(properties.get("category"), Category, coerce_category)
    priority = _commitment(properties.get("priority"), Priority, coerce_priority)
    if category is None or priority is None:
        return None

    recovered: dict[str, Any] = {
        "category": category.value,
        "priority": priority.value,
        "summary": _scalar(properties, "summary") or SCHEMA_ECHO_SUMMARY,
        "suggested_reply": _scalar(properties, "suggested_reply") or "",
        "confidence": SCHEMA_ECHO_CONFIDENCE,
    }

    echoed_confidence = properties.get("confidence")
    if isinstance(echoed_confidence, int | float) and not isinstance(echoed_confidence, bool):
        # The model gave a real number; trust it, but never above the ceiling —
        # a malformed response is not a confident one.
        recovered["confidence"] = min(float(echoed_confidence), SCHEMA_ECHO_CONFIDENCE)

    return recovered


def _commitment(
    node: Any,
    enum_cls: type[Category] | type[Priority],
    coerce: Any,
) -> Any:
    """Extract a single committed enum value from a schema-shaped node."""
    if isinstance(node, str):
        return coerce(node)

    if not isinstance(node, dict):
        return None

    if "const" in node:
        return coerce(node["const"])

    values = node.get("enum")
    if not isinstance(values, list):
        return None

    legal = {member.value for member in enum_cls}
    survivors = {v for v in values if isinstance(v, str) and v in legal}
    if len(survivors) == 1:
        return coerce(survivors.pop())
    return None


def _scalar(properties: dict[str, Any], field: str) -> str | None:
    """Read a field only if the echo contains a real value rather than a schema."""
    value = properties.get(field)
    if isinstance(value, str):
        return value
    return None


def _summarize_validation_error(exc: Exception) -> str:
    """A short, quotable message for the repair-retry prompt.

    Pydantic's full error dump is too long to paste back into a 3B model's
    context without crowding out the ticket itself.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)[:300]

    parts: list[str] = []
    for err in errors()[:4]:
        location = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{location}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or str(exc)[:300]


__all__ = ["DRAFT_FIELDS", "extract_json_object", "parse_draft"]
