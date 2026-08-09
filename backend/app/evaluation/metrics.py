"""Evaluation maths. Pure functions, no I/O, unit-tested against hand-computed values.

This module lives inside the installed package rather than in eval/ for one
reason: a metric you have not tested is a number you cannot defend, and a bare
script is not importable by a test (PRD §4.1).

Everything here reports its DENOMINATOR alongside its rate. With n=16 labelled
tickets, one ticket is 6.25% — a bare percentage implies a precision that does
not exist, and quoting "87.5%" without saying "14/16" is the single most common
way to overclaim on a small sample (PRD §11.3).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from app.domain.enums import Category, Priority

Z_95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Score:
    """A rate that always carries its denominator and its uncertainty."""

    correct: int
    total: int

    @property
    def rate(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.correct, self.total)

    def as_dict(self) -> dict[str, object]:
        low, high = self.interval
        return {
            "rate": round(self.rate, 4),
            "correct": self.correct,
            "total": self.total,
            "wilson_95_low": round(low, 4),
            "wilson_95_high": round(high, 4),
        }

    def __str__(self) -> str:
        low, high = self.interval
        return f"{self.rate:.1%} ({self.correct}/{self.total}, 95% CI {low:.1%}-{high:.1%})"


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation precisely BECAUSE n is small.
    The normal approximation produces intervals that extend below 0 or above 1
    near the boundaries and is degenerate at 0/n and n/n — exactly the cases a
    16-sample eval will hit. This is the honest expression of "n=16", and on
    this data it is roughly +/-20 points.
    """
    if total == 0:
        return (0.0, 0.0)

    phat = successes / total
    denominator = 1 + z**2 / total
    centre = phat + z**2 / (2 * total)
    spread = z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2))
    low = (centre - spread) / denominator
    high = (centre + spread) / denominator
    return (max(0.0, low), min(1.0, high))


def exact_agreement(pairs: list[tuple[str, str]]) -> Score:
    """Exact-match score over (gold, predicted) pairs."""
    correct = sum(1 for gold, predicted in pairs if gold == predicted)
    return Score(correct=correct, total=len(pairs))


def within_one_priority(pairs: list[tuple[str, str]]) -> Score:
    """Adjacency on the ordinal priority scale.

    Reported as a clearly labelled SECONDARY figure, never as the headline. The
    brief asks for `priority_agreement` and gets exact match; this exists because
    a system that never misses by more than one level is operationally very
    different from one that calls `low` on an outage, and a single number hides
    that (PRD §11.2).
    """
    correct = 0
    for gold, predicted in pairs:
        try:
            distance = abs(Priority(gold).rank - Priority(predicted).rank)
        except ValueError:
            continue
        if distance <= 1:
            correct += 1
    return Score(correct=correct, total=len(pairs))


def cohens_kappa(pairs: list[tuple[str, str]], labels: list[str]) -> float:
    """Chance-corrected agreement.

    The right statistic here because the classes are unbalanced and unequal in
    number (6 categories, 4 priorities). Raw accuracy of 60% means something very
    different across 6 classes than across 2, and kappa is what makes the two
    comparable. Returns 1.0 for perfect agreement, ~0.0 for chance, negative for
    systematic disagreement.
    """
    total = len(pairs)
    if total == 0:
        return 0.0

    observed = sum(1 for gold, predicted in pairs if gold == predicted) / total

    gold_counts = Counter(gold for gold, _ in pairs)
    pred_counts = Counter(pred for _, pred in pairs)
    expected = sum(
        (gold_counts.get(label, 0) / total) * (pred_counts.get(label, 0) / total)
        for label in labels
    )

    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1 - expected)


def majority_baseline(pairs: list[tuple[str, str]]) -> Score:
    """What you would score by always predicting the most common gold label.

    Without this number, an accuracy figure is uninterpretable. On this subset
    always-predict-`bug` is a surprisingly strong baseline, and any honest report
    has to say so (PRD §11.3).
    """
    if not pairs:
        return Score(0, 0)
    most_common = Counter(gold for gold, _ in pairs).most_common(1)[0][0]
    correct = sum(1 for gold, _ in pairs if gold == most_common)
    return Score(correct=correct, total=len(pairs))


