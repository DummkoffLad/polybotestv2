"""
Unit tests for SensitivitySweeper - parameter robustness testing.

Tests verify that the sweeper correctly generates parameter variants,
identifies fragile parameters, and produces comprehensive sweep configurations.
"""
from decimal import Decimal
import pytest

from src.validation.sensitivity import (
    SensitivitySweeper,
    SensitivityResult,
    ParamSweepResult,
)


class TestParameterVariantGeneration:
    """Test generate_variants creates correct low/baseline/high values."""

    def test_generates_three_variants_for_decimal_value(self):
        """Float values get +-15% variants by default."""
        sweeper = SensitivitySweeper({}, sweep_range=0.15)
        variants = sweeper.generate_variants("test_param", 0.5)

        assert len(variants) == 3
        assert variants[0] == pytest.approx(0.425)  # 0.5 * 0.85
        assert variants[1] == pytest.approx(0.5)    # baseline
        assert variants[2] == pytest.approx(0.575)  # 0.5 * 1.15

    def test_generates_variants_for_integer_value(self):
        """Integer values get +-15% variants."""
        sweeper = SensitivitySweeper({}, sweep_range=0.15)
        variants = sweeper.generate_variants("window_size", 100)

        assert len(variants) == 3
        assert variants[0] == pytest.approx(85)   # 100 * 0.85
        assert variants[1] == pytest.approx(100)  # baseline
        assert variants[2] == pytest.approx(115)  # 100 * 1.15

    def test_custom_sweep_range(self):
        """sweep_range parameter controls variant spread."""
        sweeper = SensitivitySweeper({}, sweep_range=0.20)
        variants = sweeper.generate_variants("kelly_fraction", 0.5)

        assert variants[0] == pytest.approx(0.4)   # 0.5 * 0.80
        assert variants[1] == pytest.approx(0.5)
        assert variants[2] == pytest.approx(0.6)   # 0.5 * 1.20


class TestOneAtATimeSweep:
    """Test one-at-a-time parameter sweep."""

    def test_generates_correct_number_of_configs(self):
        """3 values per param × N params = 3N configs."""
        baseline = {
            "kelly_fraction": 0.5,
            "quality_threshold": 0.40,
            "soft_floor": 0.85,
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)
        configs = list(sweeper.one_at_a_time_sweep())

        # 3 params × 3 values each = 9 configs
        assert len(configs) == 9

    def test_each_config_varies_only_one_param(self):
        """One-at-a-time means only one param changes from baseline."""
        baseline = {
            "kelly_fraction": 0.5,
            "quality_threshold": 0.40,
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)
        configs = list(sweeper.one_at_a_time_sweep())

        # Each config should differ from baseline in exactly 1 param
        for param_name, value, config in configs:
            diff_count = sum(
                1 for k, v in config.items()
                if abs(v - baseline[k]) > 1e-6
            )
            assert diff_count == 1, f"Config varies {diff_count} params, expected 1"

    def test_skips_non_numeric_parameters(self):
        """Non-numeric params are excluded from sweep."""
        baseline = {
            "kelly_fraction": 0.5,
            "strategy_name": "mirror",  # string - skip
            "enabled": True,             # bool - skip
            "quality_threshold": 0.40,
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)
        configs = list(sweeper.one_at_a_time_sweep())

        # Only 2 numeric params, so 2 × 3 = 6 configs
        assert len(configs) == 6

        # Verify non-numeric params never change
        for _, _, config in configs:
            assert config["strategy_name"] == "mirror"
            assert config["enabled"] is True


