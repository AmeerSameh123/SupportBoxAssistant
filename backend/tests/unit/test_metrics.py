"""Evaluation maths (app.evaluation.metrics).

Expected values come from published references, independent re-derivations of the
formula, or arithmetic shown in the test - never from the implementation's own
output. A metric you have not tested is a number you cannot defend (PRD §4.1).

Worth recording: my first pass at this file asserted hand-computed Wilson and
kappa constants that were simply wrong, and the implementation was right. That is
the argument for cross-checking against a second derivation rather than trusting
a number I typed.
"""

from __future__ import annotations

import pytest

from app.evaluation.metrics import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    calibration,
    cohens_kappa,
    confusion_matrix,
    exact_agreement,
    macro_f1,
    majority_baseline,
    per_class_metrics,
    percentile,
    wilson_interval,
    within_one_priority,
)


class TestExactAgreement:
    def test_counts_and_rate(self):
        pairs = [("billing", "billing"), ("bug", "bug"), ("bug", "other")]
        score = exact_agreement(pairs)
        assert (score.correct, score.total) == (2, 3)
        assert score.rate == pytest.approx(2 / 3)

    def test_empty_input_does_not_divide_by_zero(self):
        assert exact_agreement([]).rate == 0.0

    def test_string_form_carries_the_denominator(self):
        """A bare percentage on n=16 implies a precision that does not exist."""
        text = str(exact_agreement([("a", "a")] * 14 + [("a", "b")] * 2))
        assert "14/16" in text and "CI" in text


