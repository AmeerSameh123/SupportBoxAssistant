"""Escalation policy and confidence calibration (app.domain.policy).

Pure functions over value objects — no fixtures, no I/O, runs in microseconds.
This is the payoff for keeping the domain layer free of I/O (PRD §3.1).

EscalationPolicy — each rule fires independently (PRD §2.1):
  - confidence below ESCALATE_CONFIDENCE_THRESHOLD -> escalate
  - confidence exactly at the threshold -> does NOT escalate (boundary)
  - category == SECURITY -> escalate regardless of confidence
  - priority == URGENT -> escalate regardless of confidence
  - degraded (heuristic fallback used) -> escalate
  - injection_suspected -> escalate
  - low_signal (quality gate short-circuit) -> escalate
  - none of the above, high confidence -> does NOT escalate
  - rules compose: multiple triggers still yield exactly one escalate=True

Security property, asserted directly:
  A model-produced draft claiming `priority: low` on a SECURITY ticket still
  escalates. The LLM cannot influence escalation. This is the T-008 defence
  and it is a test, not a comment.

ConfidenceCalibrator — one assertion per named penalty (PRD §6.2):
  - body < 40 chars           x0.50
  - multi-intent detected     x0.70
  - non-English body          x0.80
  - repair required           x0.85
  - retry required            x0.80
  - heuristic fallback        capped at 0.30
  - penalties multiply, result stays within [0.0, 1.0]
  - no signals -> value passes through unchanged
"""
