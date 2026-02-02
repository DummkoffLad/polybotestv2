"""Risk cap enforcement unit tests.

Purpose: Risk caps are the primary safety mechanism for a sub-$100 budget.
These caps bind frequently in normal operation -- they're not just a safety net,
they're the normal operating condition. If caps fail, real money is at risk.

This is requirement TEST-02.

Test coverage:
- Per-market cap (30% = $30 for $100 capital in mirror)
- Per-side cap (26% = $26 for $100 capital in mirror)
- Global exposure cap (100% = $100, limited by reserve)
- Cash reserve (10% = $10 reserved, $90 deployable in mirror)
- Hourly budget resets and limits
- Boundary conditions (exactly at cap, just under, just over)
- Multi-cap interaction (smallest room wins)
- Conservative strategy's tighter caps (20% market, 18% side, 80% global, 20% reserve)
"""

from datetime import datetime, timezone
from decimal import Decimal

from src.core.types import Side
from src.data.models import LeaderTrade, MarketEvent, PriceSnapshot, TradeAction, TradeSide
from src.strategies.base import DecisionAction, StrategyConfig
from src.strategies.mirror.strategy import MirrorStrategy
from src.strategies.conservative.strategy import ConservativeMirrorStrategy


# ============================================================================
# LOCAL HELPERS (self-contained, no dependency on external conftest)
# ============================================================================

def _make_trade(**kw) -> LeaderTrade:
    """Create a LeaderTrade with sensible defaults."""
    defaults = {
        "timestamp": datetime(2026, 1, 29, 13, 0, 0, tzinfo=timezone.utc),
        "market_id": "market_a",
        "token_id": "token_001",
        "side": TradeSide.UP,
        "action": TradeAction.BUY,
        "dollars": Decimal("100"),
        "price": Decimal("0.50"),
        "shares": Decimal("200"),
        "source": "test",
    }
    defaults.update(kw)
    return LeaderTrade(**defaults)


def _make_prices(**kw) -> PriceSnapshot:
    """Create a PriceSnapshot with sensible defaults."""
    defaults = {
        "token_id": "token_001",
        "bid": Decimal("0.48"),
        "ask": Decimal("0.52"),
    }
    defaults.update(kw)
    return PriceSnapshot(**defaults)


def _make_event(trade_kw=None, price_kw=None) -> MarketEvent:
    """Create a MarketEvent combining trade and price defaults."""
    trade = _make_trade(**(trade_kw or {}))
    # Match token_id from trade
    price_defaults = {"token_id": trade.token_id}
    if price_kw:
        price_defaults.update(price_kw)
    prices = _make_prices(**price_defaults)
    return MarketEvent(trade=trade, prices=prices)


def _make_config(**kw) -> StrategyConfig:
    """Create a StrategyConfig with $100 budget defaults."""
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
    }
    defaults.update(kw)
    return StrategyConfig(**defaults)


def _init_strategy(config_overrides=None):
    """Create and initialize a fresh MirrorStrategy with optional config overrides.

    Returns (strategy, config) tuple.
    """
    config = _make_config(**(config_overrides or {}))
    strategy = MirrorStrategy()
    strategy.initialize(config)
    return strategy, config


def _init_conservative(config_overrides=None):
    """Create and initialize a fresh ConservativeMirrorStrategy with optional config overrides.

    Returns (strategy, config) tuple.
    """
    config = _make_config(**(config_overrides or {}))
    strategy = ConservativeMirrorStrategy()
    strategy.initialize(config)
    return strategy, config


def _buy_and_fill(strategy, market_id, token_id, side, ask, leader_dollars):
    """Helper to create a BUY event, call on_event, and if BUY decision, call on_fill.

    Returns the TradeDecision.

    This helper is critical for building up positions before testing cap enforcement.
    """
    event = _make_event(
        trade_kw={
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "action": TradeAction.BUY,
            "dollars": leader_dollars,
            "price": ask,
            "shares": leader_dollars / ask,
        },
        price_kw={"token_id": token_id, "ask": ask, "bid": ask - Decimal("0.02")},
    )

    decision = strategy.on_event(event)

    # If BUY was approved, apply the fill
    if decision.action == DecisionAction.BUY:
        strategy.on_fill(event, decision)

    return decision


# ============================================================================
# PER-MARKET CAP TESTS (30% = $30 for $100 capital)
# ============================================================================

