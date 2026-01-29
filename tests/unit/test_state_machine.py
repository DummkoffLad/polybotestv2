"""Tests for state machine transitions."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.state_machine import (
    MarketStateMachine,
    StateMachineConfig,
    BurstTracker,
)
from src.core.types import MarketPhase, Exposure


class TestBurstTracker:
    """Tests for BurstTracker."""
    
    def test_stable_detection(self):
        """Test stabilization detection."""
        tracker = BurstTracker()
        now = datetime.now(timezone.utc)
        
        # Add samples with same value
        for i in range(5):
            tracker.add_sample(Decimal("10"), now + timedelta(seconds=i))
        
        assert tracker.is_stable(threshold=Decimal("0.5"), required_count=3)
    
    def test_unstable_detection(self):
        """Test unstable detection."""
        tracker = BurstTracker()
        now = datetime.now(timezone.utc)
        
        # Add samples with varying values
        values = [Decimal("10"), Decimal("15"), Decimal("8"), Decimal("20"), Decimal("5")]
        for i, val in enumerate(values):
            tracker.add_sample(val, now + timedelta(seconds=i))
        
        assert not tracker.is_stable(threshold=Decimal("0.5"), required_count=3)
    
    def test_insufficient_samples(self):
        """Test with insufficient samples."""
        tracker = BurstTracker()
        now = datetime.now(timezone.utc)
        
        # Add only 2 samples
        tracker.add_sample(Decimal("10"), now)
        tracker.add_sample(Decimal("10"), now + timedelta(seconds=1))
        
        # Should not be stable (need 3)
        assert not tracker.is_stable(threshold=Decimal("0.5"), required_count=3)
    
    def test_stable_value(self):
        """Test getting stable value."""
        tracker = BurstTracker()
        now = datetime.now(timezone.utc)
        
        # Add samples
        tracker.add_sample(Decimal("10"), now)
        tracker.add_sample(Decimal("11"), now + timedelta(seconds=1))
        tracker.add_sample(Decimal("12"), now + timedelta(seconds=2))
        
        stable_val = tracker.get_stable_value(required_count=3)
        
        # Average of last 3: (10+11+12)/3 = 11
        assert stable_val == Decimal("11")


class TestMarketStateMachine:
    """Tests for MarketStateMachine."""
    
    def test_initial_state(self):
        """Test initial state is UNKNOWN."""
        config = StateMachineConfig()
        sm = MarketStateMachine("test_market", config)
        
        assert sm.state.phase == MarketPhase.UNKNOWN
    
    def test_market_open_enters_burst(self):
        """Test that market open enters BURST phase."""
        config = StateMachineConfig()
        sm = MarketStateMachine("test_market", config)
        
        open_time = datetime.now(timezone.utc)
        sm.on_market_open(open_time)
        
        assert sm.state.phase == MarketPhase.BURST
        assert sm.state.market_open_time == open_time
    
    def test_burst_to_follow_transition(self):
        """Test BURST -> FOLLOW transition."""
        config = StateMachineConfig(
            burst_window_sec=5,
            burst_stabilization_threshold=Decimal("0.5"),
            burst_stabilization_count=3,
        )
        sm = MarketStateMachine("test_market", config)
        
        open_time = datetime.now(timezone.utc)
        sm.on_market_open(open_time)
        
        # Simulate stable exposure over burst window
        exposure = Exposure(
            market_id="test_market",
            up_dollars=Decimal("10"),
            down_dollars=Decimal("5"),
        )
        
        # Update multiple times within burst window
        for i in range(4):
            current = open_time + timedelta(seconds=i)
            sm.update(current, exposure)
        
        # Still in BURST
        assert sm.state.phase == MarketPhase.BURST
        
        # Now past burst window
        past_window = open_time + timedelta(seconds=6)
        transition = sm.update(past_window, exposure)
        
        # Should transition to FOLLOW
        assert sm.state.phase == MarketPhase.FOLLOW
        assert transition is not None
        assert "burst_complete" in transition
    
    def test_follow_to_resync_transition(self):
        """Test FOLLOW -> RESYNC transition."""
        config = StateMachineConfig(
            burst_window_sec=5,
            resync_interval_sec=10,
        )
        sm = MarketStateMachine("test_market", config)
        
        open_time = datetime.now(timezone.utc)
        sm.on_market_open(open_time)
        
        # Force into FOLLOW mode
        sm.state.phase = MarketPhase.FOLLOW
        sm.state.phase_start_time = open_time
        
        exposure = Exposure(market_id="test_market")
        
        # Update before resync interval
        before_resync = open_time + timedelta(seconds=5)
        transition = sm.update(before_resync, exposure)
        assert sm.state.phase == MarketPhase.FOLLOW
        assert transition is None
        
        # Update after resync interval
        after_resync = open_time + timedelta(seconds=11)
        transition = sm.update(after_resync, exposure)
        assert sm.state.phase == MarketPhase.RESYNC
        assert transition is not None
        assert "resync_due" in transition
    
    def test_resync_to_follow_transition(self):
        """Test RESYNC -> FOLLOW transition."""
        config = StateMachineConfig()
        sm = MarketStateMachine("test_market", config)
        
        # Put in RESYNC mode
        sm.state.phase = MarketPhase.RESYNC
        
        exposure = Exposure(market_id="test_market")
        now = datetime.now(timezone.utc)
        
        # Update should transition to FOLLOW
        transition = sm.update(now, exposure)
        
        assert sm.state.phase == MarketPhase.FOLLOW
        assert transition == "resync_complete"
    
    def test_market_close(self):
        """Test market close."""
        config = StateMachineConfig()
        sm = MarketStateMachine("test_market", config)
        
        sm.state.phase = MarketPhase.FOLLOW
        sm.on_market_close()
        
        assert sm.state.phase == MarketPhase.CLOSED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
