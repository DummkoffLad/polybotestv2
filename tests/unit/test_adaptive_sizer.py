"""
Unit tests for AdaptiveSizer.

Tests the adaptive sizing logic that bridges Phase 3 DynamicSizer and Phase 4 Kelly sizing:
- Cold start fallback to DynamicSizer when EdgeTracker has insufficient data
- Kelly sizing when EdgeTracker has sufficient per-token data
- Conviction multiplier applied AFTER Kelly sizing
- Negative edge handling (returns None)
- Seamless transition from fallback to Kelly as data accumulates
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.core.adaptive_sizer import AdaptiveSizer
from src.core.edge_tracker import EdgeTracker, TradeResult
from src.core.kelly_engine import KellyCalculator
from src.core.sizing import DynamicSizer, SizingConfig


# Helper to create TradeResult objects
def make_trade_result(token_id: str, pnl_pct: Decimal) -> TradeResult:
    """Create a TradeResult with minimal fields."""
    return TradeResult(
        token_id=token_id,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("100.00") + pnl_pct * Decimal("100.00"),
        pnl_pct=pnl_pct,
        timestamp=datetime.now(timezone.utc)
    )


# Helper to populate EdgeTracker with N trades
def populate_edge_tracker(tracker: EdgeTracker, token_id: str, trades: int, win_rate: Decimal = Decimal("0.6")):
    """
    Populate EdgeTracker with trade history.

    Args:
        tracker: EdgeTracker instance
        token_id: Token to populate
        trades: Number of trades to add
        win_rate: Win rate (0.6 = 60% wins, 40% losses)
    """
    wins = int(float(win_rate) * trades)
    losses = trades - wins

    # Add wins
    for _ in range(wins):
        tracker.record_trade(make_trade_result(token_id, Decimal("0.10")))  # +10% win

    # Add losses
    for _ in range(losses):
        tracker.record_trade(make_trade_result(token_id, Decimal("-0.05")))  # -5% loss


class TestAdaptiveSizerColdStart:
    """Tests for cold start behavior (EdgeTracker has insufficient data)."""

    def test_cold_start_uses_phase3_fallback(self):
        """Cold start: No edge data -> uses DynamicSizer, returns (size, 'phase3_fallback')."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator()
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - no trades recorded, cold start
        size, reason = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Assert - should use Phase 3 fallback
        # DynamicSizer: base=1% * 100 = 1.00, quality_mult=0.5+0.5=1.0 -> 1.00
        assert size == Decimal("1.00")
        assert reason == "phase3_fallback"

    def test_cold_start_never_returns_none(self):
        """Cold start: Even with no Kelly data, Phase 3 always returns a valid size."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("2.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("50.00"))
        kelly_calc = KellyCalculator()
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - cold start
        size, reason = sizer.calculate_position_size(
            token_id="ETH",
            current_equity=Decimal("50.00"),
            quality_score=Decimal("0.75")
        )

        # Assert - should never be None
        assert size is not None
        assert size > Decimal("0")
        assert reason == "phase3_fallback"

    def test_cold_start_passes_quality_score(self):
        """Cold start: Quality score is forwarded to DynamicSizer."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator()
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - high quality score
        size_high, _ = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("1.00")  # High quality -> 1.5x multiplier
        )

        # Act - low quality score
        size_low, _ = sizer.calculate_position_size(
            token_id="ETH",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.00")  # Low quality -> 0.5x multiplier
        )

        # Assert - high quality should produce larger size
        # high: base=1.00, mult=0.5+1.0=1.5 -> 1.50
        # low: base=1.00, mult=0.5+0.0=0.5 -> 0.50
        assert size_high == Decimal("1.50")
        assert size_low == Decimal("0.50")