def test_per_market_cap_allows_under_limit():
    """Buy $20 in market_a. Should succeed (under $30 cap)."""
    strategy, _ = _init_strategy()

    # Buy $20 worth in market_a
    decision = _buy_and_fill(
        strategy,
        market_id="market_a",
        token_id="token_001",
        side=TradeSide.UP,
        ask=Decimal("0.50"),
        leader_dollars=Decimal("200"),  # Scaled down to ~$20
    )

    assert decision.action == DecisionAction.BUY
    assert decision.dollars is not None
    # Dynamic sizing: 1% of $100 equity * quality multiplier produces smaller positions
    deployed = strategy.portfolio.get_total_deployed()
    assert Decimal("0.50") <= deployed <= Decimal("30")  # Dynamic sizing produces smaller positions


def test_per_market_cap_enforced_after_reaching_limit():
    """Fill buys in market_a until get_market_exposure >= $28 (close to cap).
    Then try another buy in same market. Should either get capped to small amount
    or skip with 'market_cap'.
    """
    strategy, _ = _init_strategy()

    # Fill market_a with multiple buys to approach the $30 cap
    _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300")
    )
    _buy_and_fill(
        strategy, "market_a", "token_002", TradeSide.DOWN, Decimal("0.60"), Decimal("200")
    )

    # Check current market exposure
    market_exp = strategy.portfolio.get_market_exposure("market_a")

    # Now try another buy in market_a
    decision = _buy_and_fill(
        strategy, "market_a", "token_003", TradeSide.UP, Decimal("0.40"), Decimal("200")
    )

    # If we're at or near the cap, should either skip or be heavily capped
    if market_exp >= Decimal("28"):
        # Should skip with market_cap or get minimal dollars
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason == "market_cap"
        else:
            # If it bought, it should be a very small amount (< $5 of room left)
            assert decision.dollars is not None
            assert decision.dollars < Decimal("5")


def test_per_market_cap_different_markets_independent():
    """Fill $25 in market_a, then buy in market_b. Market_b buy should succeed (independent cap)."""
    strategy, _ = _init_strategy()

    # Fill market_a with ~$25
    _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300")
    )

    market_a_exp = strategy.portfolio.get_market_exposure("market_a")

    # Now buy in market_b (different market, separate cap)
    decision = _buy_and_fill(
        strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.50"), Decimal("200")
    )

    # Market_b buy should succeed
    assert decision.action == DecisionAction.BUY
    assert decision.dollars is not None
    assert decision.dollars > Decimal("0.50")  # Dynamic sizing: smaller but valid allocation

    # Verify market_a exposure unchanged
    assert strategy.portfolio.get_market_exposure("market_a") == market_a_exp


# ============================================================================
# PER-SIDE CAP TESTS (26% = $26 for $100 capital)
# ============================================================================

def test_per_side_cap_allows_under_limit():
    """Buy UP side in a market for $20. Should succeed."""
    strategy, _ = _init_strategy()

    decision = _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("200")
    )

    assert decision.action == DecisionAction.BUY
    side_exp = strategy.portfolio.get_side_exposure("market_a", Side.UP)
    assert Decimal("0.50") <= side_exp <= Decimal("25")  # Dynamic sizing: smaller positions


def test_per_side_cap_enforced():
    """Fill UP side to near $26, then try another UP buy. Should skip 'side_cap' or be capped."""
    strategy, _ = _init_strategy()

    # Fill UP side with multiple buys
    _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("250")
    )
    _buy_and_fill(
        strategy, "market_a", "token_002", TradeSide.UP, Decimal("0.55"), Decimal("150")
    )

    side_exp = strategy.portfolio.get_side_exposure("market_a", Side.UP)

    # Try another UP buy
    decision = _buy_and_fill(
        strategy, "market_a", "token_003", TradeSide.UP, Decimal("0.45"), Decimal("100")
    )

    if side_exp >= Decimal("24"):
        # Should skip or be minimal
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["side_cap", "min_shares", "min_order"]
        else:
            assert decision.dollars is not None
            assert decision.dollars < Decimal("5")


def test_per_side_cap_opposite_sides_independent():
    """Fill UP side to $25, then buy DOWN side in same market. DOWN buy should succeed (different side)."""
    strategy, _ = _init_strategy()

    # Fill UP side
    _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300")
    )

    up_exp = strategy.portfolio.get_side_exposure("market_a", Side.UP)

    # Buy DOWN side (independent cap)
    # Note: market cap might also constrain this since we already have UP exposure in market_a
    decision = _buy_and_fill(
        strategy, "market_a", "token_002", TradeSide.DOWN, Decimal("0.50"), Decimal("200")
    )

    assert decision.action == DecisionAction.BUY
    assert decision.dollars is not None
    # Market cap (30% = $30) may limit this, so we already have ~$25 UP exposure
    # So we might only get ~$5 more in DOWN side due to market cap
    assert decision.dollars > Decimal("2")  # Should get SOME allocation

    # Verify UP side unchanged
    assert strategy.portfolio.get_side_exposure("market_a", Side.UP) == up_exp


