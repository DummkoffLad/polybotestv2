"""Core domain types for the copy-trading bot.

This module contains all fundamental data structures used throughout the system.
These types are pure data classes with no external dependencies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, List, Dict, Any


class ExecutionMode(Enum):
    """Execution mode - determines which adapter is used.
    
    CRITICAL: This is locked at startup and cannot be changed without restart.
    """
    DRY_RUN = "DRY_RUN"   # Real data, no orders (NullExecutionAdapter)
    LIVE = "LIVE"         # Real orders (LiveExecutionAdapter)
    
    def __str__(self) -> str:
        return self.value


class MarketPhase(Enum):
    """Per-market state machine phase."""
    BURST = "BURST"       # First N seconds after market open
    FOLLOW = "FOLLOW"     # Normal operation - copy leader trades
    RESYNC = "RESYNC"     # Periodic drift correction
    CLOSED = "CLOSED"     # Market is closed
    UNKNOWN = "UNKNOWN"   # Cannot determine phase
    
    def __str__(self) -> str:
        return self.value


class Side(Enum):
    """Position/order side."""
    UP = "UP"     # Betting price goes up
    DOWN = "DOWN" # Betting price goes down
    
    def __str__(self) -> str:
        return self.value
    
    @property
    def opposite(self) -> Side:
        return Side.DOWN if self == Side.UP else Side.UP


class OrderType(Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    
    def __str__(self) -> str:
        return self.value


class OrderStatus(Enum):
    """Order fill status."""
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    
    def __str__(self) -> str:
        return self.value


# Type alias for market identifiers
MarketId = str


@dataclass(frozen=True)
class Position:
    """A position in a single side of a market."""
    market_id: MarketId
    side: Side
    shares: Decimal
    dollars: Decimal  # Current value in dollars
    avg_price: Optional[Decimal] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "side": str(self.side),
            "shares": str(self.shares),
            "dollars": str(self.dollars),
            "avg_price": str(self.avg_price) if self.avg_price else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Position:
        return cls(
            market_id=data["market_id"],
            side=Side(data["side"]),
            shares=Decimal(data["shares"]),
            dollars=Decimal(data["dollars"]),
            avg_price=Decimal(data["avg_price"]) if data.get("avg_price") else None,
        )


@dataclass(frozen=True)
class Exposure:
    """Exposure summary for a market (UP and DOWN combined)."""
    market_id: MarketId
    up_shares: Decimal = Decimal("0")
    up_dollars: Decimal = Decimal("0")
    down_shares: Decimal = Decimal("0")
    down_dollars: Decimal = Decimal("0")
    
    @property
    def gross_shares(self) -> Decimal:
        """Total shares (UP + DOWN)."""
        return self.up_shares + self.down_shares
    
    @property
    def gross_dollars(self) -> Decimal:
        """Total dollar exposure (UP + DOWN)."""
        return self.up_dollars + self.down_dollars
    
    @property
    def net_shares(self) -> Decimal:
        """Net shares (UP - DOWN)."""
        return self.up_shares - self.down_shares
    
    @property
    def net_dollars(self) -> Decimal:
        """Net dollar exposure (UP - DOWN)."""
        return self.up_dollars - self.down_dollars
    
    def get_side_dollars(self, side: Side) -> Decimal:
        return self.up_dollars if side == Side.UP else self.down_dollars
    
    def get_side_shares(self, side: Side) -> Decimal:
        return self.up_shares if side == Side.UP else self.down_shares
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "up_shares": str(self.up_shares),
            "up_dollars": str(self.up_dollars),
            "down_shares": str(self.down_shares),
            "down_dollars": str(self.down_dollars),
            "gross_dollars": str(self.gross_dollars),
            "net_dollars": str(self.net_dollars),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Exposure:
        return cls(
            market_id=data["market_id"],
            up_shares=Decimal(data.get("up_shares", "0")),
            up_dollars=Decimal(data.get("up_dollars", "0")),
            down_shares=Decimal(data.get("down_shares", "0")),
            down_dollars=Decimal(data.get("down_dollars", "0")),
        )


@dataclass
class LeaderSnapshot:
    """Point-in-time snapshot of leader state."""
    timestamp: datetime
    exposures: Dict[MarketId, Exposure] = field(default_factory=dict)
    total_assets: Decimal = Decimal("0")  # Estimated total (proxy for capital)
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "exposures": {k: v.to_dict() for k, v in self.exposures.items()},
            "total_assets": str(self.total_assets),
            "recent_trades": self.recent_trades,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LeaderSnapshot:
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            exposures={k: Exposure.from_dict(v) for k, v in data.get("exposures", {}).items()},
            total_assets=Decimal(data.get("total_assets", "0")),
            recent_trades=data.get("recent_trades", []),
        )


@dataclass
class MySnapshot:
    """Point-in-time snapshot of our state."""
    timestamp: datetime
    exposures: Dict[MarketId, Exposure] = field(default_factory=dict)
    available_capital: Decimal = Decimal("0")
    used_capital: Decimal = Decimal("0")
    pending_orders: List[str] = field(default_factory=list)  # Order IDs
    hourly_budget_used: Decimal = Decimal("0")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "exposures": {k: v.to_dict() for k, v in self.exposures.items()},
            "available_capital": str(self.available_capital),
            "used_capital": str(self.used_capital),
            "pending_orders": self.pending_orders,
            "hourly_budget_used": str(self.hourly_budget_used),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MySnapshot:
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            exposures={k: Exposure.from_dict(v) for k, v in data.get("exposures", {}).items()},
            available_capital=Decimal(data.get("available_capital", "0")),
            used_capital=Decimal(data.get("used_capital", "0")),
            pending_orders=data.get("pending_orders", []),
            hourly_budget_used=Decimal(data.get("hourly_budget_used", "0")),
        )


@dataclass
class OrderRequest:
    """Request to place an order."""
    market_id: MarketId
    side: Side
    order_type: OrderType
    shares: Optional[Decimal] = None   # For limit orders
    dollars: Optional[Decimal] = None  # For market orders
    limit_price: Optional[Decimal] = None
    correlation_id: str = ""  # For tracing
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "side": str(self.side),
            "order_type": str(self.order_type),
            "shares": str(self.shares) if self.shares else None,
            "dollars": str(self.dollars) if self.dollars else None,
            "limit_price": str(self.limit_price) if self.limit_price else None,
            "correlation_id": self.correlation_id,
        }


@dataclass
class OrderResponse:
    """Response from order placement."""
    success: bool
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.UNKNOWN
    filled_shares: Decimal = Decimal("0")
    filled_dollars: Decimal = Decimal("0")
    error_message: Optional[str] = None
    correlation_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "status": str(self.status),
            "filled_shares": str(self.filled_shares),
            "filled_dollars": str(self.filled_dollars),
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
        }


@dataclass
class DecisionTarget:
    """Computed target for a market."""
    market_id: MarketId
    target_up_dollars: Decimal = Decimal("0")
    target_down_dollars: Decimal = Decimal("0")
    current_up_dollars: Decimal = Decimal("0")
    current_down_dollars: Decimal = Decimal("0")
    
    @property
    def delta_up(self) -> Decimal:
        return self.target_up_dollars - self.current_up_dollars
    
    @property
    def delta_down(self) -> Decimal:
        return self.target_down_dollars - self.current_down_dollars
    
    @property
    def target_gross(self) -> Decimal:
        return self.target_up_dollars + self.target_down_dollars
    
    @property
    def current_gross(self) -> Decimal:
        return self.current_up_dollars + self.current_down_dollars
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "target_up_dollars": str(self.target_up_dollars),
            "target_down_dollars": str(self.target_down_dollars),
            "current_up_dollars": str(self.current_up_dollars),
            "current_down_dollars": str(self.current_down_dollars),
            "delta_up": str(self.delta_up),
            "delta_down": str(self.delta_down),
        }


@dataclass
class Decision:
    """A decision made by the strategy for one cycle.
    
    This is the core unit for decision tracing and verification.
    """
    timestamp: datetime
    correlation_id: str
    mode: ExecutionMode
    market_id: MarketId
    phase: MarketPhase
    
    # Inputs
    leader_snapshot: LeaderSnapshot
    my_snapshot: MySnapshot
    config_hash: str  # Hash of relevant config for reproducibility
    
    # Computed
    target: DecisionTarget
    
    # Caps applied
    caps_applied: Dict[str, Any] = field(default_factory=dict)
    
    # Minimum checks
    min_checks: Dict[str, Any] = field(default_factory=dict)
    
    # Action
    action: Optional[OrderRequest] = None
    action_reason: str = ""
    
    # Result (filled after execution)
    result: Optional[OrderResponse] = None
    
    def compute_hash(self) -> str:
        """Compute deterministic hash for this decision.
        
        Hash is based on: config + leader snapshot + my snapshot
        This allows comparing decisions across runs.
        """
        hash_input = {
            "config_hash": self.config_hash,
            "market_id": self.market_id,
            "phase": str(self.phase),
            "leader_snapshot": self.leader_snapshot.to_dict(),
            "my_snapshot": self.my_snapshot.to_dict(),
        }
        hash_str = json.dumps(hash_input, sort_keys=True, default=str)
        return hashlib.sha256(hash_str.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "mode": str(self.mode),
            "market_id": self.market_id,
            "phase": str(self.phase),
            "leader_snapshot": self.leader_snapshot.to_dict(),
            "my_snapshot": self.my_snapshot.to_dict(),
            "config_hash": self.config_hash,
            "target": self.target.to_dict(),
            "caps_applied": self.caps_applied,
            "min_checks": self.min_checks,
            "action": self.action.to_dict() if self.action else None,
            "action_reason": self.action_reason,
            "result": self.result.to_dict() if self.result else None,
            "decision_hash": self.compute_hash(),
        }
    
    def to_trace_line(self) -> str:
        """Convert to JSONL trace line."""
        return json.dumps(self.to_dict(), default=str)


@dataclass
class MarketMetadata:
    """Metadata about a market."""
    market_id: MarketId
    name: str
    market_type: str  # e.g., "hourly_btc"
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "name": self.name,
            "market_type": self.market_type,
            "open_time": self.open_time.isoformat() if self.open_time else None,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "is_active": self.is_active,
        }


@dataclass
class PriceSnapshot:
    """Price data at a point in time."""
    timestamp: datetime
    market_id: MarketId
    side: Side
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    last_price: Optional[Decimal] = None
    midpoint: Optional[Decimal] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "market_id": self.market_id,
            "side": str(self.side),
            "best_bid": str(self.best_bid) if self.best_bid else None,
            "best_ask": str(self.best_ask) if self.best_ask else None,
            "last_price": str(self.last_price) if self.last_price else None,
            "midpoint": str(self.midpoint) if self.midpoint else None,
        }
