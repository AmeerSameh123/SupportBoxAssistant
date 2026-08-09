# ADR-0003 — A five-stage cascade with typed retry semantics

**Date**: 2026-08-09 · **Status**: Accepted

## Context

`llama3.2:3b` will, across 30 tickets, produce fenced JSON, prose-wrapped JSON,
`"confidence": "high"`, `"category": "Billing Issue"`, truncated output, schema
echoes, and occasional refusals. Every one of those is a **normal operating
condition**, not an exception.

## Decision

```
gate -> cache -> generate -> extract/normalize/validate -> heuristic fallback
```

Implemented as a **linear method with named private helpers** in `TriageService`,
not as a Chain of Responsibility.

Retry semantics are **typed rather than uniform**:

| Failure | Response | Why |
| --- | --- | --- |
| Transport / timeout | Backoff retry ≤2, exponential + full jitter | Transient |
| Schema violation | **Repair-retry** ≤2, re-prompting with the validation error | At temperature 0 a blind retry reproduces the same bytes |
| Refusal / empty completion / 4xx | No retry → fallback | Deterministic; will fail identically |
| Circuit open | No call at all | See below |

A **circuit breaker** opens after 3 consecutive transport failures.

## Consequences

- The eval produces **30/30 predictions with Ollama stopped**, and the breaker
  turns what would be 30 × 3 timeouts (~15 minutes) into ~40 seconds. This is
  asserted by a test, not hoped for.
- `SchemaViolationError` carries the field-level error so the repair prompt can
  name what was wrong. A retry that does not say what was wrong is a second roll
  of the same dice.
- The heuristic fallback's accuracy became a **published baseline** (81.2% vs the
  LLM's 87.5%), which turned out to be the eval's most useful finding.

## Alternatives rejected

- **Chain of Responsibility** — the cascade genuinely is a chain, but it has four
  fixed stages with no runtime reconfiguration. CoR would add a framework to
  express a straight line and replace a readable stack trace with a walk through
  handler objects.
- **`tenacity` / `pybreaker`** — ~50 lines hand-written, no transitive
  dependencies, and every state transition is mine to explain.
- **Uniform retry** — would retry deterministic failures, burning ~2s each to
  fail identically, and would trip the breaker on model errors that are not
  transport problems.