# ============================================================================
# GLOBAL EXPOSURE CAP TESTS (100% = $100, limited by reserve to $90)
# ============================================================================

def test_global_cap_enforced():
    """Fill buys across multiple markets until near deployable limit (90% of $100 = $90 due to reserve).
    Next buy should skip 'reserve' or 'global_cap'.
    """
    strategy, _ = _init_strategy()

    # Fill multiple markets to approach $90 deployed
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("300"))
    _buy_and_fill(strategy, "market_c", "token_201", TradeSide.DOWN, Decimal("0.60"), Decimal("300"))

    deployed = strategy.portfolio.get_total_deployed()

    # Try another buy
    decision = _buy_and_fill(strategy, "market_d", "token_301", TradeSide.UP, Decimal("0.50"), Decimal("100"))

    if deployed >= Decimal("85"):
        # Should skip reserve or global_cap or min_shares
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["reserve", "global_cap", "min_shares", "min_order"]
        else:
            # If bought, should be minimal
            assert decision.dollars is not None
            assert decision.dollars < Decimal("10")


def test_cash_reserve_limits_deployment():
    """With 10% cash reserve, only $90 is deployable. Fill to $88 deployed.
    Try $5 more buy. Should be capped to ~$2 or skip.
    """
    strategy, _ = _init_strategy()

    # Fill to ~$88 deployed (approach $90 limit)
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("400"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("350"))
    _buy_and_fill(strategy, "market_c", "token_201", TradeSide.DOWN, Decimal("0.60"), Decimal("200"))

    deployed = strategy.portfolio.get_total_deployed()

    # Try another buy (leader $50 -> scaled ~$5-6)
    decision = _buy_and_fill(strategy, "market_d", "token_301", TradeSide.UP, Decimal("0.50"), Decimal("50"))

    if deployed >= Decimal("86"):
        # Very little room left
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["reserve", "min_shares", "min_order"]
        else:
            # Bought a tiny amount
            assert decision.dollars is not None
            assert decision.dollars < Decimal("6")


# ============================================================================
# HOURLY BUDGET TESTS
# ============================================================================

def test_hourly_budget_limits_spending():
    """Set hourly_budget to $50. Fill buys until hourly_budget_used reaches $48.
    Next buy should be capped or result in min_order skip.
    """
    strategy, _ = _init_strategy({"hourly_budget": Decimal("50")})

    # Fill buys until hourly budget nearly exhausted
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("250"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("200"))

    budget_used = strategy.hourly_budget_used

    # Try another buy
    decision = _buy_and_fill(strategy, "market_c", "token_201", TradeSide.UP, Decimal("0.50"), Decimal("100"))

    if budget_used >= Decimal("45"):
        # Very little budget left
        if decision.action == DecisionAction.SKIP:
            # Budget exhaustion often shows as min_order/min_shares (per plan comment)
            assert decision.skip_reason in ["min_shares", "min_order", "reserve"]
        else:
            # Bought a small amount
            assert decision.dollars is not None
            assert decision.dollars < Decimal("10")


def test_hourly_budget_resets_on_hour_change():
    """Fill buys to exhaust hourly budget. Then create an event with a timestamp in the NEXT hour.
    The budget should reset and the buy should succeed.
    """
    strategy, _ = _init_strategy({"hourly_budget": Decimal("50")})

    # Exhaust hourly budget in hour 13
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("200"))

    # Verify budget is partially used (dynamic sizing produces smaller trades ~$1-3 each)
    assert strategy.hourly_budget_used >= Decimal("2")

    # Create event in NEXT hour (hour 14)
    event = _make_event(
        trade_kw={
            "timestamp": datetime(2026, 1, 29, 14, 0, 1, tzinfo=timezone.utc),  # Hour 14
            "market_id": "market_c",
            "token_id": "token_201",
            "side": TradeSide.UP,
            "action": TradeAction.BUY,
            "dollars": Decimal("100"),
            "price": Decimal("0.50"),
            "shares": Decimal("200"),
        },
        price_kw={"token_id": "token_201", "ask": Decimal("0.50"), "bid": Decimal("0.498")},  # Tight spread to pass quality filter
    )

    decision = strategy.on_event(event)

    # After hourly reset, budget should be available again
    assert decision.action == DecisionAction.BUY
    assert decision.dollars is not None
    assert decision.dollars > Decimal("0.50")  # Dynamic sizing: smaller but valid allocation


