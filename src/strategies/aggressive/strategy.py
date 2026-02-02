"""Aggressive Mirror Strategy - higher risk/reward copy trading.

Trades with wider cost tolerances, lower cash reserve, and stronger size
boosts for large leader trades. Designed to maximize upside when the
leader is on a winning streak, at the cost of larger drawdowns.

Key differences from base mirror:
  - 5% cash reserve (vs 10%)
  - 40% per-market cap (vs 30%)
  - 35% per-side cap (vs 26%)
  - Higher k_factor (1.2x on top of config k_factor)
  - Bigger size boosts for large leader trades
  - Wider cost tolerance (10% vs 8%)
  - Follows sells immediately (no loss protection blocking)
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
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide

logger = logging.getLogger(__name__)

CASH_RESERVE_PCT = Decimal("5")
PER_MARKET_CAP_PCT = Decimal("40")
PER_SIDE_PCT = Decimal("35")
GLOBAL_EXPOSURE_PCT = Decimal("100")
MAX_TOTAL_COST_PCT = Decimal("10")
K_FACTOR_MULT = Decimal("1.2")  # Applied on top of config k_factor


def _to_side(side: TradeSide) -> Side:
    return Side.UP if side == TradeSide.UP else Side.DOWN


@register_strategy
class AggressiveMirrorStrategy(Strategy):

    def __init__(self):
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_tracker: Dict[str, Dict] = {}
        self.scale_ratio = Decimal("0")
        self.hourly_budget_used = Decimal("0")
        self._current_hour: Optional[int] = None
        self.buys = self.sells = self.skips = 0
        self.skip_reasons: Dict[str, int] = {}

    @property
    def name(self) -> str:
        return "aggressive_mirror"

    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        effective_k = config.k_factor * K_FACTOR_MULT
        self.scale_ratio = (config.starting_capital / config.leader_capital * effective_k
                            if config.leader_capital > 0 else Decimal("0.15"))

    def _check_hourly_reset(self, event_time: datetime) -> None:
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        
        if pos.shares > 0 and prices.bid and prices.bid >= PRICE_EXTREME_HIGH:
            logger.info(f"Auto-sell at extreme price {prices.bid}")
            shares_to_sell = pos.shares
            if shares_to_sell >= MIN_LIMIT_ORDER_SHARES:
                self.sells += 1
                return TradeDecision.sell(
                    shares_to_sell * prices.bid, shares_to_sell, prices.bid,
                    order_type=OrderType.LIMIT
                )
        
        if trade.action == TradeAction.BUY and prices.ask and prices.ask <= PRICE_EXTREME_LOW:
            return self._skip("price_extreme_low")
        
        return None

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._check_hourly_reset(event.trade.timestamp)

        # Check for price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision

        trade = event.trade
        our_dollars = trade.dollars * self.scale_ratio

        # Aggressive size boosts based on leader trade percentage
        if self.config.leader_capital > 0:
            trade_pct = trade.dollars / self.config.leader_capital * 100
            if trade_pct >= 5:
                our_dollars *= Decimal("1.50")    # >5% = very high conviction
            elif trade_pct >= 3:
                our_dollars *= Decimal("1.35")    # 3-5% = high conviction
            elif trade_pct >= 1:
                our_dollars *= Decimal("1.20")    # 1-3% = moderate conviction

        if trade.action == TradeAction.BUY:
            return self._buy(event, our_dollars)
        return self._sell(event, our_dollars)

    def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Wider cost tolerance
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > MAX_TOTAL_COST_PCT:
                return self._skip("cost_too_high")

        # Aggressive capacity checks
        deployable = cfg.starting_capital * (1 - CASH_RESERVE_PCT / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

        dollars = min(scaled, available, cfg.hourly_budget - self.hourly_budget_used)
        if cfg.hourly_budget - self.hourly_budget_used <= 0:
            return self._skip("budget")

        # Wider cap checks
        mkt_cap = cfg.starting_capital * PER_MARKET_CAP_PCT / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, mkt_room)

        side_cap = cfg.starting_capital * PER_SIDE_PCT / 100
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, _to_side(trade.side))
        if side_room <= 0:
            return self._skip("side_cap")
        dollars = min(dollars, side_room)

        global_cap = cfg.starting_capital * GLOBAL_EXPOSURE_PCT / 100
        global_room = global_cap - deployed
        if global_room <= 0:
            return self._skip("global_cap")
        dollars = min(dollars, global_room)

        # Calculate shares from dollars
        shares = (dollars / ask).quantize(Decimal("0.01"))
        
        # Enforce Polymarket minimum order size (5 shares for limit orders)
        if shares < MIN_LIMIT_ORDER_SHARES:
            # Try to scale up to minimum
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

    def _sell(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Aggressive: NO loss protection — follow leader exits immediately
        # Rationale: if leader exits, we exit. Holding when leader exits is riskier.

        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        
        # Enforce Polymarket minimum order size (5 shares for limit orders)
        if shares < MIN_LIMIT_ORDER_SHARES:
            # Check if we have at least 5 shares to sell
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                # Sell all remaining if below minimum
                shares = pos.shares
        
        if shares <= 0:
            return self._skip("zero_shares")

        self.sells += 1
        return TradeDecision.sell(shares * bid, shares, bid)

    def _skip(self, reason: str) -> TradeDecision:
        self.skips += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        return TradeDecision.skip(reason)

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        trade = event.trade
        side = _to_side(trade.side)
        shares = decision.shares or Decimal("0")
        price = decision.price or Decimal("0")

        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(trade.token_id, trade.market_id, side, shares, price)
            if decision.dollars:
                self.hourly_budget_used += decision.dollars
        elif decision.action == DecisionAction.SELL:
            self.portfolio.apply_sell(trade.token_id, trade.market_id, side, shares, price)

        # Track leader
        if trade.action == TradeAction.BUY:
            if trade.token_id not in self.leader_tracker:
                self.leader_tracker[trade.token_id] = {"shares": Decimal("0"), "cost_basis": Decimal("0")}
            lt = self.leader_tracker[trade.token_id]
            lt["shares"] += trade.shares
            lt["cost_basis"] += trade.dollars
            if lt["shares"] > 0:
                lt["avg_price"] = lt["cost_basis"] / lt["shares"]
        elif trade.action == TradeAction.SELL:
            lt = self.leader_tracker.get(trade.token_id)
            if lt and lt["shares"] > 0:
                ratio = min(trade.shares / lt["shares"], Decimal("1"))
                lt["cost_basis"] -= lt["cost_basis"] * ratio
                lt["shares"] -= trade.shares

    def get_state(self) -> Dict[str, Any]:
        positions = {tid: {"market_id": p.market_id, "side": p.side.value, "shares": str(p.shares),
                           "avg_price": str(p.avg_price), "cost_basis": str(p.cost_basis)}
                     for tid, p in self.portfolio.get_positions().items()}
        return {"positions": positions, "total_deployed": str(self.portfolio.get_total_deployed()),
                "realized_pnl": str(self.portfolio.realized_pnl),
                "total_bought": str(self.portfolio.total_bought),
                "total_sold": str(self.portfolio.total_sold),
                "hourly_budget_used": str(self.hourly_budget_used), "buys": self.buys,
                "sells": self.sells, "skips": self.skips, "skip_reasons": self.skip_reasons}

    def on_session_end(self) -> Dict[str, Any]:
        return {"buys_executed": self.buys, "sells_executed": self.sells, "skips": self.skips,
                "skip_reasons": self.skip_reasons, "total_deployed": str(self.portfolio.get_total_deployed()),
                "realized_pnl": str(self.portfolio.realized_pnl),
                "total_bought": str(self.portfolio.total_bought),
                "total_sold": str(self.portfolio.total_sold)}

    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        unrealized = sum((p.shares * final_prices[tid].bid - p.cost_basis
                          for tid, p in self.portfolio.get_positions().items()
                          if tid in final_prices and final_prices[tid].bid), Decimal("0"))
        return self.portfolio.realized_pnl, unrealized
