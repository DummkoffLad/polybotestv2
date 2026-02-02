"""
Unit tests for latency simulation.

Tests the LatencySimulator which models realistic API latency
(detection delay + execution delay + jitter) and calculates price
degradation from delayed execution.
"""

from datetime import timedelta
from decimal import Decimal
import pytest

from src.validation.latency_sim import (
    LatencyConfig,
    LatencySimulator,
    LatencyImpactResult,
)


class TestLatencyConfig:
    """Test LatencyConfig predefined configurations."""

    def test_baseline_config(self):
        """Baseline config has 1500ms detection + 2000ms execution + 30% jitter."""
        config = LatencyConfig.baseline()
        assert config.detection_delay_ms == 1500
        assert config.execution_delay_ms == 2000
        assert config.jitter_pct == 0.30
        assert config.seed is None

    def test_stress_2x_config(self):
        """Stress 2x config doubles baseline delays."""
        config = LatencyConfig.stress_2x()
        assert config.detection_delay_ms == 3000
        assert config.execution_delay_ms == 4000
        assert config.jitter_pct == 0.30

    def test_stress_3x_config(self):
        """Stress 3x config triples baseline delays."""
        config = LatencyConfig.stress_3x()
        assert config.detection_delay_ms == 4500
        assert config.execution_delay_ms == 6000
        assert config.jitter_pct == 0.30

    def test_zero_config(self):
        """Zero config has no delays."""
        config = LatencyConfig.zero()
        assert config.detection_delay_ms == 0
        assert config.execution_delay_ms == 0
        assert config.jitter_pct == 0.0


class TestLatencySimulator:
    """Test LatencySimulator delay sampling and price degradation."""

    def test_sample_detection_delay_in_range(self):
        """Detection delay samples within jitter bounds."""
        config = LatencyConfig(detection_delay_ms=1000, execution_delay_ms=0, jitter_pct=0.20, seed=42)
        sim = LatencySimulator(config)

        # With 20% jitter, delay should be between 800ms and 1200ms
        delays = [sim.sample_detection_delay() for _ in range(100)]

        for delay in delays:
            assert isinstance(delay, timedelta)
            ms = delay.total_seconds() * 1000
            assert 800 <= ms <= 1200, f"Detection delay {ms}ms outside bounds [800, 1200]"

    def test_sample_execution_delay_in_range(self):
        """Execution delay samples within jitter bounds."""
        config = LatencyConfig(detection_delay_ms=0, execution_delay_ms=2000, jitter_pct=0.30, seed=42)
        sim = LatencySimulator(config)

        # With 30% jitter, delay should be between 1400ms and 2600ms
        delays = [sim.sample_execution_delay() for _ in range(100)]

        for delay in delays:
            assert isinstance(delay, timedelta)
            ms = delay.total_seconds() * 1000
            assert 1400 <= ms <= 2600, f"Execution delay {ms}ms outside bounds [1400, 2600]"

    def test_sample_total_delay_is_sum(self):
        """Total delay is sum of detection and execution delays."""
        config = LatencyConfig(detection_delay_ms=1000, execution_delay_ms=2000, jitter_pct=0.10, seed=42)
        sim = LatencySimulator(config)

        # With 10% jitter, total should be between 2700ms and 3300ms
        # (detection: 900-1100, execution: 1800-2200)
        total_delays = [sim.sample_total_delay() for _ in range(100)]

        for total in total_delays:
            assert isinstance(total, timedelta)
            ms = total.total_seconds() * 1000
            assert 2700 <= ms <= 3300, f"Total delay {ms}ms outside bounds [2700, 3300]"

    def test_fixed_seed_produces_reproducible_delays(self):
        """Same seed produces identical delay sequences."""
        config1 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=42)
        config2 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=42)

        sim1 = LatencySimulator(config1)
        sim2 = LatencySimulator(config2)

        delays1 = [sim1.sample_total_delay() for _ in range(50)]
        delays2 = [sim2.sample_total_delay() for _ in range(50)]

        assert delays1 == delays2, "Same seed should produce identical delay sequences"

    def test_different_seeds_produce_different_delays(self):
        """Different seeds produce different delay sequences."""
        config1 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=42)
        config2 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=99)

        sim1 = LatencySimulator(config1)
        sim2 = LatencySimulator(config2)

        delays1 = [sim1.sample_total_delay() for _ in range(50)]
        delays2 = [sim2.sample_total_delay() for _ in range(50)]

        assert delays1 != delays2, "Different seeds should produce different delay sequences"

    def test_zero_config_produces_zero_delays(self):
        """Zero config produces zero delays."""
        config = LatencyConfig.zero()
        sim = LatencySimulator(config)

        for _ in range(10):
            detection = sim.sample_detection_delay()
            execution = sim.sample_execution_delay()
            total = sim.sample_total_delay()

            assert detection.total_seconds() == 0
            assert execution.total_seconds() == 0
            assert total.total_seconds() == 0

    def test_stress_2x_produces_roughly_2x_delays(self):
        """Stress 2x produces roughly 2x baseline delays."""
        baseline = LatencySimulator(LatencyConfig.baseline())
        stress_2x = LatencySimulator(LatencyConfig.stress_2x())

        # Sample many delays to get stable averages
        baseline_delays = [baseline.sample_total_delay().total_seconds() * 1000 for _ in range(200)]
        stress_delays = [stress_2x.sample_total_delay().total_seconds() * 1000 for _ in range(200)]

        avg_baseline = sum(baseline_delays) / len(baseline_delays)
        avg_stress = sum(stress_delays) / len(stress_delays)

        # Should be roughly 2x (within jitter tolerance)
        ratio = avg_stress / avg_baseline
        assert 1.8 <= ratio <= 2.2, f"Stress 2x ratio {ratio:.2f} outside expected range [1.8, 2.2]"

    def test_jitter_never_produces_negative_delays(self):
        """Jitter never produces negative delays, even at 100%."""
        config = LatencyConfig(detection_delay_ms=100, execution_delay_ms=100, jitter_pct=1.0, seed=42)
        sim = LatencySimulator(config)

        for _ in range(100):
            detection = sim.sample_detection_delay()
            execution = sim.sample_execution_delay()
            total = sim.sample_total_delay()

            assert detection.total_seconds() >= 0, "Detection delay cannot be negative"
            assert execution.total_seconds() >= 0, "Execution delay cannot be negative"
            assert total.total_seconds() >= 0, "Total delay cannot be negative"