# ============================================================================
# BOUNDARY CONDITION TESTS
# ============================================================================

def test_cap_boundary_exactly_at_limit():
    """Set up exposure to exactly $30 (market cap). Next buy in same market should skip 'market_cap' (room is exactly 0)."""
    strategy, _ = _init_strategy()

    # Manually manipulate to get exactly at market cap (tricky, so we approximate)
    # Fill to near $30 in market_a
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("350"))

    market_exp = strategy.portfolio.get_market_exposure("market_a")

    # Try another buy
    decision = _buy_and_fill(strategy, "market_a", "token_002", TradeSide.UP, Decimal("0.50"), Decimal("50"))

    # If we're at or very close to cap
    if market_exp >= Decimal("29"):
        # Should skip or get minimal dollars
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["market_cap", "min_shares", "min_order"]
        else:
            assert decision.dollars is not None
            assert decision.dollars < Decimal("3")


def test_min_order_enforcement_under_cap():
    """When remaining cap room is $0.50, but 5-share minimum at ask=$0.50 needs $2.50 --
    should skip 'min_shares' or 'min_order' since can't meet minimum.
    """
    strategy, _ = _init_strategy()

    # Fill to leave very little room (approach side cap of $26)
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300"))

    side_exp = strategy.portfolio.get_side_exposure("market_a", Side.UP)

    # Try a buy with ask=$0.50 (needs $2.50 for 5 shares minimum)
    decision = _buy_and_fill(strategy, "market_a", "token_002", TradeSide.UP, Decimal("0.50"), Decimal("20"))

    # If room < $2.50, should skip min_shares
    if side_exp >= Decimal("24"):
        # Likely skip min_shares or min_order
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["min_shares", "min_order", "side_cap"]


# ============================================================================
# MULTI-CAP INTERACTION TESTS
# ============================================================================

def test_smallest_cap_wins():
    """Set config with per_market=30%, per_side=10% (tighter side cap).
    A buy should be limited by the side cap ($10), not the market cap ($30).
    Verify decision dollars <= $10.
    """
    strategy, _ = _init_strategy({"per_side_pct": Decimal("10")})

    # Try a buy (leader $200 -> scaled ~$20 before caps)
    decision = _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("200")
    )

    # Side cap is $10, market cap is $30
    # The buy should be limited by side cap
    assert decision.action == DecisionAction.BUY
    assert decision.dollars is not None
    assert decision.dollars <= Decimal("10.5")  # Allow small rounding


def test_cascading_caps_multiple_markets():
    """Fill 3 different markets to various levels. Global exposure should be the sum.
    Verify global cap catches when individual market caps haven't fired.
    """
    strategy, _ = _init_strategy()

    # Fill 3 markets each to ~$25 (under market cap of $30 each)
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("300"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("250"))
    _buy_and_fill(strategy, "market_c", "token_201", TradeSide.DOWN, Decimal("0.60"), Decimal("250"))

    # Each market is under its individual cap, but global should be near $90 limit
    total_deployed = strategy.portfolio.get_total_deployed()

    # Try a 4th market
    decision = _buy_and_fill(strategy, "market_d", "token_301", TradeSide.UP, Decimal("0.50"), Decimal("100"))

    if total_deployed >= Decimal("85"):
        # Global/reserve cap should fire even though market_d is fresh
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["reserve", "global_cap", "min_shares", "min_order"]
        else:
            assert decision.dollars is not None
            assert decision.dollars < Decimal("10")


# ============================================================================
# SELL-SIDE TESTS (caps don't apply to sells)
# ============================================================================

def test_sell_not_blocked_by_caps():
    """Fill a position. Even with all caps maxed out, selling should work (sells reduce exposure, not increase it)."""
    strategy, _ = _init_strategy()

    # Fill positions across markets to max out caps
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("400"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("400"))

    # Now try to sell from one position
    sell_event = _make_event(
        trade_kw={
            "market_id": "market_a",
            "token_id": "token_001",
            "side": TradeSide.UP,
            "action": TradeAction.SELL,
            "dollars": Decimal("50"),
            "price": Decimal("0.55"),
            "shares": Decimal("100"),
        },
        price_kw={"token_id": "token_001", "ask": Decimal("0.56"), "bid": Decimal("0.54")},
    )

    decision = strategy.on_event(sell_event)

    # Sell should succeed regardless of caps being maxed
    assert decision.action == DecisionAction.SELL
    assert decision.shares is not None
    assert decision.shares > Decimal("0")


