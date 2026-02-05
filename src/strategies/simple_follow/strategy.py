"""Simple Follow Strategy - Fixed sizing with rolling window filter.

Based on user's past successful approach:
- Fixed position sizing ($1-7 per trade) instead of complex scaling
- Rolling window to filter "destroyed" trades (leader reversal)
- Per-market caps to prevent over-concentration
- Optional scaled mode using estimated leader capital

Key features:
  - Aggregates partial fills by tx_hash into single logical trades
  - Rolling window detects leader reversals (buy then sell = skip)
  - Fixed bet sizing: $1-$7 per trade (configurable)
  - Per-market cap: $20 max per market (configurable)
  - Simple sell logic: mirror leader exits proportionally
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from ...core.portfolio import Portfolio
from ...core.types import Side
from ..base import (
    Strategy, StrategyConfig, TradeDecision, DecisionAction, OrderType,
    register_strategy, MIN_LIMIT_ORDER_SHARES, PRICE_EXTREME_HIGH, PRICE_EXTREME_LOW
)
from ...data.models import MarketEvent, PriceSnapshot, TradeAction, TradeSide
from ...analysis.pnl_calculator import calculate_strategy_pnl
from ..mixins import SkipHelperMixin
from ..utils import to_side

logger = logging.getLogger(__name__)

# Default configuration - can be overridden via config.params
DEFAULT_MIN_BET = Decimal("1")
DEFAULT_MAX_BET = Decimal("7")
DEFAULT_PER_MARKET_CAP = Decimal("20")
DEFAULT_WINDOW_SECONDS = 10
DEFAULT_USE_SCALING = False
DEFAULT_LEADER_CAPITAL = Decimal("1000")


@dataclass
class WindowedTrade:
    """Trade info stored in rolling window."""
    timestamp: datetime
    tx_hash: str
    token_id: str
    market_id: str
    side: TradeSide
    action: TradeAction
    dollars: Decimal
    shares: Decimal
    price: Decimal
    partial_count: int = 1  # How many fills aggregated into this trade


@register_strategy
class SimpleFollowStrategy(SkipHelperMixin, Strategy):
    """Simple copy trading with fixed sizing and rolling window filter."""

    def __init__(self):
        SkipHelperMixin.__init__(self)
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()

        # Rolling window per token
        self._window: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

        # Partial fill aggregator: tx_hash -> aggregated trade info
        self._tx_aggregator: Dict[str, WindowedTrade] = {}

        # Leader position tracking (for proportional sells)
        self.leader_tracker: Dict[str, Dict] = {}

        # Configurable parameters
        self.min_bet = DEFAULT_MIN_BET
        self.max_bet = DEFAULT_MAX_BET
        self.per_market_cap = DEFAULT_PER_MARKET_CAP
        self.window_seconds = DEFAULT_WINDOW_SECONDS
        self.use_scaling = DEFAULT_USE_SCALING
        self.leader_capital = DEFAULT_LEADER_CAPITAL

        # Stats
        self.buys = self.sells = 0
        self.partials_aggregated = 0

        # Compatibility: other strategies have scale_ratio for tests
        self.scale_ratio = Decimal("0.1")

    @property
    def name(self) -> str:
        return "simple_follow"

    def initialize(self, config: StrategyConfig) -> None:
        SkipHelperMixin.__init__(self)
        self.config = config
        self.portfolio = Portfolio()
        self._window = defaultdict(lambda: deque(maxlen=20))
        self._tx_aggregator = {}
        self.leader_tracker = {}
        self.buys = self.sells = 0
        self.partials_aggregated = 0

        # Load configurable params from config.params if present
        params = config.params or {}
        self.min_bet = Decimal(str(params.get("min_bet", DEFAULT_MIN_BET)))
        self.max_bet = Decimal(str(params.get("max_bet", DEFAULT_MAX_BET)))
        self.per_market_cap = Decimal(str(params.get("per_market_cap", DEFAULT_PER_MARKET_CAP)))
        self.window_seconds = int(params.get("window_seconds", DEFAULT_WINDOW_SECONDS))
        self.use_scaling = bool(params.get("use_scaling", DEFAULT_USE_SCALING))
        self.leader_capital = Decimal(str(params.get("leader_capital", DEFAULT_LEADER_CAPITAL)))

        logger.info(f"SimpleFollow initialized: min=${self.min_bet}, max=${self.max_bet}, "
                    f"per_market_cap=${self.per_market_cap}, window={self.window_seconds}s, "
                    f"scaling={'ON' if self.use_scaling else 'OFF'}")

    def _aggregate_partial(self, event: MarketEvent) -> WindowedTrade:
        """Aggregate partial fills by tx_hash into single logical trade."""
        trade = event.trade
        tx_hash = trade.tx_hash or f"no_tx_{trade.timestamp.timestamp()}"

        if tx_hash in self._tx_aggregator:
            # Add to existing aggregation
            existing = self._tx_aggregator[tx_hash]
            existing.dollars += trade.dollars
            existing.shares += trade.shares
            # Volume-weighted average price
            if existing.shares > 0:
                existing.price = existing.dollars / existing.shares
            existing.partial_count += 1
            self.partials_aggregated += 1
            logger.debug(f"Aggregated partial fill #{existing.partial_count} for tx {tx_hash[:16]}...")
            return existing
        else:
            # New trade
            windowed = WindowedTrade(
                timestamp=trade.timestamp,
                tx_hash=tx_hash,
                token_id=trade.token_id,
                market_id=trade.market_id,
                side=trade.side,
                action=trade.action,
                dollars=trade.dollars,
                shares=trade.shares,
                price=trade.price,
                partial_count=1
            )
            self._tx_aggregator[tx_hash] = windowed
            return windowed

    def _clean_old_aggregations(self, now: datetime) -> None:
        """Remove old tx aggregations after window expires."""
        cutoff = now - timedelta(seconds=self.window_seconds * 2)
        old_txs = [tx for tx, w in self._tx_aggregator.items() if w.timestamp < cutoff]
        for tx in old_txs:
            del self._tx_aggregator[tx]

    def _add_to_window(self, windowed: WindowedTrade) -> None:
        """Add trade to rolling window for its token."""
        self._window[windowed.token_id].append(windowed)

    def _clean_window(self, token_id: str, now: datetime) -> None:
        """Remove expired trades from window."""
        cutoff = now - timedelta(seconds=self.window_seconds)
        window = self._window[token_id]
        # deque doesn't support list comprehension filtering, rebuild
        fresh = deque((t for t in window if t.timestamp > cutoff), maxlen=20)
        self._window[token_id] = fresh

    def _trade_survives(self, windowed: WindowedTrade) -> bool:
        """
        Check if trade survives the rolling window.
        Trade fails if leader reversed direction within window.
        """
        window = self._window[windowed.token_id]

        for past in window:
            if past.tx_hash == windowed.tx_hash:
                continue  # Same trade (different partial fill)

            time_diff = (windowed.timestamp - past.timestamp).total_seconds()
            if 0 < time_diff < self.window_seconds:
                if past.action != windowed.action:
                    logger.debug(f"Trade destroyed: leader {past.action.value} then {windowed.action.value} "
                                f"within {time_diff:.1f}s on {windowed.token_id[:8]}...")
                    return False  # Leader reversed direction

        return True

    def _calculate_bet_size(self, windowed: WindowedTrade) -> Decimal:
        """Calculate position size based on mode."""
        if self.use_scaling:
            # Scaled mode: proportion of leader's trade relative to their capital
            if self.leader_capital > 0:
                scale = self.config.starting_capital / self.leader_capital
                return windowed.dollars * scale * self.config.k_factor
            return self.min_bet
        else:
            # Fixed mode: clamp between min and max bet
            # Use a simple heuristic: larger leader trades get closer to max
            if windowed.dollars >= Decimal("50"):
                return self.max_bet
            elif windowed.dollars >= Decimal("20"):
                return (self.min_bet + self.max_bet) / 2
            else:
                return self.min_bet

    def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
        """Auto-sell at 0.99, treat 0.01 as zero value."""
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
        trade = event.trade
        now = trade.timestamp

        # Check for price extremes first
        extreme_decision = self._check_extreme_prices(event)
        if extreme_decision:
            return extreme_decision

        # Aggregate partial fills
        windowed = self._aggregate_partial(event)

        # Clean old data
        self._clean_old_aggregations(now)
        self._clean_window(trade.token_id, now)

        # Add to window
        self._add_to_window(windowed)

        # Check if trade survives rolling window
        if not self._trade_survives(windowed):
            return self._skip("reversed")

        # Route to buy or sell
        if trade.action == TradeAction.BUY:
            return self._buy(event, windowed)
        return self._sell(event, windowed)

    def _buy(self, event: MarketEvent, windowed: WindowedTrade) -> TradeDecision:
        trade, prices = event.trade, event.prices
        ask = prices.ask

        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")

        # Calculate bet size
        dollars = self._calculate_bet_size(windowed)

        # Apply per-market cap
        market_exposure = self.portfolio.get_market_exposure(trade.market_id)
        market_room = self.per_market_cap - market_exposure
        if market_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, market_room)

        # Apply global cap (use config if available)
        if self.config:
            deployed = self.portfolio.get_total_deployed()
            global_cap = self.config.starting_capital * Decimal("0.8")  # 80% max
            global_room = global_cap - deployed
            if global_room <= 0:
                return self._skip("global_cap")
            dollars = min(dollars, global_room)

        # Ensure we meet minimum bet
        if dollars < self.min_bet:
            return self._skip("below_min_bet")

        # Calculate shares
        shares = (dollars / ask).quantize(Decimal("0.01"))

        # Enforce Polymarket minimum (5 shares for limit orders)
        if shares < MIN_LIMIT_ORDER_SHARES:
            min_dollars = MIN_LIMIT_ORDER_SHARES * ask
            if min_dollars <= market_room:
                shares = MIN_LIMIT_ORDER_SHARES
                dollars = min_dollars
            else:
                return self._skip("min_shares")

        if dollars <= 0 or shares <= 0:
            return self._skip("zero_order")

        self.buys += 1
        logger.info(f"BUY ${dollars:.2f} ({shares:.2f} shares @ {ask:.4f}) "
                   f"[leader: ${windowed.dollars:.2f}, partials: {windowed.partial_count}]")
        return TradeDecision.buy(dollars, shares, ask)

    def _sell(self, event: MarketEvent, windowed: WindowedTrade) -> TradeDecision:
        trade, prices = event.trade, event.prices
        pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))

        if pos.shares <= 0:
            return self._skip("no_position")

        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")

        # Calculate sell shares: proportional to leader's sell vs their position
        leader_pos = self.leader_tracker.get(trade.token_id, {}).get("shares", Decimal("0"))
        if leader_pos > 0:
            # Sell same proportion as leader
            sell_ratio = min(windowed.shares / leader_pos, Decimal("1"))
            shares = (pos.shares * sell_ratio).quantize(Decimal("0.01"))
        else:
            # Leader has no tracked position, sell all
            shares = pos.shares

        # Ensure we have shares to sell
        shares = min(shares, pos.shares)

        # Enforce minimum
        if shares < MIN_LIMIT_ORDER_SHARES:
            if pos.shares >= MIN_LIMIT_ORDER_SHARES:
                shares = MIN_LIMIT_ORDER_SHARES
            else:
                shares = pos.shares  # Sell remaining even if below min

        if shares <= 0:
            return self._skip("zero_shares")

        self.sells += 1
        dollars = shares * bid
        logger.info(f"SELL ${dollars:.2f} ({shares:.2f} shares @ {bid:.4f}) "
                   f"[leader: ${windowed.dollars:.2f}, partials: {windowed.partial_count}]")
        return TradeDecision.sell(dollars, shares, bid)

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        trade = event.trade
        side = to_side(trade.side)
        shares = decision.shares or Decimal("0")
        price = decision.price or Decimal("0")

        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(trade.token_id, trade.market_id, side, shares, price)
        elif decision.action == DecisionAction.SELL:
            self.portfolio.apply_sell(trade.token_id, trade.market_id, side, shares, price)

        # Track leader position
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
                lt["shares"] = max(Decimal("0"), lt["shares"] - trade.shares)

    def get_state(self) -> Dict[str, Any]:
        positions = {
            tid: {
                "market_id": p.market_id,
                "side": p.side.value,
                "shares": str(p.shares),
                "avg_price": str(p.avg_price),
                "cost_basis": str(p.cost_basis)
            }
            for tid, p in self.portfolio.get_positions().items()
        }
        return {
            "positions": positions,
            "total_deployed": str(self.portfolio.get_total_deployed()),
            "realized_pnl": str(self.portfolio.realized_pnl),
            "total_bought": str(self.portfolio.total_bought),
            "total_sold": str(self.portfolio.total_sold),
            "buys": self.buys,
            "sells": self.sells,
            "skips": self.skips,
            "skip_reasons": self.skip_reasons,
            "partials_aggregated": self.partials_aggregated,
            "config": {
                "min_bet": str(self.min_bet),
                "max_bet": str(self.max_bet),
                "per_market_cap": str(self.per_market_cap),
                "window_seconds": self.window_seconds,
                "use_scaling": self.use_scaling,
                "leader_capital": str(self.leader_capital)
            }
        }

    def on_session_end(self) -> Dict[str, Any]:
        return {
            "buys_executed": self.buys,
            "sells_executed": self.sells,
            "skips": self.skips,
            "skip_reasons": self.skip_reasons,
            "partials_aggregated": self.partials_aggregated,
            "total_deployed": str(self.portfolio.get_total_deployed()),
            "realized_pnl": str(self.portfolio.realized_pnl),
            "total_bought": str(self.portfolio.total_bought),
            "total_sold": str(self.portfolio.total_sold)
        }

    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        return calculate_strategy_pnl(self.portfolio, final_prices)
