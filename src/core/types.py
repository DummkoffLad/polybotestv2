"""Core types - shared definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

# Type aliases
MarketId = str


class ExecutionMode(Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class Side(Enum):
    UP = "UP"
    DOWN = "DOWN"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    SIMULATED = "SIMULATED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Exposure:
    """Exposure in a market (both sides)."""
    market_id: MarketId
    up_shares: Decimal = Decimal("0")
    up_dollars: Decimal = Decimal("0")
    down_shares: Decimal = Decimal("0")
    down_dollars: Decimal = Decimal("0")
    
    @property
    def total_dollars(self) -> Decimal:
        return self.up_dollars + self.down_dollars


@dataclass
class LeaderSnapshot:
    """Snapshot of leader's state."""
    timestamp: datetime
    exposures: Dict[MarketId, Exposure]
    total_assets: Decimal
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MySnapshot:
    """Snapshot of our state."""
    timestamp: datetime
    exposures: Dict[MarketId, Exposure]
    available_capital: Decimal
    used_capital: Decimal
    pending_orders: List[str] = field(default_factory=list)
    hourly_budget_used: Decimal = Decimal("0")


@dataclass
class OrderRequest:
    """Order request sent to execution adapter."""
    market_id: str
    token_id: str
    side: Side
    action: str  # "BUY" or "SELL"
    order_type: OrderType
    shares: Decimal
    price: Decimal
    amount_dollars: Decimal
    correlation_id: Optional[str] = None  # For order tracing


@dataclass
class OrderResponse:
    """Response from execution adapter."""
    order_id: str
    status: OrderStatus
    filled_shares: Decimal = Decimal("0")
    filled_price: Decimal = Decimal("0")
    error: Optional[str] = None
    correlation_id: Optional[str] = None  # Matches request correlation_id
