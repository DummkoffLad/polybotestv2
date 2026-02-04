"""Tests for ConfidenceIntervalCalculator using TDD approach."""

import pytest
from decimal import Decimal
from src.statistics.confidence import ConfidenceIntervalCalculator


class TestConfidenceIntervalCalculatorPnL:
    """Tests for PnL confidence interval calculations."""

    def test_pnl_ci_empty_list(self):
        """Empty list should return (0.0, 0.0, 0.0)."""
        calculator = ConfidenceIntervalCalculator()
        result = calculator.pnl_confidence_interval([])
        assert result == (0.0, 0.0, 0.0)

    def test_pnl_ci_single_element(self):
        """Single element should return (value, value, value)."""
        calculator = ConfidenceIntervalCalculator()
        result = calculator.pnl_confidence_interval([Decimal("5.0")])
        assert result == (5.0, 5.0, 5.0)

    def test_pnl_ci_known_distribution(self):
        """Known distribution [1,2,3,4,5] should have mean=3.0 with CI around it."""
        calculator = ConfidenceIntervalCalculator()
        pnls = [Decimal(str(x)) for x in [1, 2, 3, 4, 5]]
        mean, lower, upper = calculator.pnl_confidence_interval(pnls)

        assert mean == 3.0
        assert lower < 3.0  # CI lower bound should be less than mean
        assert upper > 3.0  # CI upper bound should be greater than mean
        assert lower < upper  # CI should be valid interval

    def test_pnl_ci_reproducibility(self):
        """Same input should produce same output (fixed seed)."""
        calculator = ConfidenceIntervalCalculator()
        pnls = [Decimal(str(x)) for x in [1, 2, 3, 4, 5]]

        result1 = calculator.pnl_confidence_interval(pnls)
        result2 = calculator.pnl_confidence_interval(pnls)

        assert result1 == result2


class TestConfidenceIntervalCalculatorWinRate:
    """Tests for win rate confidence interval calculations."""

    def test_win_rate_ci_all_positive(self):
        """All positive PnLs should give 100% win rate."""
        calculator = ConfidenceIntervalCalculator()
        pnls = [Decimal("1"), Decimal("2"), Decimal("3")]
        rate, lower, upper = calculator.win_rate_confidence_interval(pnls)

        assert rate == 100.0
        assert lower == 100.0
        assert upper == 100.0

    def test_win_rate_ci_all_negative(self):
        """All negative PnLs should give 0% win rate."""
        calculator = ConfidenceIntervalCalculator()
        pnls = [Decimal("-1"), Decimal("-2"), Decimal("-3")]
        rate, lower, upper = calculator.win_rate_confidence_interval(pnls)

        assert rate == 0.0
        assert lower == 0.0
        assert upper == 0.0

    def test_win_rate_ci_mixed(self):
        """Mixed PnLs should give win rate between 0-100 with valid CI."""
        calculator = ConfidenceIntervalCalculator()
        pnls = [Decimal("1"), Decimal("-1"), Decimal("2"), Decimal("-2")]
        rate, lower, upper = calculator.win_rate_confidence_interval(pnls)

        assert 0.0 <= rate <= 100.0
        assert 0.0 <= lower <= 100.0
        assert 0.0 <= upper <= 100.0
        assert lower <= rate <= upper
        assert rate == 50.0  # Exactly 50% win rate

    def test_win_rate_ci_empty_list(self):
        """Empty list should return (0.0, 0.0, 0.0)."""
        calculator = ConfidenceIntervalCalculator()
        result = calculator.win_rate_confidence_interval([])
        assert result == (0.0, 0.0, 0.0)


class TestConfidenceIntervalCalculatorConfiguration:
    """Tests for configurable confidence levels."""

    def test_configurable_confidence_level(self):
        """Lower confidence level (0.90) should produce narrower CI than 0.95."""
        pnls = [Decimal(str(x)) for x in range(1, 21)]  # 1 to 20

        calc_90 = ConfidenceIntervalCalculator(confidence_level=0.90)
        calc_95 = ConfidenceIntervalCalculator(confidence_level=0.95)

        mean_90, lower_90, upper_90 = calc_90.pnl_confidence_interval(pnls)
        mean_95, lower_95, upper_95 = calc_95.pnl_confidence_interval(pnls)

        # Same mean
        assert mean_90 == mean_95

        # 90% CI should be narrower than 95% CI
        width_90 = upper_90 - lower_90
        width_95 = upper_95 - lower_95
        assert width_90 < width_95
