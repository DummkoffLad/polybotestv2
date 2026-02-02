"""
Parameter sensitivity analysis for strategy robustness testing.

The SensitivitySweeper tests how strategy performance varies when parameters
change by small amounts (e.g., +-15%). This identifies "fragile" parameters
where small changes cause large PnL swings, indicating potential instability.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Tuple


@dataclass
class ParamSweepResult:
    """Results from sweeping a single parameter."""

    param_name: str
    values_tested: List[float]
    pnl_results: List[Decimal]
    baseline_pnl: Decimal

    @property
    def max_swing_pct(self) -> float:
        """Maximum percentage swing from baseline PnL."""
        if self.baseline_pnl == 0:
            # Edge case: baseline is zero, any non-zero result is infinite swing
            # Treat this as maximally fragile
            max_deviation = max(abs(pnl) for pnl in self.pnl_results)
            if max_deviation > 0:
                return 1.0  # 100% swing (maximally fragile)
            return 0.0  # All zero, no swing

        # Calculate max swing as percentage of absolute baseline
        # Only include non-baseline values (low and high variants)
        swings = [
            abs(pnl - self.baseline_pnl) / abs(self.baseline_pnl)
            for pnl in self.pnl_results
            if pnl != self.baseline_pnl
        ]
        return float(max(swings)) if swings else 0.0

    @property
    def is_fragile(self) -> bool:
        """True if max swing exceeds 30% threshold."""
        return self.max_swing_pct > 0.30


@dataclass
class SensitivityResult:
    """Aggregated results from parameter sensitivity analysis."""

    param_results: List[ParamSweepResult]
    total_configs_tested: int

    @property
    def fragile_params(self) -> List[str]:
        """Parameters where small changes cause >30% PnL swings."""
        return [r.param_name for r in self.param_results if r.is_fragile]

    @property
    def robust_params(self) -> List[str]:
        """Parameters where small changes cause <30% PnL swings."""
        return [r.param_name for r in self.param_results if not r.is_fragile]


class SensitivitySweeper:
    """
    Systematic parameter robustness testing.

    Tests strategy performance across parameter ranges to identify fragile
    parameters that could cause unexpected behavior in live trading.

    Two sweep modes:
    1. One-at-a-time: Vary each param independently (3N configs for N params)
    2. Full grid: Vary subset of params together (3^k configs for k params)
    """

    def __init__(self, baseline_params: Dict[str, Any], sweep_range: float = 0.15):
        """
        Initialize sweeper with baseline configuration.

        Args:
            baseline_params: Baseline parameter values (optimized config)
            sweep_range: Percentage to vary each param (default 15%)
        """
        self.baseline_params = baseline_params
        self.sweep_range = sweep_range

    def generate_variants(self, param_name: str, param_value: float) -> List[float]:
        """
        Generate [low, baseline, high] variants for a parameter.

        Args:
            param_name: Parameter name (for logging/debugging)
            param_value: Baseline parameter value

        Returns:
            List of 3 values: [value * (1-range), value, value * (1+range)]
        """
        low = param_value * (1 - self.sweep_range)
        baseline = param_value
        high = param_value * (1 + self.sweep_range)
        return [low, baseline, high]

    def one_at_a_time_sweep(self) -> Iterator[Tuple[str, float, Dict[str, Any]]]:
        """
        Generate configs varying one parameter at a time.

        For each numeric parameter, generate 3 configs (low, baseline, high)
        while keeping all other params at baseline.

        Yields:
            Tuples of (param_name, param_value, full_config_dict)
        """
        for param_name, baseline_value in self.baseline_params.items():
            # Skip non-numeric parameters (including bool which Python treats as int)
            if isinstance(baseline_value, bool) or not isinstance(
                baseline_value, (int, float, Decimal)
            ):
                continue

            # Convert to float for variant generation
            baseline_float = float(baseline_value)
            variants = self.generate_variants(param_name, baseline_float)

            # Yield one config for each variant
            for variant_value in variants:
                config = dict(self.baseline_params)
                config[param_name] = variant_value
                yield (param_name, variant_value, config)

    def full_grid_sweep(self, param_subset: List[str]) -> Iterator[Dict[str, Any]]:
        """
        Generate full cartesian product for a subset of parameters.

        WARNING: Combinatorial explosion! Use sparingly.
        2 params = 9 configs, 3 params = 27 configs, 4 params = 81 configs

        Args:
            param_subset: List of parameter names to vary together

        Yields:
            Full config dicts with all combinations of param values
        """
        # Generate variants for each param in subset
        param_variants = {}
        for param_name in param_subset:
            if param_name not in self.baseline_params:
                continue

            baseline_value = self.baseline_params[param_name]

            # Skip non-numeric (including bool)
            if isinstance(baseline_value, bool) or not isinstance(
                baseline_value, (int, float, Decimal)
            ):
                continue

            baseline_float = float(baseline_value)
            param_variants[param_name] = self.generate_variants(param_name, baseline_float)

        # Generate cartesian product
        param_names = list(param_variants.keys())
        param_value_lists = [param_variants[name] for name in param_names]

        for combo in itertools.product(*param_value_lists):
            config = dict(self.baseline_params)
            for param_name, value in zip(param_names, combo):
                config[param_name] = value
            yield config

    def assess_robustness(
        self, param_results: List[ParamSweepResult]
    ) -> SensitivityResult:
        """
        Analyze sweep results to identify fragile parameters.

        Args:
            param_results: Results from parameter sweeps

        Returns:
            SensitivityResult with fragile/robust param lists
        """
        total_configs = sum(len(r.values_tested) for r in param_results)
        return SensitivityResult(
            param_results=param_results,
            total_configs_tested=total_configs,
        )
