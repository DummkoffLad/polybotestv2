"""Session Recorder for DRY_RUN replay and strategy testing.

Records everything needed to exactly reproduce bot behavior:
- Leader trades (all, including ones we skip)
- Market prices at decision time (bid/ask/spread)
- Our decisions (execute or skip + reason)
- Our position state (shares, avg entry, unrealized PnL)
- Final session summary (trades made, PnL, skip stats)

Output: JSONL file in data/sessions/ for later replay/analysis.
Usage: python main.py --mode dry-run --record
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO


@dataclass
class LeaderTradeEvent:
    """A leader trade as detected by our system."""
    timestamp: str  # ISO format
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    leader_dollars: str  # Decimal as string
    leader_price: str  # Decimal as string
    leader_shares: str  # Decimal as string
    source: str  # "blockchain" or "data_api"
    latency_sec: int  # Seconds since trade occurred
    tx_hash: Optional[str] = None


@dataclass
class PriceContext:
    """Market prices at the moment of our decision."""
    token_id: str
    bid: Optional[str]  # Best bid (sell price)
    ask: Optional[str]  # Best ask (buy price)
    spread_pct: Optional[str]  # Spread as percentage
    price_source: str  # "websocket" or "http"
    price_age_sec: Optional[float] = None


@dataclass  
class PositionState:
    """Our position state at decision time."""
    token_id: str
    market_id: str
    side: str
    shares_held: str  # Decimal as string
    avg_entry_price: str  # Decimal as string
    cost_basis: str  # Total dollars invested
    current_value: str  # shares * current_price
    unrealized_pnl: str  # current_value - cost_basis
    unrealized_pnl_pct: str  # as percentage


@dataclass
class Decision:
    """Our decision on this trade."""
    action: str  # "BUY", "SELL", "SKIP"
    skip_reason: Optional[str] = None  # If skipped, why
    our_dollars: Optional[str] = None  # Amount we would trade
    our_shares: Optional[str] = None  # Shares we would trade
    scale_ratio: Optional[str] = None  # Our scale vs leader
    
    # Cost analysis (for BUYs)
    price_drift_pct: Optional[str] = None  # How much price moved since leader
    estimated_total_cost_pct: Optional[str] = None  # Drift + spread + slippage
    
    # Profit analysis (for SELLs)
    our_would_profit: Optional[bool] = None
    our_pnl_pct: Optional[str] = None
    leader_profited: Optional[bool] = None
    leader_pnl_pct: Optional[str] = None


@dataclass
class TradeRecord:
    """Complete record of a leader trade and our response."""
    type: str = "leader_trade"
    timestamp: str = ""
    leader_trade: Optional[Dict] = None
    price_context: Optional[Dict] = None
    position_state: Optional[Dict] = None
    decision: Optional[Dict] = None


@dataclass
class SessionSummary:
    """Summary statistics for the session."""
    duration_seconds: float
    leader_trades_seen: int
    leader_trades_stale: int
    
    # Our actions
    buys_executed: int
    buys_dollars: str
    sells_executed: int
    sells_dollars: str
    
    # Skip breakdown
    skips_by_reason: Dict[str, int]
    
    # PnL
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    total_pnl_pct: str
    
    # Position summary
    positions_held: int
    capital_deployed: str
    capital_reserved: str


class SessionRecorder:
    """Records a DRY_RUN session for replay and strategy testing.
    
    Captures all data needed to:
    1. Exactly reproduce our behavior (deterministic replay)
    2. Test alternative strategies on the same data
    3. Analyze why we skipped trades (prices at decision time)
    """
    
    def __init__(
        self,
        output_dir: Path,
        config_snapshot: Dict[str, Any],
        session_id: Optional[str] = None,
    ):
        """Initialize the recorder.
        
        Args:
            output_dir: Directory to write session files
            config_snapshot: Copy of relevant config for reproducibility
            session_id: Optional session ID (defaults to timestamp)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.config_snapshot = config_snapshot
        
        # Output file
        self.file_path = self.output_dir / f"session_{self.session_id}.jsonl"
        self._file: Optional[TextIO] = None
        
        # Tracking
        self._start_time: Optional[datetime] = None
        self._trade_count = 0
        self._skip_reasons: Dict[str, int] = {}
        
        # Stats
        self.buys_executed = 0
        self.buys_dollars = Decimal("0")
        self.sells_executed = 0
        self.sells_dollars = Decimal("0")
    
    def start(self) -> None:
        """Start recording - write session header."""
        self._start_time = datetime.now(timezone.utc)
        self._file = open(self.file_path, "w", encoding="utf-8")
        
        header = {
            "type": "session_start",
            "timestamp": self._start_time.isoformat(),
            "session_id": self.session_id,
            "config": self.config_snapshot,
        }
        self._write_line(header)
        
        print(f"  [RECORDER] Session recording to: {self.file_path}")
    
    def record_trade(
        self,
        leader_trade: LeaderTradeEvent,
        price_context: PriceContext,
        position_state: Optional[PositionState],
        decision: Decision,
    ) -> None:
        """Record a leader trade and our decision.
        
        This is called for EVERY leader trade, whether we execute or skip.
        """
        if not self._file:
            return
        
        self._trade_count += 1
        
        # Track skip reasons
        if decision.action == "SKIP" and decision.skip_reason:
            self._skip_reasons[decision.skip_reason] = (
                self._skip_reasons.get(decision.skip_reason, 0) + 1
            )
        
        # Track executions
        if decision.action == "BUY" and decision.our_dollars:
            self.buys_executed += 1
            self.buys_dollars += Decimal(decision.our_dollars)
        elif decision.action == "SELL" and decision.our_dollars:
            self.sells_executed += 1
            self.sells_dollars += Decimal(decision.our_dollars)
        
        record = {
            "type": "leader_trade",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self._trade_count,
            "leader_trade": asdict(leader_trade),
            "price_context": asdict(price_context),
            "position_state": asdict(position_state) if position_state else None,
            "decision": asdict(decision),
        }
        self._write_line(record)
    
    def record_position_update(
        self,
        action: str,
        market_id: str,
        token_id: str,
        side: str,
        shares: Decimal,
        price: Decimal,
        dollars: Decimal,
    ) -> None:
        """Record a position update (fill confirmation)."""
        if not self._file:
            return
        
        record = {
            "type": "position_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "shares": str(shares),
            "price": str(price),
            "dollars": str(dollars),
        }
        self._write_line(record)
    
    def finish(
        self,
        leader_trades_seen: int,
        leader_trades_stale: int,
        positions_held: int,
        capital_deployed: Decimal,
        capital_reserved: Decimal,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        starting_capital: Decimal,
    ) -> None:
        """Finish recording - write session summary and close."""
        if not self._file or not self._start_time:
            return
        
        end_time = datetime.now(timezone.utc)
        duration = (end_time - self._start_time).total_seconds()
        
        total_pnl = realized_pnl + unrealized_pnl
        total_pnl_pct = (
            (total_pnl / starting_capital * Decimal("100"))
            if starting_capital > Decimal("0")
            else Decimal("0")
        )
        
        summary = {
            "type": "session_end",
            "timestamp": end_time.isoformat(),
            "session_id": self.session_id,
            "summary": {
                "duration_seconds": duration,
                "leader_trades_seen": leader_trades_seen,
                "leader_trades_stale": leader_trades_stale,
                "buys_executed": self.buys_executed,
                "buys_dollars": str(self.buys_dollars),
                "sells_executed": self.sells_executed,
                "sells_dollars": str(self.sells_dollars),
                "skips_by_reason": self._skip_reasons,
                "realized_pnl": str(realized_pnl),
                "unrealized_pnl": str(unrealized_pnl),
                "total_pnl": str(total_pnl),
                "total_pnl_pct": str(total_pnl_pct.quantize(Decimal("0.01"))),
                "positions_held": positions_held,
                "capital_deployed": str(capital_deployed),
                "capital_reserved": str(capital_reserved),
            },
        }
        self._write_line(summary)
        
        self._file.close()
        self._file = None
        
        print(f"\n  [RECORDER] Session saved: {self.file_path}")
        print(f"  [RECORDER] Trades recorded: {self._trade_count}")
        print(f"  [RECORDER] Duration: {duration:.1f}s")
    
    def _write_line(self, data: Dict[str, Any]) -> None:
        """Write a JSON line to the file."""
        if self._file:
            json_str = json.dumps(data, default=str)
            self._file.write(json_str + "\n")
            self._file.flush()
    
    @property
    def is_active(self) -> bool:
        """Check if recording is active."""
        return self._file is not None


