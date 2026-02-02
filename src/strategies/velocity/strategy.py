"""Velocity Mirror Strategy - responds to trade rate acceleration.

The idea: sudden increases in trading activity signal important information.
- Burst of buys = leader seeing opportunity → follow aggressively
- Burst of sells = leader exiting → exit faster
- Slowing activity = fade the move → be more conservative

This strategy tracks:
1. Trade velocity: trades per second/minute for each token
2. Velocity changes: acceleration/deceleration
3. Net direction: buy vs sell pressure

Behavior:
- Accelerating buys → scale up position size, relax thresholds
- Decelerating/reversing → scale down, tighten thresholds
- Mean reversion: after burst, be cautious
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ...core.portfolio import Portfolio
from ...core.types import Side
from ..base import (
    Strategy, StrategyConfig, TradeDecision, DecisionAction, OrderType,
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide

logger = logging.getLogger(__name__)

# Velocity calculation windows
SHORT_WINDOW_SECONDS = 30   # Recent activity
LONG_WINDOW_SECONDS = 120   # Baseline activity

# Velocity thresholds
HIGH_VELOCITY_THRESHOLD = 3.0  # 3x baseline = high velocity
LOW_VELOCITY_THRESHOLD = 0.5   # Below 0.5x baseline = low velocity

# Position sizing by velocity
HIGH_VELOCITY_MULT = Decimal("1.5")    # 50% larger
NORMAL_VELOCITY_MULT = Decimal("1.0")  # Normal
LOW_VELOCITY_MULT = Decimal("0.6")     # 40% smaller

# Standard caps
CASH_RESERVE_PCT = Decimal("10")
PER_MARKET_CAP_PCT = Decimal("30")
PER_SIDE_PCT = Decimal("26")
GLOBAL_EXPOSURE_PCT = Decimal("100")
MAX_TOTAL_COST_PCT = Decimal("8")


def _to_side(side: TradeSide) -> Side:
    return Side.UP if side == TradeSide.UP else Side.DOWN


@dataclass
class TradeRecord:
    """Simple record of a trade for velocity tracking."""
    timestamp: datetime
    action: str  # "BUY" or "SELL"
    dollars: Decimal


@register_strategy
class VelocityStrategy(Strategy):
    """
    Adjusts position sizing based on trade velocity (acceleration).
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
        
        # Velocity tracking per token
        self._trade_history: Dict[str, List[TradeRecord]] = defaultdict(list)

    @property
    def name(self) -> str:
        return "velocity"

    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        self._trade_history = defaultdict(list)
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                            if config.leader_capital > 0 else Decimal("0.10"))

    def _check_hourly_reset(self, event_time: datetime) -> None:
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _record_trade(self, token_id: str, timestamp: datetime, 
                     action: str, dollars: Decimal) -> None:
        """Record a trade for velocity tracking."""
        self._trade_history[token_id].append(TradeRecord(
            timestamp=timestamp, action=action, dollars=dollars
        ))
        # Cleanup old trades (older than LONG_WINDOW)
        cutoff = timestamp - timedelta(seconds=LONG_WINDOW_SECONDS * 2)
        self._trade_history[token_id] = [
            t for t in self._trade_history[token_id] 
            if t.timestamp > cutoff
        ]

    def _calculate_velocity(self, token_id: str, timestamp: datetime,
                           action: str) -> Tuple[str, Decimal]:
        """
        Calculate velocity regime and position multiplier.
        
        Returns: (regime, multiplier)
        """
        trades = self._trade_history.get(token_id, [])
        
        # Calculate trades in short and long windows
        short_cutoff = timestamp - timedelta(seconds=SHORT_WINDOW_SECONDS)
        long_cutoff = timestamp - timedelta(seconds=LONG_WINDOW_SECONDS)
        
        short_trades = [t for t in trades if t.timestamp > short_cutoff]
        long_trades = [t for t in trades if t.timestamp > long_cutoff]
        
        # Count same-direction trades
        same_action_short = sum(1 for t in short_trades if t.action == action)
        same_action_long = sum(1 for t in long_trades if t.action == action)
        
        # Calculate velocity ratio (short-term vs long-term rate)
        short_rate = same_action_short / (SHORT_WINDOW_SECONDS / 60)  # trades per minute
        long_rate = same_action_long / (LONG_WINDOW_SECONDS / 60)  # trades per minute
        
        if long_rate < 0.1:  # Avoid division issues
            velocity_ratio = 1.0
        else:
            velocity_ratio = short_rate / long_rate
        
        # Determine regime
        if velocity_ratio >= HIGH_VELOCITY_THRESHOLD:
            return "high", HIGH_VELOCITY_MULT
        elif velocity_ratio <= LOW_VELOCITY_THRESHOLD:
            return "low", LOW_VELOCITY_MULT
        else:
            return "normal", NORMAL_VELOCITY_MULT

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

        trade = event.trade
        action_str = "BUY" if trade.action == TradeAction.BUY else "SELL"
        
        # Record this trade for velocity tracking
        self._record_trade(trade.token_id, trade.timestamp, action_str, trade.dollars)
        
        # Get velocity regime and multiplier
        regime, multiplier = self._calculate_velocity(
            trade.token_id, trade.timestamp, action_str
        )
        
        # Calculate position size
        our_dollars = trade.dollars * self.scale_ratio * multiplier
        
        if trade.action == TradeAction.BUY:
            return self._buy(event, our_dollars, regime)
        return self._sell(event, our_dollars, regime)

    def _buy(self, event: MarketEvent, scaled: Decimal, regime: str) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Adjust cost threshold by velocity
        max_cost = MAX_TOTAL_COST_PCT
        if regime == "high":
            max_cost += Decimal("2")  # More lenient in high velocity
        elif regime == "low":
            max_cost -= Decimal("2")  # Stricter in low velocity

        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            spread_pct = prices.spread_pct or Decimal("0")
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

    def _sell(self, event: MarketEvent, scaled: Decimal, regime: str) -> TradeDecision:
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # High velocity sells → skip loss protection (exit fast)
        if regime != "high":
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
