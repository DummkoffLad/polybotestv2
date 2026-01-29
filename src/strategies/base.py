"""Base strategy interface.

All trading strategies must implement this interface to work with
the universal runner and replayer.

Key Design:
- Strategies receive normalized MarketEvent objects
- Strategies return TradeDecision objects  
- Framework handles execution, recording, replay
- Strategies manage their own internal state (positions, caps, etc.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# NORMALIZED EVENT MODEL
# =============================================================================

class TradeAction(Enum):
    """Leader trade action."""
    BUY = "BUY"
    SELL = "SELL"


class TradeSide(Enum):
    """Market side (UP/DOWN for binary markets)."""
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class LeaderTrade:
    """Normalized leader trade - the primary signal.
    
    This is what the leader did. Strategies decide how to respond.
    """
    timestamp: datetime
    market_id: str
    token_id: str
    side: TradeSide
    action: TradeAction
    dollars: Decimal      # USD value of trade
    price: Decimal        # Fill price (0-1 for binary)
    shares: Decimal       # Number of shares
    source: str           # "blockchain", "websocket", "api"
    tx_hash: Optional[str] = None
    latency_ms: int = 0   # Detection latency in ms


@dataclass(frozen=True)
class PriceSnapshot:
    """Current market prices at decision time."""
    token_id: str
    bid: Optional[Decimal]      # Best bid (what we would get selling)
    ask: Optional[Decimal]      # Best ask (what we would pay buying)
    mid: Optional[Decimal] = None  # Mid price
    spread_pct: Optional[Decimal] = None  # Spread as percentage
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        # Calculate mid if not provided
        if self.mid is None and self.bid and self.ask:
            object.__setattr__(self, "mid", (self.bid + self.ask) / 2)
    
    def get_price(self, is_sell: bool) -> Optional[Decimal]:
        """Get appropriate price for buy/sell."""
        return self.bid if is_sell else self.ask


@dataclass(frozen=True)
class MarketEvent:
    """Complete event for strategy processing.
    
    Contains everything a strategy needs to make a decision:
    - What the leader did (trade)
    - Current market prices (prices)
    - Optional context (for advanced strategies)
    """
    trade: LeaderTrade
    prices: PriceSnapshot
    context: Dict[str, Any] = field(default_factory=dict)
    
    # Sequence number for ordering
    sequence: int = 0


# =============================================================================
# STRATEGY DECISION MODEL
# =============================================================================

class DecisionAction(Enum):
    """Our decision on what to do."""
    BUY = "BUY"
    SELL = "SELL"
    SKIP = "SKIP"


@dataclass
class TradeDecision:
    """Strategy decision on a market event.
    
    If action is BUY/SELL, dollars and shares must be set.
    If action is SKIP, skip_reason should explain why.
    """
    action: DecisionAction
    
    # For BUY/SELL
    dollars: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    price: Optional[Decimal] = None  # Expected execution price
    
    # For SKIP
    skip_reason: Optional[str] = None
    
    # Optional metadata for logging/debugging
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def buy(cls, dollars: Decimal, shares: Decimal, price: Decimal, **meta) -> "TradeDecision":
        return cls(DecisionAction.BUY, dollars, shares, price, metadata=meta)
    
    @classmethod
    def sell(cls, dollars: Decimal, shares: Decimal, price: Decimal, **meta) -> "TradeDecision":
        return cls(DecisionAction.SELL, dollars, shares, price, metadata=meta)
    
    @classmethod
    def skip(cls, reason: str, **meta) -> "TradeDecision":
        return cls(DecisionAction.SKIP, skip_reason=reason, metadata=meta)


# =============================================================================
# STRATEGY INTERFACE
# =============================================================================

@dataclass
class StrategyConfig:
    """Configuration for a strategy.
    
    Strategies can define their own config schema.
    This base contains common parameters.
    """
    # Capital management
    starting_capital: Decimal = Decimal("100")
    hourly_budget: Decimal = Decimal("100")
    cash_reserve_pct: Decimal = Decimal("10")
    
    # Scaling
    leader_capital: Decimal = Decimal("800")
    k_factor: Decimal = Decimal("0.85")
    
    # Caps
    per_market_cap_pct: Decimal = Decimal("30")
    per_side_pct: Decimal = Decimal("26")
    global_exposure_pct: Decimal = Decimal("100")
    
    # Execution costs (for simulation)
    spread_cost_pct: Decimal = Decimal("2.0")
    slippage_cost_pct: Decimal = Decimal("1.0")
    max_total_cost_pct: Decimal = Decimal("8.0")
    
    # Strategy-specific overrides
    params: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyConfig":
        """Create from config dictionary."""
        scaling = data.get("scaling", {})
        mirror = data.get("mirror_strategy", {})
        
        return cls(
            starting_capital=Decimal(str(scaling.get("our_capital", "100"))),
            hourly_budget=Decimal(str(scaling.get("hourly_budget", "100"))),
            cash_reserve_pct=Decimal(str(mirror.get("cash_reserve_pct", "10"))),
            leader_capital=Decimal(str(scaling.get("leader_estimated_capital", "800"))),
            k_factor=Decimal(str(scaling.get("k_factor", "0.85"))),
            per_market_cap_pct=Decimal(str(mirror.get("per_market_cap_pct", "30"))),
            per_side_pct=Decimal(str(scaling.get("per_side_pct", "26"))),
            global_exposure_pct=Decimal(str(scaling.get("global_exposure_pct", "100"))),
            params=data,
        )


class Strategy(ABC):
    """Abstract base class for all trading strategies.
    
    Implement this interface to create a new strategy that works with:
    - Live runner (real-time trading)
    - Session recorder (saving events for later)
    - Session replayer (testing/backtesting)
    
    Key Methods:
    - initialize(): Set up with config
    - on_event(): Process a market event and return decision
    - on_fill(): Update internal state after execution
    - get_state(): Return current state for reporting
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name (e.g., mirror, delta)."""
        pass
    
    @abstractmethod
    def initialize(self, config: StrategyConfig) -> None:
        """Initialize strategy with configuration.
        
        Called once before processing any events.
        Set up internal state, caps, trackers, etc.
        """
        pass
    
    @abstractmethod
    def on_event(self, event: MarketEvent) -> TradeDecision:
        """Process a market event and return trading decision.
        
        This is the core strategy logic. Receives normalized event,
        returns decision on what to do.
        
        Args:
            event: Normalized market event (leader trade + prices)
            
        Returns:
            TradeDecision (BUY/SELL/SKIP with details)
        """
        pass
    
    @abstractmethod
    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        """Called after a trade is executed.
        
        Update internal state (positions, budget used, etc.)
        Only called if decision.action is BUY or SELL.
        
        Args:
            event: The original event
            decision: The decision that was executed
        """
        pass
    
    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return current strategy state for reporting.
        
        Used for:
        - Session summary
        - Debugging
        - Position display
        """
        pass
    
    def on_session_start(self) -> None:
        """Called when a trading session starts.
        
        Optional hook for session-level initialization.
        """
        pass
    
    def on_session_end(self) -> Dict[str, Any]:
        """Called when a trading session ends.
        
        Optional hook for cleanup and final statistics.
        Returns summary data for logging.
        """
        return {}
    
    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        """Calculate realized and unrealized PnL.
        
        Args:
            final_prices: Current prices for open positions
            
        Returns:
            (realized_pnl, unrealized_pnl)
        """
        return Decimal("0"), Decimal("0")


# =============================================================================
# STRATEGY REGISTRY
# =============================================================================

_STRATEGIES: Dict[str, type] = {}


def register_strategy(cls: type) -> type:
    """Decorator to register a strategy class.
    
    Usage:
        @register_strategy
        class MyStrategy(Strategy):
            ...
    """
    if not issubclass(cls, Strategy):
        raise TypeError(f"{cls} is not a Strategy subclass")
    
    # Get name from instance
    instance = cls()
    _STRATEGIES[instance.name] = cls
    return cls


def get_strategy(name: str) -> Strategy:
    """Get a strategy instance by name."""
    if name not in _STRATEGIES:
        available = ", ".join(_STRATEGIES.keys())
        raise ValueError(f"Unknown strategy: {name}. Available: {available}")
    return _STRATEGIES[name]()


def list_strategies() -> List[str]:
    """List all registered strategy names."""
    return list(_STRATEGIES.keys())
