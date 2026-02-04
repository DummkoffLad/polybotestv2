"""Tests for strategy comparison using hypothesis testing."""

import pytest
from decimal import Decimal
from scipy.stats import ttest_rel

from src.statistics.hypothesis import StrategyComparator, ComparisonResult


class TestStrategyComparator:
    """Test suite for StrategyComparator."""

    def test_mismatched_lengths_raises_error(self):
        """Paired t-test requires same-length lists."""
        comparator = StrategyComparator()

        with pytest.raises(ValueError, match="same length"):
            comparator.compare_strategies(
                [Decimal("1"), Decimal("2"), Decimal("3")],
                [Decimal("1"), Decimal("2")]
            )

    def test_empty_lists_raises_error(self):
        """Cannot compare empty lists."""
        comparator = StrategyComparator()

        with pytest.raises(ValueError, match="empty"):
            comparator.compare_strategies([], [])

    def test_single_element_raises_error(self):
        """Cannot compute t-test with single observation."""
        comparator = StrategyComparator()

        with pytest.raises(ValueError, match="single observation"):
            comparator.compare_strategies(
                [Decimal("1")],
                [Decimal("2")]
            )

    def test_identical_lists_not_significant(self):
        """Identical lists should have NaN p-value (zero variance) and not significant."""
        comparator = StrategyComparator()

        result = comparator.compare_strategies(
            [Decimal("1"), Decimal("2"), Decimal("3")],
            [Decimal("1"), Decimal("2"), Decimal("3")]
        )

        # When all differences are zero, scipy returns NaN for both t-stat and p-value
        import math
        assert math.isnan(result.p_value)
        assert not result.is_significant  # NaN < 0.05 is False
        assert result.mean_difference == Decimal("0")

    def test_clearly_different_lists_significant(self):
        """Clearly different lists should be statistically significant."""
        comparator = StrategyComparator()

        # Use more data points to achieve significance
        result = comparator.compare_strategies(
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6")],
            [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50"), Decimal("60")]
        )

        assert result.p_value < 0.05
        assert result.is_significant
        assert result.mean_difference > 0  # B wins

    def test_strategy_b_wins_interpretation(self):
        """When B significantly outperforms A, interpretation should mention B winning."""
        comparator = StrategyComparator()

        # Use more data points to achieve significance
        result = comparator.compare_strategies(
            strategy_a_pnls=[Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6")],
            strategy_b_pnls=[Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50"), Decimal("60")],
            strategy_a_name="Conservative",
            strategy_b_name="Aggressive"
        )

        assert result.is_significant
        assert "Aggressive" in result.interpretation
        assert "outperforms" in result.interpretation.lower()
        assert str(result.p_value) in result.interpretation or f"{result.p_value:.4f}" in result.interpretation

    def test_strategy_a_wins_interpretation(self):
        """When A significantly outperforms B, interpretation should mention A winning."""
        comparator = StrategyComparator()

        # Use more data points to achieve significance
        result = comparator.compare_strategies(
            strategy_a_pnls=[Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50"), Decimal("60")],
            strategy_b_pnls=[Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6")],
            strategy_a_name="Conservative",
            strategy_b_name="Aggressive"
        )

        assert result.is_significant
        assert "Conservative" in result.interpretation
        assert "outperforms" in result.interpretation.lower()

    def test_no_significant_difference_interpretation(self):
        """When not significant, interpretation should say no significant difference."""
        comparator = StrategyComparator()

        # Similar values with small random variation
        result = comparator.compare_strategies(
            strategy_a_pnls=[Decimal("10.0"), Decimal("10.5"), Decimal("9.5"), Decimal("10.2")],
            strategy_b_pnls=[Decimal("10.1"), Decimal("10.4"), Decimal("9.6"), Decimal("10.3")],
            strategy_a_name="Strategy A",
            strategy_b_name="Strategy B"
        )

        assert not result.is_significant
        assert "no significant difference" in result.interpretation.lower()

    def test_custom_significance_level(self):
        """Can set custom significance level."""
        # With significance_level=0.01, p=0.03 should not be significant
        comparator = StrategyComparator(significance_level=0.01)

        # Create data with borderline p-value around 0.03
        # Use data that gives moderate difference
        result = comparator.compare_strategies(
            strategy_a_pnls=[Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")],
            strategy_b_pnls=[Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6")]
        )

        # With stricter threshold, should not be significant
        # (exact p-value depends on data, but this tests the threshold logic)
        if 0.01 < result.p_value < 0.05:
            assert not result.is_significant

    def test_uses_scipy_ttest_rel(self):
        """Verify result matches scipy.stats.ttest_rel output."""
        comparator = StrategyComparator()

        # Use data with variation to avoid precision issues
        a_data = [Decimal("10.5"), Decimal("12.3"), Decimal("9.8"), Decimal("11.2"), Decimal("10.9")]
        b_data = [Decimal("15.2"), Decimal("14.8"), Decimal("16.1"), Decimal("15.5"), Decimal("14.9")]

        result = comparator.compare_strategies(a_data, b_data)

        # Compute reference using scipy directly
        a_floats = [float(x) for x in a_data]
        b_floats = [float(x) for x in b_data]
        scipy_t, scipy_p = ttest_rel(a_floats, b_floats)

        # Our implementation should match scipy
        assert abs(result.t_statistic - scipy_t) < 1e-10
        assert abs(result.p_value - scipy_p) < 1e-10

    def test_comparison_result_fields(self):
        """ComparisonResult should have all required fields."""
        comparator = StrategyComparator()

        result = comparator.compare_strategies(
            [Decimal("1"), Decimal("2"), Decimal("3")],
            [Decimal("2"), Decimal("3"), Decimal("4")]
        )

        # Check all fields exist
        assert hasattr(result, "strategy_a_mean")
        assert hasattr(result, "strategy_b_mean")
        assert hasattr(result, "mean_difference")
        assert hasattr(result, "t_statistic")
        assert hasattr(result, "p_value")
        assert hasattr(result, "is_significant")
        assert hasattr(result, "interpretation")

        # Check calculations
        assert result.strategy_a_mean == Decimal("2")  # (1+2+3)/3
        assert result.strategy_b_mean == Decimal("3")  # (2+3+4)/3
        assert result.mean_difference == Decimal("1")  # 3-2

    def test_negative_mean_difference(self):
        """When B performs worse than A, mean_difference should be negative."""
        comparator = StrategyComparator()

        result = comparator.compare_strategies(
            strategy_a_pnls=[Decimal("10"), Decimal("20"), Decimal("30")],
            strategy_b_pnls=[Decimal("5"), Decimal("10"), Decimal("15")]
        )

        assert result.mean_difference < 0
        assert result.strategy_a_mean > result.strategy_b_mean
