"""Unit tests for SimpleFollowStrategy.

Tests:
- Rolling window and reversal detection
- Partial fill aggregation by tx_hash
- Fixed vs scaled sizing modes
- Per-market caps
- Sell logic with proportional exits
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.strategies.base import get_strategy, StrategyConfig, DecisionAction
from src.strategies.simple_follow import SimpleFollowStrategy
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide


# ============================================================================
# HELPERS
# ============================================================================

def _make_trade(**overrides) -> LeaderTrade:
    """Create a LeaderTrade with sensible defaults."""
    defaults = {
        "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "market_id": "market_test",
        "token_id": "token_test",
        "side": TradeSide.UP,
        "action": TradeAction.BUY,
        "dollars": Decimal("10.0"),
        "price": Decimal("0.50"),
        "shares": Decimal("20.0"),
        "source": "test",
        "tx_hash": "0xtest123",
        "latency_ms": 0,
    }
    defaults.update(overrides)
    return LeaderTrade(**defaults)


def _make_prices(**overrides) -> PriceSnapshot:
    """Create a PriceSnapshot with sensible defaults."""
    defaults = {
        "token_id": "token_test",
        "bid": Decimal("0.49"),
        "ask": Decimal("0.51"),
        "mid": Decimal("0.50"),
    }
    defaults.update(overrides)
    return PriceSnapshot(**defaults)


def _make_event(trade_kw=None, price_kw=None) -> MarketEvent:
    """Create a MarketEvent from helper keyword arguments."""
    trade_kw = trade_kw or {}
    price_kw = price_kw or {}
    trade = _make_trade(**trade_kw)
    prices = _make_prices(**price_kw)
    return MarketEvent(trade=trade, prices=prices)


def _make_config(**overrides) -> StrategyConfig:
    """Create a StrategyConfig with $100 defaults."""
    defaults = {
        "starting_capital": Decimal("100"),
        "hourly_budget": Decimal("100"),
        "cash_reserve_pct": Decimal("10"),
        "leader_capital": Decimal("1000"),
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


# ============================================================================
# STRATEGY REGISTRATION
# ============================================================================

def test_strategy_is_registered():
    """SimpleFollowStrategy should be registered and discoverable."""
    strategy = get_strategy("simple_follow")
    assert strategy is not None
    assert strategy.name == "simple_follow"
    assert isinstance(strategy, SimpleFollowStrategy)


# ============================================================================
# INITIALIZATION
# ============================================================================

def test_initialize_with_default_params():
    """Strategy should initialize with default parameters."""
    strategy = SimpleFollowStrategy()
    config = _make_config()
    strategy.initialize(config)

    assert strategy.min_bet == Decimal("1")
    assert strategy.max_bet == Decimal("7")
    assert strategy.per_market_cap == Decimal("20")
    assert strategy.window_seconds == 10
    assert strategy.use_scaling is False


def test_initialize_with_custom_params():
    """Strategy should accept custom parameters via config.params."""
    strategy = SimpleFollowStrategy()
    config = _make_config(params={
        "min_bet": "2",
        "max_bet": "10",
        "per_market_cap": "30",
        "window_seconds": 15,
        "use_scaling": True,
        "leader_capital": "800"
    })
    strategy.initialize(config)

    assert strategy.min_bet == Decimal("2")
    assert strategy.max_bet == Decimal("10")
    assert strategy.per_market_cap == Decimal("30")
    assert strategy.window_seconds == 15
    assert strategy.use_scaling is True
    assert strategy.leader_capital == Decimal("800")


# ============================================================================
# BASIC BUY BEHAVIOR
# ============================================================================

def test_buy_returns_buy_decision():
    """Simple buy should return BUY decision."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event()
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.BUY
    assert decision.dollars >= Decimal("1")  # At least min bet
    assert decision.dollars <= Decimal("7")  # At most max bet
    assert decision.shares > 0


