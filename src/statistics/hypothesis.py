"""Strategy comparison using hypothesis testing.

Uses paired t-tests to determine if one strategy significantly outperforms another
when tested on the same sessions (paired observations).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List
from scipy.stats import ttest_rel


@dataclass
class ComparisonResult:
    """Result of strategy comparison analysis."""

    strategy_a_mean: Decimal
    strategy_b_mean: Decimal
    mean_difference: Decimal  # b - a (positive means B wins)
    t_statistic: float
    p_value: float
    is_significant: bool
    interpretation: str


class StrategyComparator:
    """Statistical comparator for strategy performance using paired t-tests."""

    def __init__(self, significance_level: float = 0.05):
        """Initialize comparator with significance threshold.

        Args:
            significance_level: P-value threshold for significance (default 0.05)
        """
        self.significance_level = significance_level

    def compare_strategies(
        self,
        strategy_a_pnls: List[Decimal],
        strategy_b_pnls: List[Decimal],
        strategy_a_name: str = "Strategy A",
        strategy_b_name: str = "Strategy B",
    ) -> ComparisonResult:
        """Compare two strategies using paired t-test.

        Uses scipy.stats.ttest_rel for battle-tested statistical accuracy.
        Paired t-test is appropriate because strategies are tested on the same
        sessions (paired observations).

        Args:
            strategy_a_pnls: List of Strategy A session PnLs
            strategy_b_pnls: List of Strategy B session PnLs (must be same length)
            strategy_a_name: Name of Strategy A for interpretation
            strategy_b_name: Name of Strategy B for interpretation

        Returns:
            ComparisonResult with statistical analysis

        Raises:
            ValueError: If lists are empty, single element, or mismatched lengths
        """
        # Validate input
        if len(strategy_a_pnls) == 0 or len(strategy_b_pnls) == 0:
            raise ValueError("Cannot compare empty lists")

        if len(strategy_a_pnls) != len(strategy_b_pnls):
            raise ValueError(
                "Strategy PnL lists must have same length (paired observations)"
            )

        if len(strategy_a_pnls) == 1:
            raise ValueError(
                "Cannot compute t-test with single observation (need at least 2)"
            )

        # Convert to float for scipy
        a_floats = [float(x) for x in strategy_a_pnls]
        b_floats = [float(x) for x in strategy_b_pnls]

        # Calculate means
        strategy_a_mean = sum(strategy_a_pnls) / len(strategy_a_pnls)
        strategy_b_mean = sum(strategy_b_pnls) / len(strategy_b_pnls)
        mean_difference = strategy_b_mean - strategy_a_mean

        # Use scipy's paired t-test (two-sided)
        t_statistic, p_value = ttest_rel(a_floats, b_floats)

        # Determine significance (handle NaN from identical data)
        is_significant = bool(p_value < self.significance_level)

        # Generate interpretation
        interpretation = self._generate_interpretation(
            strategy_a_name=strategy_a_name,
            strategy_b_name=strategy_b_name,
            mean_difference=mean_difference,
            p_value=p_value,
            is_significant=is_significant,
        )

        return ComparisonResult(
            strategy_a_mean=strategy_a_mean,
            strategy_b_mean=strategy_b_mean,
            mean_difference=mean_difference,
            t_statistic=float(t_statistic),
            p_value=float(p_value),
            is_significant=is_significant,
            interpretation=interpretation,
        )

    def _generate_interpretation(
        self,
        strategy_a_name: str,
        strategy_b_name: str,
        mean_difference: Decimal,
        p_value: float,
        is_significant: bool,
    ) -> str:
        """Generate human-readable interpretation of comparison result.

        Args:
            strategy_a_name: Name of Strategy A
            strategy_b_name: Name of Strategy B
            mean_difference: Mean difference (B - A)
            p_value: P-value from t-test
            is_significant: Whether difference is significant

        Returns:
            Interpretation string
        """
        if not is_significant:
            return (
                f"No significant difference between {strategy_a_name} and {strategy_b_name} "
                f"(p = {p_value:.4f} >= {self.significance_level})"
            )

        # Significant difference - determine winner
        if mean_difference > 0:
            winner = strategy_b_name
            loser = strategy_a_name
        else:
            winner = strategy_a_name
            loser = strategy_b_name

        return (
            f"{winner} significantly outperforms {loser} "
            f"(p = {p_value:.4f} < {self.significance_level})"
        )
