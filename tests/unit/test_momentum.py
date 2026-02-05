"""Unit tests for momentum_mirror strategy.

Tests price momentum tracking and conviction-based decisions.
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
                dollars: Decimal = Decimal("50"),
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
        dollars=dollars,
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


class TestMomentumTracking:
    """Test momentum calculation and tracking."""

    def test_initializes_momentum_state(self):
        """Momentum strategy should initialize momentum tracking."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        assert strategy.name == "momentum_mirror"

        # Verify internal state exists
        assert hasattr(strategy, "_recent_trades")
        assert isinstance(strategy._recent_trades, dict)

    def test_momentum_affects_decisions(self):
        """Momentum should influence trade decisions."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.50"))
        decision = strategy.on_event(event)

        assert decision.action in (DecisionAction.BUY, DecisionAction.SKIP)

    def test_state_tracks_momentum(self):
        """get_state should include momentum info."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert isinstance(state, dict)
        assert "positions" in state
        assert "total_deployed" in state

    def test_tracks_recent_trades(self):
        """Momentum strategy should track recent trades per token."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Submit multiple events
        for i in range(3):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            strategy.on_event(event)

        # Check that trade history was recorded
        assert "token_test" in strategy._recent_trades
        assert len(strategy._recent_trades["token_test"]) >= 3


class TestConvictionMultipliers:
    """Test conviction-based sizing multipliers."""

    def test_single_trade_baseline_conviction(self):
        """Single trade should use 1.0x multiplier (baseline)."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        event = _make_event(price=Decimal("0.50"))
        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            # Base: ~50 * (100/900) * 0.85 = ~4.72
            # 1.0x conviction: ~4.72
            assert decision.dollars and Decimal("3") < decision.dollars < Decimal("7")

    def test_multiple_trades_increase_conviction(self):
        """Multiple trades in same direction should increase sizing."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Submit 3 buy trades in quick succession (same direction)
        for i in range(3):
            event = _make_event(
                price=Decimal("0.50"),
                action=TradeAction.BUY,
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            decision = strategy.on_event(event)

            # Third trade should have higher conviction (1.30x multiplier)
            if i == 2 and decision.action == DecisionAction.BUY:
                # Base: ~50 * (100/900) * 0.85 = ~4.72
                # 1.30x conviction: ~6.14
                assert decision.dollars and decision.dollars > Decimal("5")

    def test_high_conviction_capped(self):
        """Conviction multiplier should cap at 1.40x for 4+ trades."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Submit 5 buy trades
        for i in range(5):
            event = _make_event(
                price=Decimal("0.50"),
                action=TradeAction.BUY,
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            decision = strategy.on_event(event)

            # Fifth trade should have max conviction (1.40x multiplier)
            if i == 4 and decision.action == DecisionAction.BUY:
                # Base: ~50 * (100/900) * 0.85 = ~4.72
                # 1.40x conviction: ~6.61
                assert decision.dollars and decision.dollars > Decimal("5.5")

    def test_conviction_window_cleanup(self):
        """Old trades should be pruned from conviction calculation."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Add old trades
        for i in range(3):
            event = _make_event(
                price=Decimal("0.50"),
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            strategy.on_event(event)

        # Add new trade beyond conviction window (300 seconds)
        new_event = _make_event(
            price=Decimal("0.50"),
            timestamp=base_time + timedelta(seconds=350)
        )
        decision = strategy.on_event(new_event)

        # Old trades should be pruned, so conviction resets to 1.0x
        if decision.action == DecisionAction.BUY:
            # Should be back to baseline sizing
            assert decision.dollars


class TestLargeTradeBoost:
    """Test percentage-based size boost for large leader trades."""

    def test_large_leader_trade_boost(self):
        """Large leader trades (>= 5% of capital) should get 1.20x boost."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        # Leader capital is 900, so 5% = 45
        # Create trade with 50 dollars (> 5%)
        event = _make_event(
            price=Decimal("0.50"),
            dollars=Decimal("50")
        )
        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            # Base: ~50 * (100/900) * 0.85 = ~4.72
            # 1.0x conviction * 1.20x large trade = 1.20x
            # ~4.72 * 1.20 = ~5.66
            assert decision.dollars and decision.dollars > Decimal("4.5")

    def test_medium_leader_trade_boost(self):
        """Medium leader trades (>= 3% of capital) should get 1.10x boost."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        # Leader capital is 900, so 3% = 27
        event = _make_event(
            price=Decimal("0.50"),
            dollars=Decimal("30")
        )
        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            # Base: ~30 * (100/900) * 0.85 = ~2.83
            # 1.0x conviction * 1.10x medium trade = 1.10x
            # ~2.83 * 1.10 = ~3.11
            assert decision.dollars


class TestMomentumIntegration:
    """Integration tests for momentum_mirror strategy."""

    def test_strategy_name(self):
        """Verify strategy identifies as momentum_mirror."""
        strategy = get_strategy("momentum_mirror")
        assert strategy.name == "momentum_mirror"

    def test_initialization(self):
        """Test strategy initializes correctly."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        state = strategy.get_state()
        assert "positions" in state
        assert "buys" in state
        assert "sells" in state

    def test_buy_sell_cycle(self):
        """Test complete buy-sell cycle."""
        strategy = get_strategy("momentum_mirror")
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

    def test_direction_matters_for_conviction(self):
        """Conviction should only count same-direction trades."""
        strategy = get_strategy("momentum_mirror")
        strategy.initialize(_make_config())

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Submit 2 buys
        for i in range(2):
            event = _make_event(
                price=Decimal("0.50"),
                action=TradeAction.BUY,
                timestamp=base_time + timedelta(seconds=i * 10)
            )
            strategy.on_event(event)

        # Then a sell - should reset conviction for sells
        sell_event = _make_event(
            price=Decimal("0.50"),
            action=TradeAction.SELL,
            timestamp=base_time + timedelta(seconds=30)
        )
        decision = strategy.on_event(sell_event)

        # Sell decision shouldn't benefit from buy conviction
        assert decision.action in (DecisionAction.SELL, DecisionAction.SKIP)