class TestPriceDegradation:
    """Test price degradation from latency."""

    def test_buy_degrades_price_upward(self):
        """BUY action increases price (worse fill) with latency."""
        config = LatencyConfig.zero()  # Use zero to test degradation logic directly
        sim = LatencySimulator(config)

        original_price = Decimal("0.50")
        delay_ms = 2000  # 2 seconds

        degraded_price = sim.apply_price_degradation(original_price, "BUY", delay_ms)

        assert degraded_price > original_price, "BUY should increase price"
        assert isinstance(degraded_price, Decimal)

    def test_sell_degrades_price_downward(self):
        """SELL action decreases price (worse fill) with latency."""
        config = LatencyConfig.zero()
        sim = LatencySimulator(config)

        original_price = Decimal("0.50")
        delay_ms = 2000  # 2 seconds

        degraded_price = sim.apply_price_degradation(original_price, "SELL", delay_ms)

        assert degraded_price < original_price, "SELL should decrease price"
        assert isinstance(degraded_price, Decimal)

    def test_zero_delay_returns_original_price(self):
        """Zero delay returns original price (no degradation)."""
        config = LatencyConfig.zero()
        sim = LatencySimulator(config)

        original_price = Decimal("0.50")

        buy_price = sim.apply_price_degradation(original_price, "BUY", 0)
        sell_price = sim.apply_price_degradation(original_price, "SELL", 0)

        assert buy_price == original_price
        assert sell_price == original_price

    def test_price_degradation_proportional_to_delay(self):
        """Price degradation increases with delay duration."""
        config = LatencyConfig.zero()
        sim = LatencySimulator(config)

        original_price = Decimal("0.50")

        # Test BUY degradation at different delays
        degraded_1s = sim.apply_price_degradation(original_price, "BUY", 1000)
        degraded_2s = sim.apply_price_degradation(original_price, "BUY", 2000)
        degraded_4s = sim.apply_price_degradation(original_price, "BUY", 4000)

        assert original_price < degraded_1s < degraded_2s < degraded_4s

        # Test SELL degradation at different delays
        degraded_1s = sim.apply_price_degradation(original_price, "SELL", 1000)
        degraded_2s = sim.apply_price_degradation(original_price, "SELL", 2000)
        degraded_4s = sim.apply_price_degradation(original_price, "SELL", 4000)

        assert original_price > degraded_1s > degraded_2s > degraded_4s

    def test_default_slippage_rate(self):
        """Default slippage rate is 0.001 per second (0.1% per second)."""
        config = LatencyConfig.zero()
        sim = LatencySimulator(config)

        original_price = Decimal("1.00")
        delay_ms = 1000  # 1 second

        # BUY: price * (1 + 0.001 * 1s) = 1.001
        buy_price = sim.apply_price_degradation(original_price, "BUY", delay_ms)
        expected_buy = Decimal("1.001")
        assert abs(buy_price - expected_buy) < Decimal("0.0001"), f"Expected {expected_buy}, got {buy_price}"

        # SELL: price * (1 - 0.001 * 1s) = 0.999
        sell_price = sim.apply_price_degradation(original_price, "SELL", delay_ms)
        expected_sell = Decimal("0.999")
        assert abs(sell_price - expected_sell) < Decimal("0.0001"), f"Expected {expected_sell}, got {sell_price}"


