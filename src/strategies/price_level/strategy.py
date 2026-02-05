"""Price Level Mirror Strategy - behavior varies by price zone.

The idea: different price levels signal different risk/reward:

Extreme High (>= 0.85): Token likely to resolve to 1.00
- Very profitable if you own it
- Risky to buy (upside is 1.00 - price, limited)
- Easy to sell (high liquidity usually)
→ Strategy: Sell aggressively, buy only small amounts

Extreme Low (<= 0.15): Token likely to resolve to 0.00  
- Very cheap, but likely worthless
- Could be a steal if it reverses
→ Strategy: Avoid buying, sell quickly if holding

Mid-Range (0.35 - 0.65): Maximum uncertainty
- Could go either way
- Leader's edge matters most here
→ Strategy: Follow leader closely, normal sizing

Transition Zones (0.15-0.35 and 0.65-0.85):
- Market making a decision
- Follow momentum more closely
→ Strategy: Momentum-weighted positioning
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
from ..mixins import SkipHelperMixin, HourlyBudgetMixin
from ..utils import to_side

logger = logging.getLogger(__name__)

# Price zones
EXTREME_HIGH = Decimal("0.85")     # Near certain win
TRANSITION_HIGH = Decimal("0.65")  # Trending to win
MID_HIGH = Decimal("0.50")         # Fair value
MID_LOW = Decimal("0.35")          # Below fair value
TRANSITION_LOW = Decimal("0.15")   # Trending to loss
EXTREME_LOW = Decimal("0.05")      # Near certain loss

# Position sizing by zone
ZONE_MULTIPLIERS = {
    "extreme_high": {"buy": Decimal("0.3"), "sell": Decimal("1.5")},   # Limit buys, aggressive sells
    "transition_high": {"buy": Decimal("0.7"), "sell": Decimal("1.2")}, # Cautious buy, normal sell
    "mid": {"buy": Decimal("1.0"), "sell": Decimal("1.0")},            # Normal both
    "transition_low": {"buy": Decimal("0.7"), "sell": Decimal("1.2")},  # Cautious buy, aggressive sell
    "extreme_low": {"buy": Decimal("0.2"), "sell": Decimal("1.5")},    # Avoid buys, exit fast
}

# Standard caps
CASH_RESERVE_PCT = Decimal("10")
PER_MARKET_CAP_PCT = Decimal("30")
PER_SIDE_PCT = Decimal("26")
GLOBAL_EXPOSURE_PCT = Decimal("100")
MAX_TOTAL_COST_PCT = Decimal("8")


@register_strategy
class PriceLevelStrategy(SkipHelperMixin, HourlyBudgetMixin, Strategy):
    """
    Adjusts position sizing based on current price level/zone.
    """

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
        return "price_level"

    def initialize(self, config: StrategyConfig) -> None:
        SkipHelperMixin.__init__(self)
        HourlyBudgetMixin.__init__(self)
        self._current_hour = datetime.now(timezone.utc).hour
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.buys = self.sells = 0
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                            if config.leader_capital > 0 else Decimal("0.10"))

    def _get_price_zone(self, price: Decimal) -> str:
        """Determine which price zone we're in."""
        if price >= EXTREME_HIGH:
            return "extreme_high"
        elif price >= TRANSITION_HIGH:
            return "transition_high"
        elif price > TRANSITION_LOW:
            return "mid"
        elif price > EXTREME_LOW:
            return "transition_low"
        else:
            return "extreme_low"

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        
        if pos.shares > 0 and prices.bid and prices.bid >= PRICE_EXTREME_HIGH:
            logger.info(f"Auto-sell at extreme price {prices.bid}")
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                self.sells += 1
                return TradeDecision.sell(
                    pos.shares * prices.bid, pos.shares, prices.bid,
                    order_type=OrderType.LIMIT
                )
        
        if trade.action == TradeAction.BUY and prices.ask and prices.ask <= PRICE_EXTREME_LOW:
            return self._skip("price_extreme_low")
        
        return None

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._check_hourly_reset(event.trade.timestamp)
        
        # Check price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision

        trade, prices = event.trade, event.prices
        
        # Determine price zone from mid price
        if prices.bid and prices.ask:
            mid = (prices.bid + prices.ask) / 2
        else:
            mid = trade.price
        
        zone = self._get_price_zone(mid)
        zone_mults = ZONE_MULTIPLIERS.get(zone, ZONE_MULTIPLIERS["mid"])
        
        if trade.action == TradeAction.BUY:
            multiplier = zone_mults["buy"]
            our_dollars = trade.dollars * self.scale_ratio * multiplier
            return self._buy(event, our_dollars, zone)
        else:
            multiplier = zone_mults["sell"]
            our_dollars = trade.dollars * self.scale_ratio * multiplier
            return self._sell(event, our_dollars, zone)

    def _buy(self, event: MarketEvent, scaled: Decimal, zone: str) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Stricter cost threshold in extreme zones
        max_cost = MAX_TOTAL_COST_PCT
        if zone in ["extreme_high", "extreme_low"]:
            max_cost = MAX_TOTAL_COST_PCT - Decimal("3")  # Much stricter

        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > max_cost:
                return self._skip("cost_too_high")

        # Capacity checks
        deployable = cfg.starting_capital * (1 - CASH_RESERVE_PCT / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

        dollars = min(scaled, available, cfg.hourly_budget - self.hourly_budget_used)
        if cfg.hourly_budget - self.hourly_budget_used <= 0:
            return self._skip("budget")

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

        # Calculate shares and enforce 5-share minimum
        shares = (dollars / ask).quantize(Decimal("0.01"))
        if shares < MIN_LIMIT_ORDER_SHARES:
            min_dollars = MIN_LIMIT_ORDER_SHARES * ask
            if all(x >= min_dollars for x in [available, mkt_room, side_room, global_room,
                                               cfg.hourly_budget - self.hourly_budget_used]):
                shares = MIN_LIMIT_ORDER_SHARES
                dollars = min_dollars
            else:
                return self._skip("min_order")
        
        if dollars <= 0 or shares <= 0:
            return self._skip("min_order")

        self.buys += 1
        return TradeDecision.buy(dollars, shares, ask, order_type=OrderType.LIMIT)

    def _sell(self, event: MarketEvent, scaled: Decimal, zone: str) -> TradeDecision:
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Skip loss protection in extreme zones (exit is priority)
        if zone not in ["extreme_high", "extreme_low"]:
            if pos.avg_price > 0 and bid < pos.avg_price:
                lt = self.leader_tracker.get(trade.token_id)
                if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
                    return self._skip("leader_profit_our_loss")

        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        
        # Enforce 5-share minimum
        if shares < MIN_LIMIT_ORDER_SHARES:
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                shares = pos.shares
        
        if shares <= 0:
            return self._skip("zero_shares")

        self.sells += 1
        return TradeDecision.sell(shares * bid, shares, bid, order_type=OrderType.LIMIT)

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
        unrealized = sum((p.shares * final_prices[tid].bid - p.cost_basis
                          for tid, p in self.portfolio.get_positions().items()
                          if tid in final_prices and final_prices[tid].bid), Decimal("0"))
        return self.portfolio.realized_pnl, unrealized
