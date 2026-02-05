"""Momentum Mirror Strategy - conviction-weighted copy trading.

Tracks the leader's recent trading patterns to measure conviction.
Multiple trades in the same direction on the same token within a short
window indicate higher conviction, which increases our position size.
Single small trades get normal or reduced sizing.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ...core.portfolio import Portfolio
from ...core.types import Side
from ..base import (
    Strategy, StrategyConfig, TradeDecision, DecisionAction, OrderType,
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide
from ..utils import to_side

logger = logging.getLogger(__name__)

CONVICTION_WINDOW_SEC = 300  # 5 minute window for conviction tracking
CONVICTION_DECAY_SEC = 120   # Trades older than 2 min contribute less


@register_strategy
class MomentumMirrorStrategy(Strategy):
    """Mirrors leader trades with conviction-based sizing.

    Conviction score is computed from recent leader activity in the same
    token and direction. More trades = higher conviction = bigger size.

    Conviction multipliers:
      - 1 trade:  1.0x (baseline)
      - 2 trades: 1.15x
      - 3 trades: 1.30x
      - 4+ trades: 1.40x (capped)
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
        # Conviction tracking: token_id -> list of (timestamp, action, dollars)
        self._recent_trades: Dict[str, List[Tuple[datetime, str, Decimal]]] = defaultdict(list)

    @property
    def name(self) -> str:
        return "momentum_mirror"

    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        self._recent_trades = defaultdict(list)
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                            if config.leader_capital > 0 else Decimal("0.1"))

    def _check_hourly_reset(self, event_time: datetime) -> None:
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _get_conviction(self, token_id: str, action: str, now: datetime) -> Decimal:
        """Compute conviction multiplier from recent leader activity.

        Returns a multiplier >= 1.0 based on how many recent trades the
        leader has made in the same token and direction.
        """
        cutoff = now - timedelta(seconds=CONVICTION_WINDOW_SEC)
        # Prune old entries
        self._recent_trades[token_id] = [
            (ts, act, d) for ts, act, d in self._recent_trades[token_id]
            if ts > cutoff
        ]
        recent = self._recent_trades[token_id]
        # Count trades in same direction
        same_dir = sum(1 for _, act, _ in recent if act == action)

        if same_dir >= 4:
            return Decimal("1.40")
        elif same_dir >= 3:
            return Decimal("1.30")
        elif same_dir >= 2:
            return Decimal("1.15")
        return Decimal("1.0")

    def _record_leader_trade(self, event: MarketEvent) -> None:
        trade = event.trade
        self._recent_trades[trade.token_id].append(
            (trade.timestamp, trade.action.value, trade.dollars)
        )

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        
        # Auto-sell at 0.99 - position is essentially won
        if pos.shares > 0 and prices.bid and prices.bid >= PRICE_EXTREME_HIGH:
            logger.info(f"Auto-sell at extreme price {prices.bid}")
            shares_to_sell = pos.shares
            if shares_to_sell >= MIN_LIMIT_ORDER_SHARES:
                self.sells += 1
                return TradeDecision.sell(
                    shares_to_sell * prices.bid, shares_to_sell, prices.bid,
                    order_type=OrderType.LIMIT
                )
        
        # Skip buys at 0.01 or below
        if trade.action == TradeAction.BUY and prices.ask and prices.ask <= PRICE_EXTREME_LOW:
            return self._skip("price_extreme_low")
        
        return None

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._check_hourly_reset(event.trade.timestamp)
        self._record_leader_trade(event)

        # Check for price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision

        trade, prices = event.trade, event.prices
        our_dollars = trade.dollars * self.scale_ratio

        # Conviction-based sizing
        conviction = self._get_conviction(trade.token_id, trade.action.value, trade.timestamp)
        our_dollars *= conviction

        # Also apply percentage-based size boost for large leader trades
        if self.config.leader_capital > 0:
            trade_pct = trade.dollars / self.config.leader_capital * 100
            if trade_pct >= 5:
                our_dollars *= Decimal("1.20")
            elif trade_pct >= 3:
                our_dollars *= Decimal("1.10")

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

        # Cost check
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > cfg.max_total_cost_pct:
                return self._skip("cost_too_high")

        # Capacity checks
        deployable = cfg.starting_capital * (1 - cfg.cash_reserve_pct / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

        budget_room = cfg.hourly_budget - self.hourly_budget_used
        dollars = min(scaled, available, budget_room)
        if budget_room <= 0:
            return self._skip("budget")

        # Cap checks
        mkt_cap = cfg.starting_capital * cfg.per_market_cap_pct / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, mkt_room)

        side_cap = cfg.starting_capital * cfg.per_side_pct / 100
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, to_side(trade.side))
        if side_room <= 0:
            return self._skip("side_cap")
        dollars = min(dollars, side_room)

        global_cap = cfg.starting_capital * cfg.global_exposure_pct / 100
        global_room = global_cap - deployed
        if global_room <= 0:
            return self._skip("global_cap")
        dollars = min(dollars, global_room)

        # Calculate shares for limit order (5-share minimum)
        shares = (dollars / ask).quantize(Decimal("0.01"))
        
        # Enforce 5-share minimum for limit orders
        if shares < MIN_LIMIT_ORDER_SHARES:
            min_dollars_needed = MIN_LIMIT_ORDER_SHARES * ask
            if all(x >= min_dollars_needed for x in [available, mkt_room, side_room, global_room, budget_room]):
                shares = MIN_LIMIT_ORDER_SHARES
                dollars = shares * ask
            else:
                return self._skip("min_shares")
        
        if dollars <= 0:
            return self._skip("min_order")

        self.buys += 1
        return TradeDecision.buy(dollars, shares, ask, order_type=OrderType.LIMIT)

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

        # Loss protection
        if cfg.params.get("safety", {}).get("block_loss_sells_if_leader_profit", True):
            if pos.avg_price > 0 and bid < pos.avg_price:
                lt = self.leader_tracker.get(trade.token_id)
                if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
                    return self._skip("leader_profit_our_loss")

        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        
        # Enforce 5-share minimum for limit orders
        if shares < MIN_LIMIT_ORDER_SHARES:
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            elif pos.shares >= Decimal("4"):
                shares = pos.shares
            else:
                return self._skip("min_shares")
        
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
