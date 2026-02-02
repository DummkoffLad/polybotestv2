"""Injectable clock for deterministic time handling."""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional
import time

class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: pass
    @abstractmethod
    def now_unix(self) -> float: pass
    @abstractmethod
    def sleep(self, seconds: float) -> None: pass
    
    def market_hour_start(self, ref: Optional[datetime] = None) -> datetime:
        r = ref or self.now()
        return r.replace(minute=0, second=0, microsecond=0)
    
    def seconds_since_market_open(self, ref: Optional[datetime] = None) -> float:
        r = ref or self.now()
        return (r - self.market_hour_start(r)).total_seconds()

class SystemClock(Clock):
    def now(self) -> datetime: return datetime.now(timezone.utc)
    def now_unix(self) -> float: return time.time()
    def sleep(self, seconds: float) -> None:
        if seconds > 0: time.sleep(seconds)

class SimulatedClock(Clock):
    def __init__(self, start: Optional[datetime] = None):
        self._time = start or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._sleep_cb: Optional[callable] = None
    
    def now(self) -> datetime: return self._time
    def now_unix(self) -> float: return self._time.timestamp()
    
    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._time += timedelta(seconds=seconds)
            if self._sleep_cb: self._sleep_cb(seconds)
    
    def set_time(self, t: datetime) -> None:
        self._time = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    
    def advance(self, seconds: float) -> None:
        self._time += timedelta(seconds=seconds)
    
    def advance_to(self, target: datetime) -> None:
        t = target if target.tzinfo else target.replace(tzinfo=timezone.utc)
        if t > self._time: self._time = t
    
    def set_sleep_callback(self, cb: Optional[callable]) -> None:
        self._sleep_cb = cb

def create_clock(mode: str, start: Optional[datetime] = None) -> Clock:
    return SimulatedClock(start) if mode == "PAPER" else SystemClock()
