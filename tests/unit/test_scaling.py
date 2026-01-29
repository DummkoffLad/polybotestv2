"""Tests for scaling calculations."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.scaling import ScalingCalculator, ScalingConfig
from src.core.types import Exposure, LeaderSnapshot


class TestScalingCalculator:
    """Tests for ScalingCalculator."""
    
    def test_basic_scaling(self):
        """Test basic scaling calculation."""
        config = ScalingConfig(
            our_capital=Decimal("50"),
            k_factor=Decimal("0.6"),
            leader_capital=Decimal("1000"),
        )
        calc = ScalingCalculator(config)
        
        leader_exposure = Exposure(
            market_id="test",
            up_dollars=Decimal("100"),
            down_dollars=Decimal("50"),
        )
        
        leader_snapshot = LeaderSnapshot(
            timestamp=datetime.now(timezone.utc),
            exposures={"test": leader_exposure},
            total_assets=Decimal("1000"),
        )
        
        result = calc.compute_target(leader_exposure, leader_snapshot)
        
        # Expected: 100 * (50/1000) * 0.6 = 3.0 for UP
        assert result.target_up_dollars == Decimal("3.00")
        # Expected: 50 * (50/1000) * 0.6 = 1.5 for DOWN
        assert result.target_down_dollars == Decimal("1.50")
    
    def test_scale_ratio(self):
        """Test scale ratio computation."""
        config = ScalingConfig(
            our_capital=Decimal("100"),
            k_factor=Decimal("0.5"),
            leader_capital=Decimal("1000"),
        )
        calc = ScalingCalculator(config)
        
        ratio = calc.compute_scale_ratio(Decimal("1000"))
        
        # Expected: (100/1000) * 0.5 = 0.05
        assert ratio == Decimal("0.05")
    
    def test_zero_leader_capital(self):
        """Test handling of zero leader capital."""
        config = ScalingConfig(
            our_capital=Decimal("50"),
            k_factor=Decimal("0.6"),
            leader_capital=Decimal("0"),  # Will use min
            min_leader_capital=Decimal("100"),
        )
        calc = ScalingCalculator(config)
        
        ratio = calc.compute_scale_ratio(Decimal("0"))
        
        # Zero capital should return zero ratio
        assert ratio == Decimal("0")
    
    def test_leader_capital_estimation(self):
        """Test automatic leader capital estimation."""
        config = ScalingConfig(
            our_capital=Decimal("50"),
            k_factor=Decimal("0.6"),
            leader_capital=Decimal("0"),  # Auto-detect
            min_leader_capital=Decimal("100"),
        )
        calc = ScalingCalculator(config)
        
        leader_snapshot = LeaderSnapshot(
            timestamp=datetime.now(timezone.utc),
            exposures={},
            total_assets=Decimal("500"),
        )
        
        estimated = calc.get_leader_capital(leader_snapshot)
        
        # Should use total_assets
        assert estimated == Decimal("500")
    
    def test_leader_capital_minimum(self):
        """Test minimum leader capital enforcement."""
        config = ScalingConfig(
            our_capital=Decimal("50"),
            k_factor=Decimal("0.6"),
            leader_capital=Decimal("0"),
            min_leader_capital=Decimal("100"),
        )
        calc = ScalingCalculator(config)
        
        leader_snapshot = LeaderSnapshot(
            timestamp=datetime.now(timezone.utc),
            exposures={},
            total_assets=Decimal("50"),  # Below minimum
        )
        
        estimated = calc.get_leader_capital(leader_snapshot)
        
        # Should use minimum
        assert estimated == Decimal("100")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
