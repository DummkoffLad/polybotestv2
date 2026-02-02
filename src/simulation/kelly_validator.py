"""Statistical validation of Kelly sizing improvements."""

from dataclasses import dataclass
from decimal import Decimal
from typing import List
import numpy as np
import math


@dataclass
class ValidationResult:
    """Result of Kelly validation analysis."""

    is_significant: bool
    p_value: float
    t_statistic: float
    interpretation: str
    mean_improvement: Decimal
    phase3_mean: Decimal
    phase4_mean: Decimal


class KellyValidator:
    """Statistical validator for Kelly sizing improvements."""

    def __init__(self, significance_level: float = 0.05):
        """Initialize validator with significance threshold.

        Args:
            significance_level: P-value threshold for significance (default 0.05)
        """
        self.significance_level = significance_level

    def validate_improvement(
        self, phase3_pnls: List[Decimal], phase4_pnls: List[Decimal]
    ) -> ValidationResult:
        """Run paired t-test comparing Phase 3 vs Phase 4 PnL.

        Args:
            phase3_pnls: List of Phase 3 session PnLs
            phase4_pnls: List of Phase 4 session PnLs (must be same length)

        Returns:
            ValidationResult with statistical analysis
        """
        if len(phase3_pnls) != len(phase4_pnls):
            raise ValueError("Phase 3 and Phase 4 PnL lists must have same length")

        n = len(phase3_pnls)

        # Convert to numpy arrays for calculations
        arr3 = np.array([float(p) for p in phase3_pnls])
        arr4 = np.array([float(p) for p in phase4_pnls])

        # Calculate means
        phase3_mean = Decimal(str(np.mean(arr3)))
        phase4_mean = Decimal(str(np.mean(arr4)))
        mean_improvement = phase4_mean - phase3_mean

        # Calculate differences (paired)
        differences = arr4 - arr3
        mean_diff = np.mean(differences)
        std_diff = np.std(differences, ddof=1)  # Sample std deviation

        # Handle edge cases
        if n == 1:
            # Single sample: cannot compute t-test
            t_stat = 0.0
            p_value = 1.0
        elif std_diff == 0:
            # Zero variance: all differences are identical
            if mean_diff == 0:
                # No difference at all
                t_stat = 0.0
                p_value = 1.0
            else:
                # All differences are the same non-zero value
                # This is infinitely significant, but we'll use a very small p-value
                t_stat = float('inf') if mean_diff > 0 else float('-inf')
                p_value = 0.0
        else:
            # Normal case: compute t-statistic
            t_stat = mean_diff / (std_diff / math.sqrt(n))

            # Compute p-value using t-distribution
            # For one-sided test (we only care if Phase 4 is BETTER)
            # We use a one-sided p-value
            p_value = self._t_distribution_cdf(-abs(t_stat), n - 1)

            # For two-sided, we'd multiply by 2, but we want one-sided
            # If t_stat is positive (improvement), we want upper tail p-value
            if t_stat > 0:
                p_value = 1.0 - self._t_distribution_cdf(t_stat, n - 1)
            else:
                p_value = self._t_distribution_cdf(t_stat, n - 1)

        # Determine significance: p < threshold AND improvement is positive
        is_significant = (p_value < self.significance_level) and (mean_improvement > 0)

        # Generate interpretation
        if is_significant:
            interpretation = (
                f"Phase 4 shows statistically significant improvement over Phase 3 "
                f"(p={p_value:.4f}, mean improvement=${float(mean_improvement):.2f})"
            )
        elif mean_improvement > 0:
            interpretation = (
                f"Phase 4 shows positive improvement but not statistically significant "
                f"(p={p_value:.4f}, mean improvement=${float(mean_improvement):.2f})"
            )
        else:
            interpretation = (
                f"Phase 4 does not show improvement over Phase 3 "
                f"(p={p_value:.4f}, mean change=${float(mean_improvement):.2f})"
            )

        return ValidationResult(
            is_significant=is_significant,
            p_value=float(p_value),
            t_statistic=float(t_stat),
            interpretation=interpretation,
            mean_improvement=mean_improvement,
            phase3_mean=phase3_mean,
            phase4_mean=phase4_mean,
        )

    def _t_distribution_cdf(self, t: float, df: int) -> float:
        """Approximate t-distribution CDF using normal distribution for large df.

        For small df, uses a more accurate approximation.

        Args:
            t: t-statistic value
            df: Degrees of freedom

        Returns:
            Cumulative probability
        """
        if df > 30:
            # For large df, t-distribution approaches normal distribution
            return self._normal_cdf(t)
        else:
            # Use a more sophisticated approximation for small samples
            # This is based on the Wilson-Hilferty approximation
            return self._normal_cdf(t * (1 - 1/(4*df)) / math.sqrt(1 + t*t/(2*df)))

    def _normal_cdf(self, x: float) -> float:
        """Standard normal CDF using error function.

        Args:
            x: Value

        Returns:
            Cumulative probability
        """
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
