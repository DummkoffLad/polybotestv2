"""Profit Taker Strategy - SELECTIVE following of leader's most profitable trades.

Key insight from 48-session analysis (20,817 trades):
- MID-HIGH prices (0.60-0.80) + MEDIUM trades ($10-30) = +156% ROI (BEST!)
- LOW prices (<0.20) + small = +89% ROI (good but smaller profit pool)
- LARGE trades ($30+) at mid prices = -62% ROI (AVOID!)
- HIGH prices (0.80+) with any size = still profitable (+14-20% ROI)

The winning pattern is COUNTER-INTUITIVE:
- MID-HIGH prices look "expensive" but have the most predictable outcomes
- Medium trades ($10-30) signal conviction without overcommitting
- Large trades often indicate market making or averaging down (LOSERS)

Key logic:
1. TARGET: MID-HIGH (0.60-0.80) + Medium ($10-30) = +156% ROI zone
2. ALSO GOOD: Any price + Medium trade (always >+14% ROI)
3. AVOID: Large trades ($30+) at mid prices (negative ROI)
4. EXIT: Follow leader (they know when to get out)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from ...core.portfolio import Portfolio
from ...core.types import Side
from ..base import (
    Strategy, StrategyConfig, TradeDecision, DecisionAction, OrderType,
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW,
    calculate_actual_spread_pct
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide
from ...analysis.pnl_calculator import calculate_strategy_pnl
from ..mixins import SkipHelperMixin, HourlyBudgetMixin
from ..utils import to_side

logger = logging.getLogger(__name__)

# =============================================================================
# AGGRESSIVE FOLLOWING - Maximize trade size on trades we CAN follow
# =============================================================================
# PROBLEM: 77% of leader trades are too small to follow ($1 min, 9.4% scale)
# SOLUTION: Be MORE aggressive on the trades we CAN follow
#
# Data insight: Leader makes $348/hour, we need to capture more of it
# At 9.4% scale: $11+ leader trade = $1.03+ for us (just above minimum)
#
# Strategy: Don't filter by price/size - BOOST allocation to compensate
# for all the small trades we must skip

# Base scale multiplier (grid-search optimized: 8x across 78 hourly trials)
SCALE_BOOST = Decimal("8")

# Price filter - only skip extremes
SKIP_PRICE_HIGH = Decimal("0.97")  # Only skip very close to resolution
SKIP_PRICE_LOW = Decimal("0.03")   # Only skip near-zero

# =============================================================================
# DRAWDOWN CIRCUIT BREAKER - Reduce risk when hour is going badly
# =============================================================================
# Checks UNREALIZED + realized losses before each new buy.
# Key insight from loss analysis: worst hours have leader buying 2-4 positions
# that ALL resolve at $0.01 with no mid-hour exits. Without this, we deploy
# $40+ and lose almost everything. The breaker catches the drawdown mid-hour
# from underwater positions and stops us from piling on.
DRAWDOWN_REDUCE_THRESHOLD = Decimal("15")  # After $15 drawdown → halve new buy size
DRAWDOWN_STOP_THRESHOLD = Decimal("25")    # After $25 drawdown → stop buying entirely
# Tradeoff: costs ~$1/hour in PnL but prevents catastrophic sessions.
# Without: worst session = -$66. With: worst session ≈ -$42.

# Late-hour caution: DISABLED (grid-search tested, hurts more than helps)
# Late trades are actually profitable — leader has conviction near resolution
LATE_HOUR_REDUCE_MIN = 59  # Effectively disabled
LATE_HOUR_STOP_MIN = 60    # Effectively disabled

# =============================================================================
# PROFIT TARGETS - DISABLED (Leader knows best when to exit)
# =============================================================================
# Testing shows early exits DESTROY value - trust leader timing
PROFIT_TARGET_LOW = Decimal("100")   # Disabled
PROFIT_TARGET_MID = Decimal("100")   # Disabled
PROFIT_TARGET_HIGH = Decimal("50")   # Only if 50%+ profit
TRAILING_STOP_PCT = Decimal("100")   # Disabled

# =============================================================================
# RISK MANAGEMENT - MAXIMUM AGGRESSION
# =============================================================================
# We miss 77% of trades due to size constraints - compensate by going
# bigger on every trade we DO take
IGNORE_LEADER_MINISELLS_PCT = Decimal("10")
MIN_LEADER_TRADE_PCT = Decimal("1.2")  # Skip trades < 1.2% of leader capital ($10.80 with $900)
MAX_TOTAL_COST_PCT = Decimal("6")      # Looser - accept more slippage
CASH_RESERVE_PCT = Decimal("0")        # No reserve - deploy everything
PER_MARKET_CAP_PCT = Decimal("50")     # Cap per market (down from 60% to reduce concentration)
PER_SIDE_PCT = Decimal("55")           # Big positions per side
GLOBAL_EXPOSURE_PCT = Decimal("100")   # Use ALL capital
MIN_OUR_TRADE = Decimal("1")           # Keep $1 minimum
POOL_CAPITAL_MULTIPLIER = Decimal("2") # Pool = 2x starting capital (e.g. $100 pool for $50 deploy)

# Realistic slippage: tiered based on order size
# Small orders (<$15) likely get best price, larger orders eat into book
SLIPPAGE_THRESHOLD = Decimal("15")     # Orders below this get no slippage
SLIPPAGE_PER_SHARE = Decimal("0.01")   # $0.01 worse per share on larger orders


@register_strategy
class ProfitTakerStrategy(SkipHelperMixin, HourlyBudgetMixin, Strategy):
    """Copy buys, exit on profit target or leader exit."""

    def __init__(self):
        SkipHelperMixin.__init__(self)
        HourlyBudgetMixin.__init__(self)
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_positions: Dict[str, Dict] = {}  # Track leader's positions
        self.our_entries: Dict[str, Decimal] = {}  # Our entry prices by token_id
        self.high_water_marks: Dict[str, Decimal] = {}  # Track highest bid seen for trailing stop
        self.scale_ratio = Decimal("0")
        self.buys = self.sells = 0
        self.profit_exits = 0  # Track how many exits were profit-based
        self.trailing_stops = 0  # Track trailing stop exits
        self.conviction_buys = 0  # Track buys on large leader trades
        self.cash = Decimal("0")  # Track actual cash balance (set in initialize)
        self.scale_boost = Decimal("8")  # Default, set properly in initialize()

    @property
    def name(self) -> str:
        return "profit_taker"

    def initialize(self, config: StrategyConfig) -> None:
        SkipHelperMixin.__init__(self)
        HourlyBudgetMixin.__init__(self)
        self._current_hour = datetime.now(timezone.utc).hour
        self.config = config
        self.portfolio = Portfolio()
        self.leader_positions = {}
        self.our_entries = {}
        self.high_water_marks = {}
        self.buys = self.sells = 0
        self.profit_exits = 0
        self.trailing_stops = 0
        self.conviction_buys = 0
        self.cash = config.starting_capital * POOL_CAPITAL_MULTIPLIER  # Pool: 2x deploy cap
        self._last_all_prices: Dict[str, PriceSnapshot] = {}  # End-of-hour prices for resolution
        self.scale_boost = SCALE_BOOST
        self._hourly_realized_loss = Decimal("0")  # Track realized sell losses per hour
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                           if config.leader_capital > 0 else Decimal("0.1"))

    def _check_profit_target(self, token_id: str, current_bid: Decimal) -> bool:
        """Check if position has hit dynamic profit target based on entry price."""
        if token_id not in self.our_entries:
            return False
        entry_price = self.our_entries[token_id]
        if entry_price <= 0:
            return False

        # Dynamic target: lower prices have more room to run
        if entry_price < Decimal("0.30"):
            target = PROFIT_TARGET_LOW
        elif entry_price < Decimal("0.60"):
            target = PROFIT_TARGET_MID
        else:
            target = PROFIT_TARGET_HIGH

        profit_pct = ((current_bid - entry_price) / entry_price) * 100
        return profit_pct >= target

    def _is_leader_minisell(self, event: MarketEvent) -> bool:
        """Check if this is a small leader sell (likely rebalancing, not exit signal)."""
        trade = event.trade
        if trade.token_id not in self.leader_positions:
            return False
        leader_pos = self.leader_positions[trade.token_id]
        if leader_pos.get("shares", 0) <= 0:
            return False
        sell_pct = (trade.shares / leader_pos["shares"]) * 100
        return sell_pct < IGNORE_LEADER_MINISELLS_PCT

    def _get_hourly_drawdown(self, all_prices: Dict[str, PriceSnapshot]) -> Decimal:
        """Calculate total drawdown this hour: realized losses + unrealized losses.

        This powers the circuit breaker. It catches BOTH:
        - Hours where we sell mid-hour at a loss (realized)
        - Hours where open positions are underwater (unrealized, the common case)
        """
        drawdown = self._hourly_realized_loss
        for token_id, pos in self.portfolio.get_positions().items():
            if pos.shares <= 0:
                continue
            price_snap = all_prices.get(token_id)
            if price_snap and price_snap.bid and price_snap.bid > 0:
                current_value = pos.shares * price_snap.bid
                unrealized_pnl = current_value - pos.cost_basis
                if unrealized_pnl < 0:
                    drawdown += abs(unrealized_pnl)
        return drawdown

    def _liquidate_hour_boundary(self, all_prices: Dict[str, PriceSnapshot]) -> None:
        """Sell all open positions at hour boundary (hourly markets resolve).

        Uses resolution prices: if last bid >= 0.50 our side won -> $0.99,
        otherwise our side lost -> $0.01.
        If no price data available, use entry price to guess outcome.
        """
        for token_id, pos in list(self.portfolio.get_positions().items()):
            if pos.shares <= 0:
                continue
            price_snap = all_prices.get(token_id)
            if price_snap and price_snap.bid and price_snap.bid > 0:
                last_bid = price_snap.bid
            else:
                # No price snapshot — use entry price as best guess
                last_bid = self.our_entries.get(token_id, Decimal("0.50"))
            # Resolution price: winning side -> $0.99, losing side -> $0.01
            if last_bid >= Decimal("0.50"):
                resolution_price = Decimal("0.99")
            else:
                resolution_price = Decimal("0.01")
            dollars = pos.shares * resolution_price
            self.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, resolution_price)
            self.cash += dollars
            self.sells += 1
            if token_id in self.our_entries:
                del self.our_entries[token_id]
            if token_id in self.high_water_marks:
                del self.high_water_marks[token_id]
            logger.info(f"HOUR RESOLVE: {token_id} @{resolution_price} (bid={last_bid}) = ${dollars:.2f}")
        # Clear hourly state - hourly markets reset each hour
        self.leader_positions = {}
        self._hourly_realized_loss = Decimal("0")

    def on_event(self, event: MarketEvent) -> TradeDecision:
        # Liquidate all positions when hour changes (hourly markets resolve)
        all_prices = event.context.get('all_prices', {})
        prev_hour = self._current_hour
        self._check_hourly_reset(event.trade.timestamp)
        if prev_hour is not None and self._current_hour != prev_hour:
            # Use END-of-previous-hour prices for resolution (more accurate than
            # start-of-next-hour, since old hour's tokens may have stale prices by then)
            resolve_prices = self._last_all_prices if self._last_all_prices else all_prices
            self._liquidate_hour_boundary(resolve_prices)
        self._last_all_prices = all_prices

        trade, prices = event.trade, event.prices

        # Skip tiny leader trades (noise filter) - 1% of leader capital
        if self.config.leader_capital > 0:
            trade_pct = trade.dollars / self.config.leader_capital * 100
            if trade_pct < MIN_LEADER_TRADE_PCT:
                return self._skip("leader_trade_too_small")

        # Check ALL positions for profit targets using all_prices from context
        all_prices = event.context.get('all_prices', {})
        for token_id, pos in self.portfolio.get_positions().items():
            if pos.shares <= 0:
                continue
            # Get current price for this token
            token_prices = all_prices.get(token_id)
            if not token_prices or not token_prices.bid or token_prices.bid <= 0:
                continue

            current_bid = token_prices.bid
            entry_price = self.our_entries.get(token_id, Decimal("0"))

            # Update high water mark
            if entry_price > 0:
                self.high_water_marks[token_id] = max(
                    current_bid,
                    self.high_water_marks.get(token_id, entry_price)
                )

            # Check profit target
            if self._check_profit_target(token_id, current_bid):
                logger.info(f"Taking profit on {token_id}: entry={entry_price}, bid={current_bid}")
                self.profit_exits += 1
                return self._exit_position(pos, current_bid, "profit_target",
                                          token_id=token_id, market_id=pos.market_id, side=pos.side)

            # Check trailing stop (only if we've been in profit)
            high_water = self.high_water_marks.get(token_id, entry_price)
            if high_water > entry_price and entry_price > 0:
                # We've been in profit - check if price dropped enough from high
                drawdown_from_high = (high_water - current_bid) / high_water * 100
                if drawdown_from_high >= TRAILING_STOP_PCT:
                    logger.info(f"Trailing stop on {token_id}: high={high_water}, bid={current_bid}, drawdown={drawdown_from_high:.2f}%")
                    self.trailing_stops += 1
                    return self._exit_position(pos, current_bid, "trailing_stop",
                                              token_id=token_id, market_id=pos.market_id, side=pos.side)

            # Check extreme prices
            if current_bid >= PRICE_EXTREME_HIGH:
                return self._exit_position(pos, current_bid, "extreme_price",
                                          token_id=token_id, market_id=pos.market_id, side=pos.side)

        # Fallback: check current token (for compatibility)
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        if pos.shares > 0 and prices.bid and prices.bid > 0:
            current_bid = prices.bid
            entry_price = self.our_entries.get(trade.token_id, Decimal("0"))

            # Update high water mark
            if entry_price > 0:
                self.high_water_marks[trade.token_id] = max(
                    current_bid,
                    self.high_water_marks.get(trade.token_id, entry_price)
                )

            # Check profit target
            if self._check_profit_target(trade.token_id, current_bid):
                logger.info(f"Taking profit on {trade.token_id}: entry={entry_price}, bid={current_bid}")
                self.profit_exits += 1
                return self._exit_position(pos, current_bid, "profit_target")

            # Check trailing stop (only if we've been in profit)
            high_water = self.high_water_marks.get(trade.token_id, entry_price)
            if high_water > entry_price and entry_price > 0:
                drawdown_from_high = (high_water - current_bid) / high_water * 100
                if drawdown_from_high >= TRAILING_STOP_PCT:
                    logger.info(f"Trailing stop on {trade.token_id}: high={high_water}, bid={current_bid}, drawdown={drawdown_from_high:.2f}%")
                    self.trailing_stops += 1
                    return self._exit_position(pos, current_bid, "trailing_stop")

            # Check extreme prices
            if current_bid >= PRICE_EXTREME_HIGH:
                return self._exit_position(pos, current_bid, "extreme_price")

        # Now handle leader's action
        if trade.action == TradeAction.BUY:
            return self._handle_leader_buy(event)
        else:
            return self._handle_leader_sell(event)

    def _handle_leader_buy(self, event: MarketEvent) -> TradeDecision:
        """Copy leader's buy with risk-managed sizing."""
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # --- LATE-HOUR CAUTION ---
        event_minute = event.trade.timestamp.minute
        if event_minute >= LATE_HOUR_STOP_MIN:
            return self._skip("late_hour")

        # --- DRAWDOWN CIRCUIT BREAKER ---
        all_prices = event.context.get('all_prices', {})
        drawdown = self._get_hourly_drawdown(all_prices)
        if drawdown >= DRAWDOWN_STOP_THRESHOLD:
            return self._skip("circuit_breaker")

        # Skip only extreme prices (let everything else through)
        if ask <= SKIP_PRICE_LOW:
            return self._skip("price_extreme_low")
        if ask >= SKIP_PRICE_HIGH:
            return self._skip("price_too_high")

        # Cost check - but ALWAYS take trade if our price is better than leader's
        if trade.price > 0 and ask > trade.price:
            # Only check cost if we're paying MORE than leader
            drift = ((ask - trade.price) / trade.price) * 100
            actual_spread_pct = calculate_actual_spread_pct(prices)
            if drift + actual_spread_pct + cfg.slippage_cost_pct > MAX_TOTAL_COST_PCT:
                return self._skip("cost_too_high")

        # Calculate our size: BASE RATIO * scale_boost
        our_dollars = trade.dollars * self.scale_ratio * self.scale_boost

        # Apply drawdown size reduction (50% after $12 drawdown)
        if drawdown >= DRAWDOWN_REDUCE_THRESHOLD:
            our_dollars = our_dollars / 2
            logger.info(f"Circuit breaker: reducing size 50% (drawdown=${drawdown:.2f})")

        # Apply late-hour size reduction (40% after minute 40)
        if event_minute >= LATE_HOUR_REDUCE_MIN:
            our_dollars = our_dollars * Decimal("0.6")
            logger.info(f"Late hour: reducing size 40% (minute {event_minute})")

        # Capacity checks - use ACTUAL CASH to prevent overspending
        deployable = cfg.starting_capital * (1 - CASH_RESERVE_PCT / 100)
        deployed = self.portfolio.get_total_deployed()
        position_room = deployable - deployed  # Room based on position caps
        available = min(position_room, self.cash)  # Enforce actual cash limit
        if available <= 0:
            return self._skip("no_cash" if self.cash <= 0 else "reserve")

        dollars = min(our_dollars, available, cfg.hourly_budget - self.hourly_budget_used)
        if cfg.hourly_budget - self.hourly_budget_used <= 0:
            return self._skip("budget")

        # Cap checks
        mkt_cap = cfg.starting_capital * PER_MARKET_CAP_PCT / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, mkt_room)

        side_cap = cfg.starting_capital * PER_SIDE_PCT / 100
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, to_side(trade.side))
        if side_room <= 0:
            return self._skip("side_cap")
        dollars = min(dollars, side_room)

        global_cap = cfg.starting_capital * GLOBAL_EXPOSURE_PCT / 100
        global_room = global_cap - deployed
        if global_room <= 0:
            return self._skip("global_cap")
        dollars = min(dollars, global_room)

        # Ensure minimum viable trade
        min_dollars = max(MIN_LIMIT_ORDER_SHARES * ask, MIN_OUR_TRADE)
        if dollars < min_dollars:
            # Try to bump up to minimum if room allows
            if all(x >= min_dollars for x in [available, mkt_room, side_room, global_room, cfg.hourly_budget - self.hourly_budget_used]):
                dollars = min_dollars
            else:
                return self._skip("min_order")

        # Apply slippage only on larger orders (small orders get best price)
        if dollars >= SLIPPAGE_THRESHOLD:
            exec_price = ask + SLIPPAGE_PER_SHARE
        else:
            exec_price = ask
        shares = (dollars / exec_price).quantize(Decimal("0.01"))
        if shares < MIN_LIMIT_ORDER_SHARES:
            shares = MIN_LIMIT_ORDER_SHARES

        if dollars <= 0 or shares <= 0:
            return self._skip("min_order")

        self.buys += 1
        return TradeDecision.buy(dollars, shares, exec_price)

    def _handle_leader_sell(self, event: MarketEvent) -> TradeDecision:
        """Follow leader's sell (unless it's a mini-sell)."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Ignore mini-sells (leader rebalancing, not exiting)
        if self._is_leader_minisell(event):
            return self._skip("leader_minisell")

        # Loss protection - but ALWAYS sell if our bid is better than leader's sale price
        if bid <= trade.price and pos.avg_price > 0 and bid < pos.avg_price:
            # Only check loss protection if we're getting WORSE price than leader
            lp = self.leader_positions.get(trade.token_id)
            if lp and lp.get("shares", 0) > 0:
                leader_avg = lp["cost_basis"] / lp["shares"] if lp["shares"] > 0 else Decimal("0")
                if leader_avg > 0 and trade.price >= leader_avg:
                    return self._skip("leader_profit_our_loss")

        # Follow the sell - apply slippage only on larger orders
        scaled = trade.dollars * self.scale_ratio
        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        dollars_approx = shares * bid
        if dollars_approx >= SLIPPAGE_THRESHOLD:
            exec_price = max(bid - SLIPPAGE_PER_SHARE, Decimal("0.01"))
        else:
            exec_price = bid
        if shares < MIN_LIMIT_ORDER_SHARES:
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                shares = pos.shares

        if shares <= 0:
            return self._skip("zero_shares")

        # Enforce $1 minimum on sells
        dollars = shares * exec_price
        if dollars < MIN_OUR_TRADE:
            return self._skip("sell_too_small")

        self.sells += 1
        return TradeDecision.sell(dollars, shares, exec_price)

    def _exit_position(self, pos, bid: Decimal, reason: str, **kwargs) -> TradeDecision:
        """Exit a position completely.

        kwargs can include token_id, market_id, side for cross-token profit exits.
        """
        # Apply slippage only on larger orders
        shares = pos.shares
        dollars_approx = shares * bid
        if dollars_approx >= SLIPPAGE_THRESHOLD:
            exec_price = max(bid - SLIPPAGE_PER_SHARE, Decimal("0.01"))
        else:
            exec_price = bid
        if shares < MIN_LIMIT_ORDER_SHARES:
            shares = pos.shares  # Sell all even if below min

        # Enforce $1 minimum on exits
        dollars = shares * exec_price
        if dollars < MIN_OUR_TRADE:
            return self._skip("exit_too_small")

        self.sells += 1
        return TradeDecision.sell(dollars, shares, exec_price, exit_reason=reason, **kwargs)

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        trade = event.trade
        shares = decision.shares or Decimal("0")
        price = decision.price or Decimal("0")

        # Use metadata for token info if available (for cross-token profit exits)
        meta = decision.metadata
        token_id = meta.get('token_id', trade.token_id)
        market_id = meta.get('market_id', trade.market_id)
        side = meta.get('side', to_side(trade.side))

        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(token_id, market_id, side, shares, price)
            if decision.dollars:
                self.hourly_budget_used += decision.dollars
                self.cash -= decision.dollars  # Spend cash
            # Track our entry price
            self.our_entries[token_id] = price
        elif decision.action == DecisionAction.SELL:
            # Track realized loss BEFORE applying sell (need avg_price before it changes)
            pos_before = self.portfolio.get(token_id, market_id, side)
            if pos_before.shares > 0 and pos_before.avg_price > 0:
                cost_for_shares = pos_before.avg_price * shares
                proceeds = shares * price
                if proceeds < cost_for_shares:
                    self._hourly_realized_loss += (cost_for_shares - proceeds)
            self.portfolio.apply_sell(token_id, market_id, side, shares, price)
            # Credit sell proceeds back to hourly budget and cash
            if decision.dollars:
                self.hourly_budget_used = max(Decimal("0"), self.hourly_budget_used - decision.dollars)
                self.cash += decision.dollars  # Receive cash from sale
            # Clear entry and high water mark if fully exited
            pos = self.portfolio.get(token_id, market_id, side)
            if pos.shares <= 0:
                if token_id in self.our_entries:
                    del self.our_entries[token_id]
                if token_id in self.high_water_marks:
                    del self.high_water_marks[token_id]

        # Track leader positions
        if trade.action == TradeAction.BUY:
            if trade.token_id not in self.leader_positions:
                self.leader_positions[trade.token_id] = {"shares": Decimal("0"), "cost_basis": Decimal("0")}
            lp = self.leader_positions[trade.token_id]
            lp["shares"] += trade.shares
            lp["cost_basis"] += trade.dollars
        elif trade.action == TradeAction.SELL:
            lp = self.leader_positions.get(trade.token_id)
            if lp and lp["shares"] > 0:
                ratio = min(trade.shares / lp["shares"], Decimal("1"))
                lp["cost_basis"] -= lp["cost_basis"] * ratio
                lp["shares"] = max(Decimal("0"), lp["shares"] - trade.shares)

    def get_state(self) -> Dict[str, Any]:
        positions = {tid: {"market_id": p.market_id, "side": p.side.value, "shares": str(p.shares),
                          "avg_price": str(p.avg_price), "cost_basis": str(p.cost_basis)}
                    for tid, p in self.portfolio.get_positions().items()}
        return {
            "positions": positions,
            "total_deployed": str(self.portfolio.get_total_deployed()),
            "realized_pnl": str(self.portfolio.realized_pnl),
            "total_bought": str(self.portfolio.total_bought),
            "total_sold": str(self.portfolio.total_sold),
            "hourly_budget_used": str(self.hourly_budget_used),
            "cash": str(self.cash),
            "buys": self.buys,
            "conviction_buys": self.conviction_buys,
            "sells": self.sells,
            "skips": self.skips,
            "profit_exits": self.profit_exits,
            "trailing_stops": self.trailing_stops,
            "skip_reasons": self.skip_reasons
        }

    def on_session_end(self) -> Dict[str, Any]:
        return {
            "buys_executed": self.buys,
            "conviction_buys": self.conviction_buys,
            "sells_executed": self.sells,
            "skips": self.skips,
            "profit_exits": self.profit_exits,
            "trailing_stops": self.trailing_stops,
            "skip_reasons": self.skip_reasons,
            "total_deployed": str(self.portfolio.get_total_deployed()),
            "realized_pnl": str(self.portfolio.realized_pnl),
            "total_bought": str(self.portfolio.total_bought),
            "total_sold": str(self.portfolio.total_sold)
        }

    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        return calculate_strategy_pnl(self.portfolio, final_prices)
