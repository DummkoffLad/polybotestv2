"""
Unit tests for KellyCalculator - Half Kelly position sizing calculator.

Tests verify:
- Positive edge returns position size
- Negative edge returns None
- Half Kelly formula (0.5x full Kelly)
- 20% equity cap
- Edge cases: zero win rate, 100% win rate, zero avg_loss
- Expected value calculation
- Custom Kelly fractions
- Small equity scenarios
"""

import pytest
from decimal import Decimal
from src.core.kelly_engine import KellyCalculator


class TestKellyCalculator:
    """Test KellyCalculator Half Kelly position sizing."""

    def test_positive_edge_returns_position_size(self):
        """Positive edge should return a position size."""
        calc = KellyCalculator()

        # Win rate 60%, avg win 15%, avg loss 10%
        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.15"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("100.00")
        )

        assert size is not None
        assert size > Decimal("0")

    def test_negative_edge_returns_none(self):
        """Negative edge should return None (don't bet)."""
        calc = KellyCalculator()

        # Win rate 30%, avg win 5%, avg loss 15% -> negative edge
        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.30"),
            avg_win_pct=Decimal("0.05"),
            avg_loss_pct=Decimal("0.15"),
            current_equity=Decimal("100.00")
        )

        assert size is None

    def test_half_kelly_is_half_of_full(self):
        """Verify fractional Kelly = full_kelly * 0.5."""
        calc = KellyCalculator(kelly_fraction=Decimal("0.5"))

        # Use modest parameters that won't hit 20% cap
        # Calculate with half Kelly
        half_size = calc.calculate_kelly_size(
            win_rate=Decimal("0.55"),
            avg_win_pct=Decimal("0.10"),
            avg_loss_pct=Decimal("0.08"),
            current_equity=Decimal("100.00")
        )

        # Calculate with full Kelly
        calc_full = KellyCalculator(kelly_fraction=Decimal("1.0"))
        full_size = calc_full.calculate_kelly_size(
            win_rate=Decimal("0.55"),
            avg_win_pct=Decimal("0.10"),
            avg_loss_pct=Decimal("0.08"),
            current_equity=Decimal("100.00")
        )

        assert half_size is not None
        assert full_size is not None
        # Half Kelly should be half of full Kelly (before cap)
        assert half_size == full_size / Decimal("2")

    def test_kelly_caps_at_20pct_equity(self):
        """Even with huge edge, position capped at 20% of equity."""
        calc = KellyCalculator()

        # Extreme edge: 90% win rate, 50% avg win, 5% avg loss
        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.90"),
            avg_win_pct=Decimal("0.50"),
            avg_loss_pct=Decimal("0.05"),
            current_equity=Decimal("100.00")
        )

        assert size is not None
        # Should be capped at 20% of equity
        assert size == Decimal("20.00")

    def test_zero_win_rate_returns_none(self):
        """Win rate = 0 should return None (no edge)."""
        calc = KellyCalculator()

        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.00"),
            avg_win_pct=Decimal("0.10"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("100.00")
        )

        assert size is None

    def test_one_win_rate_returns_none(self):
        """Win rate = 1.0 should return None (invalid, unrealistic)."""
        calc = KellyCalculator()

        size = calc.calculate_kelly_size(
            win_rate=Decimal("1.00"),
            avg_win_pct=Decimal("0.10"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("100.00")
        )

        # 100% win rate is unrealistic, should be rejected
        assert size is None

    def test_zero_avg_loss_returns_none(self):
        """avg_loss_pct = 0 should return None (avoid div by zero)."""
        calc = KellyCalculator()

        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.10"),
            avg_loss_pct=Decimal("0.00"),
            current_equity=Decimal("100.00")
        )

        assert size is None

    def test_quantized_to_cents(self):
        """Result should be quantized to Decimal('0.01')."""
        calc = KellyCalculator()

        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.55"),
            avg_win_pct=Decimal("0.12"),
            avg_loss_pct=Decimal("0.08"),
            current_equity=Decimal("100.00")
        )

        assert size is not None
        # Check that size has at most 2 decimal places
        assert size == size.quantize(Decimal("0.01"))

    def test_expected_value_positive(self):
        """E = (W x AvgWin) - ((1-W) x AvgLoss) should be positive."""
        calc = KellyCalculator()

        # E = (0.60 * 0.15) - (0.40 * 0.10) = 0.09 - 0.04 = 0.05
        ev = calc.calculate_expected_value(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.15"),
            avg_loss_pct=Decimal("0.10")
        )

        expected = Decimal("0.05")
        assert ev == expected

    def test_expected_value_negative(self):
        """E = (0.30*0.05) - (0.70*0.15) should be negative."""
        calc = KellyCalculator()

        # E = (0.30 * 0.05) - (0.70 * 0.15) = 0.015 - 0.105 = -0.09
        ev = calc.calculate_expected_value(
            win_rate=Decimal("0.30"),
            avg_win_pct=Decimal("0.05"),
            avg_loss_pct=Decimal("0.15")
        )

        expected = Decimal("-0.09")
        assert ev == expected

    def test_custom_kelly_fraction(self):
        """KellyCalculator with kelly_fraction=0.25 should use quarter Kelly."""
        calc_quarter = KellyCalculator(kelly_fraction=Decimal("0.25"))
        calc_half = KellyCalculator(kelly_fraction=Decimal("0.50"))

        quarter_size = calc_quarter.calculate_kelly_size(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.20"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("100.00")
        )

        half_size = calc_half.calculate_kelly_size(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.20"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("100.00")
        )

        assert quarter_size is not None
        assert half_size is not None
        # Quarter should be half of half
        assert quarter_size == half_size / Decimal("2")

    def test_small_equity_produces_small_size(self):
        """equity=$10 should produce proportionally small position."""
        calc = KellyCalculator()

        size = calc.calculate_kelly_size(
            win_rate=Decimal("0.60"),
            avg_win_pct=Decimal("0.15"),
            avg_loss_pct=Decimal("0.10"),
            current_equity=Decimal("10.00")
        )

        assert size is not None
        assert size < Decimal("10.00")  # Less than total equity
        assert size > Decimal("0.00")   # But positive
