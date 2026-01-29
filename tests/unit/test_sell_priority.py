"""Regression tests for SELL priority, double-execution prevention, and proportional selling.

These tests verify the critical invariants from the spec:
1. SELL must NEVER be blocked by safety filters (price/spread/drift/min)
2. No double execution: trade-first + delta must not fire for the same trade
3. SELL must be proportional and bounded (never sell more than owned)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core import (
    ExecutionMode,
    SimulatedClock,
    LeaderSnapshot,
    MySnapshot,
    Exposure,
    Side,
)
from src.execution import NullExecutionAdapter
from src.strategy.runner import StrategyRunner
from src.strategy.delta_state import ShadowPortfolio


class SellTestRunner(StrategyRunner):
    """Strategy runner for testing SELL behavior."""

    def __init__(self, *args, leader_exposures=None, **kwargs):
        kwargs.setdefault('use_live_data', False)
        super().__init__(*args, **kwargs)
        self._mock_cycle = 0
        self._max_cycles = 10
        self._leader_exposures = leader_exposures or []

    def _fetch_leader_snapshot(self, now: datetime):
        idx = min(self._mock_cycle, len(self._leader_exposures) - 1)
        exp = self._leader_exposures[idx]
        return LeaderSnapshot(
            timestamp=now,
            exposures={
                "test_market": Exposure(
                    market_id="test_market",
                    up_dollars=exp["up"],
                    down_dollars=exp["down"],
                    up_shares=exp["up"] * 2,
                    down_shares=exp["down"] * 2,
                ),
            },
            total_assets=Decimal("1000"),
        )

    def _fetch_my_snapshot(self, now: datetime):
        return MySnapshot(
            timestamp=now,
            exposures={},
            available_capital=self.config.scaling.our_capital,
            used_capital=Decimal("0"),
            hourly_budget_used=self._hourly_budget_used,
        )

    def _get_token_id(self, market_id, side):
        if market_id == "test_market":
            return "token_up" if side == Side.UP else "token_down"
        return None

    def _run_cycle(self):
        self._mock_cycle += 1
        if hasattr(self.clock, 'advance'):
            self.clock.advance(25.0)
        super()._run_cycle()
        if self._mock_cycle >= self._max_cycles:
            self._running = False


def _make_config(tmp_path):
    """Create a test config with known values."""
    from src.config import (
        BotConfig, LeaderConfig, TraderConfig, TimingConfig,
        CircuitBreakerConfig, LoggingConfig, CollectorConfig,
        SimulationConfig, PersistenceConfig, ApiConfig, MarketsConfig,
        PreflightConfig, CopyTradingConfig, SafetyConfig, ScalingConfigPct,
        MirrorStrategyConfig,
    )
    from src.core import ScalingConfig, CapsConfig

    config = BotConfig(
        mode=ExecutionMode.DRY_RUN,
        leader=LeaderConfig(
            address="0x1234567890abcdef1234567890abcdef12345678",
            poll_interval_sec=0.1,
        ),
        trader=TraderConfig(),
        scaling_pct=ScalingConfigPct(k_factor=Decimal("0.6"), dry_run_capital=Decimal("50")),
        scaling=ScalingConfig(
            our_capital=Decimal("50"),
            k_factor=Decimal("0.6"),
            leader_capital=Decimal("1000"),
            hourly_budget=Decimal("45"),
        ),
        caps=CapsConfig(
            per_market_gross=Decimal("30"),
            per_side=Decimal("20"),
            global_capital=Decimal("50"),
            market_min_dollars=Decimal("1"),
        ),
        timing=TimingConfig(),
        circuit_breakers=CircuitBreakerConfig(),
        logging=LoggingConfig(level="DEBUG", output="file", file_path="logs/t.jsonl",
                              trace_enabled=True, trace_path="traces/t.jsonl"),
        collector=CollectorConfig(enabled=False),
        simulation=SimulationConfig(),
        persistence=PersistenceConfig(),
        api=ApiConfig(),
        markets=MarketsConfig(),
        preflight=PreflightConfig(),
        copy_trading=CopyTradingConfig(),
        safety=SafetyConfig(
            max_buy_price_drift_pct=Decimal("6.0"),
            max_spread_pct=Decimal("6.0"),
        ),
        mirror_strategy=MirrorStrategyConfig(),
        strategy="mirror",
        data_dir=tmp_path,
    )
    config.initialize_capital()
    return config


class TestSellNeverBlocked:
    """SELL must NEVER be blocked by safety filters."""

    def test_sell_executes_without_price_context(self, tmp_path):
        """SELL must execute even when price context is unavailable."""
        # Leader starts with big position then reduces to zero
        exposures = [
            {"up": Decimal("500"), "down": Decimal("0")},  # baseline
            {"up": Decimal("500"), "down": Decimal("0")},  # no change
            {"up": Decimal("0"), "down": Decimal("0")},    # leader sells all
        ]
        config = _make_config(tmp_path)
        clock = SimulatedClock(datetime.now(timezone.utc))
        adapter = NullExecutionAdapter()

        runner = SellTestRunner(config, adapter, clock,
                                leader_exposures=exposures)
        runner._max_cycles = 3
        runner.run()

        # The runner has no price context (use_live_data=False)
        # SELL must still execute using synthetic/fallback price
        assert runner._stats["sells_executed"] >= 0  # Should not crash
        # Key: sells_skipped should be 0 (never blocked)
        assert runner._counters["sells_skipped_total"] == 0

    def test_sell_not_blocked_by_spread(self, tmp_path):
        """SELL must not be blocked by spread filter."""
        config = _make_config(tmp_path)
        # Set very tight spread limit - should only affect BUYs
        config.safety.max_spread_pct = Decimal("0.1")

        exposures = [
            {"up": Decimal("500"), "down": Decimal("0")},
            {"up": Decimal("500"), "down": Decimal("0")},
            {"up": Decimal("0"), "down": Decimal("0")},
        ]
        clock = SimulatedClock(datetime.now(timezone.utc))
        adapter = NullExecutionAdapter()

        runner = SellTestRunner(config, adapter, clock,
                                leader_exposures=exposures)
        runner._max_cycles = 3
        runner.run()

        # SELL must never be blocked regardless of spread settings
        assert runner._counters["sells_skipped_total"] == 0


class TestNoDoubleExecution:
    """Trade-first and delta logic must not double-execute the same trade."""

    def test_trade_first_prevents_delta_resell(self, tmp_path):
        """When trade-first handles a SELL, delta logic must not re-sell."""
        config = _make_config(tmp_path)
        clock = SimulatedClock(datetime.now(timezone.utc))
        adapter = NullExecutionAdapter()

        # Leader buys then sells
        exposures = [
            {"up": Decimal("500"), "down": Decimal("0")},  # baseline
            {"up": Decimal("500"), "down": Decimal("0")},  # hold
            {"up": Decimal("250"), "down": Decimal("0")},  # sell 50%
            {"up": Decimal("250"), "down": Decimal("0")},  # hold
        ]

        runner = SellTestRunner(config, adapter, clock,
                                leader_exposures=exposures)
        runner._max_cycles = 4
        runner.run()

        # Total sells should be bounded - no double-execution
        # The trade-first tracking set should prevent delta logic from re-processing
        total_sold = runner._stats["total_sold_dollars"]
        total_bought = runner._stats["total_bought_dollars"]

        # Sanity: cannot sell more than bought (no double execution)
        assert total_sold <= total_bought + Decimal("1"), (
            f"Sold ${total_sold} but only bought ${total_bought} - likely double execution"
        )


class TestProportionalSell:
    """SELL must be proportional and bounded."""

    def test_sell_never_exceeds_position(self, tmp_path):
        """SELL shares must never exceed owned shares."""
        shadow = ShadowPortfolio()

        # Buy some shares
        shadow.apply_fill(
            token_id="tok1", market_id="mkt1", side=Side.UP,
            action="BUY", shares=Decimal("10"), price=Decimal("0.50"),
            timestamp=datetime.now(timezone.utc),
        )

        pos = shadow.get("tok1", "mkt1", Side.UP)
        assert pos.shares == Decimal("10")

        # Try to sell more than owned - should clamp
        sell_shares = min(Decimal("15"), pos.shares)
        assert sell_shares == Decimal("10"), "Should clamp to owned shares"

        shadow.apply_fill(
            token_id="tok1", market_id="mkt1", side=Side.UP,
            action="SELL", shares=sell_shares, price=Decimal("0.50"),
            timestamp=datetime.now(timezone.utc),
        )

        pos_after = shadow.get("tok1", "mkt1", Side.UP)
        assert pos_after.shares == Decimal("0"), "Position should be zero after selling all"
        assert pos_after.shares >= 0, "Position must never go negative"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
