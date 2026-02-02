"""
KellyCalculator: Kelly criterion position sizing calculator.

Computes optimal position sizes using the Kelly criterion formula, with support
for fractional Kelly (default: Half Kelly = 0.5x) and equity caps.

Key features:
- Half Kelly (0.5x) position sizing by default
- 20% equity cap (risk management)
- Returns None for negative edge (don't bet)
- All calculations use Decimal with quantize("0.01")

Kelly formula:
    f* = (p*b - q) / b
    where:
        p = win_rate
        q = 1 - win_rate
        b = avg_win / avg_loss

Expected value:
    E = (W x AvgWin) - ((1-W) x AvgLoss)
"""

import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


class KellyCalculator:
    """
    Calculates position sizes using Kelly criterion.

    Uses fractional Kelly (default 0.5) to reduce volatility while maintaining
    most of the growth rate. Caps position at 20% of equity for risk management.
    """

    # Maximum position size as percentage of equity (risk management)
    MAX_POSITION_PCT = Decimal("0.20")

    def __init__(self, kelly_fraction: Decimal = Decimal("0.5")):
        """
        Initialize KellyCalculator.

        Args:
            kelly_fraction: Fraction of Kelly to use (default 0.5 = Half Kelly)
                           0.5 reduces volatility while keeping ~75% of growth rate
        """
        self.kelly_fraction = kelly_fraction

        logger.info(
            f"KellyCalculator initialized: kelly_fraction={kelly_fraction}, "
            f"max_position_pct={self.MAX_POSITION_PCT}"
        )

    def calculate_expected_value(
        self,
        win_rate: Decimal,
        avg_win_pct: Decimal,
        avg_loss_pct: Decimal
    ) -> Decimal:
        """
        Calculate expected value of the edge.

        Formula: E = (W x AvgWin) - ((1-W) x AvgLoss)

        Args:
            win_rate: Win rate (0 to 1)
            avg_win_pct: Average win percentage (e.g., 0.15 for 15%)
            avg_loss_pct: Average loss percentage (e.g., 0.10 for 10%)

        Returns:
            Expected value (positive = edge, negative = no edge)
        """
        loss_rate = Decimal("1") - win_rate
        ev = (win_rate * avg_win_pct) - (loss_rate * avg_loss_pct)
        return ev.quantize(Decimal("0.01"))

    def calculate_kelly_size(
        self,
        win_rate: Decimal,
        avg_win_pct: Decimal,
        avg_loss_pct: Decimal,
        current_equity: Decimal
    ) -> Optional[Decimal]:
        """
        Calculate Kelly position size.

        Args:
            win_rate: Win rate (0 to 1)
            avg_win_pct: Average win percentage (e.g., 0.15 for 15%)
            avg_loss_pct: Average loss percentage (e.g., 0.10 for 10%)
            current_equity: Current equity (dollars)

        Returns:
            Position size in dollars, or None if:
            - Negative edge (don't bet)
            - Invalid inputs (zero/one win rate, zero avg_loss)

        Notes:
            - Kelly formula: f* = (p*b - q) / b
            - Fractional Kelly: f* * kelly_fraction
            - Capped at 20% of equity
        """
        # Validate inputs
        if win_rate <= Decimal("0") or win_rate >= Decimal("1"):
            logger.debug(f"Invalid win_rate={win_rate}, returning None")
            return None

        if avg_loss_pct <= Decimal("0"):
            logger.debug(f"Invalid avg_loss_pct={avg_loss_pct}, returning None")
            return None

        # Calculate expected value
        ev = self.calculate_expected_value(win_rate, avg_win_pct, avg_loss_pct)

        # Don't bet on negative edge
        if ev <= Decimal("0"):
            logger.debug(f"Negative edge (EV={ev}), returning None")
            return None

        # Calculate Kelly fraction
        # f* = (p*b - q) / b
        # where b = avg_win / avg_loss
        b = avg_win_pct / avg_loss_pct
        loss_rate = Decimal("1") - win_rate

        kelly_pct = (win_rate * b - loss_rate) / b

        # Apply fractional Kelly
        fractional_kelly_pct = kelly_pct * self.kelly_fraction

        # Cap at maximum position percentage
        capped_kelly_pct = min(fractional_kelly_pct, self.MAX_POSITION_PCT)

        # Convert to dollar amount
        position_size = (capped_kelly_pct * current_equity).quantize(Decimal("0.01"))

        logger.debug(
            f"Kelly calculation: win_rate={win_rate}, b={b:.4f}, "
            f"kelly_pct={kelly_pct:.4f}, fractional={fractional_kelly_pct:.4f}, "
            f"capped={capped_kelly_pct:.4f}, size=${position_size}"
        )

        return position_size
