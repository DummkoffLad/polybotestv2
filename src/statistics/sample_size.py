"""Sample size adequacy checker for trading statistics.

Prevents over-interpreting results from insufficient data.
Uses research-backed thresholds for trading analysis.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List
import math


# Research-backed thresholds (from 06-RESEARCH.md)
MINIMUM_FLOOR = 30  # CLT baseline - bare minimum
BASIC_RELIABILITY = 100  # Basic confidence in results
INSTITUTIONAL_GRADE = 200  # High confidence, institutional standards


@dataclass
class SampleSizeWarning:
    """Result of sample size adequacy check.

    Attributes:
        trade_count: Number of trades in sample
        minimum_required: Minimum trades required for target confidence
        confidence_level: Target confidence level ("minimum", "basic", "high")
        warning_message: Human-readable warning or approval message
        is_adequate: Whether sample size meets threshold
    """

    trade_count: int
    minimum_required: int
    confidence_level: str
    warning_message: str
    is_adequate: bool


class SampleSizeChecker:
    """Check if sample size is adequate for statistical conclusions.

    Uses research-backed thresholds:
    - minimum: 30 trades (CLT baseline)
    - basic: 100 trades (basic reliability)
    - high: 200 trades (institutional grade)
    """

    def __init__(self, target_confidence: str = "basic"):
        """Initialize checker with target confidence level.

        Args:
            target_confidence: One of "minimum", "basic", or "high" (default "basic")
        """
        self.target_confidence = target_confidence

        # Map confidence levels to thresholds
        self.thresholds = {
            "minimum": MINIMUM_FLOOR,
            "basic": BASIC_RELIABILITY,
            "high": INSTITUTIONAL_GRADE,
        }

        if target_confidence not in self.thresholds:
            raise ValueError(
                f"Invalid target_confidence: {target_confidence}. "
                f"Must be one of: {list(self.thresholds.keys())}"
            )

    def check_adequacy(self, pnls: List[Decimal]) -> SampleSizeWarning:
        """Check if sample size is adequate for target confidence level.

        Args:
            pnls: List of PnL values (trade-level or session-level)

        Returns:
            SampleSizeWarning with adequacy assessment
        """
        trade_count = len(pnls)
        minimum_required = self.thresholds[self.target_confidence]
        is_adequate = trade_count >= minimum_required

        if is_adequate:
            warning_message = (
                f"Sample size adequate: {trade_count} trades meets "
                f"{self.target_confidence} confidence threshold ({minimum_required} trades)."
            )
        else:
            shortage = minimum_required - trade_count
            warning_message = (
                f"Warning: Sample size inadequate. Have {trade_count} trades, "
                f"need {minimum_required} for {self.target_confidence} confidence. "
                f"Missing {shortage} trades."
            )

        return SampleSizeWarning(
            trade_count=trade_count,
            minimum_required=minimum_required,
            confidence_level=self.target_confidence,
            warning_message=warning_message,
            is_adequate=is_adequate,
        )

    def recommend_more_data(self, current_count: int) -> str:
        """Recommend how much more data to collect.

        Assumes ~10 trades per session (conservative estimate).

        Args:
            current_count: Current number of trades

        Returns:
            Human-readable recommendation message
        """
        minimum_required = self.thresholds[self.target_confidence]

        if current_count >= minimum_required:
            return (
                f"Sample size adequate: {current_count} trades meets "
                f"{self.target_confidence} confidence threshold ({minimum_required} trades)."
            )

        shortage = minimum_required - current_count
        sessions_needed = math.ceil(shortage / 10)  # Conservative: 10 trades per session

        return (
            f"Recommendation: Collect {shortage} more trades to reach "
            f"{self.target_confidence} confidence ({minimum_required} trades total). "
            f"Estimated sessions needed: ~{sessions_needed} (assuming 10 trades/session)."
        )
