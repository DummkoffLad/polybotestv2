"""Mixins for common strategy behaviors.

Provides reusable components that can be mixed into strategy classes:
- SkipHelperMixin: Skip tracking with reason counters
- HourlyBudgetMixin: Hourly budget reset and tracking

Each mixin manages its own state and requires explicit initialization.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from .base import TradeDecision

logger = logging.getLogger(__name__)


class SkipHelperMixin:
    """Mixin for skip tracking with reason counters.

    Provides:
    - _skip(reason) method for consistent skip handling
    - skip_reasons dict for tracking skip frequency
    - skips counter for total skips

    Usage:
        class MyStrategy(SkipHelperMixin, Strategy):
            def __init__(self):
                SkipHelperMixin.__init__(self)
                # ... rest of init
    """

    def __init__(self):
        """Initialize skip tracking state."""
        self.skips = 0
        self.skip_reasons: Dict[str, int] = {}

    def _skip(self, reason: str) -> TradeDecision:
        """Record a skip with the given reason and return skip decision.

        Args:
            reason: String key for skip reason (e.g., "no_price", "budget")

        Returns:
            TradeDecision.skip(reason)
        """
        self.skips += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        return TradeDecision.skip(reason)


class HourlyBudgetMixin:
    """Mixin for hourly budget reset and tracking.

    Provides:
    - _check_hourly_reset(event_time) method to reset budget on hour change
    - hourly_budget_used tracking
    - _current_hour state

    Usage:
        class MyStrategy(HourlyBudgetMixin, Strategy):
            def __init__(self):
                HourlyBudgetMixin.__init__(self)
                # ... rest of init

            def on_event(self, event):
                self._check_hourly_reset(event.trade.timestamp)
                # ... rest of logic
    """

    def __init__(self):
        """Initialize hourly budget tracking state."""
        self.hourly_budget_used = Decimal("0")
        self._current_hour: Optional[int] = None

    def _check_hourly_reset(self, event_time: datetime) -> None:
        """Check if hour changed and reset budget if needed.

        Args:
            event_time: Timestamp from current event
        """
        current_hour = event_time.hour
        if self._current_hour is not None and current_hour != self._current_hour:
            logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
            self.hourly_budget_used = Decimal("0")
        self._current_hour = current_hour
