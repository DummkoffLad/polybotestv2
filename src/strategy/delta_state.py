"""Persistent delta-driven state for copy trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Any

from ..core import MarketId, Side, Exposure, MySnapshot


@dataclass
class ShadowPosition:
    """Shadow position tracked per token_id."""
    token_id: str
    market_id: MarketId
    side: Side
    shares: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")  # cost basis
    last_update: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "market_id": self.market_id,
            "side": str(self.side),
            "shares": str(self.shares),
            "avg_price": str(self.avg_price),
            "notional": str(self.notional),
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }


class ShadowPortfolio:
    """Persistent shadow portfolio (token_id keyed)."""

    def __init__(self):
        self._positions: Dict[str, ShadowPosition] = {}

    def get(self, token_id: str, market_id: MarketId, side: Side) -> ShadowPosition:
        if token_id not in self._positions:
            self._positions[token_id] = ShadowPosition(
                token_id=token_id,
                market_id=market_id,
                side=side,
            )
        return self._positions[token_id]

    def has_position(self, token_id: str) -> bool:
        pos = self._positions.get(token_id)
        return pos is not None and pos.shares > 0

    def apply_fill(
        self,
        token_id: str,
        market_id: MarketId,
        side: Side,
        action: str,  # BUY or SELL
        shares: Decimal,
        price: Decimal,
        timestamp: datetime,
        strict: bool = False,
    ) -> ShadowPosition:
        """Apply a fill to the shadow portfolio.
        
        Args:
            token_id: Token identifier
            market_id: Market identifier
            side: UP or DOWN
            action: "BUY" or "SELL"
            shares: Number of shares
            price: Fill price
            timestamp: Time of fill
            strict: If True, raise error on invalid operations (e.g., selling more than owned)
        
        Returns:
            Updated ShadowPosition
            
        Raises:
            ValueError: If strict=True and operation is invalid
        """
        # Validate inputs
        if shares < 0:
            raise ValueError(f"Cannot apply fill with negative shares: {shares}")
        if price < 0:
            raise ValueError(f"Cannot apply fill with negative price: {price}")
        if action not in ("BUY", "SELL"):
            raise ValueError(f"Invalid action: {action}. Must be 'BUY' or 'SELL'")
        
        pos = self.get(token_id, market_id, side)

        if action == "BUY":
            # Update average price with weighted cost basis
            total_cost = pos.notional + (shares * price)
            total_shares = pos.shares + shares
            if total_shares > 0:
                pos.avg_price = (total_cost / total_shares).quantize(Decimal("0.0001"))
            pos.shares = total_shares
            pos.notional = total_cost
        else:
            # SELL: reduce shares and notional proportionally
            if shares > pos.shares:
                if strict:
                    raise ValueError(
                        f"Cannot sell {shares} shares when only {pos.shares} owned "
                        f"(token_id={token_id}, market_id={market_id}, side={side})"
                    )
                # Non-strict: clamp to available shares (log warning)
                import logging
                logging.getLogger(__name__).warning(
                    f"Clamping sell from {shares} to {pos.shares} "
                    f"(token_id={token_id}, market_id={market_id})"
                )
                shares = pos.shares
            
            sell_shares = shares
            if pos.shares > 0 and sell_shares > 0:
                ratio = sell_shares / pos.shares
                pos.notional -= pos.notional * ratio
            pos.shares -= sell_shares
            
            # Clean up small rounding errors
            if pos.shares < Decimal("0.001"):
                pos.shares = Decimal("0")
                pos.notional = Decimal("0")
                pos.avg_price = Decimal("0")

        pos.last_update = timestamp
        return pos

    def build_snapshot(
        self,
        timestamp: datetime,
        available_capital: Decimal,
        hourly_budget_used: Decimal,
    ) -> MySnapshot:
        exposures: Dict[MarketId, Exposure] = {}
        used_capital = Decimal("0")

        for pos in self._positions.values():
            if pos.shares <= 0:
                continue
            market_id = pos.market_id
            if market_id not in exposures:
                exposures[market_id] = Exposure(market_id=market_id)

            exp = exposures[market_id]
            dollars = pos.shares * pos.avg_price if pos.avg_price > 0 else pos.notional

            if pos.side == Side.UP:
                exposures[market_id] = Exposure(
                    market_id=market_id,
                    up_shares=exp.up_shares + pos.shares,
                    up_dollars=exp.up_dollars + dollars,
                    down_shares=exp.down_shares,
                    down_dollars=exp.down_dollars,
                )
            else:
                exposures[market_id] = Exposure(
                    market_id=market_id,
                    up_shares=exp.up_shares,
                    up_dollars=exp.up_dollars,
                    down_shares=exp.down_shares + pos.shares,
                    down_dollars=exp.down_dollars + dollars,
                )

            used_capital += dollars

        return MySnapshot(
            timestamp=timestamp,
            exposures=exposures,
            available_capital=max(Decimal("0"), available_capital - used_capital),
            used_capital=used_capital,
            pending_orders=[],
            hourly_budget_used=hourly_budget_used,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {token_id: pos.to_dict() for token_id, pos in self._positions.items()}

    def iter_positions(self) -> Dict[str, ShadowPosition]:
        """Return a snapshot of tracked positions."""
        return dict(self._positions)


@dataclass
class SideDedupeState:
    """Per-side dedupe state for strict delta tracking.
    
    Tracks last processed position value and timestamp to:
    1. Reject stale/out-of-order updates
    2. Reject duplicate (same position value) updates
    """
    last_position_dollars: Optional[Decimal] = None
    last_position_shares: Optional[Decimal] = None
    last_ts: Optional[datetime] = None
    is_baseline: bool = True  # First observation is baseline
    
    def check_and_update(
        self,
        curr_dollars: Decimal,
        curr_shares: Decimal,
        curr_ts: datetime,
        tolerance_sec: float = 2.0,
    ) -> tuple[bool, str, Optional[Decimal]]:
        """Check if this update should be processed.
        
        Returns:
            (accepted, reason, delta_dollars)
            - accepted: True if this is a valid new update
            - reason: Why accepted/rejected
            - delta_dollars: Change from last position (None if rejected)
        """
        # First observation = baseline (not a delta)
        if self.is_baseline:
            self.last_position_dollars = curr_dollars
            self.last_position_shares = curr_shares
            self.last_ts = curr_ts
            self.is_baseline = False
            return (False, "BASELINE", None)
        
        # Check for stale/out-of-order
        if self.last_ts is not None:
            delta_sec = (curr_ts - self.last_ts).total_seconds()
            if delta_sec < -tolerance_sec:
                return (False, f"STALE (ts={delta_sec:.1f}s behind)", None)
        
        # Check for duplicate position (no change in SHARES)
        # CRITICAL: Compare SHARES not DOLLARS!
        # Dollars change with price fluctuations, shares only change with TRADES.
        if self.last_position_shares is not None:
            if curr_shares == self.last_position_shares:
                return (False, "DUPLICATE (same shares)", None)
        
        # ACCEPTED: This is a valid delta (share count changed = actual trade)
        delta = curr_dollars - (self.last_position_dollars or Decimal("0"))
        prev_dollars = self.last_position_dollars
        
        # Update state AFTER acceptance
        self.last_position_dollars = curr_dollars
        self.last_position_shares = curr_shares
        self.last_ts = curr_ts
        
        return (True, f"ACCEPTED (delta={delta:+.2f})", delta)


@dataclass
class MarketCopyState:
    """Per-market persistent state for delta-driven logic.
    
    CRITICAL: Uses per-side dedupe to ensure:
    1. Baseline is marked and NOT treated as a delta
    2. Same position value is never processed twice
    3. Stale/out-of-order updates are rejected
    """
    market_id: MarketId
    
    # Per-side dedupe state (AUTHORITATIVE)
    up_dedupe: SideDedupeState = field(default_factory=SideDedupeState)
    down_dedupe: SideDedupeState = field(default_factory=SideDedupeState)
    
    # Legacy fields (for compatibility)
    last_leader_exposure: Optional[Exposure] = None
    last_leader_snapshot_time: Optional[datetime] = None
    last_seen_trade_id: Optional[str] = None
    last_seen_trade_ts: Optional[int] = None
    last_action_time: Optional[datetime] = None
    last_activity_time: Optional[datetime] = None
    burst_aligned: bool = False
    resync_active: bool = False
    last_resync_time: Optional[datetime] = None
    intent_accumulator: Dict[str, Decimal] = field(
        default_factory=lambda: {"UP": Decimal("0"), "DOWN": Decimal("0")}
    )
    sell_accumulator: Dict[str, Decimal] = field(
        default_factory=lambda: {"UP": Decimal("0"), "DOWN": Decimal("0")}
    )
    
    # Instrumentation counters
    updates_accepted: int = 0
    updates_rejected_baseline: int = 0
    updates_rejected_stale: int = 0
    updates_rejected_duplicate: int = 0

    def get_dedupe_state(self, side: Side) -> SideDedupeState:
        """Get dedupe state for a side."""
        return self.up_dedupe if side == Side.UP else self.down_dedupe
    
    def record_dedupe_decision(self, reason: str) -> None:
        """Record dedupe decision for instrumentation."""
        if "ACCEPTED" in reason:
            self.updates_accepted += 1
        elif "BASELINE" in reason:
            self.updates_rejected_baseline += 1
        elif "STALE" in reason:
            self.updates_rejected_stale += 1
        elif "DUPLICATE" in reason:
            self.updates_rejected_duplicate += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "last_leader_snapshot_time": self.last_leader_snapshot_time.isoformat() if self.last_leader_snapshot_time else None,
            "last_seen_trade_id": self.last_seen_trade_id,
            "last_seen_trade_ts": self.last_seen_trade_ts,
            "last_action_time": self.last_action_time.isoformat() if self.last_action_time else None,
            "last_activity_time": self.last_activity_time.isoformat() if self.last_activity_time else None,
            "burst_aligned": self.burst_aligned,
            "resync_active": self.resync_active,
            "last_resync_time": self.last_resync_time.isoformat() if self.last_resync_time else None,
            "intent_accumulator": {k: str(v) for k, v in self.intent_accumulator.items()},
            "sell_accumulator": {k: str(v) for k, v in self.sell_accumulator.items()},
            "up_dedupe": {
                "last_dollars": str(self.up_dedupe.last_position_dollars) if self.up_dedupe.last_position_dollars else None,
                "last_ts": self.up_dedupe.last_ts.isoformat() if self.up_dedupe.last_ts else None,
                "is_baseline": self.up_dedupe.is_baseline,
            },
            "down_dedupe": {
                "last_dollars": str(self.down_dedupe.last_position_dollars) if self.down_dedupe.last_position_dollars else None,
                "last_ts": self.down_dedupe.last_ts.isoformat() if self.down_dedupe.last_ts else None,
                "is_baseline": self.down_dedupe.is_baseline,
            },
            "instrumentation": {
                "updates_accepted": self.updates_accepted,
                "updates_rejected_baseline": self.updates_rejected_baseline,
                "updates_rejected_stale": self.updates_rejected_stale,
                "updates_rejected_duplicate": self.updates_rejected_duplicate,
            },
        }
