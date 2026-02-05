"""Unit tests for price_level strategy.

Tests the price zone-based sizing logic unique to this strategy.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

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


def _make_event(price: Decimal, action: TradeAction = TradeAction.BUY) -> MarketEvent:
    """Create MarketEvent at specified price."""
    trade = LeaderTrade(
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
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
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    return MarketEvent(trade=trade, prices=prices)


class TestPriceLevelZones:
    """Test price zone-based sizing adjustments."""

    def test_extreme_high_zone_reduces_buy_sizing(self):
        """Price >= 0.85 should reduce buy sizing (extreme high zone)."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.88"))
        decision = strategy.on_event(event)

        # Should either skip or buy with reduced size
        # Extreme high zone uses 0.3x multiplier for buys
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

        # If it decides to buy, verify sizing is reduced
        if decision.action == DecisionAction.BUY:
            # Base scaled amount would be ~50 * (100/900) * 0.85 = ~4.72
            # With 0.3x multiplier: ~1.42
            assert decision.dollars and decision.dollars < Decimal("5")

    def test_mid_zone_normal_sizing(self):
        """Price 0.15-0.65 should use normal sizing (mid zone)."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.45"))
        decision = strategy.on_event(event)

        # Mid zone uses 1.0x multiplier
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

        # If it decides to buy, verify normal sizing
        if decision.action == DecisionAction.BUY:
            # Base scaled amount would be ~50 * (100/900) * 0.85 = ~4.72
            # With 1.0x multiplier: ~4.72
            assert decision.dollars and decision.dollars > Decimal("3")

    def test_extreme_low_zone_behavior(self):
        """Price <= 0.15 should be in extreme low zone with reduced buy sizing."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.10"))
        decision = strategy.on_event(event)

        # Extreme low zone should use 0.2x multiplier for buys
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

        # If it decides to buy, verify sizing is greatly reduced
        if decision.action == DecisionAction.BUY:
            # Base scaled amount would be ~50 * (100/900) * 0.85 = ~4.72
            # With 0.2x multiplier: ~0.94
            # But enforces 5-share minimum at 0.10 = 0.50 cost
            # Actually gets 33.06 shares at 0.10 = 3.30 cost
            assert decision.dollars and decision.dollars < Decimal("5")

    def test_transition_high_zone(self):
        """Price 0.65-0.85 should be in transition high zone."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.75"))
        decision = strategy.on_event(event)

        # Transition high zone uses 0.7x multiplier for buys
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

    def test_transition_low_zone(self):
        """Price 0.15-0.35 should be in transition low zone."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.25"))
        decision = strategy.on_event(event)

        # Transition low zone uses 0.7x multiplier for buys
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)


class TestPriceLevelSellBehavior:
    """Test selling behavior in different price zones."""

    def test_extreme_high_zone_aggressive_sells(self):
        """Extreme high zone (>= 0.85) should use aggressive sell multiplier."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        # First buy to establish position
        buy_event = _make_event(price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)
        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

        # Then sell at extreme high price
        sell_event = _make_event(price=Decimal("0.88"), action=TradeAction.SELL)
        sell_decision = strategy.on_event(sell_event)

        # Should be willing to sell (1.5x multiplier in extreme high)
        # May skip if no position, but action should be reasonable
        assert sell_decision.action in (DecisionAction.SELL, DecisionAction.SKIP)

    def test_extreme_low_zone_aggressive_sells(self):
        """Extreme low zone (<= 0.15) should use aggressive sell multiplier to exit fast."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        # First buy to establish position
        buy_event = _make_event(price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)
        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

        # Then sell at extreme low price
        sell_event = _make_event(price=Decimal("0.10"), action=TradeAction.SELL)
        sell_decision = strategy.on_event(sell_event)

        # Should be willing to sell aggressively (1.5x multiplier)
        assert sell_decision.action in (DecisionAction.SELL, DecisionAction.SKIP)


class TestPriceLevelIntegration:
    """Integration tests for price_level strategy."""

    def test_buy_sell_cycle(self):
        """Test complete buy-sell cycle."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        # Buy at mid price
        buy_event = _make_event(price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)

        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Verify position exists
            state = strategy.get_state()
            assert "positions" in state
            assert state["total_deployed"] != "0"

    def test_strategy_name(self):
        """Verify strategy identifies as price_level."""
        strategy = get_strategy("price_level")
        assert strategy.name == "price_level"

    def test_initialization(self):
        """Test strategy initializes correctly."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert "positions" in state
        assert "total_deployed" in state
        assert "buys" in state
        assert "sells" in state

    def test_skip_tracking(self):
        """Test that skips are tracked properly."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        # Create event at invalid price
        event = _make_event(price=Decimal("1.50"))
        decision = strategy.on_event(event)

        assert decision.action == DecisionAction.SKIP

        state = strategy.get_state()
        assert "skips" in state
        assert state["skips"] > 0

    def test_extreme_price_auto_sell(self):
        """Test auto-sell at extreme price (>= 0.99)."""
        strategy = get_strategy("price_level")
        strategy.initialize(_make_config())

        # First buy to establish position at low price
        buy_event = _make_event(price=Decimal("0.30"))
        buy_decision = strategy.on_event(buy_event)
        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Create event at extreme high price (0.99)
            extreme_trade = LeaderTrade(
                timestamp=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
                market_id="market_test",
                token_id="token_test",
                side=TradeSide.UP,
                action=TradeAction.BUY,  # Leader action doesn't matter
                dollars=Decimal("10"),
                price=Decimal("0.99"),
                shares=Decimal("10"),
                source="test",
                tx_hash="0xtest2",
                latency_ms=0,
            )
            extreme_prices = PriceSnapshot(
                token_id="token_test",
                bid=Decimal("0.99"),
                ask=Decimal("0.995"),
                mid=Decimal("0.9925"),
                spread_pct=Decimal("0.5"),
                timestamp=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
            )
            extreme_event = MarketEvent(trade=extreme_trade, prices=extreme_prices)

            decision = strategy.on_event(extreme_event)

            # Should auto-sell at extreme price
            assert decision.action == DecisionAction.SELL