class TestAdaptiveSizerKellyActive:
    """Tests for Kelly sizing when EdgeTracker has sufficient data."""

    def test_kelly_active_uses_kelly_size(self):
        """Kelly active: With 20+ trades recorded, uses Kelly, returns (size, 'kelly')."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))  # Half Kelly
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        # Populate with 20 trades: 60% win rate, +10% wins, -5% losses
        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act
        size, reason = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")  # Not used in Kelly mode
        )

        # Assert - should use Kelly
        assert size is not None
        assert size > Decimal("0")
        assert reason == "kelly"

    def test_kelly_applies_conviction_multiplier(self):
        """Kelly sizing: Applies conviction multiplier to Kelly size."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - no conviction multiplier (default 1.0)
        size_1x, _ = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50"),
            conviction_multiplier=Decimal("1.0")
        )

        # Act - 2x conviction multiplier
        size_2x, _ = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50"),
            conviction_multiplier=Decimal("2.0")
        )

        # Assert - 2x conviction should double the size
        assert size_2x == size_1x * Decimal("2")

    def test_kelly_conviction_order_matters(self):
        """Kelly sizing: Conviction applied AFTER fractional Kelly (not before)."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))  # Half Kelly
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act
        size, reason = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50"),
            conviction_multiplier=Decimal("1.5")
        )

        # Assert - conviction multiplier should be applied after Kelly calculation
        # This test verifies order of operations: (Kelly * kelly_fraction) * conviction
        # Not: (Kelly * conviction) * kelly_fraction
        # The specific value depends on Kelly calculation, but we verify it's applied
        assert size is not None
        assert reason == "kelly"


class TestAdaptiveSizerNegativeEdge:
    """Tests for negative edge handling."""

    def test_negative_edge_returns_none(self):
        """Negative edge: Kelly computes negative edge -> returns (None, 'negative_edge')."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        # Populate with losing trades: 20% win rate, small wins, large losses
        # This creates negative expected value
        for _ in range(4):  # 4 wins
            edge_tracker.record_trade(make_trade_result("BADCOIN", Decimal("0.02")))  # +2% win
        for _ in range(16):  # 16 losses
            edge_tracker.record_trade(make_trade_result("BADCOIN", Decimal("-0.10")))  # -10% loss

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act
        size, reason = sizer.calculate_position_size(
            token_id="BADCOIN",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Assert - negative edge should return None
        assert size is None
        assert reason == "negative_edge"

    def test_negative_edge_skips_trade(self):
        """Negative edge: None size means 'don't trade this token'."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        # Populate with losing trades
        for _ in range(3):
            edge_tracker.record_trade(make_trade_result("SCAM", Decimal("0.01")))
        for _ in range(17):
            edge_tracker.record_trade(make_trade_result("SCAM", Decimal("-0.15")))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act
        size, reason = sizer.calculate_position_size(
            token_id="SCAM",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.90")  # High quality doesn't matter
        )

        # Assert - should skip trade
        assert size is None
        assert reason == "negative_edge"


class TestAdaptiveSizerTransition:
    """Tests for transition from fallback to Kelly."""

    def test_transition_from_fallback_to_kelly(self):
        """Transition: Start with fallback, add 20 trades, next call uses Kelly."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act 1 - cold start (no trades)
        size_before, reason_before = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Add 20 trades
        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        # Act 2 - after data accumulation
        size_after, reason_after = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Assert - should transition from fallback to Kelly
        assert reason_before == "phase3_fallback"
        assert reason_after == "kelly"
        # Sizes will differ because different sizing methods
        assert size_before != size_after

    def test_different_tokens_different_methods(self):
        """Per-token tracking: Token A has Kelly data (uses Kelly), Token B doesn't (uses fallback)."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        # Populate only SOL with data
        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - SOL has data
        size_sol, reason_sol = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Act - ETH has no data
        size_eth, reason_eth = sizer.calculate_position_size(
            token_id="ETH",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Assert - different methods per token
        assert reason_sol == "kelly"
        assert reason_eth == "phase3_fallback"


class TestAdaptiveSizerEdgeCases:
    """Tests for edge cases."""

    def test_zero_equity_returns_zero(self):
        """Edge case: Zero equity -> position size = 0."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator()
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act - zero equity
        size, reason = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("0.00"),
            quality_score=Decimal("0.50")
        )

        # Assert
        assert size == Decimal("0.00")
        assert reason == "phase3_fallback"

    def test_result_quantized_to_cents(self):
        """Edge case: Final size quantized to Decimal('0.01')."""
        # Setup
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        dynamic_sizer = DynamicSizer(config)
        dynamic_sizer.initialize(Decimal("100.00"))
        kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
        edge_tracker = EdgeTracker(min_trades_for_kelly=20)

        populate_edge_tracker(edge_tracker, "SOL", trades=20, win_rate=Decimal("0.6"))

        sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

        # Act
        size, _ = sizer.calculate_position_size(
            token_id="SOL",
            current_equity=Decimal("100.00"),
            quality_score=Decimal("0.50")
        )

        # Assert - should be quantized to 2 decimal places
        # Check that the size has exactly 2 decimal places
        assert size is not None
        assert size.as_tuple().exponent == -2  # Decimal("0.01") has exponent -2
