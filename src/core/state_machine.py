"""Per-market state machine with BURST/FOLLOW/RESYNC modes.

State transitions:
- BURST -> FOLLOW: After burst_window_sec expires AND leader stabilizes
- FOLLOW -> RESYNC: Every resync_interval_sec
- RESYNC -> FOLLOW: After resync completes
- Any -> CLOSED: When market closes
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, List

from .types import MarketPhase, MarketId, Exposure


@dataclass
class BurstTracker:
    """Tracks leader exposure during BURST mode for stabilization detection."""
    
    # History of gross exposure samples
    samples: List[Decimal] = field(default_factory=list)
    sample_timestamps: List[datetime] = field(default_factory=list)
    
    def add_sample(self, gross_exposure: Decimal, timestamp: datetime) -> None:
        """Add a new exposure sample."""
        self.samples.append(gross_exposure)
        self.sample_timestamps.append(timestamp)
    
    def is_stable(
        self,
        threshold: Decimal,
        required_count: int = 3,
    ) -> bool:
        """Check if leader exposure has stabilized.
        
        Stabilization = last N samples are within threshold of each other.
        
        Args:
            threshold: Maximum allowed variation between samples
            required_count: Number of consecutive stable samples required
        """
        if len(self.samples) < required_count:
            return False
        
        recent = self.samples[-required_count:]
        min_val = min(recent)
        max_val = max(recent)
        
        return (max_val - min_val) <= threshold
    
    def get_stable_value(self, required_count: int = 3) -> Optional[Decimal]:
        """Get the stable exposure value (average of recent samples)."""
        if len(self.samples) < required_count:
            return None
        
        recent = self.samples[-required_count:]
        return sum(recent) / len(recent)
    
    def reset(self) -> None:
        """Reset tracking."""
        self.samples.clear()
        self.sample_timestamps.clear()


@dataclass
class MarketState:
    """State for a single market."""
    market_id: MarketId
    phase: MarketPhase = MarketPhase.UNKNOWN
    
    # Timing
    market_open_time: Optional[datetime] = None
    phase_start_time: Optional[datetime] = None
    last_resync_time: Optional[datetime] = None
    
    # BURST mode tracking
    burst_tracker: BurstTracker = field(default_factory=BurstTracker)
    
    # Last known exposures
    last_leader_exposure: Optional[Exposure] = None
    last_my_exposure: Optional[Exposure] = None
    
    # Orders placed this market hour
    orders_this_hour: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "market_id": self.market_id,
            "phase": str(self.phase),
            "market_open_time": self.market_open_time.isoformat() if self.market_open_time else None,
            "phase_start_time": self.phase_start_time.isoformat() if self.phase_start_time else None,
            "last_resync_time": self.last_resync_time.isoformat() if self.last_resync_time else None,
            "orders_this_hour": self.orders_this_hour,
        }


@dataclass
class StateMachineConfig:
    """Configuration for state machine timing."""
    # BURST mode
    burst_window_sec: float = 60
    burst_poll_interval_sec: float = 1.5
    burst_stabilization_threshold: Decimal = Decimal("0.50")
    burst_stabilization_count: int = 3
    
    # RESYNC mode
    resync_interval_sec: float = 60
    resync_error_threshold: Decimal = Decimal("0.50")
    
    # FOLLOW mode
    follow_batch_window_sec: float = 2.0


class MarketStateMachine:
    """Manages state machine for a single market."""
    
    def __init__(self, market_id: MarketId, config: StateMachineConfig):
        self.market_id = market_id
        self.config = config
        self.state = MarketState(market_id=market_id)
    
    def on_market_open(self, open_time: datetime) -> None:
        """Handle market open event.
        
        Enters BURST mode.
        """
        self.state.market_open_time = open_time
        self.state.phase = MarketPhase.BURST
        self.state.phase_start_time = open_time
        self.state.burst_tracker.reset()
        self.state.orders_this_hour = 0
    
    def on_market_close(self) -> None:
        """Handle market close event."""
        self.state.phase = MarketPhase.CLOSED
    
    def update(
        self,
        current_time: datetime,
        leader_exposure: Exposure,
    ) -> Optional[str]:
        """Update state machine and return transition reason if state changed.
        
        Args:
            current_time: Current time
            leader_exposure: Current leader exposure
            
        Returns:
            Transition reason string if state changed, None otherwise
        """
        old_phase = self.state.phase
        transition_reason = None
        
        if self.state.phase == MarketPhase.BURST:
            transition_reason = self._update_burst(current_time, leader_exposure)
        elif self.state.phase == MarketPhase.FOLLOW:
            transition_reason = self._update_follow(current_time)
        elif self.state.phase == MarketPhase.RESYNC:
            # RESYNC is a momentary phase - transitions back to FOLLOW
            self.state.phase = MarketPhase.FOLLOW
            self.state.last_resync_time = current_time
            transition_reason = "resync_complete"
        
        # Update last known exposure
        self.state.last_leader_exposure = leader_exposure
        
        return transition_reason if self.state.phase != old_phase else None
    
    def _update_burst(
        self,
        current_time: datetime,
        leader_exposure: Exposure,
    ) -> Optional[str]:
        """Update during BURST mode."""
        if self.state.market_open_time is None:
            return None
        
        elapsed = (current_time - self.state.market_open_time).total_seconds()
        
        # Track leader exposure for stabilization
        self.state.burst_tracker.add_sample(
            leader_exposure.gross_dollars,
            current_time,
        )
        
        # Check if burst window has expired
        if elapsed >= self.config.burst_window_sec:
            # Check if leader has stabilized
            if self.state.burst_tracker.is_stable(
                self.config.burst_stabilization_threshold,
                self.config.burst_stabilization_count,
            ):
                self.state.phase = MarketPhase.FOLLOW
                self.state.phase_start_time = current_time
                return f"burst_complete_stable (elapsed={elapsed:.1f}s)"
            else:
                # Extended burst - leader still changing
                # Transition anyway but log it
                self.state.phase = MarketPhase.FOLLOW
                self.state.phase_start_time = current_time
                return f"burst_complete_unstable (elapsed={elapsed:.1f}s)"
        
        return None
    
    def _update_follow(self, current_time: datetime) -> Optional[str]:
        """Update during FOLLOW mode."""
        # Check if it's time for RESYNC
        last_resync = self.state.last_resync_time or self.state.phase_start_time
        
        if last_resync:
            elapsed = (current_time - last_resync).total_seconds()
            if elapsed >= self.config.resync_interval_sec:
                self.state.phase = MarketPhase.RESYNC
                return f"resync_due (elapsed={elapsed:.1f}s)"
        
        return None
    
    def should_enter_during_burst(self) -> bool:
        """Check if we should enter a position during BURST.
        
        Only enters once leader stabilizes.
        """
        return self.state.burst_tracker.is_stable(
            self.config.burst_stabilization_threshold,
            self.config.burst_stabilization_count,
        )
    
    def get_burst_target(self) -> Optional[Decimal]:
        """Get the stable target from BURST tracking."""
        return self.state.burst_tracker.get_stable_value(
            self.config.burst_stabilization_count
        )


class GlobalStateMachine:
    """Manages state machines for all markets."""
    
    def __init__(self, config: StateMachineConfig):
        self.config = config
        self.markets: Dict[MarketId, MarketStateMachine] = {}
    
    def get_or_create(self, market_id: MarketId) -> MarketStateMachine:
        """Get or create state machine for a market."""
        if market_id not in self.markets:
            self.markets[market_id] = MarketStateMachine(market_id, self.config)
        return self.markets[market_id]
    
    def on_market_open(self, market_id: MarketId, open_time: datetime) -> None:
        """Handle market open for a specific market."""
        sm = self.get_or_create(market_id)
        sm.on_market_open(open_time)
    
    def on_market_close(self, market_id: MarketId) -> None:
        """Handle market close for a specific market."""
        if market_id in self.markets:
            self.markets[market_id].on_market_close()
    
    def get_state_summary(self) -> Dict[str, Dict]:
        """Get summary of all market states."""
        return {
            market_id: sm.state.to_dict()
            for market_id, sm in self.markets.items()
        }