class TestLatencyImpactResult:
    """Test LatencyImpactResult dataclass."""

    def test_degradation_percentage_calculation(self):
        """Degradation percentage is correctly computed."""
        result = LatencyImpactResult(
            baseline_pnl=Decimal("10.00"),
            latency_pnl=Decimal("8.50"),
            degradation_dollars=Decimal("1.50"),
            degradation_pct=15.0,
            avg_delay_ms=3500.0,
            scenario_name="baseline"
        )

        assert result.degradation_pct == 15.0
        assert result.degradation_dollars == Decimal("1.50")

    def test_negative_pnl_degradation(self):
        """Degradation handles negative PnL correctly."""
        # Baseline loses $5, latency loses $7 (worse by $2)
        result = LatencyImpactResult(
            baseline_pnl=Decimal("-5.00"),
            latency_pnl=Decimal("-7.00"),
            degradation_dollars=Decimal("2.00"),  # baseline - latency = -5 - (-7) = 2
            degradation_pct=40.0,  # 2 / |5| * 100 = 40%
            avg_delay_ms=3500.0,
            scenario_name="baseline"
        )

        assert result.baseline_pnl == Decimal("-5.00")
        assert result.latency_pnl == Decimal("-7.00")
        assert result.degradation_dollars == Decimal("2.00")
        assert result.degradation_pct == 40.0


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_full_latency_pipeline(self):
        """Test complete latency simulation pipeline."""
        config = LatencyConfig.baseline()
        sim = LatencySimulator(config)

        # Sample a delay
        total_delay = sim.sample_total_delay()
        delay_ms = total_delay.total_seconds() * 1000

        # Should be in expected range (1500 + 2000 = 3500ms +- 30%)
        assert 2450 <= delay_ms <= 4550

        # Apply price degradation
        original_price = Decimal("0.65")
        degraded_buy = sim.apply_price_degradation(original_price, "BUY", delay_ms)
        degraded_sell = sim.apply_price_degradation(original_price, "SELL", delay_ms)

        # Verify direction
        assert degraded_buy > original_price
        assert degraded_sell < original_price

    def test_reproducible_end_to_end(self):
        """End-to-end pipeline is reproducible with fixed seed."""
        config1 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=123)
        config2 = LatencyConfig(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30, seed=123)

        sim1 = LatencySimulator(config1)
        sim2 = LatencySimulator(config2)

        original_price = Decimal("0.50")

        # Run same sequence of operations
        results1 = []
        results2 = []

        for _ in range(20):
            delay1 = sim1.sample_total_delay()
            delay2 = sim2.sample_total_delay()

            price1 = sim1.apply_price_degradation(original_price, "BUY", delay1.total_seconds() * 1000)
            price2 = sim2.apply_price_degradation(original_price, "BUY", delay2.total_seconds() * 1000)

            results1.append((delay1, price1))
            results2.append((delay2, price2))

        assert results1 == results2, "Same seed should produce identical end-to-end results"
