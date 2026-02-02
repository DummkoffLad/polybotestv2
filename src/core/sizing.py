"""Dynamic position sizing engine.

Calculates position sizes as a percentage of current equity, adjusted for:
- Trade quality score
- Consecutive losses (reduces size after threshold)
- Maximum position cap

All calculations use Decimal for precision, quantized to 0.01 (cents).
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class SizingConfig:
    """Configuration for DynamicSizer.

    Attributes:
        base_risk_pct: Base risk per trade as % of current equity (e.g., 1.0 = 1%)
        max_position_pct: Maximum single position as % of current equity (e.g., 10.0 = 10%)
        consecutive_loss_threshold: Reduce size after N consecutive losses (default: 3)
        size_reduction_after_losses: Multiply size by this factor after consecutive losses (default: 0.5 = 50% reduction)
        max_concurrent_positions: Maximum number of concurrent positions (default: 5)
    """
    base_risk_pct: Decimal = Decimal("1.0")
    max_position_pct: Decimal = Decimal("10.0")
    consecutive_loss_threshold: int = 3
    size_reduction_after_losses: Decimal = Decimal("0.5")
    max_concurrent_positions: int = 5


class DynamicSizer:
    """Dynamic position sizing engine.

    Calculates position sizes as a percentage of current equity, with adjustments for:
    - Trade quality (maps 0.0->0.5x, 1.0->1.5x)
    - Consecutive losses (reduces size by factor after threshold)
    - Maximum position cap (enforces max % of equity)

    Example:
        config = SizingConfig(base_risk_pct=Decimal("1.0"))
        sizer = DynamicSizer(config)
        sizer.initialize(Decimal("100"))  # Starting capital

        # $100 equity, quality 0.50 -> $1.00 position
        size = sizer.calculate_position_size(Decimal("100"), Decimal("0.50"))

        # After 3 losses, size reduces by 50%
        sizer.update_after_trade(Decimal("-5"))
        sizer.update_after_trade(Decimal("-3"))
        sizer.update_after_trade(Decimal("-2"))
        size = sizer.calculate_position_size(Decimal("90"), Decimal("0.50"))  # $0.45 (reduced)

        # Win resets consecutive losses
        sizer.update_after_trade(Decimal("4"))
        size = sizer.calculate_position_size(Decimal("94"), Decimal("0.50"))  # $0.94 (back to normal)
    """

    def __init__(self, config: SizingConfig):
        """Initialize DynamicSizer with configuration.

        Args:
            config: SizingConfig with base_risk_pct, max_position_pct, thresholds
        """
        self.config = config
        self.consecutive_losses: int = 0
        self.high_water_mark: Decimal = Decimal("0")
        self.starting_capital: Decimal = Decimal("0")

    def initialize(self, starting_capital: Decimal) -> None:
        """Initialize sizer with starting capital.

        Sets starting_capital and high_water_mark to the initial capital.

        Args:
            starting_capital: Initial capital amount
        """
        self.starting_capital = starting_capital
        self.high_water_mark = starting_capital

    def calculate_position_size(
        self,
        current_equity: Decimal,
        quality_score: Decimal
    ) -> Decimal:
        """Calculate position size for a trade.

        Formula:
        1. base_size = current_equity * (base_risk_pct / 100)
        2. If consecutive_losses >= threshold: base_size *= size_reduction_after_losses
        3. quality_multiplier = 0.5 + quality_score (maps 0.0->0.5x, 1.0->1.5x)
        4. adjusted_size = base_size * quality_multiplier
        5. Cap at max_position_pct of current_equity
        6. Quantize to 0.01

        Args:
            current_equity: Current portfolio equity (not starting capital)
            quality_score: Trade quality score in [0.0, 1.0] range

        Returns:
            Position size in dollars, quantized to 0.01
        """
        # Base size as % of current equity
        base_size = current_equity * (self.config.base_risk_pct / Decimal("100"))

        # Apply consecutive loss reduction if threshold reached
        if self.consecutive_losses >= self.config.consecutive_loss_threshold:
            base_size *= self.config.size_reduction_after_losses

        # Apply quality multiplier: 0.0 -> 0.5x, 1.0 -> 1.5x
        quality_multiplier = Decimal("0.5") + quality_score
        adjusted_size = base_size * quality_multiplier

        # Cap at max position % of current equity
        max_size = current_equity * (self.config.max_position_pct / Decimal("100"))
        final_size = min(adjusted_size, max_size)

        # Quantize to cents
        return final_size.quantize(Decimal("0.01"))

    def update_after_trade(self, pnl: Decimal) -> None:
        """Update state after a trade completes.

        If trade lost money (pnl < 0), increment consecutive_losses.
        If trade made money or broke even (pnl >= 0), reset consecutive_losses to 0.

        Args:
            pnl: Profit/loss from the trade (negative = loss, positive = win)
        """
        if pnl < Decimal("0"):
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def update_high_water_mark(self, current_equity: Decimal) -> None:
        """Update high-water mark (peak equity seen).

        High-water mark only increases, never decreases.

        Args:
            current_equity: Current portfolio equity
        """
        if current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
