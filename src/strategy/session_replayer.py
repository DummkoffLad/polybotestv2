"""Session Replayer - Replay recorded sessions with any strategy.

Enables:
1. Deterministic replay of recorded sessions
2. Testing different strategies on the same market data
3. Testing different config parameters
4. Instant execution (no waiting for real time)
5. Comparison of strategies

Usage:
    python main.py --replay-session data/sessions/session_XXXXX.jsonl
    python main.py --replay-session data/sessions/session_XXXXX.jsonl --config-override k_factor=0.5
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Protocol

from ..core.types import Side, MarketId
from .delta_state import ShadowPortfolio


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ReplayLeaderTrade:
    """Leader trade event from session recording."""
    timestamp: datetime
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    leader_dollars: Decimal
    leader_price: Decimal
    leader_shares: Decimal
    source: str
    latency_sec: int
    tx_hash: Optional[str] = None


@dataclass
class ReplayPriceContext:
    """Price context at decision time from session recording."""
    token_id: str
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    spread_pct: Optional[Decimal]
    
    def get_price(self, is_sell: bool) -> Optional[Decimal]:
        """Get appropriate price for buy/sell."""
        return self.bid if is_sell else self.ask


@dataclass
class ReplayEvent:
    """Complete event for replay."""
    leader_trade: ReplayLeaderTrade
    price_context: ReplayPriceContext
    original_decision: Dict[str, Any]  # What the original run decided


@dataclass
class ReplayDecision:
    """Strategy's decision on a trade."""
    action: str  # "BUY", "SELL", "SKIP"
    skip_reason: Optional[str] = None
    dollars: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    price: Optional[Decimal] = None


@dataclass
class ReplayResult:
    """Result of replaying a session."""
    session_id: str
    strategy_name: str
    config_overrides: Dict[str, Any]
    
    # Counts
    events_processed: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    skips: int = 0
    
    # Dollars
    buy_dollars: Decimal = Decimal("0")
    sell_dollars: Decimal = Decimal("0")
    
    # PnL
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    
    # Skip reasons breakdown
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    
    # Trade log for detailed analysis
    trade_log: List[Dict[str, Any]] = field(default_factory=list)
    
    # Final positions
    final_positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "strategy_name": self.strategy_name,
            "config_overrides": self.config_overrides,
            "events_processed": self.events_processed,
            "buys_executed": self.buys_executed,
            "sells_executed": self.sells_executed,
            "skips": self.skips,
            "buy_dollars": str(self.buy_dollars),
            "sell_dollars": str(self.sell_dollars),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "total_pnl": str(self.total_pnl),
            "skip_reasons": self.skip_reasons,
            "final_positions": self.final_positions,
        }


# =============================================================================
# STRATEGY INTERFACE
# =============================================================================

class ReplayStrategy(ABC):
    """Abstract base class for replay-compatible strategies.
    
    Any strategy that wants to be replayable must implement this interface.
    The strategy receives leader trades with price context and decides what to do.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name for reporting."""
        pass
    
    @abstractmethod
    def initialize(self, config: Dict[str, Any], starting_capital: Decimal) -> None:
        """Initialize strategy with config and starting capital."""
        pass
    
    @abstractmethod
    def process_trade(
        self,
        trade: ReplayLeaderTrade,
        prices: ReplayPriceContext,
        current_time: datetime,
    ) -> ReplayDecision:
        """Process a leader trade and return decision.
        
        Args:
            trade: Leader's trade details
            prices: Bid/ask prices at decision time
            current_time: Simulated current time
            
        Returns:
            Decision on what action to take
        """
        pass
    
    @abstractmethod
    def apply_fill(
        self,
        trade: ReplayLeaderTrade,
        decision: ReplayDecision,
        current_time: datetime,
    ) -> None:
        """Apply a fill to update internal state (positions, etc)."""
        pass
    
    @abstractmethod
    def get_portfolio_state(self) -> Dict[str, Any]:
        """Get current portfolio state for final reporting."""
        pass
    
    @abstractmethod
    def calculate_pnl(self, final_prices: Dict[str, ReplayPriceContext]) -> Tuple[Decimal, Decimal]:
        """Calculate realized and unrealized PnL.
        
        Args:
            final_prices: Token ID -> price context for final valuation
            
        Returns:
            (realized_pnl, unrealized_pnl)
        """
        pass


