"""
Latency simulator for copy trading validation.

Models realistic API latency (detection delay + execution delay + jitter)
and calculates price degradation from delayed execution.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import random
from typing import Optional


@dataclass
class LatencyConfig:
    """Configuration for latency simulation."""

    detection_delay_ms: float  # Time to detect blockchain event
    execution_delay_ms: float  # Time to execute our order
    jitter_pct: float  # +-% randomness on each delay
    seed: Optional[int] = None  # For reproducible testing

    @classmethod
    def baseline(cls) -> "LatencyConfig":
        """Baseline latency: 1500ms detection + 2000ms execution + 30% jitter."""
        return cls(detection_delay_ms=1500, execution_delay_ms=2000, jitter_pct=0.30)

    @classmethod
    def stress_2x(cls) -> "LatencyConfig":
        """Stress 2x: Double baseline delays."""
        return cls(detection_delay_ms=3000, execution_delay_ms=4000, jitter_pct=0.30)

    @classmethod
    def stress_3x(cls) -> "LatencyConfig":
        """Stress 3x: Triple baseline delays."""
        return cls(detection_delay_ms=4500, execution_delay_ms=6000, jitter_pct=0.30)

    @classmethod
    def zero(cls) -> "LatencyConfig":
        """Zero latency: No delays (comparison baseline)."""
        return cls(detection_delay_ms=0, execution_delay_ms=0, jitter_pct=0.0)


class LatencySimulator:
    """
    Simulates realistic API latency for copy trading.

    Models the full latency pipeline:
    1. Detection delay: Time to detect blockchain event
    2. Execution delay: Time to execute our order
    3. Jitter: Random variance on each delay

    Also calculates price degradation from delayed execution.
    """

    def __init__(self, config: LatencyConfig):
        """
        Initialize latency simulator.

        Args:
            config: Latency configuration
        """
        self.config = config
        # Use private Random instance for reproducibility
        self._rng = random.Random(config.seed)

    def sample_detection_delay(self) -> timedelta:
        """
        Sample detection delay with jitter.

        Returns:
            timedelta representing detection delay
        """
        base_ms = self.config.detection_delay_ms
        if base_ms == 0:
            return timedelta(0)

        # Apply jitter: uniform random in [base * (1 - jitter), base * (1 + jitter)]
        jitter_range = base_ms * self.config.jitter_pct
        delay_ms = self._rng.uniform(
            max(0, base_ms - jitter_range),  # Never negative
            base_ms + jitter_range
        )

        return timedelta(milliseconds=delay_ms)

    def sample_execution_delay(self) -> timedelta:
        """
        Sample execution delay with jitter.

        Returns:
            timedelta representing execution delay
        """
        base_ms = self.config.execution_delay_ms
        if base_ms == 0:
            return timedelta(0)

        # Apply jitter: uniform random in [base * (1 - jitter), base * (1 + jitter)]
        jitter_range = base_ms * self.config.jitter_pct
        delay_ms = self._rng.uniform(
            max(0, base_ms - jitter_range),  # Never negative
            base_ms + jitter_range
        )

        return timedelta(milliseconds=delay_ms)

    def sample_total_delay(self) -> timedelta:
        """
        Sample total delay (detection + execution).

        Returns:
            timedelta representing total delay
        """
        detection = self.sample_detection_delay()
        execution = self.sample_execution_delay()
        return detection + execution

    def apply_price_degradation(
        self,
        price: Decimal,
        action: str,
        delay_ms: float,
        slippage_rate: float = 0.001
    ) -> Decimal:
        """
        Apply price degradation from latency.

        Price moves during delay window, resulting in worse fill:
        - BUY: price moves UP (pay more)
        - SELL: price moves DOWN (receive less)

        Args:
            price: Original price
            action: "BUY" or "SELL"
            delay_ms: Delay in milliseconds
            slippage_rate: Slippage per second (default: 0.001 = 0.1% per second)

        Returns:
            Degraded price
        """
        if delay_ms == 0:
            return price

        delay_s = delay_ms / 1000.0
        slippage = Decimal(str(slippage_rate * delay_s))

        if action == "BUY":
            # BUY: price increases during delay (worse fill)
            return price * (Decimal("1") + slippage)
        elif action == "SELL":
            # SELL: price decreases during delay (worse fill)
            return price * (Decimal("1") - slippage)
        else:
            raise ValueError(f"Invalid action: {action}. Must be 'BUY' or 'SELL'")


@dataclass
class LatencyImpactResult:
    """
    Result of latency impact analysis.

    Compares PnL with and without latency to quantify degradation.
    """

    baseline_pnl: Decimal  # PnL with zero latency
    latency_pnl: Decimal  # PnL with latency applied
    degradation_dollars: Decimal  # baseline_pnl - latency_pnl
    degradation_pct: float  # degradation / |baseline_pnl| * 100
    avg_delay_ms: float  # Average total delay applied
    scenario_name: str  # e.g., "baseline", "stress_2x"
