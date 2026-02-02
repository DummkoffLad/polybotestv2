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