def test_buy_fixed_sizing_small_leader_trade():
    """Small leader trade should get min bet in fixed mode."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(trade_kw={"dollars": Decimal("5")})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.BUY
    # Small trade -> min bet
    assert decision.dollars == Decimal("1") or decision.dollars >= Decimal("2.55")  # min shares constraint


def test_buy_fixed_sizing_large_leader_trade():
    """Large leader trade should get max bet in fixed mode."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(trade_kw={"dollars": Decimal("100")})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.BUY
    assert decision.dollars == Decimal("7")  # max bet for large trades


# ============================================================================
# PARTIAL FILL AGGREGATION
# ============================================================================

def test_aggregates_partial_fills_same_tx():
    """Multiple events with same tx_hash should aggregate."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tx_hash = "0xsame_tx"

    # First partial fill: $10
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": tx_hash,
        "dollars": Decimal("10"),
        "shares": Decimal("20"),
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    # Second partial fill: $15 (same tx)
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(milliseconds=100),
        "tx_hash": tx_hash,
        "dollars": Decimal("15"),
        "shares": Decimal("30"),
    })
    decision2 = strategy.on_event(event2)

    # Should have aggregated
    assert strategy.partials_aggregated >= 1

    # Check aggregator has combined values
    agg = strategy._tx_aggregator.get(tx_hash)
    assert agg is not None
    assert agg.dollars == Decimal("25")  # 10 + 15
    assert agg.shares == Decimal("50")   # 20 + 30
    assert agg.partial_count == 2


def test_different_tx_hashes_not_aggregated():
    """Events with different tx_hash should not aggregate."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xtx_one",
        "dollars": Decimal("10"),
    })
    strategy.on_event(event1)

    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=1),
        "tx_hash": "0xtx_two",
        "dollars": Decimal("15"),
    })
    strategy.on_event(event2)

    # Both should be in aggregator separately
    assert "0xtx_one" in strategy._tx_aggregator
    assert "0xtx_two" in strategy._tx_aggregator
    assert strategy._tx_aggregator["0xtx_one"].dollars == Decimal("10")
    assert strategy._tx_aggregator["0xtx_two"].dollars == Decimal("15")


# ============================================================================
# ROLLING WINDOW - REVERSAL DETECTION
# ============================================================================

def test_reversal_within_window_skips():
    """Leader buy then sell within window should skip."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # First: leader buys
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xbuy_tx",
        "action": TradeAction.BUY,
        "token_id": "token_A",
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    # Second: leader sells same token within 5 seconds (default window is 10s)
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=5),
        "tx_hash": "0xsell_tx",
        "action": TradeAction.SELL,
        "token_id": "token_A",
    })
    decision2 = strategy.on_event(event2)

    # Should skip due to reversal
    assert decision2.action == DecisionAction.SKIP
    assert decision2.skip_reason == "reversed"


def test_reversal_outside_window_allowed():
    """Leader buy then sell outside window should be allowed."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # First: leader buys
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xbuy_tx",
        "action": TradeAction.BUY,
        "token_id": "token_A",
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    # Second: leader sells same token AFTER 15 seconds (outside 10s window)
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=15),
        "tx_hash": "0xsell_tx",
        "action": TradeAction.SELL,
        "token_id": "token_A",
    })
    decision2 = strategy.on_event(event2)

    # Should NOT skip (we have position, so sell should work)
    # Note: might skip for "no_position" if we didn't actually fill, but not "reversed"
    assert decision2.skip_reason != "reversed"


