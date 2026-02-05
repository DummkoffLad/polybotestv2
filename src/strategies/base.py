"""Base strategy interface."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..data.models import PriceSnapshot as PriceSnapshotType

from ..core.types import OrderType
from ..data.models import TradeAction, TradeSide, LeaderTrade, PriceSnapshot, MarketEvent

logger = logging.getLogger(__name__)


def calculate_actual_spread_pct(prices: "PriceSnapshotType") -> Decimal:
    """Calculate actual spread percentage from bid/ask prices.

    Returns spread as percentage: (ask - bid) / mid * 100
    Falls back to 0 if prices unavailable (let other checks handle it).
    """
    if not prices.bid or not prices.ask or prices.bid <= 0 or prices.ask <= 0:
        return Decimal("0")
    mid = (prices.bid + prices.ask) / 2
    if mid <= 0:
        return Decimal("0")
    return ((prices.ask - prices.bid) / mid) * 100

# ============================================================================
# POLYMARKET ORDER CONSTRAINTS
# ============================================================================
# Market orders: minimum $1
# Limit orders: minimum 5 shares
# Price extremes: 0.99 = auto-sell, 0.01 = treat as 0
MIN_MARKET_ORDER_DOLLARS = Decimal("1.00")
MIN_LIMIT_ORDER_SHARES = Decimal("5.0")
PRICE_EXTREME_HIGH = Decimal("0.99")  # Auto-sell threshold
PRICE_EXTREME_LOW = Decimal("0.01")   # Treat as zero


class DecisionAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    SKIP = "SKIP"


@dataclass
class TradeDecision:
    action: DecisionAction
    dollars: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    price: Optional[Decimal] = None
    skip_reason: Optional[str] = None
    order_type: OrderType = OrderType.LIMIT  # Default to limit (5 share min)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def buy(cls, dollars: Decimal, shares: Decimal, price: Decimal, 
            order_type: OrderType = OrderType.LIMIT, **meta):
        return cls(DecisionAction.BUY, dollars, shares, price, 
                   order_type=order_type, metadata=meta)
    
    @classmethod
    def sell(cls, dollars: Decimal, shares: Decimal, price: Decimal,
             order_type: OrderType = OrderType.LIMIT, **meta):
        return cls(DecisionAction.SELL, dollars, shares, price,
                   order_type=order_type, metadata=meta)
    
    @classmethod
    def skip(cls, reason: str, **meta):
        return cls(DecisionAction.SKIP, skip_reason=reason, metadata=meta)
    
    def validate_order_constraints(self) -> Tuple[bool, Optional[str]]:
        """Validate order meets Polymarket minimum constraints.
        
        Returns (is_valid, error_message).
        Market orders: min $1
        Limit orders: min 5 shares
        """
        if self.action == DecisionAction.SKIP:
            return True, None
        
        if self.order_type == OrderType.MARKET:
            if self.dollars is None or self.dollars < MIN_MARKET_ORDER_DOLLARS:
                return False, f"Market order below ${MIN_MARKET_ORDER_DOLLARS} minimum: ${self.dollars}"
        else:  # LIMIT
            if self.shares is None or self.shares < MIN_LIMIT_ORDER_SHARES:
                return False, f"Limit order below {MIN_LIMIT_ORDER_SHARES} share minimum: {self.shares} shares"
        
        return True, None


@dataclass
class StrategyConfig:
    starting_capital: Decimal = Decimal("100")
    hourly_budget: Decimal = Decimal("100")
    cash_reserve_pct: Decimal = Decimal("10")
    leader_capital: Decimal = Decimal("900")
    k_factor: Decimal = Decimal("0.85")
    per_market_cap_pct: Decimal = Decimal("30")
    per_side_pct: Decimal = Decimal("26")
    global_exposure_pct: Decimal = Decimal("100")
    spread_cost_pct: Decimal = Decimal("2.0")
    slippage_cost_pct: Decimal = Decimal("1.0")
    max_total_cost_pct: Decimal = Decimal("8.0")
    params: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyConfig":
        scaling = data.get("scaling", {})
        mirror = data.get("mirror_strategy", {})
        simulation = data.get("simulation", {})
        return cls(
            starting_capital=Decimal(str(scaling.get("our_capital", "100"))),
            hourly_budget=Decimal(str(scaling.get("hourly_budget", "100"))),
            cash_reserve_pct=Decimal(str(mirror.get("cash_reserve_pct", "10"))),
            leader_capital=Decimal(str(scaling.get("leader_estimated_capital", "900"))),
            k_factor=Decimal(str(scaling.get("k_factor", "0.85"))),
            per_market_cap_pct=Decimal(str(mirror.get("per_market_cap_pct", "30"))),
            per_side_pct=Decimal(str(scaling.get("per_side_pct", "26"))),
            global_exposure_pct=Decimal(str(scaling.get("global_exposure_pct", "100"))),
            spread_cost_pct=Decimal(str(simulation.get("spread_cost_pct", mirror.get("spread_cost_pct", "2.0")))),
            slippage_cost_pct=Decimal(str(simulation.get("slippage_cost_pct", mirror.get("slippage_cost_pct", "1.0")))),
            max_total_cost_pct=Decimal(str(simulation.get("max_total_cost_pct", mirror.get("max_total_cost_pct", "8.0")))),
            params=data,
        )


class Strategy(ABC):
    """Abstract base for all strategies."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @abstractmethod
    def initialize(self, config: StrategyConfig) -> None:
        pass
    
    @abstractmethod
    def on_event(self, event: MarketEvent) -> TradeDecision:
        pass
    
    @abstractmethod
    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        pass
    
    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        pass
    
    def on_session_start(self) -> None:
        pass
    
    def on_session_end(self) -> Dict[str, Any]:
        return {}
    
    def calculate_pnl(self, final_prices: Dict[str, PriceSnapshot]) -> Tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")


_STRATEGIES: Dict[str, type] = {}


def register_strategy(cls: type) -> type:
    instance = cls()
    _STRATEGIES[instance.name] = cls
    return cls


def get_strategy(name: str) -> Strategy:
    if name not in _STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}")
    return _STRATEGIES[name]()


def list_strategies() -> List[str]:
    return list(_STRATEGIES.keys())