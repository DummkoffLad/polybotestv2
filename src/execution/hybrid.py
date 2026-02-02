"""
Hybrid Execution Mode - intelligent selection between market and limit orders.

This module provides logic to decide when to use market orders (guaranteed fill,
higher cost) vs limit orders (uncertain fill, lower cost) based on:

1. Spread width: Tight spread → more likely to use market (low cost anyway)
2. Price distance: Far from leader price → prefer limit (catch up)
3. Time pressure: Fast-moving market → prefer market (capture opportunity)
4. Slippage estimate: High slippage → prefer limit (save money)
5. Trade size: Small trades → limit ok, large trades → split or market

Polymarket constraints:
- Market orders: minimum $1
- Limit orders: minimum 5 shares
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ExecutionMode(Enum):
    """Which execution mode to use."""
    MARKET = "MARKET"           # Fill immediately at best price
    LIMIT_PASSIVE = "PASSIVE"   # Post limit, wait for fill
    LIMIT_AGGRESSIVE = "AGGRESSIVE"  # Cross spread slightly


@dataclass
class ExecutionDecision:
    """Result of execution mode selection."""
    mode: ExecutionMode
    price: Decimal              # Target price for limit, or expected price for market
    shares: Decimal
    reason: str
    confidence: float = 0.5     # 0-1, how confident we are in this choice
    
    # For limit orders
    limit_offset_pct: Decimal = Decimal("0")  # How far from best price to post
    ttl_seconds: float = 8.0    # Time-to-live for limit


class HybridExecutionSelector:
    """
    Selects between market and limit orders based on market conditions.
    
    Usage:
        selector = HybridExecutionSelector()
        decision = selector.select(
            side="BUY",
            shares=Decimal("10"),
            current_bid=Decimal("0.45"),
            current_ask=Decimal("0.47"),
            leader_price=Decimal("0.46"),
            spread_avg_pct=Decimal("2.0"),
            ...
        )
        
        if decision.mode == ExecutionMode.MARKET:
            execute_market_order(...)
        else:
            execute_limit_order(decision.price, decision.ttl_seconds, ...)
    """
    
    def __init__(
        self,
        # Thresholds for preferring market orders
        tight_spread_threshold_pct: Decimal = Decimal("1.5"),  # Below this → market
        high_urgency_threshold_pct: Decimal = Decimal("5.0"),  # Leader moved >5% → market
        
        # Thresholds for preferring limit orders
        wide_spread_threshold_pct: Decimal = Decimal("4.0"),   # Above this → limit
        
        # Limit order parameters
        default_limit_ttl: float = 8.0,
        passive_offset_pct: Decimal = Decimal("0.3"),  # Post 0.3% inside best
        aggressive_offset_pct: Decimal = Decimal("0.1"),  # Cross spread by 0.1%
    ):
        self.tight_spread_threshold = tight_spread_threshold_pct
        self.high_urgency_threshold = high_urgency_threshold_pct
        self.wide_spread_threshold = wide_spread_threshold_pct
        self.default_ttl = default_limit_ttl
        self.passive_offset = passive_offset_pct
        self.aggressive_offset = aggressive_offset_pct
    
    def select(
        self,
        side: str,  # "BUY" or "SELL"
        shares: Decimal,
        current_bid: Decimal,
        current_ask: Decimal,
        leader_price: Optional[Decimal] = None,
        leader_timestamp_age_seconds: Optional[float] = None,
        spread_avg_pct: Optional[Decimal] = None,
        volatility_pct: Optional[Decimal] = None,
    ) -> ExecutionDecision:
        """
        Select execution mode based on current conditions.
        
        Args:
            side: "BUY" or "SELL"
            shares: Number of shares to trade
            current_bid: Current best bid
            current_ask: Current best ask
            leader_price: Price the leader got (if known)
            leader_timestamp_age_seconds: How old is the leader trade
            spread_avg_pct: Average spread for this market
            volatility_pct: Recent price volatility
            
        Returns:
            ExecutionDecision with mode, price, and reasoning
        """
        # Calculate current spread
        mid = (current_bid + current_ask) / 2
        spread = current_ask - current_bid
        spread_pct = (spread / mid * 100) if mid > 0 else Decimal("100")
        
        # Factor 1: Spread width
        spread_score = self._score_spread(spread_pct)
        
        # Factor 2: Price distance from leader
        distance_score = Decimal("0.5")  # Neutral default
        if leader_price and leader_price > 0:
            if side == "BUY":
                # We're buying - if ask is higher than leader paid, we're chasing
                chase_pct = (current_ask - leader_price) / leader_price * 100
                distance_score = self._score_chase(chase_pct)
            else:
                # We're selling - if bid is lower than leader got, we're dumping
                dump_pct = (leader_price - current_bid) / leader_price * 100
                distance_score = self._score_chase(dump_pct)
        
        # Factor 3: Time pressure (age of leader trade)
        urgency_score = Decimal("0.5")  # Neutral default
        if leader_timestamp_age_seconds is not None:
            if leader_timestamp_age_seconds < 2:
                urgency_score = Decimal("0.8")  # Fresh trade - more urgent
            elif leader_timestamp_age_seconds > 10:
                urgency_score = Decimal("0.2")  # Old trade - no rush
        
        # Factor 4: Volatility
        vol_score = Decimal("0.5")
        if volatility_pct is not None:
            if volatility_pct > Decimal("5"):
                vol_score = Decimal("0.7")  # High vol → market (capture before move)
            elif volatility_pct < Decimal("1"):
                vol_score = Decimal("0.3")  # Low vol → limit (no rush)
        
        # Combine factors - higher score = prefer market
        # Weights: spread 30%, distance 25%, urgency 25%, volatility 20%
        combined_score = (
            spread_score * Decimal("0.30") +
            distance_score * Decimal("0.25") +
            urgency_score * Decimal("0.25") +
            vol_score * Decimal("0.20")
        )
        
        # Decision
        if combined_score > Decimal("0.65"):
            mode = ExecutionMode.MARKET
            reason = "High urgency/tight spread"
            price = current_ask if side == "BUY" else current_bid
            confidence = float(combined_score)
        elif combined_score > Decimal("0.45"):
            mode = ExecutionMode.LIMIT_AGGRESSIVE
            reason = "Moderate conditions - aggressive limit"
            if side == "BUY":
                price = current_ask - (current_ask * self.aggressive_offset / 100)
            else:
                price = current_bid + (current_bid * self.aggressive_offset / 100)
            confidence = 0.6
        else:
            mode = ExecutionMode.LIMIT_PASSIVE
            reason = "Wide spread/low urgency"
            if side == "BUY":
                price = current_bid + (spread * Decimal("0.1"))  # Just inside bid
            else:
                price = current_ask - (spread * Decimal("0.1"))  # Just inside ask
            confidence = float(Decimal("1") - combined_score)
        
        return ExecutionDecision(
            mode=mode,
            price=price.quantize(Decimal("0.0001")),
            shares=shares,
            reason=reason,
            confidence=confidence,
            limit_offset_pct=self.passive_offset if mode == ExecutionMode.LIMIT_PASSIVE else self.aggressive_offset,
            ttl_seconds=self.default_ttl
        )
    
    def _score_spread(self, spread_pct: Decimal) -> Decimal:
        """Score spread - higher = prefer market order."""
        if spread_pct <= self.tight_spread_threshold:
            return Decimal("0.9")  # Tight spread - market is fine
        elif spread_pct >= self.wide_spread_threshold:
            return Decimal("0.1")  # Wide spread - use limit
        else:
            # Linear interpolation
            range_size = self.wide_spread_threshold - self.tight_spread_threshold
            return Decimal("0.9") - ((spread_pct - self.tight_spread_threshold) / range_size * Decimal("0.8"))
    
    def _score_chase(self, chase_pct: Decimal) -> Decimal:
        """Score how far we're chasing the leader - higher = prefer market."""
        if chase_pct <= 0:
            return Decimal("0.2")  # We'd get better price - use limit
        elif chase_pct > self.high_urgency_threshold:
            return Decimal("0.9")  # Chasing hard - need market
        else:
            # Moderate chase - scale
            return Decimal("0.4") + (chase_pct / self.high_urgency_threshold * Decimal("0.5"))


def get_execution_mode(
    side: str,
    shares: Decimal,
    bid: Decimal,
    ask: Decimal,
    leader_price: Optional[Decimal] = None,
) -> Tuple[ExecutionMode, Decimal, str]:
    """
    Simple helper function to get execution mode.
    
    Returns: (mode, target_price, reason)
    """
    selector = HybridExecutionSelector()
    decision = selector.select(
        side=side,
        shares=shares,
        current_bid=bid,
        current_ask=ask,
        leader_price=leader_price,
    )
    return decision.mode, decision.price, decision.reason
