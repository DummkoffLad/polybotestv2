"""Hybrid Conservative-Momentum Strategy.

Blends the best of conservative (risk management) and momentum (profit capture):

Conservative Mode (default):
- Tight position limits
- Strong loss protection
- Skip small leader trades

Momentum Mode (when conditions are right):
- Larger positions on high-conviction trades
- Faster exits following leader
- More aggressive on winning positions

Mode switching based on:
1. Win streak: consecutive profitable trades → switch to momentum
2. Losing streak: consecutive losses → switch to conservative
3. Volatility: high volatility → conservative, low → momentum
4. Position P&L: positive unrealized → momentum, negative → conservative

This provides capital preservation during drawdowns while capturing
upside during winning streaks.
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
from ..utils import to_side

logger = logging.getLogger(__name__)

# Mode switching thresholds
WIN_STREAK_THRESHOLD = 3      # Consecutive wins to switch to momentum
LOSS_STREAK_THRESHOLD = 2     # Consecutive losses to switch to conservative
MOMENTUM_PNL_THRESHOLD = Decimal("2.0")  # $2 unrealized profit → momentum mode

# Conservative mode parameters
CONS_CASH_RESERVE_PCT = Decimal("20")
CONS_PER_MARKET_CAP_PCT = Decimal("20")
CONS_PER_SIDE_PCT = Decimal("18")
CONS_MAX_TOTAL_COST_PCT = Decimal("6")
CONS_MULTIPLIER = Decimal("0.6")  # Conservative sizing

# Momentum mode parameters
MOM_CASH_RESERVE_PCT = Decimal("8")
MOM_PER_MARKET_CAP_PCT = Decimal("35")
MOM_PER_SIDE_PCT = Decimal("30")
MOM_MAX_TOTAL_COST_PCT = Decimal("10")
MOM_MULTIPLIER = Decimal("1.3")  # Momentum sizing

GLOBAL_EXPOSURE_PCT = Decimal("100")


@register_strategy
class HybridConservativeStrategy(Strategy):
    """
    Switches between conservative and momentum modes based on performance.
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
        
        # Mode tracking
        self._current_mode = "conservative"  # Start conservative
        self._win_streak = 0
        self._loss_streak = 0
        self._last_trade_profitable: Optional[bool] = None

    @property
    def name(self) -> str:
        return "hybrid_conservative"

    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        self._current_mode = "conservative"
        self._win_streak = 0
        self._loss_streak = 0
        self._last_trade_profitable = None
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                            if config.leader_capital > 0 else Decimal("0.10"))

    def _check_hourly_reset(self, event_time: datetime) -> None:
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _update_mode(self) -> None:
        """Update trading mode based on recent performance."""
        old_mode = self._current_mode
        
        # Check win/loss streaks
        if self._win_streak >= WIN_STREAK_THRESHOLD:
            self._current_mode = "momentum"
        elif self._loss_streak >= LOSS_STREAK_THRESHOLD:
            self._current_mode = "conservative"
        
        # Check unrealized P&L
        unrealized = self._calculate_unrealized()
        if unrealized >= MOMENTUM_PNL_THRESHOLD:
            self._current_mode = "momentum"
        elif unrealized <= -MOMENTUM_PNL_THRESHOLD:
            self._current_mode = "conservative"
        
        if old_mode != self._current_mode:
            logger.info(f"Mode switched: {old_mode} → {self._current_mode} "
                       f"(wins: {self._win_streak}, losses: {self._loss_streak}, "
                       f"unrealized: ${unrealized:.2f})")

    def _calculate_unrealized(self) -> Decimal:
        """Calculate rough unrealized P&L."""
        # Simple estimate based on portfolio state
        return sum(
            (p.shares * p.avg_price * Decimal("0.05"))  # Assume 5% move
            for p in self.portfolio.get_positions().values()
            if p.shares > 0
        )

    def _get_mode_params(self) -> Tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """Get parameters for current mode."""
        if self._current_mode == "momentum":
            return (MOM_CASH_RESERVE_PCT, MOM_PER_MARKET_CAP_PCT, 
                   MOM_PER_SIDE_PCT, MOM_MAX_TOTAL_COST_PCT, MOM_MULTIPLIER)
        else:
            return (CONS_CASH_RESERVE_PCT, CONS_PER_MARKET_CAP_PCT,
                   CONS_PER_SIDE_PCT, CONS_MAX_TOTAL_COST_PCT, CONS_MULTIPLIER)

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
        self._update_mode()
        
        # Check price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision

        trade = event.trade
        
        # Get mode-specific parameters
        cash_reserve, per_market, per_side, max_cost, multiplier = self._get_mode_params()
        
        # Calculate position size
        our_dollars = trade.dollars * self.scale_ratio * multiplier
        
        if trade.action == TradeAction.BUY:
            return self._buy(event, our_dollars, cash_reserve, per_market, per_side, max_cost)
        return self._sell(event, our_dollars)

    def _buy(self, event: MarketEvent, scaled: Decimal, 
             cash_reserve: Decimal, per_market: Decimal,
             per_side: Decimal, max_cost: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Cost check with mode-specific threshold
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > max_cost:
                return self._skip("cost_too_high")

        # Capacity checks with mode-specific caps
        deployable = cfg.starting_capital * (1 - cash_reserve / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0:
            return self._skip("reserve")

        dollars = min(scaled, available, cfg.hourly_budget - self.hourly_budget_used)
        if cfg.hourly_budget - self.hourly_budget_used <= 0:
            return self._skip("budget")

        mkt_cap = cfg.starting_capital * per_market / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, mkt_room)

        side_cap = cfg.starting_capital * per_side / 100
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

    def _sell(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Loss protection varies by mode
        if self._current_mode == "conservative":
            # Strict loss protection
            if pos.avg_price > 0 and bid < pos.avg_price:
                lt = self.leader_tracker.get(trade.token_id)
                if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
                    return self._skip("leader_profit_our_loss")
        # Momentum mode: follow leader exits more closely

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
        side = to_side(trade.side)
        shares = decision.shares or Decimal("0")
        price = decision.price or Decimal("0")

        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(trade.token_id, trade.market_id, side, shares, price)
            if decision.dollars:
                self.hourly_budget_used += decision.dollars
        elif decision.action == DecisionAction.SELL:
            self.portfolio.apply_sell(trade.token_id, trade.market_id, side, shares, price)
            
            # Track profitability for mode switching
            pos = self.portfolio.get(trade.token_id, trade.market_id, side)
            if pos and price > 0:
                profitable = price >= pos.avg_price if pos.avg_price > 0 else True
                if profitable:
                    self._win_streak += 1
                    self._loss_streak = 0
                else:
                    self._loss_streak += 1
                    self._win_streak = 0

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
                "sells": self.sells, "skips": self.skips, "skip_reasons": self.skip_reasons,
                "current_mode": self._current_mode,
                "win_streak": self._win_streak, "loss_streak": self._loss_streak}

    def on_session_end(self) -> Dict[str, Any]:
        return {"buys_executed": self.buys, "sells_executed": self.sells, "skips": self.skips,
                "skip_reasons": self.skip_reasons, "total_deployed": str(self.portfolio.get_total_deployed()),
                "realized_pnl": str(self.portfolio.realized_pnl),
                "total_bought": str(self.portfolio.total_bought),
                "total_sold": str(self.portfolio.total_sold),
                "final_mode": self._current_mode}

    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        unrealized = sum((p.shares * final_prices[tid].bid - p.cost_basis
                          for tid, p in self.portfolio.get_positions().items()
                          if tid in final_prices and final_prices[tid].bid), Decimal("0"))
        return self.portfolio.realized_pnl, unrealized
