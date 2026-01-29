"""Scaling calculations for copy trading.

Implements the scaling formula:
    our_target = leader_exposure * (our_capital / leader_capital) * k

Where:
- leader_capital is estimated from leader total assets (including leftovers)
- k is a conservative factor (0.5-0.8)
- hourly budget limits new exposure per hour
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from .types import (
    Exposure,
    LeaderSnapshot,
    MarketId,
    Side,
)


@dataclass
class ScalingConfig:
    """Configuration for scaling calculations."""
    # Our capital
    our_capital: Decimal
    
    # Conservative factor (0.5-0.8 recommended)
    k_factor: Decimal
    
    # Leader capital (estimated from total assets)
    # If 0, will be estimated from first snapshot
    leader_capital: Decimal = Decimal("0")
    
    # Scaling mode: "dollars" or "shares"
    mode: str = "dollars"
    
    # Hourly budget (max new exposure per hour)
    hourly_budget: Decimal = Decimal("20")
    
    # Minimum leader capital to avoid division by tiny numbers
    min_leader_capital: Decimal = Decimal("100")


@dataclass
class ScalingResult:
    """Result of a scaling calculation."""
    target_up_dollars: Decimal
    target_down_dollars: Decimal
    target_up_shares: Decimal
    target_down_shares: Decimal
    scale_ratio: Decimal  # our_capital / leader_capital * k
    leader_capital_used: Decimal
    
    def to_dict(self) -> Dict:
        return {
            "target_up_dollars": str(self.target_up_dollars),
            "target_down_dollars": str(self.target_down_dollars),
            "target_up_shares": str(self.target_up_shares),
            "target_down_shares": str(self.target_down_shares),
            "scale_ratio": str(self.scale_ratio),
            "leader_capital_used": str(self.leader_capital_used),
        }


class ScalingCalculator:
    """Calculates scaled targets from leader positions."""
    
    def __init__(self, config: ScalingConfig):
        self.config = config
        self._estimated_leader_capital: Optional[Decimal] = None
    
    def get_leader_capital(self, snapshot: LeaderSnapshot) -> Decimal:
        """Get or estimate leader capital from snapshot.
        
        Uses configured value if set, otherwise estimates from
        leader total assets.
        """
        if self.config.leader_capital > 0:
            return self.config.leader_capital
        
        if self._estimated_leader_capital is None:
            # Estimate from leader total assets
            self._estimated_leader_capital = max(
                snapshot.total_assets,
                self.config.min_leader_capital
            )
        
        # Update estimate if leader assets grew significantly
        if snapshot.total_assets > self._estimated_leader_capital * Decimal("1.2"):
            self._estimated_leader_capital = snapshot.total_assets
        
        return max(self._estimated_leader_capital, self.config.min_leader_capital)
    
    def compute_scale_ratio(self, leader_capital: Decimal) -> Decimal:
        """Compute the scaling ratio.
        
        scale_ratio = (our_capital / leader_capital) * k
        """
        if leader_capital <= 0:
            return Decimal("0")
        
        return (self.config.our_capital / leader_capital) * self.config.k_factor
    
    def compute_target(
        self,
        leader_exposure: Exposure,
        leader_snapshot: LeaderSnapshot,
        up_price: Optional[Decimal] = None,
        down_price: Optional[Decimal] = None,
    ) -> ScalingResult:
        """Compute our target exposure from leader exposure.
        
        Args:
            leader_exposure: Leader's exposure in the market
            leader_snapshot: Full leader snapshot for capital estimation
            up_price: Current price for UP outcome (optional, improves accuracy)
            down_price: Current price for DOWN outcome (optional, improves accuracy)
            
        Returns:
            ScalingResult with target exposures
            
        NOTE: When prices are not provided, we estimate shares from dollars
        using a conservative 0.5 price assumption. This can cause position
        size mismatches at extreme prices (< 0.2 or > 0.8).
        """
        leader_capital = self.get_leader_capital(leader_snapshot)
        scale_ratio = self.compute_scale_ratio(leader_capital)
        
        if self.config.mode == "dollars":
            target_up_dollars = leader_exposure.up_dollars * scale_ratio
            target_down_dollars = leader_exposure.down_dollars * scale_ratio
            
            # Use actual prices if available, otherwise estimate
            # This fixes the bug where we assumed all prices are 0.5
            if up_price and up_price > Decimal("0"):
                target_up_shares = target_up_dollars / up_price
            else:
                # Conservative fallback: assume mid-range price
                target_up_shares = target_up_dollars / Decimal("0.5")
                
            if down_price and down_price > Decimal("0"):
                target_down_shares = target_down_dollars / down_price
            else:
                target_down_shares = target_down_dollars / Decimal("0.5")
        else:
            # Scale by shares
            target_up_shares = leader_exposure.up_shares * scale_ratio
            target_down_shares = leader_exposure.down_shares * scale_ratio
            
            # Use actual prices if available for dollar estimation
            if up_price and up_price > Decimal("0"):
                target_up_dollars = target_up_shares * up_price
            else:
                target_up_dollars = target_up_shares * Decimal("0.5")
                
            if down_price and down_price > Decimal("0"):
                target_down_dollars = target_down_shares * down_price
            else:
                target_down_dollars = target_down_shares * Decimal("0.5")
        
        # Quantize to reasonable precision
        target_up_dollars = target_up_dollars.quantize(Decimal("0.01"))
        target_down_dollars = target_down_dollars.quantize(Decimal("0.01"))
        target_up_shares = target_up_shares.quantize(Decimal("0.01"))
        target_down_shares = target_down_shares.quantize(Decimal("0.01"))
        
        return ScalingResult(
            target_up_dollars=target_up_dollars,
            target_down_dollars=target_down_dollars,
            target_up_shares=target_up_shares,
            target_down_shares=target_down_shares,
            scale_ratio=scale_ratio.quantize(Decimal("0.0001")),
            leader_capital_used=leader_capital,
        )


def compute_scaled_target(
    leader_exposure: Exposure,
    leader_snapshot: LeaderSnapshot,
    our_capital: Decimal,
    k_factor: Decimal = Decimal("0.6"),
    min_leader_capital: Decimal = Decimal("100"),
) -> ScalingResult:
    """Convenience function for one-off scaling calculations.
    
    For repeated calculations, use ScalingCalculator class.
    """
    config = ScalingConfig(
        our_capital=our_capital,
        k_factor=k_factor,
        min_leader_capital=min_leader_capital,
    )
    calculator = ScalingCalculator(config)
    return calculator.compute_target(leader_exposure, leader_snapshot)
