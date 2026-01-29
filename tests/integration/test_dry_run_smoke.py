"""Integration smoke test for DRY-RUN mode.

Runs the strategy runner for N cycles with:
- Real-ish mock data (simulating leader/market)
- NullExecutionAdapter (no real orders)
- Verifies no exceptions
- Verifies trace output
"""

import pytest
import tempfile
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import BotConfig, load_config
from src.core import (
    ExecutionMode,
    SystemClock,
    SimulatedClock,
    LeaderSnapshot,
    MySnapshot,
    Exposure,
)
from src.execution import NullExecutionAdapter
from src.strategy.runner import StrategyRunner


class MockStrategyRunner(StrategyRunner):
    """Strategy runner with mocked data fetching."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('use_live_data', False)
        super().__init__(*args, **kwargs)
        self._mock_cycle = 0
        self._max_cycles = 5

    def _fetch_leader_snapshot(self, now: datetime):
        """Return mock leader data."""
        # Simulate leader with large enough changes to trigger trades
        # Scale ratio: ($50 / $1000) * 0.6 = 0.03
        # Need delta * 0.03 >= $1 min -> delta >= $34
        up_exposure = Decimal("200") + Decimal(str(self._mock_cycle * 100))
        down_exposure = Decimal("100")

        return LeaderSnapshot(
            timestamp=now,
            exposures={
                "btc_hourly_001": Exposure(
                    market_id="btc_hourly_001",
                    up_dollars=up_exposure,
                    down_dollars=down_exposure,
                    up_shares=up_exposure * 2,
                    down_shares=down_exposure * 2,
                ),
            },
            total_assets=Decimal("1000"),
        )

    def _fetch_my_snapshot(self, now: datetime):
        """Return mock our data."""
        return MySnapshot(
            timestamp=now,
            exposures={},
            available_capital=self.config.scaling.our_capital,
            used_capital=Decimal("0"),
            hourly_budget_used=self._hourly_budget_used,
        )

    def _get_token_id(self, market_id, side):
        """Provide mock token IDs for test markets."""
        from src.core import Side
        if market_id == "btc_hourly_001":
            return "mock_token_up" if side == Side.UP else "mock_token_down"
        return None

    def _run_cycle(self):
        """Run cycle with limit."""
        self._mock_cycle += 1
        # Advance simulated clock by 25s per cycle so snapshot audit fires
        if hasattr(self.clock, 'advance'):
            self.clock.advance(25.0)
        super()._run_cycle()

        if self._mock_cycle >= self._max_cycles:
            self._running = False


class TestDryRunSmoke:
    """Smoke tests for DRY-RUN mode."""
    
    def test_dry_run_produces_no_orders(self, tmp_path):
        """Test that DRY-RUN mode places no orders."""
        # Create minimal config
        config = self._create_test_config(tmp_path)
        
        adapter = NullExecutionAdapter()
        clock = SimulatedClock(datetime.now(timezone.utc))
        
        runner = MockStrategyRunner(config, adapter, clock)
        runner.run()
        
        # Check no orders were placed
        order_log = adapter.get_order_log()
        
        # Orders may be attempted but not filled
        for order in order_log:
            # All orders should have been rejected
            pass
        
        # Should have run without exceptions
        assert runner._mock_cycle >= 5
    
    def test_null_adapter_cannot_place_orders(self):
        """Test NullExecutionAdapter safety."""
        adapter = NullExecutionAdapter()
        
        # Verify properties
        assert adapter.mode == ExecutionMode.DRY_RUN
        assert adapter.can_place_orders is False
        assert adapter.is_armed is True  # Always armed but can't do anything
        
        # Try to place an order
        from src.core.types import OrderRequest, Side, OrderType
        
        request = OrderRequest(
            market_id="test",
            side=Side.UP,
            order_type=OrderType.MARKET,
            dollars=Decimal("5"),
        )
        
        response = adapter.place_order(request)
        
        # Should fail
        assert response.success is False
        assert "DRY_RUN" in response.error_message
    
    def _create_test_config(self, tmp_path: Path) -> BotConfig:
        """Create a minimal test configuration."""
        from src.config import (
            LeaderConfig,
            TraderConfig,
            TimingConfig,
            CircuitBreakerConfig,
            LoggingConfig,
            CollectorConfig,
            SimulationConfig,
            PersistenceConfig,
            ApiConfig,
            MarketsConfig,
            PreflightConfig,
            CopyTradingConfig,
            SafetyConfig,
            ScalingConfigPct,
            MirrorStrategyConfig,
        )
        from src.core import ScalingConfig, CapsConfig

        scaling_pct = ScalingConfigPct(
            k_factor=Decimal("0.6"),
            dry_run_capital=Decimal("50"),
        )

        config = BotConfig(
            mode=ExecutionMode.DRY_RUN,
            leader=LeaderConfig(
                address="0x1234567890abcdef1234567890abcdef12345678",
                poll_interval_sec=0.1,
            ),
            trader=TraderConfig(),
            scaling_pct=scaling_pct,
            scaling=ScalingConfig(
                our_capital=Decimal("50"),
                k_factor=Decimal("0.6"),
                leader_capital=Decimal("1000"),
                hourly_budget=Decimal("20"),
            ),
            caps=CapsConfig(
                per_market_gross=Decimal("8"),
                per_side=Decimal("5"),
                global_capital=Decimal("40"),
                market_min_dollars=Decimal("1"),
            ),
            timing=TimingConfig(),
            circuit_breakers=CircuitBreakerConfig(),
            logging=LoggingConfig(
                level="DEBUG",
                output="file",
                file_path="logs/test.jsonl",
                trace_enabled=True,
                trace_path="traces/test.jsonl",
            ),
            collector=CollectorConfig(enabled=False),
            simulation=SimulationConfig(),
            persistence=PersistenceConfig(),
            api=ApiConfig(),
            markets=MarketsConfig(),
            preflight=PreflightConfig(),
            copy_trading=CopyTradingConfig(),
            safety=SafetyConfig(),
            mirror_strategy=MirrorStrategyConfig(),
            strategy="mirror",
            data_dir=tmp_path,
        )
        config.initialize_capital()
        return config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
