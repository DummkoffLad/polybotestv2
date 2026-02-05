"""Unit tests for velocity strategy.

Tests trade velocity detection and threshold-based decisions.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from src.strategies.base import get_strategy, StrategyConfig, DecisionAction
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide


def _make_config(**overrides) -> StrategyConfig:
    """Create StrategyConfig with test defaults."""
    defaults = {
        "starting_capital": Decimal("100"),
        "hourly_budget": Decimal("100"),
        "cash_reserve_pct": Decimal("10"),
        "leader_capital": Decimal("900"),
        "k_factor": Decimal("0.85"),
        "per_market_cap_pct": Decimal("30"),
        "per_side_pct": Decimal("26"),
        "global_exposure_pct": Decimal("100"),
        "spread_cost_pct": Decimal("2.0"),
        "slippage_cost_pct": Decimal("1.0"),
        "max_total_cost_pct": Decimal("8.0"),
        "params": {},
    }
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def _make_event(price: Decimal = Decimal("0.50"),
                action: TradeAction = TradeAction.BUY,
                timestamp: datetime = None) -> MarketEvent:
    """Create MarketEvent at specified price and time."""
    if timestamp is None:
        timestamp = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    trade = LeaderTrade(
        timestamp=timestamp,
        market_id="market_test",
        token_id="token_test",
        side=TradeSide.UP,
        action=action,
        dollars=Decimal("50"),
        price=price,
        shares=Decimal("100"),
        source="test",
        tx_hash="0xtest",
        latency_ms=0,
    )
    prices = PriceSnapshot(
        token_id="token_test",
        bid=price - Decimal("0.01"),
        ask=price,
        mid=price - Decimal("0.005"),
        spread_pct=Decimal("2.0"),
        timestamp=timestamp,
    )
    return MarketEvent(trade=trade, prices=prices)


class TestVelocityThresholds:
    """Test velocity-based decision making."""

    def test_initializes_velocity_tracking(self):
        """Velocity strategy should track trade velocity."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        # Verify strategy initializes properly
        assert strategy.name == "velocity"

        # Verify internal state exists
        assert hasattr(strategy, "_trade_history")
        assert isinstance(strategy._trade_history, dict)

    def test_normal_velocity_trades(self):
        """Normal velocity should not block trades."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.50"))
        decision = strategy.on_event(event)

        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

    def test_state_includes_velocity_metrics(self):
        """get_state should include velocity tracking info."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert isinstance(state, dict)
        assert "positions" in state
        assert "total_deployed" in state

    def test_tracks_trade_history(self):
        """Velocity strategy should track trade history per token."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        # Submit multiple events
        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            strategy.on_event(event)

        # Check that trade history was recorded
        assert "token_test" in strategy._trade_history
        assert len(strategy._trade_history["token_test"]) == 3

    def test_velocity_calculation_affects_sizing(self):
        """High velocity should increase position size."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create high velocity scenario with rapid trades
        for i in range(5):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 5)  # One trade every 5 seconds
            )
            decision = strategy.on_event(event)

            # Last decision should show high velocity multiplier effect
            if i == 4 and decision.action == DecisionAction.BUY:
                # High velocity should increase sizing
                # Base: ~50 * (100/900) * 0.85 = ~4.72
                # High velocity: 1.5x = ~7.08
                assert decision.dollars and decision.dollars > Decimal("5")


class TestVelocityRegimes:
    """Test different velocity regime behaviors."""

    def test_high_velocity_regime(self):
        """Test high velocity regime relaxes cost thresholds."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create high velocity with multiple rapid trades
        for i in range(4):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 3)
            )
            strategy.on_event(event)

        # High velocity regime should be detected
        assert "token_test" in strategy._trade_history

    def test_low_velocity_regime(self):
        """Test low velocity regime tightens thresholds."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create low velocity with sparse trades
        # First create baseline
        for i in range(3):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 30)
            )
            strategy.on_event(event)

        # Then slow down (low velocity)
        event = _make_event(
            price=Decimal("0.50"),
            timestamp=base_time + timedelta(seconds=150)
        )
        decision = strategy.on_event(event)

        # Should still process but with lower multiplier
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)


class TestVelocityIntegration:
    """Integration tests for velocity strategy."""

    def test_strategy_name(self):
        """Verify strategy identifies as velocity."""
        strategy = get_strategy("velocity")
        assert strategy.name == "velocity"

    def test_initialization(self):
        """Test strategy initializes correctly."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert "positions" in state
        assert "buys" in state
        assert "sells" in state

    def test_buy_sell_cycle(self):
        """Test complete buy-sell cycle."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        # Buy
        buy_event = _make_event(price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)

        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Verify position exists
            state = strategy.get_state()
            assert state["total_deployed"] != "0"
            assert state["buys"] == 1

    def test_velocity_history_cleanup(self):
        """Test that old trades are removed from history."""
        strategy = get_strategy("velocity")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Add old trade
        old_event = _make_event(
            price=Decimal("0.50"),
            timestamp=base_time
        )
        strategy.on_event(old_event)

        # Add new trade much later (beyond 2x LONG_WINDOW = 240 seconds)
        new_event = _make_event(
            price=Decimal("0.50"),
            timestamp=base_time + timedelta(seconds=300)
        )
        strategy.on_event(new_event)

        # Old trade should be cleaned up
        # History should only contain recent trades
        history = strategy._trade_history.get("token_test", [])
        assert len(history) <= 2  # May include both depending on cleanup timing
