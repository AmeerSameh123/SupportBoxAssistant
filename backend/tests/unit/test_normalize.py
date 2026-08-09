"""Enum synonym mapping and value coercion (app.triage.normalize).

Separate from repair.py on purpose: this module changes when the model finds a new
way to name a category, repair.py changes when it finds a new way to mangle JSON.
Different reasons to change, different modules (PRD §5, SRP).

Cases to cover (PRD §6.1 stage 4):

  Category synonyms -> canonical snake_case:
    "Feature Request" / "feature request" / "feature-request" / "enhancement" -> feature_request
    "Billing Issue" / "BILLING" / " billing " -> billing
    "Bug Report" / "defect" -> bug
    unknown / unmappable value -> raises, does NOT silently fall back to `other`

  Priority synonyms:
    "P1" / "critical" / "blocker" / "emergency" -> urgent
    "P2" / "High" -> high
    "normal" -> medium

  Confidence coercion:
    "high" -> 0.8, "medium" -> 0.5, "low" -> 0.2
    "90%" -> 0.9;  "0.85" -> 0.85;  1 -> 1.0
    out of range: 1.7 -> 1.0, -0.2 -> 0.0 (clamped, not rejected)
    non-numeric garbage -> raises

  Field hygiene:
    summary truncated to 200 chars, suggested_reply to 1500
    control characters stripped
    null -> field default
    surrounding whitespace stripped

Regression guard: the space-separated form "feature request" is accepted as INPUT
and never EMITTED. labels.json is the scoring authority (PRD §2, ADR-0006).
"""
