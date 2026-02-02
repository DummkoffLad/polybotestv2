"""Spread-Aware Mirror Strategy - scales position by spread width.

The idea: spreads signal trading cost and market liquidity.
- Tight spread (< 1.5%) = low cost to enter/exit → trade more aggressively
- Medium spread (1.5-3%) = moderate cost → trade normally  
- Wide spread (> 3%) = high cost → trade conservatively or skip

This strategy dynamically adjusts:
1. Position size: larger when spread is tight
2. Entry threshold: stricter when spread is wide
3. Exit urgency: faster exits when spread widens (costs increasing)
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

# Spread thresholds (in %)
TIGHT_SPREAD = Decimal("1.5")   # Below this = aggressive mode
MEDIUM_SPREAD = Decimal("3.0")  # Below this = normal mode
WIDE_SPREAD = Decimal("5.0")    # Above this = very conservative / skip

# Position sizing multipliers by spread regime
TIGHT_MULTIPLIER = Decimal("1.4")   # 40% larger positions
NORMAL_MULTIPLIER = Decimal("1.0")  # Normal sizing
WIDE_MULTIPLIER = Decimal("0.5")    # 50% smaller positions

# Standard caps
CASH_RESERVE_PCT = Decimal("10")
PER_MARKET_CAP_PCT = Decimal("30")
PER_SIDE_PCT = Decimal("26")
GLOBAL_EXPOSURE_PCT = Decimal("100")
MAX_TOTAL_COST_PCT = Decimal("8")


def _to_side(side: TradeSide) -> Side:
    return Side.UP if side == TradeSide.UP else Side.DOWN


@register_strategy
class SpreadAwareStrategy(Strategy):
    """
    Scales positions based on current spread:
    - Tight spread: aggressive sizing, normal thresholds
    - Wide spread: conservative sizing, stricter thresholds
    """

    def __init__(self):
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_tracker: Dict[str, Dict] = {}
        self.scale_ratio = Decimal("0")
        self.hourly_budget_used = Decimal("0")
        self._current_hour: Optional[int] = None
        self.buys = self.sells = self.skips = 0
        self.skip_reasons: Dict[str, int] = {}
        
        # Spread tracking
        self._spread_history: Dict[str, list] = {}  # token_id -> list of recent spreads
        self._spread_window = 10  # Track last 10 spreads per token

    @property
    def name(self) -> str:
        return "spread_aware"

    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        self._spread_history = {}
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                            if config.leader_capital > 0 else Decimal("0.10"))

    def _check_hourly_reset(self, event_time: datetime) -> None:
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _get_spread_regime(self, spread_pct: Decimal) -> Tuple[str, Decimal]:
        """Determine spread regime and sizing multiplier."""
        if spread_pct <= TIGHT_SPREAD:
            return "tight", TIGHT_MULTIPLIER
        elif spread_pct <= MEDIUM_SPREAD:
            return "normal", NORMAL_MULTIPLIER
        elif spread_pct <= WIDE_SPREAD:
            return "wide", WIDE_MULTIPLIER
        else:
            return "very_wide", Decimal("0")  # Skip
    
    def _update_spread_history(self, token_id: str, spread_pct: Decimal) -> None:
        """Track recent spreads for a token."""
        if token_id not in self._spread_history:
            self._spread_history[token_id] = []
        self._spread_history[token_id].append(spread_pct)
        if len(self._spread_history[token_id]) > self._spread_window:
            self._spread_history[token_id].pop(0)
    
    def _get_avg_spread(self, token_id: str) -> Optional[Decimal]:
        """Get average recent spread for a token."""
        history = self._spread_history.get(token_id, [])
        if not history:
            return None
        return sum(history) / len(history)

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        
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
        
        # Calculate and track spread
        spread_pct = prices.spread_pct or Decimal("0")
        if prices.bid and prices.ask:
            mid = (prices.bid + prices.ask) / 2
            if mid > 0:
                spread_pct = (prices.ask - prices.bid) / mid * 100
        
        self._update_spread_history(trade.token_id, spread_pct)
        
        # Get spread regime
        regime, multiplier = self._get_spread_regime(spread_pct)
        
        # Skip if spread is too wide
        if regime == "very_wide":
            return self._skip("spread_too_wide")
        
        # Calculate base position size
        our_dollars = trade.dollars * self.scale_ratio * multiplier
        
        if trade.action == TradeAction.BUY:
            return self._buy(event, our_dollars, regime, spread_pct)
        return self._sell(event, our_dollars, regime, spread_pct)

    def _buy(self, event: MarketEvent, scaled: Decimal, regime: str, 
             spread_pct: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Cost check - adjust threshold by spread regime
        max_cost = MAX_TOTAL_COST_PCT
        if regime == "wide":
            max_cost = MAX_TOTAL_COST_PCT - Decimal("2")  # Stricter when wide
        
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            total_cost = drift + spread_pct  # Include spread in cost
            if total_cost > max_cost:
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
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, _to_side(trade.side))
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

    def _sell(self, event: MarketEvent, scaled: Decimal, regime: str,
              spread_pct: Decimal) -> TradeDecision:
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Wide spread → more urgent to exit (before spread widens further)
        if regime == "wide" and pos.avg_price > 0:
            # Skip loss protection in wide spread - exit is more important
            pass
        else:
            # Normal loss protection
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
