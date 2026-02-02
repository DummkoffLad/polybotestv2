"""
Trade quality scoring and selective following.

TradeQualityScorer rates trades from 0.0 (skip) to 1.0 (max size) based on:
- Spread cost (60% weight): High-spread trades eat the edge
- Leader conviction (40% weight): Small trades signal low confidence

SelectiveFollower decides which trades to follow when capital-constrained.
Position count limit prevents over-diversification.
"""

from decimal import Decimal
import logging
from typing import Optional

log = logging.getLogger(__name__)


class TradeQualityScorer:
    """
    Scores trades from 0.0 to 1.0 based on spread cost and leader conviction.

    Spread scoring:
    - <= 50 bps (EXCELLENT_SPREAD_BPS): 1.0 score
    - >= 300 bps (POOR_SPREAD_BPS): 0.0 score
    - Between: linear interpolation

    Conviction scoring:
    - Based on leader trade size relative to their average
    - Capped at 2x average (larger trades don't increase score beyond 2x)
    - Score = min(size_ratio, 2.0) / 2.0

    Composite scoring:
    - 60% spread component (dominant factor for sub-$100 accounts)
    - 40% conviction component
    - Result quantized to 0.01 precision
    """

    EXCELLENT_SPREAD_BPS = Decimal("50")  # <0.5% spread = excellent
    POOR_SPREAD_BPS = Decimal("300")  # >3% spread = poor

    def __init__(
        self,
        leader_avg_size: Decimal = Decimal("100"),
        high_quality_threshold: Decimal = Decimal("0.70"),
        dca_quality_threshold: Decimal = Decimal("0.75"),
    ):
        """
        Initialize quality scorer.

        Args:
            leader_avg_size: Leader's average trade size in dollars
            high_quality_threshold: Minimum quality score to take initial trade
            dca_quality_threshold: Minimum quality score for DCA follow (both original and new)
        """
        self.leader_avg_size = leader_avg_size
        self.high_quality_threshold = high_quality_threshold
        self.dca_quality_threshold = dca_quality_threshold

    def score_spread(self, spread_bps: Decimal) -> Decimal:
        """
        Score spread cost from 0.0 (poor) to 1.0 (excellent).

        Args:
            spread_bps: Spread in basis points (e.g. 100 = 1%)

        Returns:
            Score in [0.0, 1.0] range, quantized to 0.01
        """
        if spread_bps <= self.EXCELLENT_SPREAD_BPS:
            score = Decimal("1.0")
        elif spread_bps >= self.POOR_SPREAD_BPS:
            score = Decimal("0.0")
        else:
            # Linear interpolation between excellent and poor thresholds
            spread_range = self.POOR_SPREAD_BPS - self.EXCELLENT_SPREAD_BPS
            score = (self.POOR_SPREAD_BPS - spread_bps) / spread_range

        result = score.quantize(Decimal("0.01"))
        log.debug(f"Spread score: {spread_bps} bps -> {result}")
        return result

    def score_conviction(self, leader_dollars: Decimal) -> Decimal:
        """
        Score leader conviction from 0.0 (low) to 1.0 (high).

        Based on trade size relative to leader's average.
        Capped at 2x average (larger trades don't increase score).

        Args:
            leader_dollars: Leader trade size in dollars

        Returns:
            Score in [0.0, 1.0] range, quantized to 0.01
        """
        if self.leader_avg_size == Decimal("0"):
            # Avoid division by zero
            score = Decimal("0.0")
        else:
            size_ratio = leader_dollars / self.leader_avg_size
            # Cap at 2x average
            capped_ratio = min(size_ratio, Decimal("2.0"))
            # Normalize to [0, 1] range
            score = capped_ratio / Decimal("2.0")

        result = score.quantize(Decimal("0.01"))
        log.debug(f"Conviction score: ${leader_dollars} (avg ${self.leader_avg_size}) -> {result}")
        return result

    def score_trade(self, spread_bps: Decimal, leader_dollars: Decimal) -> Decimal:
        """
        Compute composite quality score for a trade.

        Combines spread cost (60% weight) and leader conviction (40% weight).
        Research showed spread cost is the dominant factor for sub-$100 accounts
        where every basis point matters.

        Args:
            spread_bps: Spread in basis points
            leader_dollars: Leader trade size in dollars

        Returns:
            Composite score in [0.0, 1.0] range, quantized to 0.01
        """
        spread_score = self.score_spread(spread_bps)
        conviction_score = self.score_conviction(leader_dollars)

        spread_component = spread_score * Decimal("0.60")
        conviction_component = conviction_score * Decimal("0.40")

        composite = spread_component + conviction_component
        result = composite.quantize(Decimal("0.01"))

        log.debug(
            f"Trade score: spread={spread_score} (60%), conviction={conviction_score} (40%) -> {result}"
        )
        return result

    def should_take_trade(self, quality_score: Decimal) -> bool:
        """
        Determine if a trade meets quality threshold for initial entry.

        Args:
            quality_score: Quality score from score_trade()

        Returns:
            True if quality >= high_quality_threshold
        """
        should_take = quality_score >= self.high_quality_threshold
        if not should_take:
            log.info(
                f"Skipping trade: quality {quality_score} below threshold {self.high_quality_threshold}"
            )
        return should_take

    def should_follow_dca(self, original_quality: Decimal, new_quality: Decimal) -> bool:
        """
        Determine if a DCA trade should be followed.

        Requires both original entry and new DCA trade to meet DCA quality threshold.
        This prevents averaging down into deteriorating trade quality.

        Args:
            original_quality: Quality score of original position entry
            new_quality: Quality score of new DCA trade

        Returns:
            True if both qualities >= dca_quality_threshold
        """
        should_follow = (
            original_quality >= self.dca_quality_threshold
            and new_quality >= self.dca_quality_threshold
        )
        if not should_follow:
            log.info(
                f"Skipping DCA: original={original_quality}, new={new_quality}, "
                f"threshold={self.dca_quality_threshold}"
            )
        return should_follow


class SelectiveFollower:
    """
    Decides which trades to follow when capital-constrained.

    Uses simple position count limit to prevent over-diversification.
    FCFS (first-come-first-served) is sufficient for single-leader following.
    """

    def __init__(self, max_positions: int = 5):
        """
        Initialize selective follower.

        Args:
            max_positions: Maximum number of concurrent positions
        """
        self.max_positions = max_positions

    def can_open_position(self, current_position_count: int) -> tuple[bool, Optional[str]]:
        """
        Check if a new position can be opened.

        Args:
            current_position_count: Number of currently open positions

        Returns:
            (can_open, reason) where:
            - can_open: True if position count < max_positions
            - reason: "max_positions_reached" if blocked, None otherwise
        """
        if current_position_count >= self.max_positions:
            log.info(
                f"Cannot open position: {current_position_count}/{self.max_positions} positions"
            )
            return (False, "max_positions_reached")
        return (True, None)
