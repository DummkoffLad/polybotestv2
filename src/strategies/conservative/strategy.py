"""Conservative Mirror Strategy - strict risk controls.

A cautious copy-trading strategy that prioritizes capital preservation.
Uses tighter caps, higher cash reserves, and requires stronger signals
(larger leader trades relative to their capital) before entering.

Key differences from base mirror:
  - 20% cash reserve (vs 10%)
  - 20% per-market cap (vs 30%)
  - 18% per-side cap (vs 26%)
  - No size boosts ever
  - Lower k_factor (0.6 effective via internal scaling)
  - Minimum leader trade threshold: skips trades below 1% of leader capital
  - Stricter cost threshold (6% vs 8%)
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

# Conservative overrides applied on top of whatever config is passed
CASH_RESERVE_PCT = Decimal("20")
PER_MARKET_CAP_PCT = Decimal("20")
PER_SIDE_PCT = Decimal("18")
GLOBAL_EXPOSURE_PCT = Decimal("80")
MAX_TOTAL_COST_PCT = Decimal("6")
K_FACTOR_MULT = Decimal("0.7")  # Applied on top of config k_factor
MIN_LEADER_TRADE_PCT = Decimal("1")  # Skip trades < 1% of leader capital


@register_strategy
class ConservativeMirrorStrategy(SkipHelperMixin, HourlyBudgetMixin, Strategy):

    def __init__(self):
        SkipHelperMixin.__init__(self)
        HourlyBudgetMixin.__init__(self)
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_tracker: Dict[str, Dict] = {}
        self.scale_ratio = Decimal("0")
        self.buys = self.sells = 0

    @property
    def name(self) -> str:
        return "conservative_mirror"

    def initialize(self, config: StrategyConfig) -> None:
        SkipHelperMixin.__init__(self)
        HourlyBudgetMixin.__init__(self)
        self._current_hour = datetime.now(timezone.utc).hour
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.buys = self.sells = 0
        # Use lower effective k_factor for conservative sizing
        effective_k = config.k_factor * K_FACTOR_MULT
        self.scale_ratio = (config.starting_capital / config.leader_capital * effective_k
                            if config.leader_capital > 0 else Decimal("0.05"))

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        
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

        # Skip tiny leader trades (noise filter)
        if self.config.leader_capital > 0:
            trade_pct = trade.dollars / self.config.leader_capital * 100
            if trade_pct < MIN_LEADER_TRADE_PCT:
                return self._skip("leader_trade_too_small")

        our_dollars = trade.dollars * self.scale_ratio
        # No size boosts - conservative stays flat

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

        # Stricter cost check - use REAL spread from bid/ask prices
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            actual_spread_pct = calculate_actual_spread_pct(prices)
            if drift + actual_spread_pct + cfg.slippage_cost_pct > MAX_TOTAL_COST_PCT:
                return self._skip("cost_too_high")

        # Conservative capacity checks
        deployable = cfg.starting_capital * (1 - CASH_RESERVE_PCT / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

        dollars = min(scaled, available, cfg.hourly_budget - self.hourly_budget_used)
        if cfg.hourly_budget - self.hourly_budget_used <= 0:
            return self._skip("budget")

        # Tighter cap checks
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
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Loss protection - conservative always blocks loss sells if leader profits
        if pos.avg_price > 0 and bid < pos.avg_price:
            lt = self.leader_tracker.get(trade.token_id)
            if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
                return self._skip("leader_profit_our_loss")

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

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        trade = event.trade
        side = to_side(trade.side)
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
        return calculate_strategy_pnl(self.portfolio, final_prices)
