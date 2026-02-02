"""
ValidationPipeline - orchestrates all validation components into a single
end-to-end workflow.

This is the user-facing entry point for validation. It wires together:
1. DataSplitManager (out-of-sample session identification)
2. SensitivitySweeper (parameter robustness testing)
3. LatencySimulator (execution delay modeling)
4. ValidationReportGenerator (go/no-go decision)

Output: A comprehensive validation report with clear GO/NO-GO recommendation.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Any, Optional

from .data_split import DataSplitManager
from .sensitivity import SensitivitySweeper, SensitivityResult, ParamSweepResult
from .latency_sim import LatencySimulator, LatencyConfig, LatencyImpactResult
from .report import ValidationReportGenerator, ValidationSummary, GoNoGoDecision
from ..simulation.optimizer import SimpleOptimizer
from ..framework.replay import ReplayResult


# BASELINE_PARAMS: All tunable parameters from Phase 3+4 with optimized values
# These are the baseline values that were optimized during in-sample testing.
# Format matches SimpleOptimizer.run_strategy(config_overrides) dotted-key convention.
BASELINE_PARAMS = {
    # Scaling parameters (Phase 2-3)
    "scaling.k_factor": "0.85",

    # Mirror strategy parameters (Phase 3-4)
    "mirror_strategy.kelly_fraction": "0.5",
    "mirror_strategy.quality_threshold": "0.40",
    "mirror_strategy.soft_floor": "0.85",
    "mirror_strategy.hard_floor_drawdown": "0.30",
    "mirror_strategy.per_market_cap_pct": "30",
    "mirror_strategy.cash_reserve_pct": "10",
    "mirror_strategy.per_side_pct": "26",
    "mirror_strategy.global_exposure_pct": "100",
    "mirror_strategy.dca_quality_threshold": "0.75",
    "mirror_strategy.correlation_penalty": "0.15",
    "mirror_strategy.rebalancing_edge_gap": "1.5",
    "mirror_strategy.rolling_window_size": "50",
    "mirror_strategy.min_trades_for_kelly": "20",
    "mirror_strategy.max_position_cap": "0.20",
}


class ValidationPipeline:
    """
    Orchestrates end-to-end validation workflow.

    This pipeline:
    1. Identifies out-of-sample sessions via DataSplitManager
    2. Runs strategy replay on OOS sessions with baseline params
    3. Tests parameter sensitivity with SensitivitySweeper
    4. Models latency impact with LatencySimulator
    5. Generates go/no-go report via ValidationReportGenerator

    Example:
        >>> pipeline = ValidationPipeline()
        >>> pipeline.setup_data_split(
        ...     in_sample_ids=["session_20260130_032713"],
        ...     out_of_sample_ids=["session_20260202_120000"]
        ... )
        >>> summary = pipeline.run_full_validation()
        >>> print(f"Decision: {'GO' if summary.decision else 'NO-GO'}")
    """

    def __init__(
        self,
        session_dir: Path = Path("data/sessions"),
        output_dir: Path = Path("data/validation"),
        strategy_name: str = "mirror",
    ):
        """
        Initialize validation pipeline.

        Args:
            session_dir: Directory containing session .jsonl files
            output_dir: Directory for validation reports and metadata
            strategy_name: Strategy to validate (default: "mirror")
        """
        self.session_dir = Path(session_dir)
        self.output_dir = Path(output_dir)
        self.strategy_name = strategy_name

        # Initialize components
        self.data_split = DataSplitManager(
            sessions_dir=session_dir,
            metadata_path=output_dir / "split_metadata.json"
        )
        self.sweeper = SensitivitySweeper(
            baseline_params=BASELINE_PARAMS,
            sweep_range=0.15  # ±15% from baseline
        )
        self.latency_sim = LatencySimulator(LatencyConfig.baseline())
        self.report_gen = ValidationReportGenerator(output_dir=output_dir)

    def setup_data_split(
        self,
        in_sample_ids: List[str],
        out_of_sample_ids: List[str]
    ) -> None:
        """
        Configure which sessions are in-sample vs out-of-sample.

        Args:
            in_sample_ids: Session IDs used for optimization (Phase 1-4)
            out_of_sample_ids: Session IDs reserved for validation

        Example:
            >>> pipeline.setup_data_split(
            ...     in_sample_ids=["session_20260130_032713"],
            ...     out_of_sample_ids=["session_20260202_120000", "session_20260202_140000"]
            ... )
        """
        # Mark in-sample sessions
        for session_id in in_sample_ids:
            self.data_split.mark_in_sample(session_id)

        # Mark out-of-sample sessions
        for session_id in out_of_sample_ids:
            self.data_split.mark_out_of_sample(session_id)

        # Validate no leakage
        self.data_split.validate_no_leakage(out_of_sample_ids)

    def run_out_of_sample(self, strategy_name: str = None) -> List[Decimal]:
        """
        Run strategy on all out-of-sample sessions and collect PnLs.

        Args:
            strategy_name: Strategy to run (default: self.strategy_name)

        Returns:
            List of total PnL (Decimal) for each OOS session
        """
        if strategy_name is None:
            strategy_name = self.strategy_name

        # Get OOS session paths
        oos_paths = self.data_split.get_validation_sessions()

        if not oos_paths:
            raise ValueError("No out-of-sample sessions configured. Call setup_data_split() first.")

        pnls = []
        for session_path in oos_paths:
            # Create optimizer for this session
            optimizer = SimpleOptimizer(
                session_path=session_path,
                simulate_resolution=True
            )

            # Run strategy with baseline params
            result: ReplayResult = optimizer.run_strategy(
                strategy_name=strategy_name,
                config_overrides=BASELINE_PARAMS,
                collect_trades=False
            )

            # Use resolved_pnl if available (includes market resolution simulation)
            # Otherwise fall back to total_pnl
            pnl = result.resolved_pnl if result.resolved_pnl != Decimal("0") else result.total_pnl
            pnls.append(pnl)

        return pnls

    def run_sensitivity(self, session_path: Path) -> SensitivityResult:
        """
        Run parameter sensitivity sweep on a single session.

        Uses one-at-a-time sweep to test each parameter independently.

        Args:
            session_path: Path to session .jsonl file

        Returns:
            SensitivityResult with fragile/robust parameter lists
        """
        # Create optimizer for this session
        optimizer = SimpleOptimizer(
            session_path=session_path,
            simulate_resolution=True
        )

        # Collect results for each parameter
        param_results: List[ParamSweepResult] = []

        # Group configs by parameter for sweep analysis
        param_configs: Dict[str, List[float]] = {}
        param_pnls: Dict[str, List[Decimal]] = {}

        # Run one-at-a-time sweep
        for param_name, param_value, config in self.sweeper.one_at_a_time_sweep():
            # Run replay with this config
            result: ReplayResult = optimizer.run_strategy(
                strategy_name=self.strategy_name,
                config_overrides=config,
                collect_trades=False
            )

            # Collect PnL
            pnl = result.resolved_pnl if result.resolved_pnl != Decimal("0") else result.total_pnl

            # Group by parameter
            if param_name not in param_configs:
                param_configs[param_name] = []
                param_pnls[param_name] = []

            param_configs[param_name].append(param_value)
            param_pnls[param_name].append(pnl)

        # Build ParamSweepResult for each parameter
        for param_name in param_configs:
            # Find baseline value
            baseline_str = BASELINE_PARAMS.get(param_name, "0")
            baseline_value = Decimal(baseline_str) if isinstance(baseline_str, str) else Decimal(str(baseline_str))

            # Find baseline PnL (the middle value in the sweep)
            values = param_configs[param_name]
            pnls = param_pnls[param_name]

            # Baseline should be the middle value
            baseline_idx = len(values) // 2
            baseline_pnl = pnls[baseline_idx] if len(pnls) > baseline_idx else pnls[0]

            param_results.append(ParamSweepResult(
                param_name=param_name,
                values_tested=values,
                pnl_results=pnls,
                baseline_pnl=baseline_pnl
            ))

        # Assess robustness
        return self.sweeper.assess_robustness(param_results)

    def run_latency_analysis(
        self,
        session_path: Path,
        scenarios: List[str] = None
    ) -> List[LatencyImpactResult]:
        """
        Run latency impact analysis using post-processed ExecutedTrade records.

        Approach: Post-process ExecutedTrade records (approach b from checker feedback).
        1. Run replay normally (zero latency) to get baseline PnL and ExecutedTrade records
        2. For each latency scenario:
           a. Iterate over ExecutedTrade records
           b. Sample latency delay for each trade
           c. Apply price degradation based on delay
           d. Compute per-trade PnL delta
           e. Sum all deltas to get total latency cost
           f. latency_pnl = baseline_pnl + total_latency_cost

        This approach is accurate enough for validation because:
        - Small price deltas (0.1%/s * 3.5s = 0.35%) don't meaningfully change sizing
        - We care about aggregate PnL impact, not individual trade re-simulation
        - It avoids modifying SessionReplayer internals

        Args:
            session_path: Path to session .jsonl file
            scenarios: List of scenario names (default: ["zero", "baseline", "stress_2x", "stress_3x"])

        Returns:
            List of LatencyImpactResult for each scenario
        """
        if scenarios is None:
            scenarios = ["zero", "baseline", "stress_2x", "stress_3x"]

        # Step 1: Run replay with zero latency to get baseline PnL and ExecutedTrade records
        optimizer = SimpleOptimizer(
            session_path=session_path,
            simulate_resolution=True
        )

        baseline_result: ReplayResult = optimizer.run_strategy(
            strategy_name=self.strategy_name,
            config_overrides=BASELINE_PARAMS,
            collect_trades=True  # CRITICAL: We need ExecutedTrade records
        )

        # Get baseline PnL
        baseline_pnl = (
            baseline_result.resolved_pnl
            if baseline_result.resolved_pnl != Decimal("0")
            else baseline_result.total_pnl
        )

        # Get ExecutedTrade records
        trades = baseline_result.trades

        if not trades:
            # No trades executed - latency has no impact
            return [
                LatencyImpactResult(
                    baseline_pnl=baseline_pnl,
                    latency_pnl=baseline_pnl,
                    degradation_dollars=Decimal("0"),
                    degradation_pct=0.0,
                    avg_delay_ms=0.0,
                    scenario_name=scenario
                )
                for scenario in scenarios
            ]

        # Step 2: For each latency scenario, compute latency impact
        results = []

        for scenario_name in scenarios:
            # Get latency config for this scenario
            if scenario_name == "zero":
                config = LatencyConfig.zero()
            elif scenario_name == "baseline":
                config = LatencyConfig.baseline()
            elif scenario_name == "stress_2x":
                config = LatencyConfig.stress_2x()
            elif scenario_name == "stress_3x":
                config = LatencyConfig.stress_3x()
            else:
                raise ValueError(f"Unknown scenario: {scenario_name}")

            # Create latency simulator for this scenario
            sim = LatencySimulator(config)

            # Compute latency cost for each trade
            total_latency_cost = Decimal("0")
            total_delay_ms = 0.0

            for trade in trades:
                # Sample delay for this trade
                delay = sim.sample_total_delay()
                delay_ms = delay.total_seconds() * 1000
                total_delay_ms += delay_ms

                # Compute degraded price
                degraded_price = sim.apply_price_degradation(
                    price=trade.our_price,
                    action=trade.action,
                    delay_ms=delay_ms
                )

                # Compute per-trade PnL delta
                # For BUY: worse fill = higher price = negative delta (pay more)
                # For SELL: worse fill = lower price = negative delta (receive less)
                if trade.action == "BUY":
                    # BUY: degraded_price > our_price (worse fill)
                    # Cost delta = (degraded_price - our_price) * shares (negative)
                    cost_delta = (degraded_price - trade.our_price) * trade.our_shares
                    total_latency_cost -= cost_delta  # Negative impact on PnL
                elif trade.action == "SELL":
                    # SELL: degraded_price < our_price (worse fill)
                    # Revenue delta = (degraded_price - our_price) * shares (negative)
                    revenue_delta = (degraded_price - trade.our_price) * trade.our_shares
                    total_latency_cost += revenue_delta  # Negative impact on PnL

            # Compute latency-adjusted PnL
            latency_pnl = baseline_pnl + total_latency_cost

            # Compute degradation metrics
            degradation_dollars = baseline_pnl - latency_pnl
            avg_delay_ms = total_delay_ms / len(trades) if trades else 0.0

            # Compute degradation percentage
            if baseline_pnl != Decimal("0"):
                degradation_pct = float(degradation_dollars / abs(baseline_pnl) * 100)
            else:
                # Edge case: zero baseline, any degradation is 100%
                degradation_pct = 100.0 if degradation_dollars != Decimal("0") else 0.0

            results.append(LatencyImpactResult(
                baseline_pnl=baseline_pnl,
                latency_pnl=latency_pnl,
                degradation_dollars=degradation_dollars,
                degradation_pct=degradation_pct,
                avg_delay_ms=avg_delay_ms,
                scenario_name=scenario_name
            ))

        return results

    def run_full_validation(self) -> ValidationSummary:
        """
        Run complete validation workflow end-to-end.

        Workflow:
        1. Run out-of-sample replays to get session PnLs
        2. Compute confidence interval via bootstrap (using KellyValidator logic)
        3. Run sensitivity sweep on representative session
        4. Run latency analysis on representative session
        5. Aggregate results into ValidationSummary
        6. Generate and save report
        7. Print console summary

        Returns:
            ValidationSummary with all validation results and go/no-go decision
        """
        # TODO: Implementation in Task 2
        raise NotImplementedError("Task 2 will implement run_full_validation()")


def run_validation(
    out_of_sample_sessions: List[str],
    in_sample_sessions: List[str] = None,
    session_dir: Path = Path("data/sessions"),
    output_dir: Path = Path("data/validation"),
    strategy_name: str = "mirror"
) -> ValidationSummary:
    """
    Convenience function to run full validation pipeline.

    Args:
        out_of_sample_sessions: List of session IDs to validate on
        in_sample_sessions: List of session IDs used for optimization
            (default: ["session_20260130_032713"])
        session_dir: Directory containing session files
        output_dir: Directory for validation reports
        strategy_name: Strategy to validate

    Returns:
        ValidationSummary with all validation results

    Example:
        >>> summary = run_validation(
        ...     out_of_sample_sessions=["session_20260202_120000"],
        ...     in_sample_sessions=["session_20260130_032713"]
        ... )
        >>> print(f"Decision: {'GO' if summary.decision else 'NO-GO'}")
    """
    if in_sample_sessions is None:
        in_sample_sessions = ["session_20260130_032713"]

    # Create pipeline
    pipeline = ValidationPipeline(
        session_dir=session_dir,
        output_dir=output_dir,
        strategy_name=strategy_name
    )

    # Setup data split
    pipeline.setup_data_split(
        in_sample_ids=in_sample_sessions,
        out_of_sample_ids=out_of_sample_sessions
    )

    # Run full validation
    return pipeline.run_full_validation()
