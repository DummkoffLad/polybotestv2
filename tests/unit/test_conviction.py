"""
Unit tests for ConvictionScorer - leader conviction-based confidence multiplier.

Tests verify multiplier calculation from leader signals:
- Position size relative to leader average (60% weight)
- Scale-in behavior (25% weight)
- Entry speed (15% weight)

Result range: [0.25, 2.0], quantized to Decimal("0.01")
"""
import pytest
from decimal import Decimal
from src.core.conviction import ConvictionScorer, ConvictionSignals


class TestConvictionScorer:
    """Test ConvictionScorer multiplier calculation."""

    def test_average_size_returns_neutral_multiplier(self):
        """Leader's average trade size returns ~1.0 multiplier."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # With average size, neutral speed, no scale-in -> ~1.0
        assert Decimal("0.90") <= multiplier <= Decimal("1.10")

    def test_double_avg_size_returns_high_multiplier(self):
        """Trade 2x leader average returns ~2.0 multiplier (capped)."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("200"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Large position should push toward cap
        assert multiplier >= Decimal("1.80")
        assert multiplier <= Decimal("2.00")

    def test_half_avg_size_returns_low_multiplier(self):
        """Trade 0.5x leader average returns ~0.5 multiplier."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("50"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Half-size position, neutral other signals
        assert Decimal("0.40") <= multiplier <= Decimal("0.70")

    def test_tiny_trade_floors_at_025(self):
        """Tiny trade (10% of average) hits floor at 0.25."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("10"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Floor is 0.25
        assert multiplier == Decimal("0.25")

    def test_huge_trade_caps_at_2(self):
        """Huge trade (5x average) caps at 2.0."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("500"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Cap is 2.0
        assert multiplier == Decimal("2.00")

    def test_scale_in_boosts_multiplier(self):
        """Scale-in adds 25% weight boost."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))

        # Without scale-in
        signals_no_scale = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier_no_scale = scorer.calculate_multiplier(signals_no_scale)

        # With scale-in
        signals_with_scale = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=True
        )
        multiplier_with_scale = scorer.calculate_multiplier(signals_with_scale)

        # Scale-in should boost
        assert multiplier_with_scale > multiplier_no_scale

    def test_no_scale_in_neutral(self):
        """No scale-in means scale component = 1.0 (neutral)."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Neutral position, neutral speed, no scale-in -> close to 1.0
        assert Decimal("0.90") <= multiplier <= Decimal("1.10")

    def test_fast_entry_penalizes(self):
        """Fast entry (<5s) penalizes multiplier."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))

        # Fast entry
        signals_fast = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=2.0,
            is_scale_in=False
        )
        multiplier_fast = scorer.calculate_multiplier(signals_fast)

        # Normal entry
        signals_normal = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier_normal = scorer.calculate_multiplier(signals_normal)

        # Fast should be lower than normal
        assert multiplier_fast < multiplier_normal

    def test_deliberate_entry_boosts(self):
        """Deliberate entry (>30s) boosts multiplier."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))

        # Deliberate entry
        signals_slow = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=45.0,
            is_scale_in=False
        )
        multiplier_slow = scorer.calculate_multiplier(signals_slow)

        # Normal entry
        signals_normal = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier_normal = scorer.calculate_multiplier(signals_normal)

        # Slow should be higher than normal
        assert multiplier_slow > multiplier_normal

    def test_normal_entry_speed_neutral(self):
        """Normal entry speed (5-30s) is neutral."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))

        # Test multiple speeds in neutral range
        for speed in [5.0, 15.0, 25.0, 30.0]:
            signals = ConvictionSignals(
                position_size_dollars=Decimal("100"),
                entry_speed_seconds=speed,
                is_scale_in=False
            )
            multiplier = scorer.calculate_multiplier(signals)

            # Should be close to 1.0 with neutral position size
            assert Decimal("0.90") <= multiplier <= Decimal("1.10")

    def test_result_quantized_to_cents(self):
        """Multiplier is quantized to Decimal('0.01')."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))
        signals = ConvictionSignals(
            position_size_dollars=Decimal("123.456"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier = scorer.calculate_multiplier(signals)

        # Check that multiplier has at most 2 decimal places
        # Convert to string and check decimal places
        str_multiplier = str(multiplier)
        if '.' in str_multiplier:
            decimal_places = len(str_multiplier.split('.')[1])
            assert decimal_places <= 2

    def test_update_leader_avg_size(self):
        """Update leader average from recent trades."""
        scorer = ConvictionScorer(leader_avg_size=Decimal("100"))

        # Check initial
        signals_before = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier_before = scorer.calculate_multiplier(signals_before)

        # Update average to 200
        recent_trades = [Decimal("200"), Decimal("200"), Decimal("200")]
        scorer.update_leader_avg_size(recent_trades)

        # Now $100 should be below average -> lower multiplier
        signals_after = ConvictionSignals(
            position_size_dollars=Decimal("100"),
            entry_speed_seconds=15.0,
            is_scale_in=False
        )
        multiplier_after = scorer.calculate_multiplier(signals_after)

        # After update, $100 is only half the new average
        assert multiplier_after < multiplier_before
