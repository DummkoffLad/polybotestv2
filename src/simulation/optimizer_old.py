"""
Strategy Optimizer - Grid search over strategy parameters to find best configuration.

Tests multiple strategy configurations against recorded session data to find
optimal parameters for spread tolerance, slippage, price drift, and other filters.

IMPORTANT: This simulator is designed to be PESSIMISTIC not optimistic:
- Buys execute at ASK (worst price for us)
- Sells execute at BID (worst price for us)  
- Unrealized PnL uses BID (what we'd get if we exited now)
- No assumptions about perfect fills or favorable conditions
"""
from __future__ import annotations

import json
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy

from ..data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from ..strategies.base import Strategy, StrategyConfig, DecisionAction
from ..core.portfolio import Portfolio
from ..core.types import Side

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result from a single parameter configuration test."""
    config_name: str
    params: Dict[str, Any]
    
    # Execution stats
    events_processed: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    skips: int = 0
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    
    # Financial metrics
    buy_dollars: Decimal = Decimal("0")
    sell_dollars: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")  # Calculated using BID (exit price)
    total_pnl: Decimal = Decimal("0")
    final_portfolio_value: Decimal = Decimal("0")
    
    # Open position tracking
    open_positions: int = 0
    open_cost_basis: Decimal = Decimal("0")
    
    # Risk metrics
    max_drawdown_pct: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")  # On CLOSED trades only
    profit_factor: Decimal = Decimal("0")  # gross profit / gross loss
    
    @property
    def return_pct(self) -> Decimal:
        """Return as percentage of capital deployed."""
        if self.buy_dollars > 0:
            return (self.total_pnl / self.buy_dollars) * 100
        return Decimal("0")
    
    @property
    def score(self) -> Decimal:
        """
        Composite score for ranking configurations.
        Penalizes configurations with many open positions (unrealized losses hiding).
        """
        # Base: total PnL is most important
        pnl_score = float(self.total_pnl) * 40
        
        # Realized is better than unrealized (actually locked in vs paper)
        realized_bonus = float(self.realized_pnl) * 20 if self.realized_pnl > 0 else 0
        
        # Penalize having lots of capital stuck in open positions
        open_penalty = float(self.open_cost_basis) * -5
        
        # Win rate bonus (but only if we have enough trades for it to be meaningful)
        wr_bonus = float(self.win_rate) * 15 if (self.sells_executed >= 3) else 0
        
        return Decimal(str(pnl_score + realized_bonus + open_penalty + wr_bonus))


@dataclass
class StrategyVariant:
    """Defines a strategy approach with its parameter ranges."""
    name: str
    description: str
    base_params: Dict[str, Any]
    param_grid: Dict[str, List[Any]]  # Parameters to search over


class ConfigurableStrategy(Strategy):
    """
    Configurable mirror strategy with tunable parameters for optimization.
    
    This extends the base mirror logic with additional configurable filters:
    - max_spread_pct: Maximum spread to accept for trades
    - max_price_drift_pct: Maximum price drift from leader's execution
    - min_leader_trade_dollars: Minimum leader trade size to mirror
    - max_leader_trade_dollars: Maximum leader trade size to mirror (filter MM)
    - conviction_boost: Whether to apply size boost based on leader conviction
    - sell_only_profitable: Only sell if we're profitable on the position
    - min_unrealized_pnl_to_sell: Minimum unrealized PnL% to trigger sell
    """
    
    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.config: Optional[StrategyConfig] = None
        self.portfolio = Portfolio()
        self.leader_tracker: Dict[str, Dict] = {}
        self.scale_ratio = Decimal("0")
        self.hourly_budget_used = Decimal("0")
        self._current_hour: Optional[int] = None
        self.buys = self.sells = self.skips = 0
        self.skip_reasons: Dict[str, int] = {}
        
        # Trade tracking for metrics
        self._winning_trades = 0
        self._losing_trades = 0
        self._gross_profit = Decimal("0")
        self._gross_loss = Decimal("0")
        self._peak_value = Decimal("0")
        self._max_drawdown = Decimal("0")
    
    @property
    def name(self) -> str:
        return self.params.get("name", "configurable")
    
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
        
        self._winning_trades = 0
        self._losing_trades = 0
        self._gross_profit = Decimal("0")
        self._gross_loss = Decimal("0")
        self._peak_value = config.starting_capital
        self._max_drawdown = Decimal("0")
    
    def on_event(self, event: MarketEvent) -> "TradeDecision":
        from ..strategies.base import TradeDecision
        
        trade, prices = event.trade, event.prices
        
        # === FILTER 1: Leader trade size filter ===
        min_trade = Decimal(str(self.params.get("min_leader_trade_dollars", 0)))
        max_trade = Decimal(str(self.params.get("max_leader_trade_dollars", 10000)))
        if trade.dollars < min_trade:
            return self._skip("leader_trade_too_small")
        if trade.dollars > max_trade:
            return self._skip("leader_trade_too_large")
        
        # === FILTER 2: Spread filter ===
        max_spread = Decimal(str(self.params.get("max_spread_pct", 10.0)))
        if prices.spread_pct and prices.spread_pct > max_spread:
            return self._skip("spread_too_wide")
        
        # Calculate scaled position
        our_dollars = trade.dollars * self.scale_ratio
        
        # === CONVICTION BOOST ===
        if self.params.get("conviction_boost", True) and self.config.leader_capital > 0:
            trade_pct = trade.dollars / self.config.leader_capital * 100
            boost_thresholds = self.params.get("conviction_thresholds", {
                "high": (5, Decimal("1.30")),
                "medium": (3, Decimal("1.20")),
                "low": (1, Decimal("1.10")),
            })
            for level, (threshold, boost) in boost_thresholds.items():
                if trade_pct >= threshold:
                    our_dollars *= boost
                    break
        
        if trade.action == TradeAction.BUY:
            return self._buy(event, our_dollars)
        else:
            return self._sell(event, our_dollars)
    
    def _buy(self, event: MarketEvent, scaled: Decimal) -> "TradeDecision":
        from ..strategies.base import TradeDecision
        
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")
        if ask >= Decimal("1"):
            return self._skip("invalid_price")
        
        # === FILTER 3: Price drift check ===
        max_drift = Decimal(str(self.params.get("max_price_drift_pct", 5.0)))
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            if drift > max_drift:
                return self._skip("price_drift_too_high")
        
        # === FILTER 4: Total cost check ===
        spread_cost = Decimal(str(self.params.get("spread_cost_pct", 2.0)))
        slippage_cost = Decimal(str(self.params.get("slippage_cost_pct", 1.0)))
        max_total_cost = Decimal(str(self.params.get("max_total_cost_pct", 8.0)))
        
        if trade.price > 0:
            drift = ((ask - trade.price) / trade.price) * 100
            total_cost = drift + spread_cost + slippage_cost
            if total_cost > max_total_cost:
                return self._skip("cost_too_high")
        
        # === CAPITAL CALCULATION ===
        # Check if we should use the broken budget logic (for verification against actual sessions)
        use_broken_budget = self.params.get("use_broken_budget", False)
        
        if use_broken_budget:
            # BROKEN LOGIC (matches original behavior):
            # - Fixed deployable based on starting capital only
            # - Budget only increases, never decreases when selling
            deployable = cfg.starting_capital * (1 - cfg.cash_reserve_pct / 100)
            current_capital = cfg.starting_capital  # Fixed, doesn't adjust with realized PnL
            deployed = self.portfolio.get_total_deployed()
            available = deployable - deployed
            if available <= 0:
                return self._skip("reserve")
            
            # Broken: hourly_budget_used only goes UP
            budget_room = cfg.hourly_budget - self.hourly_budget_used
            dollars = min(scaled, available, budget_room)
            if budget_room <= 0:
                return self._skip("budget")
        else:
            # FIXED LOGIC (realistic):
            # Current capital = starting + realized PnL (gains/losses from closed trades)
            # This means if we lose $5, we have $95 to work with, not $100
            current_capital = cfg.starting_capital + self.portfolio.realized_pnl
            
            # Deployable = current capital minus cash reserve
            deployable = current_capital * (1 - cfg.cash_reserve_pct / 100)
            
            # Deployed = cost basis of open positions
            deployed = self.portfolio.get_total_deployed()
            
            # Available = what we can spend on new buys
            available = deployable - deployed
            if available <= 0:
                return self._skip("reserve")
            
            # Net budget = buys - sells (allows re-buying with proceeds)
            net_deployed = self.portfolio.total_bought - self.portfolio.total_sold
            budget_limit = cfg.starting_capital * Decimal("0.9")
            budget_room = budget_limit - net_deployed
            
            dollars = min(scaled, available, budget_room)
            if budget_room <= 0:
                return self._skip("budget")
        
        # Cap checks - use current_capital for dynamic limits
        mkt_cap = current_capital * cfg.per_market_cap_pct / 100
        mkt_room = mkt_cap - self.portfolio.get_market_exposure(trade.market_id)
        if mkt_room <= 0:
            return self._skip("market_cap")
        dollars = min(dollars, mkt_room)
        
        side_cap = current_capital * cfg.per_side_pct / 100
        side = Side.UP if trade.side == TradeSide.UP else Side.DOWN
        side_room = side_cap - self.portfolio.get_side_exposure(trade.market_id, side)
        if side_room <= 0:
            return self._skip("side_cap")
        dollars = min(dollars, side_room)
        
        global_cap = current_capital * cfg.global_exposure_pct / 100
        global_room = global_cap - deployed
        if global_room <= 0:
            return self._skip("global_cap")
        dollars = min(dollars, global_room)
        
        # Min order enforcement
        if 0 < dollars < 1:
            if all(x >= 1 for x in [available, mkt_room, side_room, global_room, budget_room]):
                dollars = Decimal("1.0")
            else:
                return self._skip("min_order")
        if dollars <= 0:
            return self._skip("min_order")
        
        self.buys += 1
        return TradeDecision.buy(dollars, (dollars / ask).quantize(Decimal("0.01")), ask)
    
    def _sell(self, event: MarketEvent, scaled: Decimal) -> "TradeDecision":
        from ..strategies.base import TradeDecision
        
        trade, prices, cfg = event.trade, event.prices, self.config
        side = Side.UP if trade.side == TradeSide.UP else Side.DOWN
        pos = self.portfolio.get(trade.token_id, trade.market_id, side)
        
        if pos.shares <= 0:
            return self._skip("no_position")
        
        bid = prices.bid
        if not bid or bid <= 0:
            return self._skip("no_price")
        if bid >= Decimal("1"):
            return self._skip("invalid_price")
        
        # === FILTER 5: Sell profitability check ===
        if self.params.get("sell_only_profitable", False):
            if pos.avg_price > 0 and bid < pos.avg_price:
                return self._skip("sell_would_lose")
        
        # === FILTER 6: Minimum profit threshold ===
        min_pnl_pct = Decimal(str(self.params.get("min_unrealized_pnl_to_sell", -100)))
        if pos.avg_price > 0:
            pnl_pct = ((bid - pos.avg_price) / pos.avg_price) * 100
            if pnl_pct < min_pnl_pct:
                return self._skip("pnl_below_threshold")
        
        # === FILTER 7: Leader profit / our loss check ===
        if self.params.get("block_loss_sells_if_leader_profit", True):
            if pos.avg_price > 0 and bid < pos.avg_price:
                lt = self.leader_tracker.get(trade.token_id)
                if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
                    return self._skip("leader_profit_our_loss")
        
        shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)
        if shares <= 0:
            return self._skip("zero_shares")
        
        self.sells += 1
        return TradeDecision.sell(shares * bid, shares, bid)
    
    def _skip(self, reason: str) -> "TradeDecision":
        from ..strategies.base import TradeDecision
        self.skips += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        return TradeDecision.skip(reason)
    
    def on_fill(self, event: MarketEvent, decision: "TradeDecision") -> None:
        trade = event.trade
        side = Side.UP if trade.side == TradeSide.UP else Side.DOWN
        shares = decision.shares or Decimal("0")
        price = decision.price or Decimal("0")
        
        if decision.action == DecisionAction.BUY:
            self.portfolio.apply_buy(trade.token_id, trade.market_id, side, shares, price)
            # Track hourly_budget_used for broken budget mode
            if self.params.get("use_broken_budget", False) and decision.dollars:
                self.hourly_budget_used += decision.dollars
        elif decision.action == DecisionAction.SELL:
            # Calculate PnL before sell
            pos = self.portfolio.get(trade.token_id, trade.market_id, side)
            if pos.avg_price > 0:
                pnl = (price - pos.avg_price) * shares
                if pnl >= 0:
                    self._winning_trades += 1
                    self._gross_profit += pnl
                else:
                    self._losing_trades += 1
                    self._gross_loss += abs(pnl)
            
            self.portfolio.apply_sell(trade.token_id, trade.market_id, side, shares, price)
        
        # Track leader positions
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
        
        # Track drawdown
        current_value = self.config.starting_capital - self.portfolio.get_total_deployed() + self.portfolio.realized_pnl
        if current_value > self._peak_value:
            self._peak_value = current_value
        drawdown = (self._peak_value - current_value) / self._peak_value * 100 if self._peak_value > 0 else Decimal("0")
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown
    
    def get_state(self) -> Dict[str, Any]:
        total_trades = self._winning_trades + self._losing_trades
        win_rate = Decimal(str(self._winning_trades / total_trades)) if total_trades > 0 else Decimal("0")
        profit_factor = self._gross_profit / self._gross_loss if self._gross_loss > 0 else Decimal("999")
        
        return {
            "buys": self.buys,
            "sells": self.sells,
            "skips": self.skips,
            "skip_reasons": self.skip_reasons,
            "realized_pnl": str(self.portfolio.realized_pnl),
            "total_bought": str(self.portfolio.total_bought),
            "total_sold": str(self.portfolio.total_sold),
            "total_deployed": str(self.portfolio.get_total_deployed()),
            "win_rate": str(win_rate),
            "profit_factor": str(profit_factor),
            "max_drawdown": str(self._max_drawdown),
            "winning_trades": self._winning_trades,
            "losing_trades": self._losing_trades,
        }
    
    def on_session_end(self) -> Dict[str, Any]:
        return self.get_state()


# === PREDEFINED STRATEGY VARIANTS ===

STRATEGY_VARIANTS = {
    # ORIGINAL: Exact config from the dry-run session that lost -5.7%
    # Uses BROKEN budget logic to match actual session behavior
    "original": StrategyVariant(
        name="original",
        description="EXACT config from dry-run session - uses broken budget logic for verification",
        base_params={
            "max_spread_pct": 8.0,  # From session_start config
            "max_price_drift_pct": 5.0,  # From session_start config
            "max_total_cost_pct": 100.0,  # Was not implemented in original
            "min_leader_trade_dollars": 0.0,  # No min filter in original
            "max_leader_trade_dollars": 10000.0,  # No max filter in original
            "conviction_boost": True,  # WAS ENABLED (see MirrorStrategy)
            "sell_only_profitable": False,  # Not in original - sells at any price
            "min_unrealized_pnl_to_sell": -100.0,  # Not in original
            "block_loss_sells_if_leader_profit": True,  # WAS ENABLED in original
            "use_broken_budget": True,  # Use broken budget logic to match actual session
        },
        param_grid={
            # Just one config - exact original
            "conviction_boost": [True],
        }
    ),
    
    # BASELINE: No filters at all - should match original behavior and LOSE money
    # Use this to validate the simulator is working correctly
    "baseline": StrategyVariant(
        name="baseline",
        description="NO FILTERS - mirrors everything. Should lose money like original. Use for validation.",
        base_params={
            "max_spread_pct": 100.0,  # No spread filter
            "max_price_drift_pct": 100.0,  # No drift filter
            "max_total_cost_pct": 100.0,  # No cost filter
            "min_leader_trade_dollars": 0.0,  # No min trade
            "max_leader_trade_dollars": 10000.0,  # No max trade
            "conviction_boost": False,  # No boost
            "sell_only_profitable": False,  # Sell even at a loss
            "min_unrealized_pnl_to_sell": -100.0,  # No profit threshold
            "block_loss_sells_if_leader_profit": False,  # Don't block any sells
        },
        param_grid={
            # Just one config - no variation
            "conviction_boost": [False],
        }
    ),
    
    "aggressive_mirror": StrategyVariant(
        name="aggressive_mirror",
        description="Follows most leader trades with loose filters - max capture",
        base_params={
            "max_spread_pct": 8.0,
            "max_price_drift_pct": 8.0,
            "max_total_cost_pct": 12.0,
            "min_leader_trade_dollars": 5.0,
            "max_leader_trade_dollars": 500.0,
            "conviction_boost": True,
            "sell_only_profitable": False,
            "block_loss_sells_if_leader_profit": False,
        },
        param_grid={
            "max_spread_pct": [5.0, 8.0, 12.0],
            "max_price_drift_pct": [5.0, 8.0, 12.0],
        }
    ),
    
    "tight_spread": StrategyVariant(
        name="tight_spread",
        description="Only trades with tight spreads - avoids market maker activity",
        base_params={
            "max_spread_pct": 3.0,
            "max_price_drift_pct": 3.0,
            "max_total_cost_pct": 5.0,
            "min_leader_trade_dollars": 8.0,
            "max_leader_trade_dollars": 200.0,
            "conviction_boost": True,
            "sell_only_profitable": False,
            "block_loss_sells_if_leader_profit": True,
        },
        param_grid={
            "max_spread_pct": [2.0, 3.0, 4.0],
            "max_price_drift_pct": [2.0, 3.0, 5.0],
            "min_leader_trade_dollars": [5.0, 8.0, 12.0],
        }
    ),
    
    "momentum_only": StrategyVariant(
        name="momentum_only",
        description="Focus on larger trades (momentum) - skip small MM trades",
        base_params={
            "max_spread_pct": 4.0,
            "max_price_drift_pct": 4.0,
            "max_total_cost_pct": 6.0,
            "min_leader_trade_dollars": 15.0,  # Higher min = skip MM
            "max_leader_trade_dollars": 500.0,
            "conviction_boost": True,
            "sell_only_profitable": False,
            "block_loss_sells_if_leader_profit": True,
        },
        param_grid={
            "min_leader_trade_dollars": [10.0, 15.0, 20.0, 25.0],
            "max_spread_pct": [3.0, 4.0, 5.0],
        }
    ),
    
    "conservative": StrategyVariant(
        name="conservative",
        description="Very selective - only best opportunities with profit protection",
        base_params={
            "max_spread_pct": 2.5,
            "max_price_drift_pct": 2.0,
            "max_total_cost_pct": 4.0,
            "min_leader_trade_dollars": 10.0,
            "max_leader_trade_dollars": 100.0,
            "conviction_boost": True,
            "sell_only_profitable": True,  # Only sell at profit
            "min_unrealized_pnl_to_sell": 2.0,  # Need 2% gain to sell
            "block_loss_sells_if_leader_profit": True,
        },
        param_grid={
            "max_spread_pct": [2.0, 2.5, 3.0],
            "min_unrealized_pnl_to_sell": [0.0, 2.0, 5.0],
            "sell_only_profitable": [True, False],
        }
    ),
    
    "buy_focused": StrategyVariant(
        name="buy_focused",
        description="Optimized for buys - your original profitable approach",
        base_params={
            "max_spread_pct": 3.0,
            "max_price_drift_pct": 3.0,
            "max_total_cost_pct": 5.0,
            "min_leader_trade_dollars": 8.0,
            "max_leader_trade_dollars": 300.0,
            "conviction_boost": True,
            "sell_only_profitable": True,  # Don't sell at a loss
            "min_unrealized_pnl_to_sell": 3.0,  # Wait for 3% profit
            "block_loss_sells_if_leader_profit": True,
        },
        param_grid={
            "max_spread_pct": [2.5, 3.0, 4.0],
            "max_price_drift_pct": [2.0, 3.0, 4.0],
            "min_unrealized_pnl_to_sell": [2.0, 3.0, 5.0, 8.0],
        }
    ),
}


class StrategyOptimizer:
    """Runs grid search over strategy parameters."""
    
    def __init__(self, session_path: Path, starting_capital: Decimal = Decimal("100")):
        self.session_path = Path(session_path)
        self.starting_capital = starting_capital
        self.events: List[MarketEvent] = []
        self.final_prices: Dict[str, PriceSnapshot] = {}
        self.session_config: Dict[str, Any] = {}
        self.results: List[OptimizationResult] = []
    
    def load_session(self) -> int:
        """Load session data. Returns event count."""
        if not self.session_path.exists():
            raise FileNotFoundError(f"Session not found: {self.session_path}")
        
        with open(self.session_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.strip())
                except:
                    continue
                
                t = data.get("type")
                if t == "session_start":
                    self.session_config = data.get("config", {})
                elif t == "leader_trade":
                    event = self._parse_event(data)
                    if event:
                        self.events.append(event)
                        self.final_prices[event.prices.token_id] = event.prices
        
        return len(self.events)
    
    def _parse_event(self, data: Dict) -> Optional[MarketEvent]:
        """Parse event from session format."""
        try:
            trade_data = data.get("leader_trade", {})
            price_data = data.get("price_context", {})
            
            side = TradeSide.UP if trade_data.get("side") == "UP" else TradeSide.DOWN
            action = TradeAction.BUY if trade_data.get("action") == "BUY" else TradeAction.SELL
            
            ts = trade_data.get("timestamp", "")
            timestamp = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            
            trade = LeaderTrade(
                timestamp=timestamp,
                market_id=trade_data.get("market_id", ""),
                token_id=trade_data.get("token_id", ""),
                side=side,
                action=action,
                dollars=Decimal(str(trade_data.get("leader_dollars", 0))),
                price=Decimal(str(trade_data.get("leader_price", 0))),
                shares=Decimal(str(trade_data.get("leader_shares", 0))),
                source=trade_data.get("source", "replay"),
            )
            
            prices = PriceSnapshot(
                token_id=price_data.get("token_id", trade.token_id),
                bid=Decimal(price_data["bid"]) if price_data.get("bid") else None,
                ask=Decimal(price_data["ask"]) if price_data.get("ask") else None,
                spread_pct=Decimal(price_data["spread_pct"]) if price_data.get("spread_pct") else None,
            )
            
            return MarketEvent(trade=trade, prices=prices, context=data.get("context", {}))
        except Exception as e:
            logger.debug(f"Parse error: {e}")
            return None
    
    def _run_single_config(self, params: Dict[str, Any], config_name: str) -> OptimizationResult:
        """Run a single parameter configuration."""
        strategy = ConfigurableStrategy(params)
        
        # Build strategy config from session + overrides
        scaling = self.session_config.get("scaling", {})
        mirror = self.session_config.get("mirror_strategy", {})
        
        leader_cap = Decimal(str(scaling.get("leader_estimated_capital", 800)))
        k_factor = Decimal(str(scaling.get("k_factor", 0.85)))
        
        cfg = StrategyConfig(
            starting_capital=self.starting_capital,
            hourly_budget=self.starting_capital * Decimal("0.9"),
            cash_reserve_pct=Decimal(str(mirror.get("cash_reserve_pct", 10))),
            leader_capital=leader_cap,
            k_factor=k_factor,
            per_market_cap_pct=Decimal(str(mirror.get("per_market_cap_pct", 30))),
            per_side_pct=Decimal(str(mirror.get("per_side_pct", 26))),
            global_exposure_pct=Decimal(str(mirror.get("global_exposure_pct", 100))),
            spread_cost_pct=Decimal(str(params.get("spread_cost_pct", 2))),
            slippage_cost_pct=Decimal(str(params.get("slippage_cost_pct", 1))),
            max_total_cost_pct=Decimal(str(params.get("max_total_cost_pct", 8))),
            params=params,
        )
        
        strategy.initialize(cfg)
        
        result = OptimizationResult(config_name=config_name, params=params)
        
        for event in self.events:
            result.events_processed += 1
            decision = strategy.on_event(event)
            
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
                if decision.action == DecisionAction.BUY:
                    result.buys_executed += 1
                    result.buy_dollars += decision.dollars or Decimal("0")
                else:
                    result.sells_executed += 1
                    result.sell_dollars += decision.dollars or Decimal("0")
            else:
                result.skips += 1
                reason = decision.skip_reason or "unknown"
                result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
        
        # Get final state
        state = strategy.get_state()
        result.realized_pnl = Decimal(state.get("realized_pnl", "0"))
        result.win_rate = Decimal(state.get("win_rate", "0"))
        result.profit_factor = Decimal(state.get("profit_factor", "0"))
        result.max_drawdown_pct = Decimal(state.get("max_drawdown", "0"))
        result.skip_reasons = state.get("skip_reasons", {})
        
        # Calculate unrealized PnL from open positions
        unrealized = Decimal("0")
        for tid, pos in strategy.portfolio.get_positions().items():
            if tid in self.final_prices and self.final_prices[tid].bid:
                unrealized += pos.shares * self.final_prices[tid].bid - pos.cost_basis
        result.unrealized_pnl = unrealized
        result.total_pnl = result.realized_pnl + result.unrealized_pnl
        result.final_portfolio_value = self.starting_capital + result.total_pnl
        result.open_positions = len([p for p in strategy.portfolio.get_positions().values() if p.shares > 0])
        result.open_cost_basis = strategy.portfolio.get_total_deployed()
        
        return result
    
    def run_variant(self, variant_name: str, max_configs: int = 100) -> List[OptimizationResult]:
        """Run optimization for a specific strategy variant."""
        if variant_name not in STRATEGY_VARIANTS:
            raise ValueError(f"Unknown variant: {variant_name}. Available: {list(STRATEGY_VARIANTS.keys())}")
        
        variant = STRATEGY_VARIANTS[variant_name]
        
        # Generate all parameter combinations
        param_names = list(variant.param_grid.keys())
        param_values = list(variant.param_grid.values())
        
        configs = []
        for combo in itertools.product(*param_values):
            params = dict(variant.base_params)  # Start with base
            params["name"] = variant_name
            for name, value in zip(param_names, combo):
                params[name] = value
            configs.append(params)
        
        # Limit configs if needed
        if len(configs) > max_configs:
            logger.warning(f"Limiting from {len(configs)} to {max_configs} configs")
            configs = configs[:max_configs]
        
        results = []
        for i, params in enumerate(configs):
            config_name = f"{variant_name}_{i+1}"
            result = self._run_single_config(params, config_name)
            results.append(result)
        
        return sorted(results, key=lambda r: r.total_pnl, reverse=True)
    
    def run_all_variants(self, max_configs_per_variant: int = 50) -> Dict[str, List[OptimizationResult]]:
        """Run optimization for all strategy variants."""
        all_results = {}
        
        for variant_name in STRATEGY_VARIANTS:
            logger.info(f"Optimizing {variant_name}...")
            results = self.run_variant(variant_name, max_configs_per_variant)
            all_results[variant_name] = results
            
            if results:
                best = results[0]
                logger.info(f"  Best {variant_name}: PnL=${best.total_pnl:.2f}, "
                          f"Buys={best.buys_executed}, Sells={best.sells_executed}")
        
        return all_results
    
    def run_custom_grid(self, param_grid: Dict[str, List[Any]], 
                        base_params: Optional[Dict[str, Any]] = None) -> List[OptimizationResult]:
        """Run custom parameter grid search."""
        if base_params is None:
            base_params = {
                "max_spread_pct": 4.0,
                "max_price_drift_pct": 4.0,
                "max_total_cost_pct": 8.0,
                "min_leader_trade_dollars": 5.0,
                "max_leader_trade_dollars": 500.0,
                "conviction_boost": True,
                "sell_only_profitable": False,
                "block_loss_sells_if_leader_profit": True,
                "spread_cost_pct": 2.0,
                "slippage_cost_pct": 1.0,
            }
        
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        
        results = []
        for i, combo in enumerate(itertools.product(*param_values)):
            params = dict(base_params)
            params["name"] = "custom"
            for name, value in zip(param_names, combo):
                params[name] = value
            
            result = self._run_single_config(params, f"custom_{i+1}")
            results.append(result)
        
        return sorted(results, key=lambda r: r.total_pnl, reverse=True)
    
    def print_results(self, results: List[OptimizationResult], top_n: int = 10, show_warnings: bool = True) -> None:
        """Print formatted results with honesty about what metrics mean."""
        print("\n" + "=" * 80)
        print(f"  TOP {min(top_n, len(results))} CONFIGURATIONS")
        print("=" * 80)
        
        for i, r in enumerate(results[:top_n]):
            # Warning flags for suspicious configs
            warnings = []
            if r.win_rate >= Decimal("0.95") and r.sells_executed >= 3:
                warnings.append("[!] HIGH WIN RATE - may be skipping losing sells")
            if r.open_cost_basis > r.sell_dollars:
                warnings.append("[!] MORE STUCK IN POSITIONS than sold")
            if r.unrealized_pnl < 0 and r.realized_pnl > 0:
                warnings.append("[!] UNREALIZED LOSSES - paper gains only")
                
            print(f"\n{i+1}. {r.config_name}")
            if warnings and show_warnings:
                for w in warnings:
                    print(f"   {w}")
            print(f"   Total PnL: ${r.total_pnl:>8.2f} ({r.return_pct:>6.2f}% return)")
            print(f"   Realized:  ${r.realized_pnl:>8.2f}  |  Unrealized: ${r.unrealized_pnl:>8.2f}")
            print(f"   Trades: {r.buys_executed} buys (${r.buy_dollars:.2f}), {r.sells_executed} sells (${r.sell_dollars:.2f})")
            print(f"   Win Rate: {r.win_rate*100:.1f}% (on {r.sells_executed} closed trades)")
            print(f"   Open Positions: {r.open_positions} (${r.open_cost_basis:.2f} stuck)")
            
            # Key parameters
            key_params = ["max_spread_pct", "max_price_drift_pct", "min_leader_trade_dollars", 
                         "sell_only_profitable", "min_unrealized_pnl_to_sell", "block_loss_sells_if_leader_profit"]
            param_str = ", ".join(f"{k}={r.params.get(k)}" for k in key_params if k in r.params)
            print(f"   Params: {param_str}")
        
        print("\n" + "=" * 80)
        
        # Summary stats
        if results:
            profitable = sum(1 for r in results if r.total_pnl > 0)
            avg_pnl = sum(r.total_pnl for r in results) / len(results)
            avg_realized = sum(r.realized_pnl for r in results) / len(results)
            print(f"\nSummary: {profitable}/{len(results)} profitable configs")
            print(f"   Avg Total PnL: ${avg_pnl:.2f}, Avg Realized PnL: ${avg_realized:.2f}")


def run_optimization(session_path: str, starting_capital: float = 100.0, 
                     variant: str = None) -> Dict[str, Any]:
    """
    Main entry point for running optimization.
    
    Args:
        session_path: Path to recorded session JSONL file
        starting_capital: Simulated capital to use
        variant: Specific variant to test, or None for all
        
    Returns:
        Dict with results summary
    """
    optimizer = StrategyOptimizer(
        session_path=Path(session_path),
        starting_capital=Decimal(str(starting_capital))
    )
    
    event_count = optimizer.load_session()
    print(f"\nLoaded {event_count} events from {session_path}")
    print(f"Starting capital: ${starting_capital}")
    
    if variant:
        results = optimizer.run_variant(variant)
        optimizer.print_results(results)
        return {"variant": variant, "results": results}
    else:
        all_results = optimizer.run_all_variants()
        
        # Find overall best
        all_flat = []
        for v, r in all_results.items():
            all_flat.extend(r)
        all_flat.sort(key=lambda x: x.total_pnl, reverse=True)
        
        print("\n" + "=" * 80)
        print("  OVERALL BEST CONFIGURATIONS (All Variants)")
        print("=" * 80)
        optimizer.print_results(all_flat, top_n=15)
        
        return {"all_results": all_results, "overall_best": all_flat[:15]}