def confusion_matrix(pairs: list[tuple[str, str]], labels: list[str]) -> dict[str, dict[str, int]]:
    """Rows are GOLD, columns are PREDICTED.

    The orientation is asserted in the tests, because a transposed matrix is a
    silent, plausible-looking bug that would invert every conclusion in the error
    analysis.
    """
    matrix = {gold: dict.fromkeys(labels, 0) for gold in labels}
    for gold, predicted in pairs:
        if gold in matrix and predicted in matrix[gold]:
            matrix[gold][predicted] += 1
    return matrix


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    label: str
    support: int
    precision: float
    recall: float
    f1: float


def per_class_metrics(pairs: list[tuple[str, str]], labels: list[str]) -> list[ClassMetrics]:
    """Precision/recall/F1 per class.

    Convention for a class with no predictions and no support: 0.0 rather than a
    ZeroDivisionError or a silent nan. Documented because it changes the macro
    average, and an undocumented convention is an undefendable number.
    """
    out: list[ClassMetrics] = []
    for label in labels:
        tp = sum(1 for g, p in pairs if g == label and p == label)
        fp = sum(1 for g, p in pairs if g != label and p == label)
        fn = sum(1 for g, p in pairs if g == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out.append(
            ClassMetrics(
                label=label,
                support=tp + fn,
                precision=precision,
                recall=recall,
                f1=f1,
            )
        )
    return out


def macro_f1(pairs: list[tuple[str, str]], labels: list[str]) -> float:
    metrics = per_class_metrics(pairs, labels)
    return sum(m.f1 for m in metrics) / len(metrics) if metrics else 0.0


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Does confidence actually separate right answers from wrong ones?

    If `separation` is <= 0 the calibration in PRD §7.2 did not work, and the
    eval reports that plainly. Measuring a heuristic is what distinguishes it
    from an unjustified one.
    """

    mean_confidence_correct: float
    mean_confidence_incorrect: float
    n_correct: int
    n_incorrect: int

    @property
    def separation(self) -> float:
        return self.mean_confidence_correct - self.mean_confidence_incorrect

    def as_dict(self) -> dict[str, object]:
        return {
            "mean_confidence_when_correct": round(self.mean_confidence_correct, 4),
            "mean_confidence_when_incorrect": round(self.mean_confidence_incorrect, 4),
            "separation": round(self.separation, 4),
            "n_correct": self.n_correct,
            "n_incorrect": self.n_incorrect,
        }


def calibration(pairs: list[tuple[str, str]], confidences: list[float]) -> CalibrationReport:
    correct = [c for (g, p), c in zip(pairs, confidences, strict=True) if g == p]
    incorrect = [c for (g, p), c in zip(pairs, confidences, strict=True) if g != p]
    return CalibrationReport(
        mean_confidence_correct=_mean(correct),
        mean_confidence_incorrect=_mean(incorrect),
        n_correct=len(correct),
        n_incorrect=len(incorrect),
    )


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Small samples do not deserve interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class PipelineHealth:
    """Reliability, measured rather than asserted (PRD §11.4).

    The brief asks for a reliable AI layer. This is the evidence, and the
    repair taxonomy in particular converts "I handled malformed output" into
    "here are the N malformed outputs and how each was handled".
    """

    total: int = 0
    by_stage: Counter[str] = field(default_factory=Counter)
    repaired: int = 0
    retried: int = 0
    escalated: int = 0
    degraded: int = 0
    injection_detected: int = 0
    spam_detected: int = 0
    repair_taxonomy: Counter[str] = field(default_factory=Counter)
    failure_taxonomy: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        llm_count = self.by_stage.get("llm", 0)
        return {
            "total_tickets": self.total,
            "by_stage": dict(self.by_stage),
            "llm_success_rate": _ratio(llm_count, self.total),
            "repair_rate": _ratio(self.repaired, max(llm_count, 1)),
            "retry_rate": _ratio(self.retried, max(llm_count, 1)),
            "fallback_rate": _ratio(self.by_stage.get("fallback", 0), self.total),
            "escalation_rate": _ratio(self.escalated, self.total),
            "degraded_count": self.degraded,
            "injection_detected_count": self.injection_detected,
            "spam_detected_count": self.spam_detected,
            "repair_taxonomy": dict(self.repair_taxonomy),
            "failure_taxonomy": dict(self.failure_taxonomy),
            "latency_ms_mean": round(_mean(self.latencies_ms), 1),
            "latency_ms_p95": round(percentile(self.latencies_ms, 0.95), 1),
        }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


CATEGORY_LABELS = [c.value for c in Category]
PRIORITY_LABELS = [p.value for p in Priority]