class TestFragilityDetection:
    """Test assess_robustness identifies fragile parameters."""

    def test_identifies_fragile_parameter_above_30pct_swing(self):
        """Parameter causing >30% PnL swing is marked fragile."""
        sweep_result = ParamSweepResult(
            param_name="kelly_fraction",
            values_tested=[0.425, 0.5, 0.575],
            pnl_results=[
                Decimal("50.00"),   # low value
                Decimal("100.00"),  # baseline
                Decimal("140.00"),  # high value - 40% swing
            ],
            baseline_pnl=Decimal("100.00"),
        )

        assert sweep_result.max_swing_pct == pytest.approx(0.40)  # 40% swing
        assert sweep_result.is_fragile is True

    def test_identifies_robust_parameter_below_30pct_swing(self):
        """Parameter causing <30% PnL swing is marked robust."""
        sweep_result = ParamSweepResult(
            param_name="cash_reserve_pct",
            values_tested=[8.5, 10, 11.5],
            pnl_results=[
                Decimal("95.00"),   # -5% swing
                Decimal("100.00"),  # baseline
                Decimal("103.00"),  # +3% swing
            ],
            baseline_pnl=Decimal("100.00"),
        )

        assert sweep_result.max_swing_pct == pytest.approx(0.05)  # 5% max swing
        assert sweep_result.is_fragile is False

    def test_handles_negative_pnl_baseline(self):
        """Fragility calculation works with negative baseline PnL."""
        sweep_result = ParamSweepResult(
            param_name="quality_threshold",
            values_tested=[0.34, 0.40, 0.46],
            pnl_results=[
                Decimal("-80.00"),   # worse loss
                Decimal("-100.00"),  # baseline loss
                Decimal("-60.00"),   # less bad
            ],
            baseline_pnl=Decimal("-100.00"),
        )

        # max swing is 40% (from -100 to -60)
        assert sweep_result.max_swing_pct == pytest.approx(0.40)
        assert sweep_result.is_fragile is True

    def test_handles_zero_baseline_pnl_gracefully(self):
        """Zero baseline PnL edge case doesn't crash."""
        sweep_result = ParamSweepResult(
            param_name="soft_floor",
            values_tested=[0.72, 0.85, 0.98],
            pnl_results=[
                Decimal("-10.00"),
                Decimal("0.00"),   # baseline = 0
                Decimal("10.00"),
            ],
            baseline_pnl=Decimal("0.00"),
        )

        # When baseline is zero, any non-zero result is 100% swing
        # Implementation should handle this gracefully
        assert sweep_result.max_swing_pct >= 0.0  # Should not crash
        assert sweep_result.is_fragile is True  # Any change from 0 is fragile


class TestFullGridSweep:
    """Test full grid sweep for parameter interactions."""

    def test_generates_cartesian_product_for_two_params(self):
        """2 params with 3 values each = 9 configs."""
        baseline = {
            "kelly_fraction": 0.5,
            "quality_threshold": 0.40,
            "soft_floor": 0.85,  # not in grid subset
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)

        # Grid sweep only these 2 params
        param_subset = ["kelly_fraction", "quality_threshold"]
        configs = list(sweeper.full_grid_sweep(param_subset))

        # 3 × 3 = 9 configs
        assert len(configs) == 9

    def test_non_swept_params_stay_at_baseline(self):
        """Params not in subset remain at baseline values."""
        baseline = {
            "kelly_fraction": 0.5,
            "quality_threshold": 0.40,
            "soft_floor": 0.85,
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)

        param_subset = ["kelly_fraction"]  # only sweep one param
        configs = list(sweeper.full_grid_sweep(param_subset))

        # All configs should have soft_floor and quality_threshold at baseline
        for config in configs:
            assert config["soft_floor"] == pytest.approx(0.85)
            assert config["quality_threshold"] == pytest.approx(0.40)

    def test_grid_sweep_with_three_params(self):
        """3 params = 27 configs (3^3)."""
        baseline = {
            "kelly_fraction": 0.5,
            "quality_threshold": 0.40,
            "soft_floor": 0.85,
        }
        sweeper = SensitivitySweeper(baseline, sweep_range=0.15)

        param_subset = ["kelly_fraction", "quality_threshold", "soft_floor"]
        configs = list(sweeper.full_grid_sweep(param_subset))

        assert len(configs) == 27  # 3^3


class TestSensitivityResult:
    """Test SensitivityResult aggregation."""

    def test_aggregates_fragile_and_robust_params(self):
        """Result separates fragile from robust parameters."""
        param_results = [
            ParamSweepResult(
                param_name="kelly_fraction",
                values_tested=[0.425, 0.5, 0.575],
                pnl_results=[Decimal("50"), Decimal("100"), Decimal("140")],
                baseline_pnl=Decimal("100"),
            ),  # fragile (40% swing)
            ParamSweepResult(
                param_name="cash_reserve_pct",
                values_tested=[8.5, 10, 11.5],
                pnl_results=[Decimal("95"), Decimal("100"), Decimal("103")],
                baseline_pnl=Decimal("100"),
            ),  # robust (5% swing)
        ]

        result = SensitivityResult(
            param_results=param_results,
            total_configs_tested=6,
        )

        assert result.fragile_params == ["kelly_fraction"]
        assert result.robust_params == ["cash_reserve_pct"]