def create_config_snapshot(config) -> Dict[str, Any]:
    """Create a serializable snapshot of relevant config for replay."""
    return {
        "mode": str(config.mode),
        "strategy": config.strategy,
        "leader_address": config.leader.address,
        "scaling": {
            "k_factor": str(config.scaling_pct.k_factor),
            "our_capital": str(config.scaling.our_capital),
            "hourly_budget": str(config.scaling.hourly_budget),
            "leader_estimated_capital": str(config.scaling.leader_capital),
        },
        "safety": {
            "price_drift_enabled": config.safety.price_drift_enabled,
            "max_buy_price_drift_pct": str(config.safety.max_buy_price_drift_pct),
            "max_spread_pct": str(config.safety.max_spread_pct),
            "block_loss_sells_if_leader_profit": config.safety.block_loss_sells_if_leader_profit,
        },
        "mirror_strategy": {
            "staleness_window_sec": config.mirror_strategy.staleness_window_sec,
            "per_market_cap_pct": str(config.mirror_strategy.per_market_cap_pct),
            "cash_reserve_pct": str(config.mirror_strategy.cash_reserve_pct),
        },
        "simulation": {
            "simulated_spread_pct": config.simulation.simulated_spread_pct,
            "simulated_slippage_pct": config.simulation.simulated_slippage_pct,
        },
    }
