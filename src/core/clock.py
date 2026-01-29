"""Injectable clock abstraction for deterministic time handling.

This module provides a clock interface that can be:
- SystemClock: Real wall-clock time for live operation
- SimulatedClock: Controlled time for replay/simulation

Using an injectable clock enables:
1. Deterministic replay of past events
2. Testable time-dependent logic
3. Consistent timestamps across decision traces
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional
import time


class Clock(ABC):
    """Abstract clock interface."""
    
    @abstractmethod
    def now(self) -> datetime:
        """Get current time as timezone-aware datetime (UTC)."""
        pass
    
    @abstractmethod
    def now_unix(self) -> float:
        """Get current time as Unix timestamp."""
        pass
    
    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep for specified duration."""
        pass
    
    def market_hour_start(self, reference: Optional[datetime] = None) -> datetime:
        """Get the start of the current market hour.
        
        Markets are hourly in ET timezone, but we work in UTC internally.
        This returns the start of the current hour in UTC.
        """
        ref = reference or self.now()
        return ref.replace(minute=0, second=0, microsecond=0)
    
    def seconds_since_market_open(self, reference: Optional[datetime] = None) -> float:
        """Get seconds elapsed since the current market hour started."""
        ref = reference or self.now()
        hour_start = self.market_hour_start(ref)
        return (ref - hour_start).total_seconds()


class SystemClock(Clock):
    """Real system clock for live operation."""
    
    def now(self) -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)
    
    def now_unix(self) -> float:
        """Get current Unix timestamp."""
        return time.time()
    
    def sleep(self, seconds: float) -> None:
        """Sleep using real time."""
        if seconds > 0:
            time.sleep(seconds)


class SimulatedClock(Clock):
    """Simulated clock for replay and testing.
    
    Time advances manually or automatically based on events.
    """
    
    def __init__(self, start_time: Optional[datetime] = None):
        """Initialize with a start time.
        
        Args:
            start_time: Starting datetime (UTC). Defaults to epoch.
        """
        self._current_time = start_time or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._sleep_callback: Optional[callable] = None
    
    def now(self) -> datetime:
        """Get current simulated time."""
        return self._current_time
    
    def now_unix(self) -> float:
        """Get current simulated Unix timestamp."""
        return self._current_time.timestamp()
    
    def sleep(self, seconds: float) -> None:
        """Advance simulated time by the sleep duration.
        
        Does not actually sleep - just advances the clock.
        """
        if seconds > 0:
            self._current_time += timedelta(seconds=seconds)
            if self._sleep_callback:
                self._sleep_callback(seconds)
    
    def set_time(self, new_time: datetime) -> None:
        """Set the current simulated time."""
        if new_time.tzinfo is None:
            new_time = new_time.replace(tzinfo=timezone.utc)
        self._current_time = new_time
    
    def advance(self, seconds: float) -> None:
        """Advance time by specified seconds."""
        self._current_time += timedelta(seconds=seconds)
    
    def advance_to(self, target: datetime) -> None:
        """Advance time to a specific point."""
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        if target > self._current_time:
            self._current_time = target
    
    def set_sleep_callback(self, callback: Optional[callable]) -> None:
        """Set a callback to be invoked on sleep.
        
        Useful for triggering replay events when time advances.
        """
        self._sleep_callback = callback


def create_clock(mode: str, start_time: Optional[datetime] = None) -> Clock:
    """Factory function to create appropriate clock.
    
    Args:
        mode: Execution mode string ("DRY_RUN", "PAPER", "LIVE")
        start_time: For simulated clock, the starting time
        
    Returns:
        Appropriate Clock instance
    """
    if mode == "PAPER":
        return SimulatedClock(start_time)
    else:
        return SystemClock()
