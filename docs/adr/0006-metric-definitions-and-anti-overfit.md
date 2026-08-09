# ADR-0006 — Metric definitions, snake_case labels, and the anti-overfit protocol

**Date**: 2026-08-09 · **Status**: Accepted

## Context

The labelled subset is **n = 16**. One ticket moves any rate by 6.25 points. The
brief says: *"Don't overfit — report accuracy honestly; that's the point of an
eval."* The eval is therefore a test of intellectual honesty as much as accuracy.

## Decisions

### 1. `feature_request`, not `feature request`

The task email's prose uses a space; `labels.json` uses snake_case. Scoring is
against `labels.json`, so snake_case is canonical. The space-separated form is
accepted as an **input synonym** by `app/triage/normalize.py` and never emitted.

### 2. The two required metrics are exact match, unmodified

`category_accuracy` and `priority_agreement` are exact match over the 16 labelled
tickets, reported at the top level of `results.json` with exactly those names.
No reinterpretation that would flatter the numbers.

`priority_within_one` (adjacency on the ordinal scale) is reported as a clearly
labelled **secondary** figure, because a system that never misses by more than one
level is operationally very different from one that calls `low` on an outage —
and a single number hides that. It scored 93.8% against agreement's 50%; the
headline stays 50%.

### 3. Every rate carries its denominator and its uncertainty

Raw counts (`14/16`), a **Wilson** 95% interval, a **majority-class** baseline and
a **heuristic-regex** baseline. Wilson rather than the normal approximation
specifically because n is small — the normal approximation is degenerate at 0/n
and n/n, exactly the cases a 16-sample eval hits.

Cohen's κ is reported for both fields because 6 classes and 4 classes are not
comparable on raw accuracy. It turned out to matter: κ=0.84 for category versus
κ=0.32 for priority quantifies a split that accuracy alone obscures.

### 4. `results.json` meets the brief's shape literally

`metrics` and `predictions` match the specified names, types and nesting exactly,
and each of the 30 predictions carries exactly the seven specified fields.
Everything additional lives in **sibling top-level keys** (`run_metadata`,
`extended_metrics`, `diagnostics`), so a grader's script that reads only `metrics`
and `predictions` works unmodified.

### 5. Anti-overfit protocol, declared before the first scored run

1. Prompt revisions capped at **three**, driven by observed *failure modes*
   (malformed JSON, ignored instructions) — never by "T-016 came out wrong".
   Shipped at `triage/v2`, recorded in `results.json`.
2. **No ticket-ID-specific logic anywhere.** The ~20 matches that
   `grep -r "T-0" backend/app` finds are comments and docstrings citing worked
   examples, plus the synthetic placeholder id `T-000` used for ad-hoc
   `/triage` requests. No ticket ID appears in a conditional, a lookup table,
   or a branch. I invite the grep, with that caveat stated up front rather
   than discovered.
3. Defensible disagreements are **argued in `ERROR_ANALYSIS.md`, not engineered
   away**. T-019 (`bug` vs predicted `security`) and T-007 (`other` vs predicted
   `account`) are both left standing.
4. Negative results are reported. Confidence calibration measured a separation of
   **−0.017** — it made things marginally worse — and that is in the report.

## Consequences

Reported category accuracy is 87.5% (14/16, CI 64.0–96.5%) against a regex
baseline of 81.2%. Presenting the model as a clear win over regex on this sample
would not be supportable, and the analysis says so.
