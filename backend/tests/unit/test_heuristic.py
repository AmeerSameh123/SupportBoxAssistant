"""Deterministic fallback strategy (app.triage.heuristic_strategy).

This is the guaranteed floor: it converts "the AI layer failed" from an exception
into a degraded but honest answer, and it guarantees 30/30 predictions even with
Ollama stopped (PRD §6.1 stage 5).

Its accuracy on the labelled subset is a PUBLISHED BASELINE in the eval. If the
LLM cannot beat regex, that is the most useful finding in the report — so this
module is held to a real standard, not treated as dead code.

Contract (shared with LlmTriageStrategy, also asserted in the LSP conformance test):
  - never raises, for any input including empty string and 100KB of binary noise
  - never returns None
  - always returns a schema-valid TriageResult
  - always sets degraded=True and confidence <= 0.3
  - reply is a neutral acknowledgement, never a substantive commitment

Category routing:
  - refund / invoice / charge / VAT / billed  -> billing   (T-001, T-009, T-025, T-028)
  - crash / freezes / error / 503 / broken    -> bug       (T-002, T-006, T-019)
  - login / password / locked out / GDPR      -> account   (T-005, T-021)
  - vulnerability / IDOR / disclosure         -> security  (T-014)
  - no rule matches                           -> other     (T-004, T-015, T-023)

Priority markers:
  - "urgent" / "production" / "SLA" / "blocked" / "down" -> urgent  (T-006)
  - "third time" / "still" / repeated-contact signals    -> high    (T-012)
  - no urgency marker                                    -> medium
  - "no rush" / "tiny thing" / "just thought"            -> low     (T-003, T-026)

Precedence is deterministic and tested: a ticket matching two category rules
resolves the same way every run.
"""
