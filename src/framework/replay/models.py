"""Data models for session replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ...data.models import PriceSnapshot


@dataclass
class ExecutedTrade:
    """Record of a trade executed during replay."""
    sequence: int
    timestamp: datetime
    action: str  # "BUY" or "SELL"
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    leader_dollars: Decimal
    leader_price: Decimal
    our_dollars: Decimal
    our_shares: Decimal
    our_price: Decimal  # Price we executed at (ask for buy, bid for sell)


@dataclass
class ReplayResult:
    """Result of replaying a session."""
    session_id: str
    strategy_name: str
    events_processed: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    skips: int = 0
    buy_dollars: Decimal = Decimal("0")
    sell_dollars: Decimal = Decimal("0")
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    events_dropped_no_prices: int = 0
    events_dropped_duplicates: int = 0  # Duplicate events filtered
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    open_cost_basis: Decimal = Decimal("0")
    # Market resolution simulation (LOW→0, HIGH→0.99)
    resolved_pnl: Decimal = Decimal("0")  # PnL if markets resolve at extremes
    win_count: int = 0  # Trades that won
    loss_count: int = 0  # Trades that lost
    # Detailed trade history
    trades: List["ExecutedTrade"] = field(default_factory=list)
    # Session timing
    session_start: Optional[datetime] = None
    session_end: Optional[datetime] = None
    session_duration_minutes: float = 0.0
    # Follow quality metrics
    follow_metrics: Optional[Dict[str, Any]] = None
    # Performance analysis (optional, populated when track_analysis=True)
    analysis: Optional[Dict[str, Any]] = None


@dataclass
class TimedPriceSnapshot:
    """A price snapshot with timestamp for chronological ordering."""
    timestamp: datetime
    prices: Dict[str, PriceSnapshot]  # token_id -> PriceSnapshot
