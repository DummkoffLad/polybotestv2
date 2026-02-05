"""Profit Taker Strategy - copy buys, take profit or follow leader exit.

Key logic:
1. Copy leader BUYs (with standard risk controls)
2. SELL when either:
   - Leader sells (follow their exit)
   - Our position has reached profit target (e.g., 5% gain)
   - Price hits extreme (0.99)

The idea is to capture leader's entry signals but exit earlier when profitable,
rather than waiting for leader to exit (who may hold through drawdowns).

Configurable parameters:
- PROFIT_TARGET_PCT: Exit when unrealized profit exceeds this (default 5%)
- TRAILING_STOP_PCT: Optional trailing stop (default disabled)
- IGNORE_LEADER_MINISELLS: Ignore leader sells below X% of their position (default 10%)
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

# Strategy parameters - Dynamic profit targets based on entry price
# Lower prices have more room to run, higher prices closer to ceiling
PROFIT_TARGET_LOW = Decimal("25")   # Prices < 0.30: let winners run
PROFIT_TARGET_MID = Decimal("15")   # Prices 0.30-0.60
PROFIT_TARGET_HIGH = Decimal("8")  # Prices > 0.60: take profits earlier
TRAILING_STOP_PCT = Decimal("8")  # 8% trailing stop from high water mark
IGNORE_LEADER_MINISELLS_PCT = Decimal("10")  # Ignore sells < 10% of leader position
MIN_LEADER_TRADE_PCT = Decimal("1")  # Skip trades < 1% of leader capital (noise filter)
MAX_TOTAL_COST_PCT = Decimal("6")  # Stricter cost threshold (was 8%)
CASH_RESERVE_PCT = Decimal("15")  # More conservative reserve
PER_MARKET_CAP_PCT = Decimal("25")
PER_SIDE_PCT = Decimal("20")
GLOBAL_EXPOSURE_PCT = Decimal("85")


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

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._check_hourly_reset(event.trade.timestamp)
        trade, prices = event.trade, event.prices

        # Skip tiny leader trades (noise filter) - applies to ALL trades like conservative
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
        """Copy leader's buy."""
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Skip extreme low prices (0.01 or below - treated as zero value)
        if ask <= PRICE_EXTREME_LOW:
            return self._skip("price_extreme_low")

        # Cost check
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            actual_spread_pct = calculate_actual_spread_pct(prices)
            if drift + actual_spread_pct + cfg.slippage_cost_pct > MAX_TOTAL_COST_PCT:
                return self._skip("cost_too_high")

        # Calculate our size
        our_dollars = trade.dollars * self.scale_ratio

        # Capacity checks
        deployable = cfg.starting_capital * (1 - CASH_RESERVE_PCT / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

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

        shares = (dollars / ask).quantize(Decimal("0.01"))
        if shares < MIN_LIMIT_ORDER_SHARES:
            min_dollars = MIN_LIMIT_ORDER_SHARES * ask
            if all(x >= min_dollars for x in [available, mkt_room, side_room, global_room, cfg.hourly_budget - self.hourly_budget_used]):
                shares = MIN_LIMIT_ORDER_SHARES
                dollars = min_dollars
            else:
                return self._skip("min_order")

        if dollars <= 0 or shares <= 0:
            return self._skip("min_order")

        self.buys += 1
        return TradeDecision.buy(dollars, shares, ask)

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

        # Loss protection - don't sell at loss if leader is selling at profit
        if pos.avg_price > 0 and bid < pos.avg_price:
            lp = self.leader_positions.get(trade.token_id)
            if lp and lp.get("shares", 0) > 0:
                leader_avg = lp["cost_basis"] / lp["shares"] if lp["shares"] > 0 else Decimal("0")
                if leader_avg > 0 and trade.price >= leader_avg:
                    return self._skip("leader_profit_our_loss")

        # Follow the sell
        scaled = trade.dollars * self.scale_ratio
        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        if shares < MIN_LIMIT_ORDER_SHARES:
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                shares = pos.shares

        if shares <= 0:
            return self._skip("zero_shares")

        self.sells += 1
        return TradeDecision.sell(shares * bid, shares, bid)

    def _exit_position(self, pos, bid: Decimal, reason: str, **kwargs) -> TradeDecision:
        """Exit a position completely.

        kwargs can include token_id, market_id, side for cross-token profit exits.
        """
        shares = pos.shares
        if shares < MIN_LIMIT_ORDER_SHARES:
            shares = pos.shares  # Sell all even if below min
        self.sells += 1
        return TradeDecision.sell(shares * bid, shares, bid, exit_reason=reason, **kwargs)

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
            # Track our entry price
            self.our_entries[token_id] = price
        elif decision.action == DecisionAction.SELL:
            self.portfolio.apply_sell(token_id, market_id, side, shares, price)
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
            "buys": self.buys,
            "sells": self.sells,
            "skips": self.skips,
            "profit_exits": self.profit_exits,
            "trailing_stops": self.trailing_stops,
            "skip_reasons": self.skip_reasons
        }

    def on_session_end(self) -> Dict[str, Any]:
        return {
            "buys_executed": self.buys,
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
