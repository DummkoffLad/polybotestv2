"""Mirror Strategy - copies leader trades with scaling and caps."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from ...core.portfolio import Portfolio
from ...core.types import Side
from ...core import DynamicSizer, SizingConfig, CapitalManager, TradingMode, TradeQualityScorer, SelectiveFollower
from ..base import (
    Strategy, StrategyConfig, TradeDecision, DecisionAction, OrderType,
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide

logger = logging.getLogger(__name__)

def _to_side(side: TradeSide) -> Side:
    return Side.UP if side == TradeSide.UP else Side.DOWN

@register_strategy
class MirrorStrategy(Strategy):
    def __init__(self):
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_tracker: Dict[str, Dict] = {}
        self.scale_ratio = Decimal("0")
        self.hourly_budget_used = Decimal("0")
        self._current_hour: Optional[int] = None  # For hourly budget reset
        self.buys = self.sells = self.skips = 0
        self.skip_reasons: Dict[str, int] = {}
        # Dynamic sizing components (initialized in initialize())
        self.sizer: Optional[DynamicSizer] = None
        self.capital_manager: Optional[CapitalManager] = None
        self.quality_scorer: Optional[TradeQualityScorer] = None
        self.selective_follower: Optional[SelectiveFollower] = None
    
    @property
    def name(self) -> str: return "mirror"
    
    def initialize(self, config: StrategyConfig) -> None:
        self.config = config
        self.portfolio = Portfolio()
        self.leader_tracker = {}
        self.hourly_budget_used = Decimal("0")
        self._current_hour = datetime.now(timezone.utc).hour
        self.buys = self.sells = self.skips = 0
        self.skip_reasons = {}
        self.scale_ratio = (config.starting_capital / config.leader_capital * config.k_factor
                           if config.leader_capital > 0 else Decimal("0.1"))

        # SAFETY: Validate scale ratio is sane
        if not (Decimal("0.001") < self.scale_ratio < Decimal("10")):
            logger.warning(f"Scale ratio out of typical bounds: {self.scale_ratio}")

        # Initialize dynamic sizing components
        sizing_config = SizingConfig(
            base_risk_pct=Decimal("1.0"),
            max_position_pct=Decimal("10.0"),
            consecutive_loss_threshold=3,
            size_reduction_after_losses=Decimal("0.5"),
            max_concurrent_positions=5
        )
        self.sizer = DynamicSizer(sizing_config)
        self.sizer.initialize(config.starting_capital)

        self.capital_manager = CapitalManager(
            soft_floor_pct=Decimal("90.0"),
            hard_floor_pct=Decimal("70.0"),
            exceptional_quality_threshold=Decimal("0.85")
        )
        self.capital_manager.initialize(config.starting_capital)

        # Assume leader avg size is ~$100 (can be updated from actual data)
        # Use permissive thresholds (0.40) to avoid breaking existing behavior
        # Quality scoring provides ordering, not hard filtering for most trades
        self.quality_scorer = TradeQualityScorer(
            leader_avg_size=Decimal("100"),
            high_quality_threshold=Decimal("0.40"),
            dca_quality_threshold=Decimal("0.50")
        )

        self.selective_follower = SelectiveFollower(max_positions=5)
    
    def _check_hourly_reset(self, event_time: datetime) -> None:
        """Reset hourly budget at hour boundary."""
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour

    def _calculate_current_equity(self) -> Decimal:
        """Calculate current equity: starting capital + realized PnL.

        This simplifies to starting_capital + realized_pnl because:
        - Cash = starting_capital - deployed + realized_pnl
        - Total equity = cash + deployed
        - Total equity = (starting_capital - deployed + realized_pnl) + deployed
        - Total equity = starting_capital + realized_pnl
        """
        return self.config.starting_capital + self.portfolio.realized_pnl
    
    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        
        # Auto-sell at 0.99 - position is essentially won
        if pos.shares > 0 and prices.bid and prices.bid >= PRICE_EXTREME_HIGH:
            logger.info(f"Auto-sell at extreme price {prices.bid} for {trade.token_id[:20]}...")
            shares_to_sell = pos.shares
            if shares_to_sell >= MIN_LIMIT_ORDER_SHARES:
                self.sells += 1
                return TradeDecision.sell(
                    shares_to_sell * prices.bid, shares_to_sell, prices.bid,
                    order_type=OrderType.LIMIT
                )
        
        # Skip buys at 0.01 or below - treat as zero value
        if trade.action == TradeAction.BUY and prices.ask and prices.ask <= PRICE_EXTREME_LOW:
            return self._skip("price_extreme_low")
        
        return None
    
    def on_event(self, event: MarketEvent) -> TradeDecision:
        # SAFETY: Check for hourly budget reset
        self._check_hourly_reset(event.trade.timestamp)
        
        # Check for price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision
        
        trade, prices = event.trade, event.prices
        our_dollars = trade.dollars * self.scale_ratio
        # Size boost: Apply flat 1.30x conviction boost to all trades
        # This matches the original session behavior where all trades got 1.30x
        our_dollars *= Decimal("1.30")
        return self._buy(event, our_dollars) if trade.action == TradeAction.BUY else self._sell(event, our_dollars)
    
    def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0: return self._skip("no_price")

        # SAFETY: Validate price in valid range
        if ask >= Decimal("1"):
            logger.warning(f"Invalid ask price >= 1: {ask}")
            return self._skip("invalid_price")

        # Calculate current equity for dynamic sizing
        current_equity = self._calculate_current_equity()

        # Update high water mark
        self.sizer.update_high_water_mark(current_equity)
        self.capital_manager.update_high_water_mark(current_equity)

        # Calculate spread for quality scoring
        bid = prices.bid if prices.bid else Decimal("0")
        mid = prices.mid if prices.mid else (ask + bid) / Decimal("2")
        if bid > 0 and mid > 0:
            spread_bps = ((ask - bid) / mid) * Decimal("10000")
        else:
            # No bid available - penalize with high spread
            spread_bps = Decimal("500")

        # Score trade quality
        quality_score = self.quality_scorer.score_trade(spread_bps, trade.dollars)

        # Check if trade meets quality threshold
        if not self.quality_scorer.should_take_trade(quality_score):
            return self._skip("low_quality")

        # Check floor status
        mode = self.capital_manager.check_floor_status(current_equity)

        # Check if we can enter new trade
        can_enter, reason = self.capital_manager.can_enter_new_trade(quality_score)
        if not can_enter:
            return self._skip(reason)

        # Check position limit
        current_position_count = len(self.portfolio.get_positions())
        can_open, reason = self.selective_follower.can_open_position(current_position_count)
        if not can_open:
            return self._skip(reason)

        # Calculate dynamic size
        dynamic_dollars = self.sizer.calculate_position_size(current_equity, quality_score)

        # Cost check FIRST - original checked cost before budget
        # This means cost_too_high can happen even when budget is exhausted
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > cfg.max_total_cost_pct:
                return self._skip("cost_too_high")

        # Capacity checks - NOW USE CURRENT EQUITY
        deployable = current_equity * (1 - cfg.cash_reserve_pct / 100)
        deployed = self.portfolio.get_total_deployed()
        available = deployable - deployed
        if available <= 0: return self._skip("reserve")

        budget_room = cfg.hourly_budget - self.hourly_budget_used
        dollars = min(dynamic_dollars, available, budget_room)

        # Don't use separate "budget" skip - let it fall through to min_order
        # This matches original behavior where budget exhaustion showed as min_order

        # Cap checks - NOW USE CURRENT EQUITY
        mkt_cap = current_equity * cfg.per_market_cap_pct / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0: return self._skip("market_cap")
        dollars = min(dollars, mkt_room)

        side_cap = current_equity * cfg.per_side_pct / 100
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, _to_side(trade.side))
        if side_room <= 0: return self._skip("side_cap")
        dollars = min(dollars, side_room)

        global_cap = current_equity * cfg.global_exposure_pct / 100
        global_room = global_cap - deployed
        if global_room <= 0: return self._skip("global_cap")
        dollars = min(dollars, global_room)

        # Calculate shares for limit order (5-share minimum)
        shares = (dollars / ask).quantize(Decimal("0.01"))

        # Enforce 5-share minimum for limit orders
        if shares < MIN_LIMIT_ORDER_SHARES:
            # Try to bump up to 5 shares if we have room
            min_dollars_needed = MIN_LIMIT_ORDER_SHARES * ask
            if all(x >= min_dollars_needed for x in [available, mkt_room, side_room, global_room, budget_room]):
                shares = MIN_LIMIT_ORDER_SHARES
                dollars = shares * ask
            else:
                return self._skip("min_shares")

        if dollars <= 0: return self._skip("min_order")

        self.buys += 1
        return TradeDecision.buy(dollars, shares, ask, order_type=OrderType.LIMIT)
    
    def _sell(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        trade, prices, cfg = event.trade, event.prices, self.config
        pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))
        if pos.shares <= 0: return self._skip("no_position")

        # Check if position management is allowed (hard floor blocks all activity)
        if not self.capital_manager.can_manage_positions():
            return self._skip("hard_floor_no_management")

        bid = prices.bid
        if not bid or bid <= 0: return self._skip("no_price")
        
        # SAFETY: Validate price in valid range
        if bid >= Decimal("1"):
            logger.warning(f"Invalid bid price >= 1: {bid}")
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
            # If we have at least 5 shares in position, sell 5
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                # Can't meet minimum, sell full position if it's close to 5
                if pos.shares >= Decimal("4"):
                    shares = pos.shares  # Sell what we have
                else:
                    return self._skip("min_shares")
        
        if shares <= 0: return self._skip("zero_shares")
        
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

        # Get position before applying trade (for PnL estimation on sells)
        pos_before = self.portfolio.get(trade.token_id, trade.market_id, side)

        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(trade.token_id, trade.market_id, side, shares, price)
            if decision.dollars: self.hourly_budget_used += decision.dollars
        elif decision.action == DecisionAction.SELL:
            self.portfolio.apply_sell(trade.token_id, trade.market_id, side, shares, price)

            # Estimate PnL for consecutive loss tracking
            # Simple heuristic: if sell price > avg_price, it's a win, else loss
            if pos_before.avg_price > 0:
                pnl_estimate = (price - pos_before.avg_price) * shares
                self.sizer.update_after_trade(pnl_estimate)

        # Update high water mark after every trade
        current_equity = self._calculate_current_equity()
        self.capital_manager.update_high_water_mark(current_equity)
        self.sizer.update_high_water_mark(current_equity)

        # Track leader
        if trade.action == TradeAction.BUY:
            if trade.token_id not in self.leader_tracker:
                self.leader_tracker[trade.token_id] = {"shares": Decimal("0"), "cost_basis": Decimal("0")}
            lt = self.leader_tracker[trade.token_id]
            lt["shares"] += trade.shares
            lt["cost_basis"] += trade.dollars
            if lt["shares"] > 0: lt["avg_price"] = lt["cost_basis"] / lt["shares"]
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
