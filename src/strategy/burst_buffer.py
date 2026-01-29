"""Burst buffer for aggregating leader's partial fills.

PROBLEM: When the leader places a $10 order, it gets filled in chunks:
  - Partial fill 1: $0.30
  - Partial fill 2: $0.45  
  - Partial fill 3: $0.25
  - ... etc

Each partial fill shows up as a separate "trade" from the leader.
If we react to each one, we'd try to place many $0.30 orders - which fail
because Polymarket has a MINIMUM ORDER SIZE (typically $1).

SOLUTION: This buffer accumulates partial fills until:
  1. The SCALED amount (our dollars, not leader's) reaches our minimum order size
  2. Then we execute a single order for the accumulated amount

KEY INSIGHT: We accumulate in LEADER dollars, but check threshold in OUR dollars
(after applying scale ratio). This way:
  - Leader trades $5 in partials
  - We accumulate until our_dollars = leader_dollars * scale_ratio >= min_order
  - Then execute once

Key: (market_id, side) — BUY and SELL are netted together.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

# OUR minimum order size (what Polymarket requires)
# This is in OUR dollars (after scaling), not leader dollars
MIN_ORDER_DOLLARS = Decimal("1.0")


@dataclass
class BurstEntry:
    """Single burst accumulator for one (market_id, side) key.

    Tracks BUY and SELL separately, then nets them when ready to execute.
    Accumulates micro-trades until they reach execution threshold.
    """

    market_id: str
    side: str  # "UP" or "DOWN"
    token_id: str

    # Accumulated leader dollars by action
    buy_leader_dollars: Decimal = Decimal("0")
    sell_leader_dollars: Decimal = Decimal("0")
    trade_count: int = 0

    # Amount we already executed (in LEADER dollars)
    already_executed_leader_dollars: Decimal = Decimal("0")

    # Timing
    first_trade_time: float = 0.0
    last_trade_time: float = 0.0
    
    # Track last seen leader price for cost analysis
    last_leader_price: Optional[Decimal] = None


@dataclass
class BurstAction:
    """Action to take from burst processing."""

    market_id: str
    side: str
    action: str  # "BUY" or "SELL"
    token_id: str
    leader_dollars: Decimal  # Total LEADER dollars for this burst action
    is_immediate: bool  # True = first trade in burst, False = aggregated follow-up
    leader_price: Optional[Decimal] = None  # Leader's entry price for cost analysis


class BurstBuffer:
    """Aggregates leader's partial fills until we can execute.

    The leader's orders get filled in many small chunks (partial fills).
    We can't react to each partial because:
    1. They're often < $1 (below Polymarket minimum)
    2. After scaling, they'd be even smaller
    3. Executing many tiny orders = high fees and slippage

    SOLUTION: Accumulate LEADER dollars, but check if OUR scaled dollars
    reach the minimum order size before executing.
    """

    def __init__(self, min_order_dollars: Decimal = MIN_ORDER_DOLLARS):
        """Initialize the burst buffer.
        
        Args:
            min_order_dollars: Minimum order size in OUR dollars (after scaling)
        """
        self.min_order_dollars = min_order_dollars
        # Persistent accumulators: key -> BurstEntry
        self._entries: Dict[str, BurstEntry] = {}
        # Track scale ratio (set by mirror_runner)
        self._scale_ratio: Decimal = Decimal("1.0")

    def set_scale_ratio(self, ratio: Decimal) -> None:
        """Update the scale ratio (our_capital / leader_capital * k)."""
        self._scale_ratio = ratio

    def _key(self, market_id: str, side: str) -> str:
        return f"{market_id}|{side}"

    def add_trade(
        self,
        market_id: str,
        side: str,
        action: str,
        token_id: str,
        leader_dollars: Decimal,
        leader_price: Optional[Decimal] = None,
    ) -> Optional[BurstAction]:
        """Add a leader partial fill to the accumulator.

        Accumulates leader dollars. Checks if OUR scaled amount >= minimum.
        
        Returns:
            BurstAction if accumulated OUR dollars >= min_order_dollars
            None if still accumulating
        """
        now = time.time()
        key = self._key(market_id, side)
        
        # Get or create accumulator
        entry = self._entries.get(key)
        
        if entry is None:
            entry = BurstEntry(
                market_id=market_id,
                side=side,
                token_id=token_id,
                buy_leader_dollars=Decimal("0"),
                sell_leader_dollars=Decimal("0"),
                trade_count=0,
                already_executed_leader_dollars=Decimal("0"),
                first_trade_time=now,
                last_trade_time=now,
                last_leader_price=leader_price,
            )
            self._entries[key] = entry
        
        # Add this partial fill to accumulator
        if action == "BUY":
            entry.buy_leader_dollars += leader_dollars
        else:
            entry.sell_leader_dollars += leader_dollars
        entry.trade_count += 1
        entry.last_trade_time = now
        if leader_price:
            entry.last_leader_price = leader_price
        
        # Net BUY - SELL to get direction
        net_leader_dollars = entry.buy_leader_dollars - entry.sell_leader_dollars
        
        # Convert to OUR dollars using scale ratio
        our_dollars = abs(net_leader_dollars) * self._scale_ratio
        
        # Check if OUR dollars reach minimum order size
        if our_dollars >= self.min_order_dollars:
            # Ready to execute!
            if net_leader_dollars > 0:
                result_action = "BUY"
                result_leader_dollars = net_leader_dollars
            else:
                result_action = "SELL"
                result_leader_dollars = abs(net_leader_dollars)
            
            # Reset accumulator
            del self._entries[key]
            
            return BurstAction(
                market_id=market_id,
                side=side,
                action=result_action,
                token_id=token_id,
                leader_dollars=result_leader_dollars,
                is_immediate=False,  # Accumulated from partials
                leader_price=entry.last_leader_price,
            )
        
        # Not yet at threshold - keep accumulating
        return None

    def get_accumulated(self, market_id: str, side: str) -> Tuple[Decimal, Decimal]:
        """Get current accumulated amounts for a market/side.
        
        Returns:
            (leader_dollars, our_dollars) tuple
        """
        key = self._key(market_id, side)
        entry = self._entries.get(key)
        if entry is None:
            return Decimal("0"), Decimal("0")
        
        net_leader = abs(entry.buy_leader_dollars - entry.sell_leader_dollars)
        our_dollars = net_leader * self._scale_ratio
        return net_leader, our_dollars

    def record_executed(
        self, market_id: str, side: str, leader_dollars: Decimal
    ) -> None:
        """Record that we executed some LEADER dollars for a burst.

        CRITICAL: This takes LEADER dollars, not our scaled dollars.
        """
        key = self._key(market_id, side)
        entry = self._entries.get(key)
        if entry:
            entry.already_executed_leader_dollars += leader_dollars

    def flush_all(self) -> List[BurstAction]:
        """Flush all pending bursts (for shutdown / end-of-hour).

        Returns aggregated actions for any remaining un-executed amounts.
        Only flushes if OUR scaled dollars >= minimum order size.
        """
        actions: List[BurstAction] = []

        for entry in self._entries.values():
            # Net BUY - SELL
            net_leader_dollars = entry.buy_leader_dollars - entry.sell_leader_dollars
            remainder_leader_dollars = net_leader_dollars - entry.already_executed_leader_dollars

            # Check if worth executing (OUR dollars must meet minimum)
            our_dollars = abs(remainder_leader_dollars) * self._scale_ratio
            
            if our_dollars >= self.min_order_dollars:
                if remainder_leader_dollars > 0:
                    action = "BUY"
                    leader_dollars = remainder_leader_dollars
                else:
                    action = "SELL"
                    leader_dollars = abs(remainder_leader_dollars)

                actions.append(
                    BurstAction(
                        market_id=entry.market_id,
                        side=entry.side,
                        action=action,
                        token_id=entry.token_id,
                        leader_dollars=leader_dollars,
                        is_immediate=False,
                        leader_price=entry.last_leader_price,
                    )
                )

        self._entries.clear()
        return actions

    def pending_count(self) -> int:
        """Number of active burst buffers."""
        return len(self._entries)
    
    def get_pending_count(self) -> int:
        """Alias for pending_count() for compatibility."""
        return self.pending_count()