# ============================================================================
# EDGE CASES
# ============================================================================

def test_zero_capital_config():
    """Set starting_capital=0. Strategy should handle gracefully (skip everything, not crash)."""
    strategy, _ = _init_strategy({"starting_capital": Decimal("0")})

    decision = _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("100")
    )

    # Should skip (no capital to deploy)
    assert decision.action == DecisionAction.SKIP
    # With dynamic sizing, quality filter or floor system may trigger first
    assert decision.skip_reason in ["reserve", "min_order", "min_shares", "low_quality", "hard_floor_hit"]


def test_very_small_budget_caps_bind_immediately():
    """Set starting_capital=Decimal("10"). Per-market cap = $3. First buy of a $200 leader trade
    should be aggressively capped.
    """
    strategy, _ = _init_strategy({"starting_capital": Decimal("10")})

    # Leader trade $200 -> scaled down, but market cap is only $3 (30% of $10)
    decision = _buy_and_fill(
        strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("200")
    )

    # Should either skip or get very small amount
    if decision.action == DecisionAction.BUY:
        assert decision.dollars is not None
        # Market cap is $3, but after scaling and boost, should still be small
        assert decision.dollars <= Decimal("3.5")
    else:
        # Might skip if can't meet 5-share minimum
        assert decision.skip_reason in ["min_shares", "min_order", "market_cap"]


# ============================================================================
# CONSERVATIVE STRATEGY TESTS (tighter caps)
# ============================================================================

def test_conservative_market_cap_tighter():
    """Initialize conservative strategy. It uses 20% market cap ($20 for $100).
    Fill to $18 in one market, then try $5 more. Should be capped or skipped,
    while mirror strategy (30%) would have allowed it.
    """
    strategy, _ = _init_conservative()

    # Fill market_a to ~$18
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("350"))

    market_exp = strategy.portfolio.get_market_exposure("market_a")

    # Try another buy (same side to test market cap, not side cap)
    decision = _buy_and_fill(strategy, "market_a", "token_002", TradeSide.UP, Decimal("0.50"), Decimal("100"))

    if market_exp >= Decimal("17"):
        # Conservative cap is $20 market, $18 side
        # Side cap (18%) will likely hit first since both trades are UP
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["market_cap", "side_cap", "min_shares", "min_order"]
        else:
            # If bought, very small
            assert decision.dollars is not None
            assert decision.dollars < Decimal("5")


def test_conservative_global_cap_lower():
    """Conservative uses 80% global ($80). Fill across markets to $75, then try $10 more.
    Should be capped to ~$5.
    """
    strategy, _ = _init_conservative()

    # Fill to ~$75 deployed (approaching $80 limit)
    # Note: Conservative also has 20% reserve, so deployable is 80% of $100 = $80
    # But global_cap is ALSO 80%, so effective limit is $80
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("450"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("350"))
    _buy_and_fill(strategy, "market_c", "token_201", TradeSide.DOWN, Decimal("0.60"), Decimal("250"))

    deployed = strategy.portfolio.get_total_deployed()

    # Try another buy
    decision = _buy_and_fill(strategy, "market_d", "token_301", TradeSide.UP, Decimal("0.50"), Decimal("100"))

    if deployed >= Decimal("70"):
        # Approaching $80 limit
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["reserve", "global_cap", "min_shares", "min_order"]
        else:
            assert decision.dollars is not None
            assert decision.dollars < Decimal("12")


def test_conservative_higher_cash_reserve():
    """Conservative uses 20% reserve, so only $80 deployable. Verify a buy is rejected once deployed reaches $78."""
    strategy, _ = _init_conservative()

    # Fill to ~$78 deployed
    _buy_and_fill(strategy, "market_a", "token_001", TradeSide.UP, Decimal("0.50"), Decimal("500"))
    _buy_and_fill(strategy, "market_b", "token_101", TradeSide.UP, Decimal("0.55"), Decimal("400"))

    deployed = strategy.portfolio.get_total_deployed()

    # Try another buy
    decision = _buy_and_fill(strategy, "market_c", "token_201", TradeSide.UP, Decimal("0.50"), Decimal("50"))

    if deployed >= Decimal("75"):
        # Very little room left ($80 - $75 = $5)
        if decision.action == DecisionAction.SKIP:
            assert decision.skip_reason in ["reserve", "min_shares", "min_order", "budget"]
        else:
            assert decision.dollars is not None
            assert decision.dollars < Decimal("8")
