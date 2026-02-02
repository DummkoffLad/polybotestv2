"""Statistical validation of Kelly sizing improvements."""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
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

    def bootstrap_confidence_interval(
        self,
        phase3_pnls: List[Decimal],
        phase4_pnls: List[Decimal],
        n_iterations: int = 1000,
        confidence: float = 0.95,
    ) -> Tuple[float, float]:
        """Calculate bootstrap confidence interval for mean PnL difference.

        Args:
            phase3_pnls: List of Phase 3 session PnLs
            phase4_pnls: List of Phase 4 session PnLs
            n_iterations: Number of bootstrap iterations (default 1000)
            confidence: Confidence level (default 0.95 for 95%)

        Returns:
            Tuple of (lower_bound, upper_bound) for confidence interval
        """
        if len(phase3_pnls) != len(phase4_pnls):
            raise ValueError("Phase 3 and Phase 4 PnL lists must have same length")

        n = len(phase3_pnls)

        # Convert to numpy arrays
        arr3 = np.array([float(p) for p in phase3_pnls])
        arr4 = np.array([float(p) for p in phase4_pnls])

        # Calculate differences
        differences = arr4 - arr3

        # Bootstrap resampling
        bootstrap_means = []
        rng = np.random.default_rng(seed=42)  # Use fixed seed for reproducibility

        for _ in range(n_iterations):
            # Resample with replacement
            indices = rng.choice(n, size=n, replace=True)
            resampled_diffs = differences[indices]
            bootstrap_means.append(np.mean(resampled_diffs))

        # Calculate confidence interval using percentiles
        alpha = 1.0 - confidence
        lower_percentile = (alpha / 2) * 100
        upper_percentile = (1.0 - alpha / 2) * 100

        lower_bound = float(np.percentile(bootstrap_means, lower_percentile))
        upper_bound = float(np.percentile(bootstrap_means, upper_percentile))

        return (lower_bound, upper_bound)

    def calculate_secondary_metrics(
        self,
        trade_pnls: List[Decimal],
        utilization_snapshots: Optional[List[Decimal]] = None,
    ) -> Dict[str, Optional[float]]:
        """Calculate secondary performance metrics.

        Args:
            trade_pnls: List of individual trade PnLs
            utilization_snapshots: Optional list of capital utilization ratios [0, 1]

        Returns:
            Dictionary with:
            - capital_utilization: Average % of capital deployed (if snapshots provided)
            - profit_factor: Gross profit / gross loss ratio
            - max_single_loss: Largest single trade loss (most negative)
        """
        metrics: Dict[str, Optional[float]] = {}

        # Capital utilization
        if utilization_snapshots:
            avg_utilization = np.mean([float(u) for u in utilization_snapshots])
            metrics["capital_utilization"] = float(avg_utilization)
        else:
            metrics["capital_utilization"] = None

        # Profit factor
        arr_pnls = np.array([float(p) for p in trade_pnls])
        gross_profit = float(np.sum(arr_pnls[arr_pnls > 0]))
        gross_loss = abs(float(np.sum(arr_pnls[arr_pnls < 0])))

        if gross_loss == 0:
            # No losses - profit factor is infinity or undefined
            metrics["profit_factor"] = None if gross_profit == 0 else float("inf")
        else:
            metrics["profit_factor"] = gross_profit / gross_loss

        # Max single loss
        if len(arr_pnls) > 0:
            min_pnl = float(np.min(arr_pnls))
            metrics["max_single_loss"] = Decimal(str(min_pnl))
        else:
            metrics["max_single_loss"] = None

        return metrics

    def generate_report(
        self,
        phase3_pnls: List[Decimal],
        phase4_pnls: List[Decimal],
        phase3_trades: Optional[List[Decimal]] = None,
        phase4_trades: Optional[List[Decimal]] = None,
    ) -> str:
        """Generate complete validation report.

        Args:
            phase3_pnls: List of Phase 3 session PnLs
            phase4_pnls: List of Phase 4 session PnLs
            phase3_trades: Optional list of Phase 3 individual trade PnLs
            phase4_trades: Optional list of Phase 4 individual trade PnLs

        Returns:
            Formatted text report
        """
        # Run paired t-test
        validation_result = self.validate_improvement(phase3_pnls, phase4_pnls)

        # Run bootstrap CI
        ci_lower, ci_upper = self.bootstrap_confidence_interval(phase3_pnls, phase4_pnls)

        # Build report
        lines = [
            "=" * 60,
            "Kelly Sizing Validation Report",
            "=" * 60,
            "",
            "PAIRED T-TEST:",
            f"  p-value: {validation_result.p_value:.4f}",
            f"  t-statistic: {validation_result.t_statistic:.4f}",
            f"  Significant: {validation_result.is_significant}",
            "",
            "BOOTSTRAP 95% CONFIDENCE INTERVAL:",
            f"  Lower bound: ${ci_lower:.2f}",
            f"  Upper bound: ${ci_upper:.2f}",
            "",
            "MEAN PnL:",
            f"  Phase 3: ${float(validation_result.phase3_mean):.2f}",
            f"  Phase 4: ${float(validation_result.phase4_mean):.2f}",
            f"  Improvement: ${float(validation_result.mean_improvement):.2f}",
            "",
            "INTERPRETATION:",
            f"  {validation_result.interpretation}",
        ]

        # Add secondary metrics if trade data provided
        if phase3_trades or phase4_trades:
            lines.append("")
            lines.append("SECONDARY METRICS:")

            if phase3_trades:
                metrics3 = self.calculate_secondary_metrics(phase3_trades)
                lines.append("  Phase 3:")
                if metrics3.get("profit_factor") is not None:
                    pf = metrics3["profit_factor"]
                    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
                    lines.append(f"    Profit factor: {pf_str}")
                if metrics3.get("max_single_loss") is not None:
                    lines.append(f"    Max single loss: ${metrics3['max_single_loss']}")

            if phase4_trades:
                metrics4 = self.calculate_secondary_metrics(phase4_trades)
                lines.append("  Phase 4:")
                if metrics4.get("profit_factor") is not None:
                    pf = metrics4["profit_factor"]
                    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
                    lines.append(f"    Profit factor: {pf_str}")
                if metrics4.get("max_single_loss") is not None:
                    lines.append(f"    Max single loss: ${metrics4['max_single_loss']}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)