# =============================================================================
# MIRROR REPLAY STRATEGY
# =============================================================================

class MirrorReplayStrategy(ReplayStrategy):
    """Replay-compatible version of the mirror strategy.
    
    Implements the same logic as MirrorRunner but uses recorded prices
    instead of live data, and tracks positions locally.
    """
    
    def __init__(self):
        self.shadow = ShadowPortfolio()
        self.config: Dict[str, Any] = {}
        self.starting_capital = Decimal("0")
        self.hourly_budget = Decimal("0")  # Total hourly budget
        self.hourly_budget_used = Decimal("0")
        self.scale_ratio = Decimal("0")
        self.leader_tracker: Dict[str, Dict] = {}  # token_id -> {shares, cost_basis}
        
        # Configurable parameters
        self.k_factor = Decimal("0.85")
        self.cash_reserve_pct = Decimal("10.0")
        self.per_market_cap_pct = Decimal("30.0")
        self.per_side_pct = Decimal("26.0")  # Per-side cap (from config)
        self.global_exposure_pct = Decimal("100.0")  # Global exposure cap
        self.max_buy_price_drift_pct = Decimal("5.0")
        self.max_total_cost_pct = Decimal("8.0")
        self.block_loss_sells_if_leader_profit = True
        
        # Execution cost model
        self.spread_cost_pct = Decimal("2.0")
        self.slippage_cost_pct = Decimal("1.0")
        
    @property
    def name(self) -> str:
        return "mirror"
    
    def initialize(self, config: Dict[str, Any], starting_capital: Decimal) -> None:
        """Initialize with config."""
        self.config = config
        self.starting_capital = starting_capital
        self.hourly_budget_used = Decimal("0")
        
        # Extract parameters from config
        scaling = config.get("scaling", {})
        safety = config.get("safety", {})
        mirror = config.get("mirror_strategy", {})
        simulation = config.get("simulation", {})
        
        self.k_factor = Decimal(str(scaling.get("k_factor", "0.85")))
        leader_capital = Decimal(str(scaling.get("leader_estimated_capital", "800")))
        
        # Extract hourly budget from config (defaults to 100% of capital)
        hourly_budget_str = scaling.get("hourly_budget", str(starting_capital))
        self.hourly_budget = Decimal(str(hourly_budget_str))
        
        # Compute scale ratio
        if leader_capital > Decimal("0"):
            self.scale_ratio = (starting_capital / leader_capital) * self.k_factor
        else:
            self.scale_ratio = Decimal("0.1")
        
        self.cash_reserve_pct = Decimal(str(mirror.get("cash_reserve_pct", "10.0")))
        self.per_market_cap_pct = Decimal(str(mirror.get("per_market_cap_pct", "30.0")))
        self.per_side_pct = Decimal(str(scaling.get("per_side_pct", "26.0")))
        self.global_exposure_pct = Decimal(str(scaling.get("global_exposure_pct", "100.0")))
        self.max_buy_price_drift_pct = Decimal(str(safety.get("max_buy_price_drift_pct", "5.0")))
        self.block_loss_sells_if_leader_profit = safety.get("block_loss_sells_if_leader_profit", True)
        
        self.spread_cost_pct = Decimal("2.0")  # Match original hardcoded value
        self.slippage_cost_pct = Decimal("1.0")  # Match original hardcoded value
        # Match original: MAX_TOTAL_COST_PCT = 8.0%
        self.max_total_cost_pct = Decimal("8.0")
    
    def process_trade(
        self,
        trade: ReplayLeaderTrade,
        prices: ReplayPriceContext,
        current_time: datetime,
    ) -> ReplayDecision:
        """Process a leader trade using mirror strategy logic."""
        
        # Scale leader dollars to our size
        our_dollars = trade.leader_dollars * self.scale_ratio
        
        # Size-proportional boost (matches original mirror_runner.py)
        # Larger leader trades get a boost (more conviction)
        if trade.leader_dollars >= Decimal("10"):
            size_boost = Decimal("1.30")
        elif trade.leader_dollars >= Decimal("5"):
            size_boost = Decimal("1.20")
        elif trade.leader_dollars >= Decimal("2"):
            size_boost = Decimal("1.10")
        else:
            size_boost = Decimal("1.0")
        
        our_dollars = our_dollars * size_boost
        
        if trade.action == "BUY":
            return self._process_buy(trade, prices, our_dollars)
        elif trade.action == "SELL":
            return self._process_sell(trade, prices, our_dollars)
        else:
            return ReplayDecision(action="SKIP", skip_reason="unknown_action")
    
    def _process_buy(
        self,
        trade: ReplayLeaderTrade,
        prices: ReplayPriceContext,
        scaled_dollars: Decimal,
    ) -> ReplayDecision:
        """Process a BUY using mirror strategy logic."""
        
        current_ask = prices.ask
        if not current_ask or current_ask <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="no_price")
        
        # Cost check
        if trade.leader_price and trade.leader_price > Decimal("0"):
            price_drift_pct = ((current_ask - trade.leader_price) / trade.leader_price) * Decimal("100")
            total_cost_pct = price_drift_pct + self.spread_cost_pct + self.slippage_cost_pct
            
            if total_cost_pct > self.max_total_cost_pct:
                return ReplayDecision(action="SKIP", skip_reason="cost_too_high")
        
        # Reserve check
        deployable = self.starting_capital * (Decimal("1") - self.cash_reserve_pct / Decimal("100"))
        currently_deployed = self._get_total_deployed()
        available = deployable - currently_deployed
        
        if available <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="reserve")
        
        dollars = min(scaled_dollars, available)
        
        # Hourly budget check (matches original - skip if exhausted)
        budget_remaining = self.hourly_budget - self.hourly_budget_used
        if budget_remaining <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="budget")
        dollars = min(dollars, budget_remaining)
        
        # Per-market cap
        market_cap = self.starting_capital * self.per_market_cap_pct / Decimal("100")
        market_exposure = self._get_market_exposure(trade.market_id)
        market_room = market_cap - market_exposure
        
        if market_room <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="market_cap")
        
        dollars = min(dollars, market_room)
        
        # Per-side cap
        side = Side.UP if trade.side == "UP" else Side.DOWN
        side_exposure = self._get_side_exposure(trade.market_id, side)
        side_cap = self.starting_capital * self.per_side_pct / Decimal("100")
        side_room = side_cap - side_exposure
        
        if side_room <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="side_cap")
        
        dollars = min(dollars, side_room)
        
        # Global exposure cap
        global_cap = self.starting_capital * self.global_exposure_pct / Decimal("100")
        global_room = global_cap - currently_deployed
        
        if global_room <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="global_cap")
        
        dollars = min(dollars, global_room)
        
        # Minimum order check - includes all constraints in bump check
        if Decimal("0") < dollars < Decimal("1.0"):
            # Try to bump to $1 - must have room in ALL constraints
            if (Decimal("1.0") <= available 
                    and Decimal("1.0") <= market_room 
                    and Decimal("1.0") <= side_room
                    and Decimal("1.0") <= global_room
                    and Decimal("1.0") <= budget_remaining):
                dollars = Decimal("1.0")
            else:
                return ReplayDecision(action="SKIP", skip_reason="min_order")
        
        if dollars <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="min_order")
        
        # Calculate shares
        shares = (dollars / current_ask).quantize(Decimal("0.01"))
        
        return ReplayDecision(
            action="BUY",
            dollars=dollars,
            shares=shares,
            price=current_ask,
        )
    
    def _process_sell(
        self,
        trade: ReplayLeaderTrade,
        prices: ReplayPriceContext,
        scaled_dollars: Decimal,
    ) -> ReplayDecision:
        """Process a SELL using mirror strategy logic."""
        
        side = Side.UP if trade.side == "UP" else Side.DOWN
        pos = self.shadow.get(trade.token_id, trade.market_id, side)
        
        if pos.shares <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="no_position")
        
        current_bid = prices.bid
        if not current_bid or current_bid <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="no_price")
        
        # Loss protection check
        if self.block_loss_sells_if_leader_profit and pos.avg_price > Decimal("0"):
            our_would_profit = current_bid >= pos.avg_price
            
            if not our_would_profit:
                # Check if leader profited
                leader_data = self.leader_tracker.get(trade.token_id)
                if leader_data and leader_data.get("avg_price", Decimal("0")) > Decimal("0"):
                    leader_avg = leader_data["avg_price"]
                    leader_profited = trade.leader_price >= leader_avg
                    
                    if leader_profited:
                        return ReplayDecision(action="SKIP", skip_reason="leader_profit_our_loss")
        
        # Calculate shares to sell
        shares_to_sell = (scaled_dollars / current_bid).quantize(Decimal("0.01"))
        shares_to_sell = min(shares_to_sell, pos.shares)
        
        if shares_to_sell <= Decimal("0"):
            return ReplayDecision(action="SKIP", skip_reason="zero_shares")
        
        dollars = shares_to_sell * current_bid
        
        return ReplayDecision(
            action="SELL",
            dollars=dollars,
            shares=shares_to_sell,
            price=current_bid,
        )
    
    def apply_fill(
        self,
        trade: ReplayLeaderTrade,
        decision: ReplayDecision,
        current_time: datetime,
    ) -> None:
        """Apply fill to shadow portfolio."""
        if decision.action not in ("BUY", "SELL"):
            return
        
        side = Side.UP if trade.side == "UP" else Side.DOWN
        
        self.shadow.apply_fill(
            token_id=trade.token_id,
            market_id=trade.market_id,
            side=side,
            action=decision.action,
            shares=decision.shares or Decimal("0"),
            price=decision.price or Decimal("0"),
            timestamp=current_time,
        )
        
        # Update hourly budget for buys
        if decision.action == "BUY" and decision.dollars:
            self.hourly_budget_used += decision.dollars
        
        # Track leader positions
        if trade.action == "BUY":
            if trade.token_id not in self.leader_tracker:
                self.leader_tracker[trade.token_id] = {"shares": Decimal("0"), "cost_basis": Decimal("0")}
            lt = self.leader_tracker[trade.token_id]
            lt["shares"] += trade.leader_shares
            lt["cost_basis"] += trade.leader_dollars
            if lt["shares"] > Decimal("0"):
                lt["avg_price"] = lt["cost_basis"] / lt["shares"]
        elif trade.action == "SELL":
            lt = self.leader_tracker.get(trade.token_id)
            if lt and lt["shares"] > Decimal("0"):
                sell_ratio = min(trade.leader_shares / lt["shares"], Decimal("1"))
                lt["cost_basis"] -= lt["cost_basis"] * sell_ratio
                lt["shares"] -= trade.leader_shares
    
    def _get_total_deployed(self) -> Decimal:
        """Get total capital currently deployed."""
        total = Decimal("0")
        for pos in self.shadow._positions.values():
            if pos.shares > Decimal("0"):
                total += pos.shares * pos.avg_price
        return total
    
    def _get_market_exposure(self, market_id: str) -> Decimal:
        """Get exposure in a specific market."""
        total = Decimal("0")
        for pos in self.shadow._positions.values():
            if pos.market_id == market_id and pos.shares > Decimal("0"):
                total += pos.shares * pos.avg_price
        return total
    
    def _get_side_exposure(self, market_id: str, side: Side) -> Decimal:
        """Get exposure on a specific side of a market."""
        total = Decimal("0")
        for pos in self.shadow._positions.values():
            if pos.market_id == market_id and pos.side == side and pos.shares > Decimal("0"):
                total += pos.shares * pos.avg_price
        return total
    
    def get_portfolio_state(self) -> Dict[str, Any]:
        """Get current portfolio state."""
        positions = {}
        for token_id, pos in self.shadow._positions.items():
            if pos.shares > Decimal("0"):
                positions[token_id] = {
                    "market_id": pos.market_id,
                    "side": str(pos.side),
                    "shares": str(pos.shares),
                    "avg_price": str(pos.avg_price),
                    "cost_basis": str(pos.notional),
                }
        return {
            "positions": positions,
            "total_deployed": str(self._get_total_deployed()),
        }
    
    def calculate_pnl(self, final_prices: Dict[str, ReplayPriceContext]) -> Tuple[Decimal, Decimal]:
        """Calculate PnL using final prices."""
        realized = Decimal("0")  # Would need to track closed positions
        unrealized = Decimal("0")
        
        for token_id, pos in self.shadow._positions.items():
            if pos.shares > Decimal("0"):
                price_ctx = final_prices.get(token_id)
                if price_ctx and price_ctx.bid:
                    current_value = pos.shares * price_ctx.bid
                    cost = pos.notional
                    unrealized += current_value - cost
        
        return realized, unrealized


