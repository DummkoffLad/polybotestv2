"""Capital protection system with two-tier floor mechanism.

The CapitalManager enforces drawdown-based trading restrictions:
- NORMAL mode: No restrictions
- SOFT_FLOOR (10% drawdown): Only exceptional quality trades (default >= 0.85)
- HARD_FLOOR (30% drawdown): All trading halted

This prevents grinding to zero by progressively restricting activity as drawdown increases.
"""
import logging
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class TradingMode(Enum):
    """Trading mode based on drawdown from high water mark."""
    NORMAL = "NORMAL"
    SOFT_FLOOR = "SOFT_FLOOR"
    HARD_FLOOR = "HARD_FLOOR"


class CapitalManager:
    """Manages capital protection via two-tier floor system.

    Tracks high water mark (HWM) and determines trading mode based on
    current equity as percentage of HWM:
    - Above soft_floor_pct: NORMAL mode (unrestricted)
    - Between soft_floor_pct and hard_floor_pct: SOFT_FLOOR (exceptional trades only)
    - Below hard_floor_pct: HARD_FLOOR (all trading stopped)

    Examples:
        >>> cm = CapitalManager()
        >>> cm.initialize(Decimal("100.00"))
        >>> cm.check_floor_status(Decimal("95.00"))  # 5% DD -> NORMAL
        TradingMode.NORMAL
        >>> cm.check_floor_status(Decimal("89.00"))  # 11% DD -> SOFT_FLOOR
        TradingMode.SOFT_FLOOR
        >>> cm.can_enter_new_trade(Decimal("0.90"))  # Exceptional quality
        (True, None)
        >>> cm.can_enter_new_trade(Decimal("0.80"))  # Below threshold
        (False, 'soft_floor_low_quality')
    """

    def __init__(
        self,
        soft_floor_pct: Decimal = Decimal("90.0"),
        hard_floor_pct: Decimal = Decimal("70.0"),
        exceptional_quality_threshold: Decimal = Decimal("0.85")
    ):
        """Initialize capital manager with floor thresholds.

        Args:
            soft_floor_pct: Equity % of HWM below which soft floor activates (default 90% = 10% DD)
            hard_floor_pct: Equity % of HWM below which hard floor activates (default 70% = 30% DD)
            exceptional_quality_threshold: Minimum quality score for trades at soft floor (default 0.85)
        """
        self._soft_floor_pct = soft_floor_pct
        self._hard_floor_pct = hard_floor_pct
        self._exceptional_quality_threshold = exceptional_quality_threshold

        # State
        self._high_water_mark: Decimal = Decimal("0")
        self._mode: TradingMode = TradingMode.HARD_FLOOR  # Start in HARD_FLOOR until initialized

    def initialize(self, starting_capital: Decimal) -> None:
        """Initialize with starting capital.

        Args:
            starting_capital: Initial capital to set as HWM
        """
        self._high_water_mark = starting_capital
        self._mode = TradingMode.NORMAL
        logger.info(f"CapitalManager initialized: HWM=${starting_capital}, mode={self._mode.value}")

    def update_high_water_mark(self, current_equity: Decimal) -> None:
        """Update high water mark if current equity exceeds it.

        HWM only increases, never decreases.

        Args:
            current_equity: Current account equity
        """
        if current_equity > self._high_water_mark:
            old_hwm = self._high_water_mark
            self._high_water_mark = current_equity
            logger.info(f"HWM updated: ${old_hwm} -> ${current_equity}")

    def check_floor_status(self, current_equity: Decimal) -> TradingMode:
        """Check floor status and update mode based on current equity.

        Calculates equity as percentage of HWM and determines mode:
        - <= hard_floor_pct: HARD_FLOOR
        - <= soft_floor_pct: SOFT_FLOOR
        - > soft_floor_pct: NORMAL

        Special cases:
        - Zero HWM (uninitialized): returns HARD_FLOOR
        - Zero equity: returns HARD_FLOOR

        Args:
            current_equity: Current account equity

        Returns:
            TradingMode based on drawdown level
        """
        # Handle edge cases
        if self._high_water_mark == Decimal("0"):
            # Uninitialized - prevent division by zero
            self._mode = TradingMode.HARD_FLOOR
            return self._mode

        if current_equity == Decimal("0"):
            # Zero equity - definitely hard floor
            old_mode = self._mode
            self._mode = TradingMode.HARD_FLOOR
            if old_mode != self._mode:
                logger.warning(f"Mode transition: {old_mode.value} -> {self._mode.value} (zero equity)")
            return self._mode

        # Calculate equity as percentage of HWM
        equity_pct = (current_equity / self._high_water_mark) * Decimal("100")

        # Determine mode based on thresholds
        old_mode = self._mode

        if equity_pct <= self._hard_floor_pct:
            self._mode = TradingMode.HARD_FLOOR
        elif equity_pct <= self._soft_floor_pct:
            self._mode = TradingMode.SOFT_FLOOR
        else:
            self._mode = TradingMode.NORMAL

        # Log mode transitions
        if old_mode != self._mode:
            drawdown_pct = Decimal("100") - equity_pct
            logger.info(
                f"Mode transition: {old_mode.value} -> {self._mode.value} "
                f"(equity=${current_equity}, HWM=${self._high_water_mark}, DD={drawdown_pct:.2f}%)"
            )

        return self._mode

    def can_enter_new_trade(self, quality_score: Decimal) -> Tuple[bool, Optional[str]]:
        """Check if new trade entry is allowed based on current mode and quality.

        Args:
            quality_score: Quality score of the proposed trade (0.0 to 1.0)

        Returns:
            Tuple of (can_enter: bool, reason: Optional[str])
            - NORMAL mode: (True, None) for any quality
            - SOFT_FLOOR mode: (True, None) if quality >= threshold, else (False, "soft_floor_low_quality")
            - HARD_FLOOR mode: (False, "hard_floor_hit") regardless of quality
        """
        if self._mode == TradingMode.HARD_FLOOR:
            return (False, "hard_floor_hit")

        if self._mode == TradingMode.SOFT_FLOOR:
            if quality_score >= self._exceptional_quality_threshold:
                return (True, None)
            else:
                return (False, "soft_floor_low_quality")

        # NORMAL mode
        return (True, None)

    def can_manage_positions(self) -> bool:
        """Check if position management (sells, adjustments) is allowed.

        Returns:
            True if position management allowed, False if blocked
            - NORMAL mode: True
            - SOFT_FLOOR mode: True (can manage existing positions)
            - HARD_FLOOR mode: False (no activity allowed)
        """
        return self._mode != TradingMode.HARD_FLOOR

    @property
    def mode(self) -> TradingMode:
        """Current trading mode."""
        return self._mode

    @property
    def high_water_mark(self) -> Decimal:
        """Current high water mark."""
        return self._high_water_mark
