"""Evaluation maths (app.evaluation.metrics).

A metric you have not tested is a number you cannot defend. Every value here is
checked against a hand-computed expected result, not against the implementation's
own output (PRD §4.1, §11).

Required metrics (PRD §11.2):
  - category_accuracy: exact match, denominator = 16 labelled tickets only
  - priority_agreement: exact match on the same 16
  - unlabelled tickets are excluded from both, never counted as wrong
  - a prediction missing from the labelled set raises rather than scoring 0

Extended metrics (PRD §11.3), each with a hand-computed fixture:
  - raw counts reported alongside every rate ("14/16", not just 0.875)
  - Wilson 95% CI: known interval for 14/16 and for the edge cases 0/16 and 16/16
      (Wilson is used precisely because the normal approximation breaks at the
       boundaries — the 16/16 case is the test that proves it)
  - majority-class baseline computed from the label distribution
  - Cohen's kappa: verified against a worked example, plus
      perfect agreement -> 1.0
      chance-level agreement -> ~0.0
      systematic disagreement -> negative
  - confusion matrix shape and cell placement (predicted vs gold orientation
    asserted explicitly — a transposed matrix is a silent, plausible-looking bug)
  - per-class precision / recall / F1, including a class with zero predictions
    (no ZeroDivisionError; documented convention returns 0.0)
  - priority_within_one uses Priority.rank adjacency, not string comparison

Pipeline-health metrics over all 30 (PRD §11.4):
  - llm_success_rate, repair_rate, retry_rate, fallback_rate, escalation_rate
  - schema-violation taxonomy counts sum to the number of violations
  - p95 latency on a known sample

Calibration (PRD §11.5):
  - mean confidence on correct vs incorrect predictions
  - separation is reported even when it is negative, i.e. when calibration
    made things worse. The eval reports what happened, not what I hoped.
"""
