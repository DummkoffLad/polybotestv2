"""Bootstrap confidence interval calculator for trading statistics.

Uses scipy.stats.bootstrap for robust non-parametric confidence intervals.
Essential for small-sample trading data where normality cannot be assumed.
"""

from typing import List, Tuple
from decimal import Decimal
import numpy as np
from scipy.stats import bootstrap


class ConfidenceIntervalCalculator:
    """Calculate bootstrap confidence intervals for PnL and win rate metrics.

    Uses percentile bootstrap method with fixed seed for reproducibility.
    Handles edge cases (empty lists, single elements) gracefully.
    """

    def __init__(self, confidence_level: float = 0.95, n_resamples: int = 10000):
        """Initialize calculator with bootstrap parameters.

        Args:
            confidence_level: Confidence level for intervals (default 0.95 for 95% CI)
            n_resamples: Number of bootstrap resamples (default 10000)
        """
        self.confidence_level = confidence_level
        self.n_resamples = n_resamples
        self.random_state = np.random.default_rng(seed=42)  # Fixed seed for reproducibility

    def pnl_confidence_interval(self, pnls: List[Decimal]) -> Tuple[float, float, float]:
        """Calculate bootstrap confidence interval for mean PnL.

        Args:
            pnls: List of PnL values (can be session-level or trade-level)

        Returns:
            Tuple of (mean_pnl, ci_lower, ci_upper)
        """
        # Handle edge cases
        if len(pnls) == 0:
            return (0.0, 0.0, 0.0)

        if len(pnls) == 1:
            value = float(pnls[0])
            return (value, value, value)

        # Convert Decimal to float for numpy compatibility
        data = np.array([float(p) for p in pnls])

        # Calculate mean
        mean_pnl = float(np.mean(data))

        # Bootstrap confidence interval
        # We pass data as a tuple of arrays (required by scipy.stats.bootstrap)
        result = bootstrap(
            (data,),
            np.mean,
            n_resamples=self.n_resamples,
            confidence_level=self.confidence_level,
            method='percentile',
            random_state=self.random_state
        )

        ci_lower = float(result.confidence_interval.low)
        ci_upper = float(result.confidence_interval.high)

        return (mean_pnl, ci_lower, ci_upper)

    def win_rate_confidence_interval(self, pnls: List[Decimal]) -> Tuple[float, float, float]:
        """Calculate bootstrap confidence interval for win rate.

        Win defined as PnL > 0. Returns percentages (0-100).

        Args:
            pnls: List of PnL values

        Returns:
            Tuple of (win_rate, ci_lower, ci_upper) as percentages
        """
        # Handle edge cases
        if len(pnls) == 0:
            return (0.0, 0.0, 0.0)

        # Convert to binary wins (1) and losses (0)
        data = np.array([1.0 if float(p) > 0 else 0.0 for p in pnls])

        # Calculate win rate
        win_rate = float(np.mean(data)) * 100.0

        # Handle single element
        if len(pnls) == 1:
            return (win_rate, win_rate, win_rate)

        # Check if all wins or all losses (no variance)
        if np.all(data == 1.0) or np.all(data == 0.0):
            return (win_rate, win_rate, win_rate)

        # Bootstrap confidence interval
        result = bootstrap(
            (data,),
            np.mean,
            n_resamples=self.n_resamples,
            confidence_level=self.confidence_level,
            method='percentile',
            random_state=self.random_state
        )

        # Convert to percentages
        ci_lower = float(result.confidence_interval.low) * 100.0
        ci_upper = float(result.confidence_interval.high) * 100.0

        return (win_rate, ci_lower, ci_upper)
