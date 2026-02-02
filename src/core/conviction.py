"""
ConvictionScorer - Leader conviction-based confidence multiplier.

Calculates multiplier in [0.25, 2.0] range from leader signals:
- Position size relative to leader average (60% weight)
- Scale-in behavior (25% weight)
- Entry speed (15% weight)

Result quantized to Decimal("0.01").
"""
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean


@dataclass(frozen=True)
class ConvictionSignals:
    """Leader signals for conviction calculation."""
    position_size_dollars: Decimal
    entry_speed_seconds: float
    is_scale_in: bool


class ConvictionScorer:
    """
    Calculates conviction multiplier from leader behavior signals.

    Weighting:
    - Position size: 60%
    - Scale-in: 25%
    - Entry speed: 15%

    Range: [0.25, 2.0], quantized to 0.01
    """

    def __init__(self, leader_avg_size: Decimal):
        """
        Initialize ConvictionScorer.

        Args:
            leader_avg_size: Leader's average trade size in dollars
        """
        self.leader_avg_size = leader_avg_size

    def calculate_multiplier(self, signals: ConvictionSignals) -> Decimal:
        """
        Calculate conviction multiplier from leader signals.

        Args:
            signals: Leader conviction signals

        Returns:
            Multiplier in [0.25, 2.0], quantized to 0.01
        """
        # Position size component (60% weight)
        # Maps size ratio to a score in expanded range to allow full [0.25, 2.0] output
        size_ratio = signals.position_size_dollars / self.leader_avg_size
        # Map to score range that allows reaching extremes with 60% weight
        # To hit 0.25 floor: 1.0 + 0.6*(score - 1.0) = 0.25 -> score = -0.25
        # To hit 2.0 cap: 1.0 + 0.6*(score - 1.0) = 2.0 -> score = 2.67
        if size_ratio <= Decimal("0.2"):
            # Tiny positions (20% or less of average) -> floor
            size_score = Decimal("-0.25")
        elif size_ratio >= Decimal("2.5"):
            # Large positions (2.5x+ average) -> cap
            size_score = Decimal("2.67")
        elif size_ratio <= Decimal("1.0"):
            # Below average: map linearly to lower scores
            # 0.2x -> -0.25, 1.0x -> 1.0
            # Linear interpolation
            t = (size_ratio - Decimal("0.2")) / (Decimal("1.0") - Decimal("0.2"))
            size_score = Decimal("-0.25") + t * (Decimal("1.0") - Decimal("-0.25"))
        else:
            # Above average: map with amplification
            # 1.0x -> 1.0, 2.0x -> 2.5+, 2.5x -> 2.67
            # Use steeper curve for above-average positions
            t = (size_ratio - Decimal("1.0")) / (Decimal("2.5") - Decimal("1.0"))
            # Quadratic amplification for larger positions
            size_score = Decimal("1.0") + (t ** 2) * Decimal("3.0") + t * Decimal("0.67")

        # Scale-in component (25% weight)
        # 1.0 = neutral (no scale), 1.5 = boost (scale-in)
        scale_score = Decimal("1.5") if signals.is_scale_in else Decimal("1.0")

        # Entry speed component (15% weight)
        # 0.8 = fast (impulsive), 1.0 = normal, 1.2 = deliberate
        if signals.entry_speed_seconds < 5.0:
            speed_score = Decimal("0.8")
        elif signals.entry_speed_seconds > 30.0:
            speed_score = Decimal("1.2")
        else:
            speed_score = Decimal("1.0")

        # Weighted combination
        w_size = Decimal("0.60")
        w_scale = Decimal("0.25")
        w_speed = Decimal("0.15")

        # Each component contributes its weighted deviation from 1.0
        size_deviation = (size_score - Decimal("1.0")) * w_size
        scale_deviation = (scale_score - Decimal("1.0")) * w_scale
        speed_deviation = (speed_score - Decimal("1.0")) * w_speed

        # Combine: 1.0 + weighted deviations
        multiplier = Decimal("1.0") + size_deviation + scale_deviation + speed_deviation

        # Clamp to [0.25, 2.0]
        multiplier = max(Decimal("0.25"), min(Decimal("2.00"), multiplier))

        # Quantize to 0.01
        return multiplier.quantize(Decimal("0.01"))

    def update_leader_avg_size(self, recent_trades: list[Decimal]) -> None:
        """
        Update leader average size from recent trades.

        Args:
            recent_trades: List of recent trade sizes in dollars
        """
        if recent_trades:
            # Calculate average as Decimal
            total = sum(recent_trades)
            count = Decimal(len(recent_trades))
            self.leader_avg_size = (total / count).quantize(Decimal("0.01"))
