"""The domain vocabulary. Single source of truth for every enumerated value.

Every other layer derives from this module — the prompt text, the JSON schema sent
to the model, the API documentation, the frontend types, and the eval. No string
literal for a category or a priority is written twice anywhere in the codebase
(PRD §2).

Decision — snake_case wins. The task email prose says "feature request" (with a
space); labels.json says "feature_request". The eval is scored against
labels.json, so feature_request is canonical. The space-separated form is accepted
as an INPUT synonym by app.triage.normalize and is never emitted (ADR-0006).
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    """The six legal triage categories, exactly as labels.json spells them."""

    BILLING = "billing"
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    ACCOUNT = "account"
    SECURITY = "security"
    OTHER = "other"


class Priority(StrEnum):
    """The four legal priorities.

    The rubric (PRD §2) is written down so the prompt, the heuristic fallback and
    the human all mean the same thing:

        urgent - active breakage of a paid/production capability, or a live
                 security exposure. Is something broken *right now* for someone
                 who is blocked?
        high   - money already lost, access already lost, or a repeatedly
                 reported unresolved failure. Hours matter.
        medium - a real problem or commercial question, but the user is
                 functioning. Days matter.
        low    - nice-to-have, informational, cosmetic, or already self-resolved.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        """Ordinal position, low=0 .. urgent=3.

        Exists so `priority_within_one` in the eval compares distance on the
        scale rather than strings. A system that never misses by more than one
        level is operationally very different from one that calls `low` on an
        outage, and string equality cannot see that difference (PRD §11.2).
        """
        return _PRIORITY_RANK[self]


_PRIORITY_RANK: dict[Priority, int] = {
    Priority.LOW: 0,
    Priority.MEDIUM: 1,
    Priority.HIGH: 2,
    Priority.URGENT: 3,
}


class ReviewStatus(StrEnum):
    """Where a ticket sits in the human review queue.

    Note what is absent: there is no SENT. Nothing in this system transmits
    anything to a customer — the brief's core product constraint, enforced by
    the absence of the capability rather than by a rule (PRD §10, API6).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TriageStage(StrEnum):
    """Which stage of the cascade produced a result (PRD §7.1).

    Recorded on every result so the eval can report *how* each prediction was
    obtained, not just what it was. A prediction from FALLBACK and a prediction
    from LLM are not the same kind of claim.
    """

    QUALITY_GATE = "quality_gate"
    CACHE = "cache"
    LLM = "llm"
    FALLBACK = "fallback"


class RepairKind(StrEnum):
    """What the repair layer had to do to salvage a model response.

    This is the schema-violation taxonomy from PRD §11.4. Counting these is the
    empirical evidence that the repair layer was necessary: it turns "I handled
    malformed output" into "here are the 11 malformed outputs and how".
    """

    FENCED = "fenced"
    PROSE_WRAPPED = "prose_wrapped"
    # Discovered by the Day-0 spike, not predicted: on short or ambiguous
    # tickets llama3.2:3b returns the JSON *Schema* rather than an instance —
    # but commits its actual answer inside single-value `enum` arrays. Most such
    # responses are recoverable; see app.triage.repair.
    SCHEMA_ECHO = "schema_echo"
    TRAILING_COMMA = "trailing_comma"
    SINGLE_QUOTES = "single_quotes"
    UNTERMINATED_STRING = "unterminated_string"
    ENUM_SYNONYM = "enum_synonym"
    TYPE_COERCED = "type_coerced"
    CLAMPED = "clamped"
    TRUNCATED_FIELD = "truncated_field"
    NULL_DEFAULTED = "null_defaulted"


class FailureKind(StrEnum):
    """Why an LLM attempt failed.

    Separate from RepairKind because these drive *retry policy* and those drive
    *telemetry*. The distinction is the point of PRD §7.1's retry table: a
    transport failure is worth retrying, a refusal is not.
    """

    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    OVERSIZED_RESPONSE = "oversized_response"
    EMPTY_COMPLETION = "empty_completion"
    NOT_JSON = "not_json"
    SCHEMA_INVALID = "schema_invalid"
    CIRCUIT_OPEN = "circuit_open"


class ResponseFormatMode(StrEnum):
    """How the JSON schema is requested from the endpoint (PRD §7.1 stage 3).

    JSON_SCHEMA is opportunistic, never load-bearing: Ollama's /v1 has
    historically ignored OpenAI's json_schema variant (ollama#10001). The repair
    layer is the guarantee, which is why NONE is a supported mode — it proves the
    repair layer standing alone.
    """

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    NONE = "none"


class ConfidenceMode(StrEnum):
    """How confidence is derived (PRD §7.2)."""

    SELF_REPORTED = "self_reported"
    ENSEMBLE = "ensemble"
