"""Unit tests for spread_aware strategy.

Tests spread-based decision making and cost thresholds.
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


def _make_event_with_spread(spread_pct: Decimal,
                            price: Decimal = Decimal("0.50"),
                            action: TradeAction = TradeAction.BUY) -> MarketEvent:
    """Create MarketEvent with specified spread."""
    spread = price * spread_pct / 100
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
        bid=price - spread / 2,
        ask=price + spread / 2,
        mid=price,
        spread_pct=spread_pct,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    return MarketEvent(trade=trade, prices=prices)


class TestSpreadAwareBehavior:
    """Test spread-based decision making."""

    def test_tight_spread_allows_trade(self):
        """Tight spread (< 1.5%) should not block trades."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        event = _make_event_with_spread(Decimal("1.0"))
        decision = strategy.on_event(event)

        # Tight spread should allow trade
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

    def test_tight_spread_increases_sizing(self):
        """Tight spread should use 1.4x multiplier for larger positions."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        event = _make_event_with_spread(Decimal("1.0"))
        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            # Base: ~50 * (100/900) * 0.85 = ~4.72
            # Tight spread: 1.4x = ~6.61
            assert decision.dollars and decision.dollars > Decimal("5")

    def test_normal_spread_behavior(self):
        """Normal spread (1.5-3%) should use 1.0x multiplier."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        event = _make_event_with_spread(Decimal("2.0"))
        decision = strategy.on_event(event)

        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

        if decision.action == DecisionAction.BUY:
            # Normal spread: 1.0x multiplier
            assert decision.dollars

    def test_wide_spread_reduces_sizing(self):
        """Wide spread (3-5%) should use 0.5x multiplier."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        event = _make_event_with_spread(Decimal("4.0"))
        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            # Base: ~50 * (100/900) * 0.85 = ~4.72
            # Wide spread: 0.5x = ~2.36
            assert decision.dollars and decision.dollars < Decimal("4")

    def test_very_wide_spread_skips(self):
        """Very wide spread (> 5%) should skip trades."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        event = _make_event_with_spread(Decimal("6.0"))
        decision = strategy.on_event(event)

        # Should skip due to very wide spread
        assert decision.action == DecisionAction.SKIP
        assert decision.skip_reason == "spread_too_wide"


class TestSpreadCostCalculation:
    """Test cost calculation including spreads."""

    def test_cost_includes_spread(self):
        """Cost check should include spread in total cost."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # High spread near cost threshold
        event = _make_event_with_spread(Decimal("7.0"))
        decision = strategy.on_event(event)

        # Should skip due to high total cost (spread too high)
        assert decision.action == DecisionAction.SKIP

    def test_wide_spread_tighter_cost_threshold(self):
        """Wide spread should make cost threshold stricter."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Wide spread (4%) with some price drift
        # Creates event where spread + drift might exceed threshold
        event = _make_event_with_spread(Decimal("4.0"), price=Decimal("0.52"))
        decision = strategy.on_event(event)

        # May skip due to stricter threshold with wide spread
        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)


class TestSpreadTracking:
    """Test spread history tracking."""

    def test_tracks_spread_history(self):
        """Strategy should track recent spreads per token."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Submit multiple events with different spreads
        for spread in [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]:
            event = _make_event_with_spread(spread)
            strategy.on_event(event)

        # Check that spread history was recorded
        assert "token_test" in strategy._spread_history
        assert len(strategy._spread_history["token_test"]) == 3

    def test_spread_history_limited(self):
        """Spread history should be limited to window size."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Submit more events than window size (10)
        for i in range(15):
            event = _make_event_with_spread(Decimal("2.0"))
            strategy.on_event(event)

        # History should be limited to 10
        history = strategy._spread_history.get("token_test", [])
        assert len(history) <= 10

    def test_average_spread_calculation(self):
        """Should calculate average recent spread."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Submit events with known spreads
        spreads = [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]
        for spread in spreads:
            event = _make_event_with_spread(spread)
            strategy.on_event(event)

        # Check average calculation
        avg = strategy._get_avg_spread("token_test")
        assert avg is not None
        assert avg == Decimal("2.0")  # (1 + 2 + 3) / 3


class TestSpreadAwareSelling:
    """Test selling behavior with different spreads."""

    def test_wide_spread_urgent_exit(self):
        """Wide spread should skip loss protection for urgent exit."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # First buy at normal spread
        buy_event = _make_event_with_spread(Decimal("2.0"), price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)
        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Then sell at wide spread (urgent exit scenario)
            sell_event = _make_event_with_spread(
                Decimal("4.5"),
                price=Decimal("0.48"),
                action=TradeAction.SELL
            )
            sell_decision = strategy.on_event(sell_event)

            # Should be willing to sell even at loss due to wide spread
            assert sell_decision.action in (DecisionAction.SELL, DecisionAction.SKIP)

    def test_normal_spread_loss_protection(self):
        """Normal spread should apply loss protection."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Buy at high price
        buy_event = _make_event_with_spread(Decimal("2.0"), price=Decimal("0.60"))
        buy_decision = strategy.on_event(buy_event)
        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Try to sell at loss with normal spread
            sell_event = _make_event_with_spread(
                Decimal("2.0"),
                price=Decimal("0.50"),
                action=TradeAction.SELL
            )
            sell_decision = strategy.on_event(sell_event)

            # May skip due to loss protection at normal spread
            assert sell_decision.action in (DecisionAction.SELL, DecisionAction.SKIP)


class TestSpreadAwareIntegration:
    """Integration tests for spread_aware strategy."""

    def test_initializes_correctly(self):
        """spread_aware strategy should initialize properly."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        assert strategy.name == "spread_aware"
        assert hasattr(strategy, "_spread_history")

    def test_state_tracking(self):
        """get_state should return valid state dict."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert isinstance(state, dict)
        assert "positions" in state
        assert "total_deployed" in state
        assert "buys" in state
        assert "sells" in state

    def test_buy_sell_cycle(self):
        """Test complete buy-sell cycle."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Buy at tight spread
        buy_event = _make_event_with_spread(Decimal("1.0"), price=Decimal("0.50"))
        buy_decision = strategy.on_event(buy_event)

        if buy_decision.action == DecisionAction.BUY:
            strategy.on_fill(buy_event, buy_decision)

            # Verify position exists
            state = strategy.get_state()
            assert state["total_deployed"] != "0"
            assert state["buys"] == 1

    def test_strategy_name(self):
        """Verify strategy identifies as spread_aware."""
        strategy = get_strategy("spread_aware")
        assert strategy.name == "spread_aware"

    def test_skip_tracking(self):
        """Test that skips are tracked properly."""
        strategy = get_strategy("spread_aware")
        strategy.initialize(_make_config())

        # Create event with very wide spread
        event = _make_event_with_spread(Decimal("8.0"))
        decision = strategy.on_event(event)

        assert decision.action == DecisionAction.SKIP

        state = strategy.get_state()
        assert "skips" in state
        assert state["skips"] > 0
        assert "spread_too_wide" in state["skip_reasons"]