# =============================================================================
# SESSION REPLAYER
# =============================================================================

class SessionReplayer:
    """Replays a recorded session with any strategy.
    
    Loads session file, feeds events to strategy in order,
    and produces results for analysis.
    """
    
    def __init__(
        self,
        session_path: Path,
        strategy: ReplayStrategy,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        self.session_path = Path(session_path)
        self.strategy = strategy
        self.config_overrides = config_overrides or {}
        
        # Loaded data
        self.session_id: str = ""
        self.original_config: Dict[str, Any] = {}
        self.events: List[ReplayEvent] = []
        self.original_summary: Dict[str, Any] = {}
        
        # Final prices (from last event's prices)
        self.final_prices: Dict[str, ReplayPriceContext] = {}
    
    def load(self) -> int:
        """Load session file.
        
        Returns:
            Number of events loaded
        """
        if not self.session_path.exists():
            raise FileNotFoundError(f"Session file not found: {self.session_path}")
        
        self.events = []
        
        with open(self.session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                event_type = data.get("type")
                
                if event_type == "session_start":
                    self.session_id = data.get("session_id", "unknown")
                    self.original_config = data.get("config", {})
                
                elif event_type == "leader_trade":
                    event = self._parse_leader_trade(data)
                    if event:
                        self.events.append(event)
                        # Update final prices
                        self.final_prices[event.price_context.token_id] = event.price_context
                
                elif event_type == "session_end":
                    self.original_summary = data.get("summary", {})
        
        return len(self.events)
    
    def _parse_leader_trade(self, data: Dict) -> Optional[ReplayEvent]:
        """Parse a leader_trade record into ReplayEvent."""
        try:
            trade_data = data.get("leader_trade", {})
            price_data = data.get("price_context", {})
            
            # Parse leader trade
            trade = ReplayLeaderTrade(
                timestamp=datetime.fromisoformat(trade_data.get("timestamp", "")),
                market_id=trade_data.get("market_id", ""),
                token_id=trade_data.get("token_id", ""),
                side=trade_data.get("side", ""),
                action=trade_data.get("action", ""),
                leader_dollars=Decimal(str(trade_data.get("leader_dollars", "0"))),
                leader_price=Decimal(str(trade_data.get("leader_price", "0"))),
                leader_shares=Decimal(str(trade_data.get("leader_shares", "0"))),
                source=trade_data.get("source", ""),
                latency_sec=int(trade_data.get("latency_sec", 0)),
                tx_hash=trade_data.get("tx_hash"),
            )
            
            # Parse price context
            bid = Decimal(price_data["bid"]) if price_data.get("bid") else None
            ask = Decimal(price_data["ask"]) if price_data.get("ask") else None
            spread = Decimal(price_data["spread_pct"]) if price_data.get("spread_pct") else None
            
            prices = ReplayPriceContext(
                token_id=price_data.get("token_id", trade.token_id),
                bid=bid,
                ask=ask,
                spread_pct=spread,
            )
            
            return ReplayEvent(
                leader_trade=trade,
                price_context=prices,
                original_decision=data.get("decision", {}),
            )
            
        except Exception as e:
            print(f"Warning: Failed to parse event: {e}")
            return None
    
    def run(self) -> ReplayResult:
        """Run the replay and return results."""
        
        # Merge config with overrides
        config = self._merge_config()
        
        # Get starting capital
        starting_capital = Decimal(str(
            config.get("scaling", {}).get("our_capital", "100")
        ))
        
        # Initialize strategy
        self.strategy.initialize(config, starting_capital)
        
        # Create result tracker
        result = ReplayResult(
            session_id=self.session_id,
            strategy_name=self.strategy.name,
            config_overrides=self.config_overrides,
        )
        
        # Process all events
        for event in self.events:
            result.events_processed += 1
            
            # Get strategy's decision
            decision = self.strategy.process_trade(
                trade=event.leader_trade,
                prices=event.price_context,
                current_time=event.leader_trade.timestamp,
            )
            
            # Apply fill if executed
            if decision.action in ("BUY", "SELL"):
                self.strategy.apply_fill(
                    trade=event.leader_trade,
                    decision=decision,
                    current_time=event.leader_trade.timestamp,
                )
                
                if decision.action == "BUY":
                    result.buys_executed += 1
                    result.buy_dollars += decision.dollars or Decimal("0")
                else:
                    result.sells_executed += 1
                    result.sell_dollars += decision.dollars or Decimal("0")
            else:
                result.skips += 1
                reason = decision.skip_reason or "unknown"
                result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
            
            # Log trade
            result.trade_log.append({
                "timestamp": event.leader_trade.timestamp.isoformat(),
                "leader_action": event.leader_trade.action,
                "leader_dollars": str(event.leader_trade.leader_dollars),
                "our_decision": decision.action,
                "our_dollars": str(decision.dollars) if decision.dollars else None,
                "skip_reason": decision.skip_reason,
            })
        
        # Calculate final PnL
        result.realized_pnl, result.unrealized_pnl = self.strategy.calculate_pnl(
            self.final_prices
        )
        
        # Get final positions
        result.final_positions = self.strategy.get_portfolio_state()
        
        return result
    
    def _merge_config(self) -> Dict[str, Any]:
        """Merge original config with overrides."""
        config = dict(self.original_config)
        
        for key, value in self.config_overrides.items():
            # Handle nested keys like "scaling.k_factor"
            parts = key.split(".")
            target = config
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
        
        return config


# =============================================================================
# COMPARISON UTILITIES
# =============================================================================

def compare_results(original: Dict[str, Any], replay: ReplayResult) -> Dict[str, Any]:
    """Compare original session results with replay results."""
    
    original_buys = original.get("buys_executed", 0)
    original_sells = original.get("sells_executed", 0)
    original_pnl = Decimal(str(original.get("total_pnl", "0")))
    
    return {
        "match": {
            "buys": original_buys == replay.buys_executed,
            "sells": original_sells == replay.sells_executed,
        },
        "diff": {
            "buys": replay.buys_executed - original_buys,
            "sells": replay.sells_executed - original_sells,
            "pnl": str(replay.total_pnl - original_pnl),
        },
        "original": {
            "buys": original_buys,
            "sells": original_sells,
            "buy_dollars": original.get("buys_dollars", "0"),
            "sell_dollars": original.get("sells_dollars", "0"),
            "pnl": str(original_pnl),
        },
        "replay": {
            "buys": replay.buys_executed,
            "sells": replay.sells_executed,
            "buy_dollars": str(replay.buy_dollars),
            "sell_dollars": str(replay.sell_dollars),
            "pnl": str(replay.total_pnl),
        },
    }


def print_replay_result(result: ReplayResult, comparison: Optional[Dict] = None) -> None:
    """Print replay results in a nice format."""
    
    print()
    print("=" * 60)
    print(f"  REPLAY RESULTS - {result.strategy_name.upper()}")
    print("=" * 60)
    print(f"  Session:    {result.session_id}")
    print(f"  Strategy:   {result.strategy_name}")
    if result.config_overrides:
        print(f"  Overrides:  {result.config_overrides}")
    print()
    print(f"  Events processed: {result.events_processed}")
    print()
    print("  TRADES")
    print(f"    Buys:   {result.buys_executed} (${result.buy_dollars:.2f})")
    print(f"    Sells:  {result.sells_executed} (${result.sell_dollars:.2f})")
    print(f"    Skips:  {result.skips}")
    print()
    print("  SKIP REASONS")
    for reason, count in sorted(result.skip_reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")
    print()
    print("  PnL")
    print(f"    Realized:   ${result.realized_pnl:.2f}")
    print(f"    Unrealized: ${result.unrealized_pnl:.2f}")
    print(f"    Total:      ${result.total_pnl:.2f}")
    print()
    
    if comparison:
        print("  COMPARISON WITH ORIGINAL")
        print(f"    Buys:  {comparison['original']['buys']} -> {comparison['replay']['buys']} (diff: {comparison['diff']['buys']:+d})")
        print(f"    Sells: {comparison['original']['sells']} -> {comparison['replay']['sells']} (diff: {comparison['diff']['sells']:+d})")
        print(f"    PnL:   {comparison['original']['pnl']} -> {comparison['replay']['pnl']} (diff: {comparison['diff']['pnl']})")
        print()
    
    print("=" * 60)


def run_replay(
    session_path: Path,
    strategy: Optional[ReplayStrategy] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> ReplayResult:
    """Convenience function to run a replay.
    
    Args:
        session_path: Path to session file
        strategy: Strategy to use (defaults to MirrorReplayStrategy)
        config_overrides: Optional config parameter overrides
        
    Returns:
        ReplayResult
    """
    if strategy is None:
        strategy = MirrorReplayStrategy()
    
    replayer = SessionReplayer(
        session_path=session_path,
        strategy=strategy,
        config_overrides=config_overrides,
    )
    
    event_count = replayer.load()
    print(f"Loaded {event_count} events from {session_path}")
    
    result = replayer.run()
    
    # Compare with original if available
    comparison = None
    if replayer.original_summary:
        comparison = compare_results(replayer.original_summary, result)
    
    print_replay_result(result, comparison)
    
    return result


def run_session_replay(
    session_path: Path,
    strategy_name: Optional[str] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """CLI entry point for session replay.
    
    This is the main function called from main.py when using --replay-session.
    
    Args:
        session_path: Path to session file (JSONL)
        strategy_name: Name of strategy to use ('mirror' or future strategies)
        config_overrides: Optional config parameter overrides
    """
    from pathlib import Path as PathLib
    
    # Ensure path is a Path object
    if not isinstance(session_path, PathLib):
        session_path = PathLib(session_path)
    
    # Check file exists
    if not session_path.exists():
        print(f"Error: Session file not found: {session_path}")
        return
    
    # Select strategy (for now, only mirror is implemented)
    if strategy_name is None or strategy_name == "mirror":
        strategy = MirrorReplayStrategy()
    else:
        print(f"Error: Unknown strategy '{strategy_name}'. Available: mirror")
        return
    
    print()
    print("=" * 60)
    print("  SESSION REPLAYER")
    print("=" * 60)
    print(f"  Session file: {session_path}")
    print(f"  Strategy:     {strategy.name}")
    if config_overrides:
        print(f"  Overrides:    {config_overrides}")
    print()
    
    # Run the replay
    try:
        run_replay(session_path, strategy, config_overrides)
    except Exception as e:
        print(f"Error during replay: {e}")
        import traceback
        traceback.print_exc()
