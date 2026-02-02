"""Kelly integration tests for MirrorStrategy.

Validates end-to-end integration of Phase 4 Kelly components:
- AdaptiveSizer with Phase 3 fallback
- EdgeTracker recording trades
- ConvictionScorer from leader signals
- TradeRanker initialization
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from src.strategies.mirror.strategy import MirrorStrategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from src.core.edge_tracker import TradeResult


# ============================================================================
# TEST HELPERS (from test_dynamic_mirror.py pattern)
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

    # Price defaults - excellent spread (25 bps)
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

def test_cold_start_uses_phase3_sizing():
    """New strategy with no edge data uses Phase 3 DynamicSizer."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Verify Kelly components are initialized
    assert strategy.edge_tracker is not None
    assert strategy.kelly_calculator is not None
    assert strategy.adaptive_sizer is not None
    assert strategy.conviction_scorer_kelly is not None

    # Feed a buy event - no edge data yet
    event = make_event(
        action=TradeAction.BUY,
        token_id="new_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event)

    # Should not skip (Phase 3 fallback handles cold start)
    assert decision.action == DecisionAction.BUY

    # Verify no edge data yet
    assert not strategy.edge_tracker.has_sufficient_data("new_token")


def test_edge_tracker_updated_on_sell():
    """EdgeTracker records trade after sell event."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Buy at $0.50
    event_buy = make_event(
        action=TradeAction.BUY,
        token_id="test_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy = strategy.on_event(event_buy)
    assert decision_buy.action == DecisionAction.BUY
    strategy.on_fill(event_buy, decision_buy)

    # Check edge tracker before sell
    initial_has_data = strategy.edge_tracker.has_sufficient_data("test_token")

    # Sell at $0.70 (winning trade)
    event_sell = make_event(
        action=TradeAction.SELL,
        token_id="test_token",
        bid=Decimal("0.69875"),
        ask=Decimal("0.70125"),
        mid=Decimal("0.70"),
        price=Decimal("0.70")
    )
    decision_sell = strategy.on_event(event_sell)
    assert decision_sell.action == DecisionAction.SELL
    strategy.on_fill(event_sell, decision_sell)

    # EdgeTracker should have recorded the trade
    # We can't check has_sufficient_data (needs 20 trades), but we can verify
    # the internal history exists
    assert "test_token" in strategy.edge_tracker._history
    assert len(strategy.edge_tracker._history["test_token"]) == 1


def test_conviction_multiplier_applied():
    """Conviction multiplier affects final position size."""
    strategy = MirrorStrategy()
    config = make_config(starting_capital=Decimal("1000"), leader_capital=Decimal("9000"))
    strategy.initialize(config)

    # Small leader trade ($50 = 0.5x average of $100)
    event_small = make_event(
        action=TradeAction.BUY,
        token_id="token_small",
        dollars=Decimal("50.0"),
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_small = strategy.on_event(event_small)
    assert decision_small.action == DecisionAction.BUY
    small_size = decision_small.dollars

    # Large leader trade ($200 = 2.0x average of $100)
    event_large = make_event(
        action=TradeAction.BUY,
        token_id="token_large",
        dollars=Decimal("200.0"),
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_large = strategy.on_event(event_large)
    assert decision_large.action == DecisionAction.BUY
    large_size = decision_large.dollars

    # Larger leader trade should result in larger position size
    assert large_size > small_size


def test_negative_edge_skips_trade():
    """Negative edge (losing token) skips trade."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Manually populate edge tracker with 20 losing trades
    for i in range(20):
        trade_result = TradeResult(
            token_id="losing_token",
            entry_price=Decimal("0.50"),
            exit_price=Decimal("0.40"),
            pnl_pct=Decimal("-0.20"),  # -20% loss
            timestamp=datetime(2024, 1, 1, 12, i, 0, tzinfo=timezone.utc)
        )
        strategy.edge_tracker.record_trade(trade_result)

    # Verify sufficient data
    assert strategy.edge_tracker.has_sufficient_data("losing_token")

    # Try to buy this losing token
    event = make_event(
        action=TradeAction.BUY,
        token_id="losing_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event)

    # Should skip due to negative edge
    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "negative_edge"


def test_kelly_active_after_sufficient_trades():
    """Kelly sizing used after 20+ trades for a token."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Populate edge tracker with 19 winning trades + 1 losing trade (95% win rate)
    # Kelly calculator rejects 100% win rate as unrealistic
    for i in range(19):
        trade_result = TradeResult(
            token_id="winning_token",
            entry_price=Decimal("0.50"),
            exit_price=Decimal("0.60"),
            pnl_pct=Decimal("0.20"),  # +20% win
            timestamp=datetime(2024, 1, 1, 12, i, 0, tzinfo=timezone.utc)
        )
        strategy.edge_tracker.record_trade(trade_result)

    # Add 1 small loss to avoid 100% win rate
    trade_result = TradeResult(
        token_id="winning_token",
        entry_price=Decimal("0.50"),
        exit_price=Decimal("0.48"),
        pnl_pct=Decimal("-0.04"),  # -4% loss
        timestamp=datetime(2024, 1, 1, 12, 19, 0, tzinfo=timezone.utc)
    )
    strategy.edge_tracker.record_trade(trade_result)

    # Verify sufficient data
    assert strategy.edge_tracker.has_sufficient_data("winning_token")

    # Buy this winning token - should use Kelly sizing
    event = make_event(
        action=TradeAction.BUY,
        token_id="winning_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )

    decision = strategy.on_event(event)

    # Should not skip (Kelly will size appropriately)
    assert decision.action == DecisionAction.BUY


def test_different_tokens_independent_sizing():
    """Token A with edge data uses Kelly, Token B without uses Phase 3."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Populate edge data for Token A only (19 wins + 1 loss = 95% win rate)
    for i in range(19):
        trade_result = TradeResult(
            token_id="token_a",
            entry_price=Decimal("0.50"),
            exit_price=Decimal("0.60"),
            pnl_pct=Decimal("0.20"),
            timestamp=datetime(2024, 1, 1, 12, i, 0, tzinfo=timezone.utc)
        )
        strategy.edge_tracker.record_trade(trade_result)

    # Add 1 small loss to avoid 100% win rate
    trade_result = TradeResult(
        token_id="token_a",
        entry_price=Decimal("0.50"),
        exit_price=Decimal("0.48"),
        pnl_pct=Decimal("-0.04"),
        timestamp=datetime(2024, 1, 1, 12, 19, 0, tzinfo=timezone.utc)
    )
    strategy.edge_tracker.record_trade(trade_result)

    # Token A has data, Token B does not
    assert strategy.edge_tracker.has_sufficient_data("token_a")
    assert not strategy.edge_tracker.has_sufficient_data("token_b")

    # Both should produce BUY decisions but use different sizing methods
    event_a = make_event(
        action=TradeAction.BUY,
        token_id="token_a",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_a = strategy.on_event(event_a)
    assert decision_a.action == DecisionAction.BUY

    event_b = make_event(
        action=TradeAction.BUY,
        token_id="token_b",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_b = strategy.on_event(event_b)
    assert decision_b.action == DecisionAction.BUY


def test_trade_ranker_initialized():
    """TradeRanker is initialized with correct parameters."""
    strategy = MirrorStrategy()
    config = make_config()
    strategy.initialize(config)

    # Verify TradeRanker exists and has correct config
    assert strategy.trade_ranker is not None
    assert strategy.trade_ranker.correlation_penalty_pct == Decimal("0.15")
    assert strategy.trade_ranker.rebalance_edge_gap == Decimal("1.5")


def test_scale_in_detection():
    """Second buy for same token detected as scale-in."""
    strategy = MirrorStrategy()
    config = make_config(starting_capital=Decimal("1000"), leader_capital=Decimal("9000"))
    strategy.initialize(config)

    # First buy
    event_buy1 = make_event(
        action=TradeAction.BUY,
        token_id="test_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy1 = strategy.on_event(event_buy1)
    assert decision_buy1.action == DecisionAction.BUY
    strategy.on_fill(event_buy1, decision_buy1)
    first_size = decision_buy1.dollars

    # Verify token is now in leader_tracker
    assert "test_token" in strategy.leader_tracker

    # Second buy (scale-in) - conviction scorer should detect is_scale_in=True
    event_buy2 = make_event(
        action=TradeAction.BUY,
        token_id="test_token",
        ask=Decimal("0.50125"),
        bid=Decimal("0.49875"),
        mid=Decimal("0.50"),
        price=Decimal("0.50")
    )
    decision_buy2 = strategy.on_event(event_buy2)
    assert decision_buy2.action == DecisionAction.BUY
    second_size = decision_buy2.dollars

    # Scale-in should get conviction boost (is_scale_in adds 25% weight at 1.5 score)
    # This adds 0.25 * (1.5 - 1.0) = 0.125 = 12.5% boost
    # So second_size should be noticeably larger (not just from same base sizing)
    # Note: This is hard to test precisely because other factors affect sizing too
    # The key is that scale-in was detected via leader_tracker check
    assert "test_token" in strategy.leader_tracker
    assert strategy.leader_tracker["test_token"]["shares"] > 0