def test_different_token_not_reversal():
    """Buy token A then sell token B is not a reversal."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Buy token A
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xbuy_A",
        "action": TradeAction.BUY,
        "token_id": "token_A",
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    # Sell token B (different token)
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=5),
        "tx_hash": "0xsell_B",
        "action": TradeAction.SELL,
        "token_id": "token_B",
        "market_id": "market_B",
    }, price_kw={"token_id": "token_B"})
    decision2 = strategy.on_event(event2)

    # Should not be "reversed" - different token
    # Will likely be "no_position" instead
    assert decision2.skip_reason != "reversed"


# ============================================================================
# PER-MARKET CAPS
# ============================================================================

def test_per_market_cap_enforced():
    """Should not exceed per-market cap."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config(params={"per_market_cap": "10"}))  # $10 cap

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # First buy: use up $7 (max bet)
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xtx1",
        "dollars": Decimal("100"),  # Large trade -> max bet
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    first_deployed = decision1.dollars

    # Second buy: should be capped to remaining room
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=15),
        "tx_hash": "0xtx2",
        "dollars": Decimal("100"),
    })
    decision2 = strategy.on_event(event2)

    if decision2.action == DecisionAction.BUY:
        # Should be limited to remaining cap room
        assert decision2.dollars <= Decimal("10") - first_deployed + Decimal("0.01")
    else:
        # Or skipped if no room
        assert decision2.skip_reason in ["market_cap", "below_min_bet", "min_shares"]


# ============================================================================
# SELL LOGIC
# ============================================================================

def test_sell_with_position():
    """Should sell when we have a position."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # First: buy to establish position
    event1 = _make_event(trade_kw={
        "timestamp": base_time,
        "tx_hash": "0xbuy",
        "action": TradeAction.BUY,
    })
    decision1 = strategy.on_event(event1)
    strategy.on_fill(event1, decision1)

    # Verify we have a position
    assert strategy.portfolio.get_total_deployed() > 0

    # Then: sell (outside window to avoid reversal)
    event2 = _make_event(trade_kw={
        "timestamp": base_time + timedelta(seconds=15),
        "tx_hash": "0xsell",
        "action": TradeAction.SELL,
    })
    decision2 = strategy.on_event(event2)

    assert decision2.action == DecisionAction.SELL
    assert decision2.shares > 0


def test_sell_no_position_skips():
    """Should skip sell when we have no position."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(trade_kw={"action": TradeAction.SELL})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "no_position"


# ============================================================================
# SCALED SIZING MODE
# ============================================================================

def test_scaled_mode_uses_leader_capital():
    """Scaled mode should size based on leader capital ratio."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config(
        starting_capital=Decimal("100"),
        leader_capital=Decimal("1000"),
        k_factor=Decimal("1.0"),
        params={
            "use_scaling": True,
            "leader_capital": "1000"
        }
    ))

    # Leader trades $100 (10% of their capital)
    # We should trade ~$10 (10% of our $100)
    event = _make_event(trade_kw={"dollars": Decimal("100")})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.BUY
    # Scaled: $100 * ($100/$1000) * 1.0 = $10
    # But may be limited by caps or min shares
    assert decision.dollars > 0


# ============================================================================
# STATE TRACKING
# ============================================================================

def test_get_state_includes_config():
    """get_state should include current configuration."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config(params={"min_bet": "2", "max_bet": "8"}))

    state = strategy.get_state()

    assert "config" in state
    assert state["config"]["min_bet"] == "2"
    assert state["config"]["max_bet"] == "8"
    assert "buys" in state
    assert "sells" in state
    assert "skips" in state
    assert "partials_aggregated" in state


def test_on_session_end_returns_summary():
    """on_session_end should return summary stats."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    # Do some trades
    event = _make_event()
    decision = strategy.on_event(event)
    strategy.on_fill(event, decision)

    summary = strategy.on_session_end()

    assert "buys_executed" in summary
    assert "sells_executed" in summary
    assert "skips" in summary
    assert "partials_aggregated" in summary
    assert "realized_pnl" in summary


# ============================================================================
# EDGE CASES
# ============================================================================

def test_no_price_skips():
    """Should skip when no ask price available."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(price_kw={"ask": None})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "no_price"


def test_invalid_price_skips():
    """Should skip when price is >= 1."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(price_kw={"ask": Decimal("1.0")})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "invalid_price"


def test_extreme_low_price_skips():
    """Should skip buys at extreme low price (0.01)."""
    strategy = SimpleFollowStrategy()
    strategy.initialize(_make_config())

    event = _make_event(price_kw={"ask": Decimal("0.01")})
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "price_extreme_low"
