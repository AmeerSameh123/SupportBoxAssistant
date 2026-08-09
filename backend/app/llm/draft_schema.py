"""The strict boundary the model's output has to survive.

Three models exist in this codebase on purpose (PRD §6.1, DTO/boundary models):

    LLMTriageDraft   (here)              - what the model is allowed to say
    TriageResult     (domain/models.py)  - validated truth, plus policy-owned fields
    API schemas      (api/schemas.py)    - what a client sees

The split is the LLM-containment boundary. No field the model controls reaches an
API response without passing through domain validation, and no client can set a
field the server owns. Collapsing these into one model would be less code and a
worse system.

Leniency lives in app.triage.normalize, which coerces junk into schema-shaped
values. This module is deliberately unforgiving: by the time a dict arrives here
it has already been repaired, and anything still wrong is a real violation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Category, Priority
from app.domain.models import MAX_REPLY_CHARS, MAX_SUMMARY_CHARS


class LLMTriageDraft(BaseModel):
    """Exactly the five fields the model is permitted to produce.

    `extra="forbid"` is the interesting one. If the model invents
    `{"escalate": false}` — and prompted with a ticket that asks it to, it will —
    the draft is rejected rather than silently carrying a model-authored
    escalation decision one layer deeper. Escalation is policy-owned (PRD §2.1),
    and this is where that ownership is enforced mechanically.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Category
    priority: Priority
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    suggested_reply: str = Field(max_length=MAX_REPLY_CHARS)
    confidence: float = Field(ge=0.0, le=1.0)


def triage_json_schema() -> dict[str, Any]:
    """JSON Schema for the draft, derived from the enums rather than retyped.

    Used two ways:
      1. as `response_format.json_schema` when that mode is enabled;
      2. embedded in the prompt in every mode, because the schema is the clearest
         possible instruction and costs ~80 tokens.

    Generated from Category/Priority so the prompt, the constraint and the
    validator can never disagree — the single-source-of-truth rule from PRD §2
    reaching all the way to the wire.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "category",
            "priority",
            "summary",
            "suggested_reply",
            "confidence",
        ],
        "properties": {
            "category": {
                "type": "string",
                "enum": [c.value for c in Category],
                "description": "The single best-fitting category.",
            },
            "priority": {
                "type": "string",
                "enum": [p.value for p in Priority],
                "description": "Urgency of the CURRENT impact on the customer.",
            },
            "summary": {
                "type": "string",
                "description": "One line, max 200 characters, factual.",
                "maxLength": MAX_SUMMARY_CHARS,
            },
            "suggested_reply": {
                "type": "string",
                "description": (
                    "A draft reply for a human agent to edit. "
                    "Written in the same language as the ticket."
                ),
                "maxLength": MAX_REPLY_CHARS,
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "How sure you are about category and priority.",
            },
        },
    }
