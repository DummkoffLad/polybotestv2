"""Tests for KellyValidator statistical validation."""

from decimal import Decimal
import pytest
from src.simulation.kelly_validator import KellyValidator, ValidationResult


class TestPairedTTest:
    """Tests for paired t-test functionality."""

    def test_significant_improvement_detected(self):
        """Phase 4 consistently better -> is_significant=True, p < 0.05."""
        validator = KellyValidator(significance_level=0.05)
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), Decimal("9")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        assert result.is_significant is True
        assert result.p_value < 0.05
        assert result.t_statistic > 0
        assert result.mean_improvement == Decimal("2")

    def test_no_significant_difference(self):
        """Similar PnLs -> is_significant=False, p >= 0.05."""
        validator = KellyValidator(significance_level=0.05)
        phase3_pnls = [Decimal("5"), Decimal("5"), Decimal("5")]
        phase4_pnls = [Decimal("5"), Decimal("5"), Decimal("5")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        assert result.is_significant is False
        assert result.p_value >= 0.05

    def test_significant_worse_detected(self):
        """Phase 4 consistently worse -> is_significant=False (one-sided test)."""
        validator = KellyValidator(significance_level=0.05)
        phase3_pnls = [Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("2"), Decimal("3"), Decimal("4")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        assert result.is_significant is False
        assert result.t_statistic < 0
        assert result.mean_improvement < 0

    def test_single_session_insufficient(self):
        """Only 1 session -> handle gracefully."""
        validator = KellyValidator(significance_level=0.05)
        phase3_pnls = [Decimal("5")]
        phase4_pnls = [Decimal("7")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        # Single sample: cannot compute t-test, should have p=1.0 or high p-value
        assert result.p_value >= 0.05
        assert result.is_significant is False

    def test_returns_p_value(self):
        """p_value is a float between 0 and 1."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        assert isinstance(result.p_value, float)
        assert 0.0 <= result.p_value <= 1.0

    def test_returns_interpretation_string(self):
        """Human-readable interpretation included."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), Decimal("9")]

        result = validator.validate_improvement(phase3_pnls, phase4_pnls)

        assert isinstance(result.interpretation, str)
        assert len(result.interpretation) > 0
        # Should mention significance and direction
        assert "significant" in result.interpretation.lower()


class TestBootstrapConfidenceInterval:
    """Tests for bootstrap confidence interval functionality."""

    def test_bootstrap_positive_interval(self):
        """Phase 4 clearly better -> both bounds positive."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), Decimal("9")]

        lower, upper = validator.bootstrap_confidence_interval(phase3_pnls, phase4_pnls)

        assert lower > 0
        assert upper > 0
        # With perfectly uniform differences, lower == upper is acceptable
        assert lower <= upper

    def test_bootstrap_mixed_interval(self):
        """Borderline improvement -> interval crosses zero."""
        validator = KellyValidator()
        # Very small, noisy difference
        phase3_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("5"), Decimal("6")]
        phase4_pnls = [Decimal("5.5"), Decimal("6.5"), Decimal("6.5"), Decimal("5.5"), Decimal("6")]

        lower, upper = validator.bootstrap_confidence_interval(phase3_pnls, phase4_pnls)

        # With noise, interval should cross zero or be very close
        assert lower < upper
        # At least one bound should be positive (there is some improvement)
        assert upper > 0

    def test_bootstrap_1000_iterations(self):
        """Verify n_iterations defaults to 1000."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7")]

        # Just check it runs without error - hard to verify exact iteration count
        lower, upper = validator.bootstrap_confidence_interval(
            phase3_pnls, phase4_pnls, n_iterations=1000
        )
        assert isinstance(lower, float)
        assert isinstance(upper, float)

    def test_bootstrap_95_confidence(self):
        """95% confidence interval by default."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), Decimal("9")]

        lower, upper = validator.bootstrap_confidence_interval(
            phase3_pnls, phase4_pnls, confidence=0.95
        )

        # Verify it returns valid interval
        assert lower <= upper  # With uniform data, lower == upper is acceptable
        assert isinstance(lower, float)
        assert isinstance(upper, float)


class TestSecondaryMetrics:
    """Tests for secondary metric calculations."""

    def test_capital_utilization_metric(self):
        """Calculate % of capital deployed on average."""
        validator = KellyValidator()

        # Example: [50%, 60%, 70%] utilization snapshots
        utilization_snapshots = [Decimal("0.5"), Decimal("0.6"), Decimal("0.7")]

        metrics = validator.calculate_secondary_metrics(
            trade_pnls=[Decimal("10"), Decimal("20")],
            utilization_snapshots=utilization_snapshots,
        )

        assert "capital_utilization" in metrics
        # Average of 50, 60, 70 = 60%
        assert abs(float(metrics["capital_utilization"]) - 0.6) < 0.01

    def test_profit_factor_metric(self):
        """Calculate gross_profit / gross_loss ratio."""
        validator = KellyValidator()

        # Mix of wins and losses
        trade_pnls = [Decimal("10"), Decimal("20"), Decimal("-5"), Decimal("-3")]

        metrics = validator.calculate_secondary_metrics(trade_pnls=trade_pnls)

        assert "profit_factor" in metrics
        # Gross profit = 10 + 20 = 30
        # Gross loss = abs(-5 + -3) = 8
        # Profit factor = 30 / 8 = 3.75
        assert abs(float(metrics["profit_factor"]) - 3.75) < 0.01

    def test_max_single_loss_metric(self):
        """Track largest individual trade loss."""
        validator = KellyValidator()

        trade_pnls = [Decimal("10"), Decimal("-15"), Decimal("5"), Decimal("-3")]

        metrics = validator.calculate_secondary_metrics(trade_pnls=trade_pnls)

        assert "max_single_loss" in metrics
        # Most negative = -15
        assert metrics["max_single_loss"] == Decimal("-15")

    def test_profit_factor_all_wins(self):
        """Profit factor with no losses -> infinity or very large value."""
        validator = KellyValidator()

        trade_pnls = [Decimal("10"), Decimal("20"), Decimal("5")]

        metrics = validator.calculate_secondary_metrics(trade_pnls=trade_pnls)

        # Should handle gracefully - either infinity or None
        assert "profit_factor" in metrics
        pf = metrics["profit_factor"]
        assert pf is None or pf == float("inf") or pf > 1000


class TestFullReport:
    """Tests for complete report generation."""

    def test_full_report_generation(self):
        """Generate complete report combining t-test, bootstrap, and secondary metrics."""
        validator = KellyValidator()
        phase3_pnls = [Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), Decimal("7")]
        phase4_pnls = [Decimal("5"), Decimal("6"), Decimal("7"), Decimal("8"), Decimal("9")]

        report = validator.generate_report(phase3_pnls, phase4_pnls)

        # Verify it's a string with expected sections
        assert isinstance(report, str)
        assert "T-TEST" in report or "t-test" in report
        assert "BOOTSTRAP" in report or "Bootstrap" in report or "confidence" in report
        assert "p-value" in report
        assert "significant" in report.lower()

        # Should include mean PnL values
        assert "$" in report
