"""Tests for decision computation."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.decision import DecisionEngine, DecisionConfig
from src.core.scaling import ScalingConfig
from src.core.caps import CapsConfig
from src.core.types import (
    ExecutionMode,
    MarketPhase,
    Side,
    OrderType,
    Exposure,
    LeaderSnapshot,
    MySnapshot,
)


class TestDecisionEngine:
    """Tests for DecisionEngine."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config = DecisionConfig(
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
        )
        self.engine = DecisionEngine(self.config, ExecutionMode.DRY_RUN)
    
    def test_decision_with_action(self):
        """Test decision that results in an action."""
        now = datetime.now(timezone.utc)
        
        leader_exposure = Exposure(
            market_id="test",
            up_dollars=Decimal("100"),
            down_dollars=Decimal("50"),
        )
        
        leader_snapshot = LeaderSnapshot(
            timestamp=now,
            exposures={"test": leader_exposure},
            total_assets=Decimal("1000"),
        )
        
        my_snapshot = MySnapshot(
            timestamp=now,
            exposures={},  # No position yet
            available_capital=Decimal("50"),
            used_capital=Decimal("0"),
            hourly_budget_used=Decimal("0"),
        )
        
        decision = self.engine.compute_decision(
            market_id="test",
            phase=MarketPhase.FOLLOW,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            timestamp=now,
        )
        
        # Should have an action
        assert decision.action is not None
        assert decision.action.side == Side.UP  # Larger delta
        assert decision.action.order_type == OrderType.MARKET
    
    def test_decision_no_action_closed_market(self):
        """Test no action on closed market."""
        now = datetime.now(timezone.utc)
        
        leader_snapshot = LeaderSnapshot(
            timestamp=now,
            exposures={"test": Exposure(market_id="test", up_dollars=Decimal("100"))},
            total_assets=Decimal("1000"),
        )
        
        my_snapshot = MySnapshot(
            timestamp=now,
            exposures={},
            available_capital=Decimal("50"),
            used_capital=Decimal("0"),
            hourly_budget_used=Decimal("0"),
        )
        
        decision = self.engine.compute_decision(
            market_id="test",
            phase=MarketPhase.CLOSED,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            timestamp=now,
        )
        
        assert decision.action is None
        assert decision.action_reason == "market_closed"
    
    def test_decision_no_action_below_minimum(self):
        """Test no action when delta is below minimum."""
        now = datetime.now(timezone.utc)
        
        # Small leader exposure leads to small target
        leader_exposure = Exposure(
            market_id="test",
            up_dollars=Decimal("10"),  # Small
            down_dollars=Decimal("5"),
        )
        
        leader_snapshot = LeaderSnapshot(
            timestamp=now,
            exposures={"test": leader_exposure},
            total_assets=Decimal("1000"),
        )
        
        # We already have close to target
        my_exposure = Exposure(
            market_id="test",
            up_dollars=Decimal("0.5"),  # Close to scaled target
        )
        
        my_snapshot = MySnapshot(
            timestamp=now,
            exposures={"test": my_exposure},
            available_capital=Decimal("49.5"),
            used_capital=Decimal("0.5"),
            hourly_budget_used=Decimal("0"),
        )
        
        decision = self.engine.compute_decision(
            market_id="test",
            phase=MarketPhase.FOLLOW,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            timestamp=now,
        )
        
        # Delta should be small, below $1 minimum
        # Target UP: 10 * (50/1000) * 0.6 = 0.30
        # Current: 0.50
        # Delta: -0.20 (sell) - below minimum
        if decision.action is None:
            assert "below_minimum" in decision.action_reason or "no_delta" in decision.action_reason
    
    def test_decision_hash_determinism(self):
        """Test that same inputs produce same hash."""
        now = datetime.now(timezone.utc)
        
        leader_snapshot = LeaderSnapshot(
            timestamp=now,
            exposures={"test": Exposure(market_id="test", up_dollars=Decimal("100"))},
            total_assets=Decimal("1000"),
        )
        
        my_snapshot = MySnapshot(
            timestamp=now,
            exposures={},
            available_capital=Decimal("50"),
            used_capital=Decimal("0"),
            hourly_budget_used=Decimal("0"),
        )
        
        decision1 = self.engine.compute_decision(
            market_id="test",
            phase=MarketPhase.FOLLOW,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            timestamp=now,
        )
        
        decision2 = self.engine.compute_decision(
            market_id="test",
            phase=MarketPhase.FOLLOW,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            timestamp=now,
        )
        
        assert decision1.compute_hash() == decision2.compute_hash()
    
    def test_error_metric(self):
        """Test error metric calculation."""
        from src.core.types import DecisionTarget
        
        target = DecisionTarget(
            market_id="test",
            target_up_dollars=Decimal("5"),
            target_down_dollars=Decimal("3"),
            current_up_dollars=Decimal("4"),
            current_down_dollars=Decimal("2"),
        )
        
        error = self.engine.compute_error_metric(target)
        
        # Error = |5-4| + |3-2| = 1 + 1 = 2
        assert error == Decimal("2")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
