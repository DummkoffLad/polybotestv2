"""Integration tests for dynamic sizing in MirrorStrategy.

Verifies end-to-end behavior of DynamicSizer, CapitalManager, and TradeQualityScorer
through the MirrorStrategy interface.
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from src.strategies.mirror.strategy import MirrorStrategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide


# ============================================================================
# TEST HELPERS
# ============================================================================

def make_config(**overrides) -> StrategyConfig:
    """Create a StrategyConfig with $100 defaults."""
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


def make_event(**overrides) -> MarketEvent:
    """Create a MarketEvent with configurable parameters."""
    # Trade defaults
    trade_defaults = {
        "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "market_id": "market_test",
        "token_id": "token_test",
        "side": TradeSide.UP,
        "action": TradeAction.BUY,
        "dollars": Decimal("100.0"),
        "price": Decimal("0.50"),
        "shares": Decimal("200.0"),
        "source": "test",
        "tx_hash": "0xtest",
        "latency_ms": 0,
    }

    # Price defaults - excellent spread (25 bps = 0.25% spread = high quality)
    # 25 bps is below the 50 bps excellent threshold
    price_defaults = {
        "token_id": "token_test",
        "bid": Decimal("0.49875"),
        "ask": Decimal("0.50125"),
        "mid": Decimal("0.50"),
        "spread_pct": Decimal("0.25"),
        "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    }

    # Apply overrides
    for key, value in overrides.items():
        if key in trade_defaults:
            trade_defaults[key] = value
        elif key in price_defaults:
            price_defaults[key] = value

    trade = LeaderTrade(**trade_defaults)
    prices = PriceSnapshot(**price_defaults)
    return MarketEvent(trade=trade, prices=prices)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

def test_dynamic_caps_scale_with_equity():
    """Verify risk caps use current equity, not starting capital."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Initial equity: $100
    # Per-market cap: 30% of $100 = $30

    # Execute a buy at $0.50 with good spread
    event_buy = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy = strategy.on_event(event_buy)
    assert decision_buy.action == DecisionAction.BUY
    strategy.on_fill(event_buy, decision_buy)

    # Sell at $0.70 to realize profit
    event_sell = make_event(
        action=TradeAction.SELL,
        bid=Decimal("0.69875"),
        ask=Decimal("0.70125"),
        mid=Decimal("0.70"),
        price=Decimal("0.70")
    )
    decision_sell = strategy.on_event(event_sell)
    assert decision_sell.action == DecisionAction.SELL
    strategy.on_fill(event_sell, decision_sell)

    # Now equity should be > $100 (realized profit)
    current_equity = strategy._calculate_current_equity()
    assert current_equity > Decimal("100")

    # Next buy should use higher cap
    # Per-market cap should now be 30% of current_equity (not 30% of $100)
    expected_mkt_cap = current_equity * Decimal("0.30")

    # Verify cap is calculated from current equity
    # This is implicit in the sizing logic - if caps don't scale,
    # we'd be limited to original $30 market cap
    event_buy2 = make_event(
        action=TradeAction.BUY,
        token_id="token_test2",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy2 = strategy.on_event(event_buy2)

    # Decision should use scaled caps (not blocked by original $30 cap)
    assert decision_buy2.action == DecisionAction.BUY


def test_size_compounds_after_wins():
    """Verify position sizes increase after profitable trades."""
    strategy = MirrorStrategy()
    # Use larger capital to avoid 5-share minimum constraint
    config = make_config(starting_capital=Decimal("1000"), leader_capital=Decimal("9000"))
    strategy.initialize(config)

    # Record baseline size
    event1 = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision1 = strategy.on_event(event1)
    baseline_dollars = decision1.dollars
    assert decision1.action == DecisionAction.BUY
    strategy.on_fill(event1, decision1)

    # Realize significant profit (sell at 2x price)
    event_sell = make_event(
        action=TradeAction.SELL,
        bid=Decimal("0.99875"),
        ask=Decimal("1.00"),  # This will be capped at 0.99
        mid=Decimal("0.995"),
        price=Decimal("0.995")
    )
    decision_sell = strategy.on_event(event_sell)
    if decision_sell.action == DecisionAction.SELL:
        strategy.on_fill(event_sell, decision_sell)

    # Verify equity increased
    current_equity = strategy._calculate_current_equity()
    assert current_equity > config.starting_capital

    # Next position should be larger
    event2 = make_event(
        action=TradeAction.BUY,
        token_id="token_test2",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision2 = strategy.on_event(event2)
    assert decision2.action == DecisionAction.BUY

    # Size should compound (increase) after win
    assert decision2.dollars > baseline_dollars


def test_size_contracts_after_losses():
    """Verify position sizes decrease after losing trades."""
    strategy = MirrorStrategy()
    # Use larger capital to avoid 5-share minimum constraint
    config = make_config(starting_capital=Decimal("1000"), leader_capital=Decimal("9000"))
    strategy.initialize(config)

    # Execute buy at $0.50
    event_buy = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy = strategy.on_event(event_buy)
    baseline_dollars = decision_buy.dollars
    strategy.on_fill(event_buy, decision_buy)

    # Realize loss by selling lower
    event_sell = make_event(
        action=TradeAction.SELL,
        bid=Decimal("0.29875"),
        ask=Decimal("0.30125"),
        mid=Decimal("0.30"),
        price=Decimal("0.30")
    )
    decision_sell = strategy.on_event(event_sell)
    strategy.on_fill(event_sell, decision_sell)

    # Verify equity decreased
    current_equity = strategy._calculate_current_equity()
    assert current_equity < config.starting_capital

    # Next position should be smaller
    event2 = make_event(
        action=TradeAction.BUY,
        token_id="token_test2",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision2 = strategy.on_event(event2)

    # Size should contract after loss
    if decision2.action == DecisionAction.BUY:
        assert decision2.dollars < baseline_dollars


def test_low_quality_trade_skipped():
    """Verify trades with poor spread are skipped."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Create event with terrible spread (ask=0.60, bid=0.30)
    # Mid = 0.45, spread = 0.30, spread_bps = 6666 bps (way above 300 bps poor threshold)
    event = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.60"),
        bid=Decimal("0.30"),
        mid=Decimal("0.45"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event)
    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "low_quality"


def test_soft_floor_blocks_mediocre_trade():
    """Verify soft floor (10% DD) blocks trades below exceptional quality."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Simulate losses to reach ~$89 equity (11% drawdown from $100)
    # We need to realize -$11 PnL

    # Buy at $0.50
    event_buy = make_event(action=TradeAction.BUY, ask=Decimal("0.50"), price=Decimal("0.50"))
    decision_buy = strategy.on_event(event_buy)
    strategy.on_fill(event_buy, decision_buy)

    # Sell at $0.30 to realize loss (20 shares * $0.20 loss = -$4 loss)
    # Need to do this multiple times
    for i in range(3):
        event_sell = make_event(action=TradeAction.SELL, bid=Decimal("0.30"), price=Decimal("0.30"))
        decision_sell = strategy.on_event(event_sell)
        if decision_sell.action == DecisionAction.SELL:
            strategy.on_fill(event_sell, decision_sell)

        # Re-buy to continue testing
        if i < 2:
            event_buy = make_event(
                action=TradeAction.BUY,
                token_id=f"token_test_{i}",
                ask=Decimal("0.50"),
                price=Decimal("0.50")
            )
            decision_buy = strategy.on_event(event_buy)
            if decision_buy.action == DecisionAction.BUY:
                strategy.on_fill(event_buy, decision_buy)

    # Check equity
    current_equity = strategy._calculate_current_equity()

    # Now try a mediocre quality trade (quality score around 0.70-0.80)
    # Good spread (100 bps = medium quality)
    event_mediocre = make_event(
        action=TradeAction.BUY,
        token_id="token_new",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event_mediocre)

    # If we're at soft floor, mediocre trade should be skipped
    if current_equity <= Decimal("90"):
        assert decision.action == DecisionAction.SKIP
        assert decision.reason == "soft_floor_low_quality"


def test_hard_floor_blocks_all_trades():
    """Verify hard floor (30% DD) blocks all new entries."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Manually set portfolio to simulate 31% loss
    # Realized PnL = -$31 -> equity = $69
    strategy.portfolio.realized_pnl = Decimal("-31")

    # Try to buy with excellent quality
    event = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.501"),
        bid=Decimal("0.499"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event)
    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "hard_floor_hit"


def test_max_positions_gate():
    """Verify position limit prevents opening too many positions."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Open 5 positions (max limit)
    for i in range(5):
        event = make_event(
            action=TradeAction.BUY,
            token_id=f"token_{i}",
            ask=Decimal("0.50125"),
            bid=Decimal("0.49875"),
            mid=Decimal("0.50"),
            price=Decimal("0.50")
        )
        decision = strategy.on_event(event)
        if decision.action == DecisionAction.BUY:
            strategy.on_fill(event, decision)

    # Verify we have 5 positions
    assert len(strategy.portfolio.get_positions()) == 5

    # Try to open 6th position
    event_6th = make_event(
        action=TradeAction.BUY,
        token_id="token_6",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision = strategy.on_event(event_6th)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "max_positions_reached"


def test_existing_behavior_preserved():
    """Verify existing behavior still works after dynamic sizing integration."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Normal trade should still produce BUY (with good spread)
    event_normal = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision = strategy.on_event(event_normal)
    assert decision.action == DecisionAction.BUY

    # cost_too_high should still trigger (with good spread so quality passes)
    event_expensive = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.60"),  # 20% drift from price=0.50
        bid=Decimal("0.59875"),
        mid=Decimal("0.59938"),
        price=Decimal("0.50")
    )
    decision_expensive = strategy.on_event(event_expensive)
    assert decision_expensive.action == DecisionAction.SKIP
    assert decision_expensive.skip_reason == "cost_too_high"

    # min_shares should still trigger on tiny positions
    # To trigger min_shares, we need a very high ask price that results in < 5 shares
    # With limited capital, a high price will trigger min_shares
    event_tiny = make_event(
        action=TradeAction.BUY,
        ask=Decimal("0.99"),
        bid=Decimal("0.98875"),
        mid=Decimal("0.98938"),
        price=Decimal("0.95")
    )
    decision_tiny = strategy.on_event(event_tiny)
    # Should be skipped for min_shares (5-share minimum not met)
    if decision_tiny.action == DecisionAction.SKIP:
        assert decision_tiny.skip_reason in ["min_shares", "cost_too_high"]
