"""Tests for cap enforcement."""

import pytest
from decimal import Decimal

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.caps import CapEnforcer, CapsConfig


class TestCapEnforcer:
    """Tests for CapEnforcer."""
    
    def test_no_caps_needed(self):
        """Test when no caps are needed."""
        config = CapsConfig(
            per_market_gross=Decimal("10"),
            per_side=Decimal("5"),
            global_capital=Decimal("50"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("3"),
            target_down=Decimal("2"),
            current_global_used=Decimal("10"),
        )
        
        assert result.capped_up_dollars == Decimal("3")
        assert result.capped_down_dollars == Decimal("2")
        assert not result.was_capped
    
    def test_per_side_cap(self):
        """Test per-side cap enforcement."""
        config = CapsConfig(
            per_market_gross=Decimal("20"),
            per_side=Decimal("5"),
            global_capital=Decimal("50"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("8"),  # Exceeds per_side
            target_down=Decimal("2"),
            current_global_used=Decimal("0"),
        )
        
        assert result.capped_up_dollars == Decimal("5")  # Capped
        assert result.capped_down_dollars == Decimal("2")  # Not capped
        assert result.was_capped
        assert "per_side_up" in result.caps_applied
    
    def test_per_market_gross_cap(self):
        """Test per-market gross cap enforcement."""
        config = CapsConfig(
            per_market_gross=Decimal("8"),
            per_side=Decimal("10"),  # Higher than gross
            global_capital=Decimal("50"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("6"),
            target_down=Decimal("6"),  # Total 12 > 8
            current_global_used=Decimal("0"),
        )
        
        # Should scale down proportionally
        # Scale = 8/12 = 0.666...
        assert result.capped_up_dollars == Decimal("4.00")
        assert result.capped_down_dollars == Decimal("4.00")
        assert result.was_capped
        assert "per_market_gross" in result.caps_applied
    
    def test_global_capital_cap(self):
        """Test global capital cap enforcement."""
        config = CapsConfig(
            per_market_gross=Decimal("20"),
            per_side=Decimal("10"),
            global_capital=Decimal("40"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("5"),
            target_down=Decimal("5"),  # Total 10
            current_global_used=Decimal("35"),  # Only 5 remaining
        )
        
        # Should scale to fit remaining global cap
        # Remaining = 5, requested = 10, scale = 0.5
        assert result.capped_up_dollars == Decimal("2.50")
        assert result.capped_down_dollars == Decimal("2.50")
        assert result.was_capped
        assert "global_capital" in result.caps_applied
    
    def test_global_cap_exhausted(self):
        """Test when global cap is exhausted."""
        config = CapsConfig(
            per_market_gross=Decimal("20"),
            per_side=Decimal("10"),
            global_capital=Decimal("40"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("5"),
            target_down=Decimal("5"),
            current_global_used=Decimal("40"),  # Already at cap
        )
        
        assert result.capped_up_dollars == Decimal("0")
        assert result.capped_down_dollars == Decimal("0")
        assert result.was_capped
    
    def test_hourly_budget_cap(self):
        """Test hourly budget cap enforcement."""
        config = CapsConfig(
            per_market_gross=Decimal("20"),
            per_side=Decimal("10"),
            global_capital=Decimal("50"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.enforce_caps(
            market_id="test",
            target_up=Decimal("5"),
            target_down=Decimal("5"),
            current_global_used=Decimal("0"),
            hourly_budget_remaining=Decimal("6"),  # Only 6 remaining
        )
        
        # Should scale to fit hourly budget
        # Remaining = 6, requested = 10, scale = 0.6
        assert result.capped_up_dollars == Decimal("3.00")
        assert result.capped_down_dollars == Decimal("3.00")
        assert result.was_capped
        assert "hourly_budget" in result.caps_applied
    
    def test_minimum_check_meets(self):
        """Test minimum check when meets threshold."""
        config = CapsConfig(
            market_min_dollars=Decimal("1"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.check_minimum(
            amount_dollars=Decimal("1.50"),
            is_market_order=True,
        )
        
        assert result["meets_minimum"] is True
    
    def test_minimum_check_fails(self):
        """Test minimum check when below threshold."""
        config = CapsConfig(
            market_min_dollars=Decimal("1"),
        )
        enforcer = CapEnforcer(config)
        
        result = enforcer.check_minimum(
            amount_dollars=Decimal("0.50"),
            is_market_order=True,
        )
        
        assert result["meets_minimum"] is False
        assert result["shortfall"] == "0.50"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