class TestWilsonInterval:
    def test_known_value_14_of_16(self):
        """The realistic case for this eval: 14 of 16 labelled tickets correct.
        The interval is ~+/-16 points wide, which is the honest expression of
        n=16 and the reason no headline number ships without it."""
        low, high = wilson_interval(14, 16)
        assert low == pytest.approx(0.639772, abs=1e-5)
        assert high == pytest.approx(0.965023, abs=1e-5)

    def test_matches_an_independent_derivation_of_the_formula(self):
        """Cross-check against the same interval written a different way. Two
        arrangements of the algebra agreeing is stronger evidence than one
        arrangement agreeing with a constant I typed."""
        from math import sqrt

        def wilson_alt(x: int, n: int, z: float = 1.959963984540054):
            p = x / n
            a = 2 * n * p + z * z
            b = z * sqrt(z * z + 4 * n * p * (1 - p))
            c = 2 * (n + z * z)
            return ((a - b) / c, (a + b) / c)

        for x, n in [(14, 16), (16, 16), (0, 16), (15, 20), (8, 10), (3, 7)]:
            assert wilson_interval(x, n) == pytest.approx(wilson_alt(x, n), abs=1e-12)

    def test_boundary_16_of_16_stays_inside_zero_one(self):
        """The case that breaks the normal approximation, which is exactly why
        Wilson is used."""
        low, high = wilson_interval(16, 16)
        assert 0.0 < low < 1.0
        assert high == pytest.approx(1.0)
        assert low == pytest.approx(0.806392, abs=1e-5)

    def test_boundary_zero(self):
        low, high = wilson_interval(0, 16)
        assert low == pytest.approx(0.0)
        assert 0.0 < high < 1.0

    def test_zero_total(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_interval_always_contains_the_estimate(self):
        for correct in range(17):
            low, high = wilson_interval(correct, 16)
            assert low <= correct / 16 <= high


class TestCohensKappa:
    def test_perfect_agreement(self):
        pairs = [("a", "a"), ("b", "b"), ("c", "c"), ("a", "a")]
        assert cohens_kappa(pairs, ["a", "b", "c"]) == pytest.approx(1.0)

    def test_canonical_2x2_reference(self):
        """The textbook 2x2 example: a=20, b=5, c=10, d=15 over n=50.
            po = (20 + 15) / 50 = 0.70
            pe = (25/50)(30/50) + (25/50)(20/50) = 0.30 + 0.20 = 0.50
            kappa = (0.70 - 0.50) / (1 - 0.50) = 0.40
        A published reference value beats arithmetic I did myself."""
        pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
        assert len(pairs) == 50
        assert cohens_kappa(pairs, ["y", "n"]) == pytest.approx(0.40, abs=1e-9)

    def test_systematic_disagreement_is_negative(self):
        pairs = [("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")]
        assert cohens_kappa(pairs, ["a", "b"]) < 0

    def test_empty(self):
        assert cohens_kappa([], ["a"]) == 0.0


class TestWithinOnePriority:
    def test_adjacent_counts_as_agreement(self):
        pairs = [("high", "urgent"), ("low", "medium"), ("medium", "medium")]
        assert within_one_priority(pairs).correct == 3

    def test_two_levels_apart_does_not(self):
        assert within_one_priority([("low", "high")]).correct == 0

    def test_uses_rank_not_string_order(self):
        """Alphabetically 'urgent' > 'medium' > 'low' > 'high', which would give
        the wrong answer for every comparison."""
        assert within_one_priority([("high", "low")]).correct == 0
        assert within_one_priority([("high", "medium")]).correct == 1


class TestConfusionMatrix:
    def test_orientation_is_gold_rows_predicted_columns(self):
        """A transposed matrix is a silent, plausible-looking bug that would
        invert every conclusion in the error analysis."""
        matrix = confusion_matrix([("billing", "bug")], CATEGORY_LABELS)
        assert matrix["billing"]["bug"] == 1
        assert matrix["bug"]["billing"] == 0

    def test_all_labels_present_even_with_no_data(self):
        matrix = confusion_matrix([], CATEGORY_LABELS)
        assert set(matrix) == set(CATEGORY_LABELS)
        assert all(set(row) == set(CATEGORY_LABELS) for row in matrix.values())


class TestPerClassMetrics:
    def test_precision_recall_f1(self):
        # 'bug': tp=2, fp=1, fn=1 -> p=2/3, r=2/3, f1=2/3
        pairs = [
            ("bug", "bug"),
            ("bug", "bug"),
            ("bug", "other"),
            ("other", "bug"),
        ]
        bug = next(m for m in per_class_metrics(pairs, CATEGORY_LABELS) if m.label == "bug")
        assert bug.precision == pytest.approx(2 / 3)
        assert bug.recall == pytest.approx(2 / 3)
        assert bug.f1 == pytest.approx(2 / 3)
        assert bug.support == 3

    def test_class_with_no_predictions_returns_zero_not_error(self):
        metrics = per_class_metrics([("bug", "bug")], CATEGORY_LABELS)
        security = next(m for m in metrics if m.label == "security")
        assert (security.precision, security.recall, security.f1) == (0.0, 0.0, 0.0)

    def test_macro_f1_averages_over_all_labels(self):
        pairs = [(label, label) for label in CATEGORY_LABELS]
        assert macro_f1(pairs, CATEGORY_LABELS) == pytest.approx(1.0)


class TestBaselines:
    def test_majority_baseline_uses_the_most_common_gold(self):
        pairs = [("bug", "x")] * 5 + [("billing", "y")] * 3
        score = majority_baseline(pairs)
        assert (score.correct, score.total) == (5, 8)

    def test_empty(self):
        assert majority_baseline([]).total == 0


class TestCalibration:
    def test_separation_is_positive_when_confidence_is_informative(self):
        pairs = [("a", "a"), ("a", "a"), ("a", "b"), ("a", "b")]
        report = calibration(pairs, [0.9, 0.8, 0.3, 0.2])
        assert report.mean_confidence_correct == pytest.approx(0.85)
        assert report.mean_confidence_incorrect == pytest.approx(0.25)
        assert report.separation == pytest.approx(0.6)

    def test_negative_separation_is_reported_not_hidden(self):
        """If calibration made things worse, the eval says so (PRD §11.5)."""
        pairs = [("a", "a"), ("a", "b")]
        assert calibration(pairs, [0.2, 0.9]).separation < 0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            calibration([("a", "a")], [0.5, 0.6])


class TestPercentile:
    def test_p95_of_twenty_values(self):
        assert percentile([float(i) for i in range(1, 21)], 0.95) == 19.0

    def test_empty(self):
        assert percentile([], 0.95) == 0.0

    def test_single_value(self):
        assert percentile([4.2], 0.95) == 4.2


def test_label_lists_come_from_the_enums():
    """The eval must score against the same vocabulary the model is constrained
    to — a hand-maintained second list is how those silently diverge."""
    assert CATEGORY_LABELS == ["billing", "bug", "feature_request", "account", "security", "other"]
    assert PRIORITY_LABELS == ["low", "medium", "high", "urgent"]
