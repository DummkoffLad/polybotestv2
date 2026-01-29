"""Cap enforcement logic.

Implements exposure caps:
- Per-market gross exposure (UP + DOWN)
- Per-side exposure
- Global used capital
- Hourly budget
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Any, Optional

from .types import Side, Exposure, MarketId


@dataclass
class CapsConfig:
    """Configuration for exposure caps."""
    # Per-market gross exposure cap (UP + DOWN combined)
    per_market_gross: Decimal = Decimal("8")
    
    # Per-side exposure cap
    per_side: Decimal = Decimal("5")
    
    # Global used capital cap
    global_capital: Decimal = Decimal("40")
    
    # Minimum trade sizes
    market_min_dollars: Decimal = Decimal("1")
    limit_min_shares: Decimal = Decimal("5")
    
    # Share precision
    share_precision: int = 2


@dataclass
class CappedTarget:
    """A target after cap enforcement."""
    market_id: MarketId
    original_up_dollars: Decimal
    original_down_dollars: Decimal
    capped_up_dollars: Decimal
    capped_down_dollars: Decimal
    caps_applied: Dict[str, Any]
    
    @property
    def was_capped(self) -> bool:
        return (
            self.capped_up_dollars != self.original_up_dollars or
            self.capped_down_dollars != self.original_down_dollars
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "original_up": str(self.original_up_dollars),
            "original_down": str(self.original_down_dollars),
            "capped_up": str(self.capped_up_dollars),
            "capped_down": str(self.capped_down_dollars),
            "was_capped": self.was_capped,
            "caps_applied": self.caps_applied,
        }


class CapEnforcer:
    """Enforces exposure caps on targets."""
    
    def __init__(self, config: CapsConfig):
        self.config = config
    
    def enforce_caps(
        self,
        market_id: MarketId,
        target_up: Decimal,
        target_down: Decimal,
        current_global_used: Decimal,
        hourly_budget_remaining: Optional[Decimal] = None,
    ) -> CappedTarget:
        """Apply all caps to a target.
        
        Args:
            market_id: Market identifier
            target_up: Target UP exposure in dollars
            target_down: Target DOWN exposure in dollars
            current_global_used: Current total capital used across all markets
            hourly_budget_remaining: Remaining hourly budget (None = unlimited)
            
        Returns:
            CappedTarget with enforced caps
        """
        caps_applied = {}
        capped_up = target_up
        capped_down = target_down
        
        # 1. Enforce per-side cap
        if capped_up > self.config.per_side:
            caps_applied["per_side_up"] = {
                "original": str(capped_up),
                "cap": str(self.config.per_side),
            }
            capped_up = self.config.per_side
        
        if capped_down > self.config.per_side:
            caps_applied["per_side_down"] = {
                "original": str(capped_down),
                "cap": str(self.config.per_side),
            }
            capped_down = self.config.per_side
        
        # 2. Enforce per-market gross cap
        gross = capped_up + capped_down
        if gross > self.config.per_market_gross:
            # Scale both sides proportionally
            scale = self.config.per_market_gross / gross
            caps_applied["per_market_gross"] = {
                "original_gross": str(gross),
                "cap": str(self.config.per_market_gross),
                "scale": str(scale),
            }
            capped_up = (capped_up * scale).quantize(Decimal("0.01"))
            capped_down = (capped_down * scale).quantize(Decimal("0.01"))
        
        # 3. Enforce global capital cap
        # How much room do we have in global cap?
        global_remaining = self.config.global_capital - current_global_used
        new_exposure = capped_up + capped_down
        
        if new_exposure > global_remaining:
            if global_remaining <= 0:
                # No room at all
                caps_applied["global_capital"] = {
                    "reason": "no_room",
                    "used": str(current_global_used),
                    "cap": str(self.config.global_capital),
                }
                capped_up = Decimal("0")
                capped_down = Decimal("0")
            else:
                # Scale to fit
                scale = global_remaining / new_exposure
                caps_applied["global_capital"] = {
                    "remaining": str(global_remaining),
                    "requested": str(new_exposure),
                    "scale": str(scale),
                }
                capped_up = (capped_up * scale).quantize(Decimal("0.01"))
                capped_down = (capped_down * scale).quantize(Decimal("0.01"))
        
        # 4. Enforce hourly budget (if applicable)
        if hourly_budget_remaining is not None:
            new_exposure = capped_up + capped_down
            if new_exposure > hourly_budget_remaining:
                if hourly_budget_remaining <= 0:
                    caps_applied["hourly_budget"] = {
                        "reason": "exhausted",
                    }
                    capped_up = Decimal("0")
                    capped_down = Decimal("0")
                else:
                    scale = hourly_budget_remaining / new_exposure
                    caps_applied["hourly_budget"] = {
                        "remaining": str(hourly_budget_remaining),
                        "requested": str(new_exposure),
                        "scale": str(scale),
                    }
                    capped_up = (capped_up * scale).quantize(Decimal("0.01"))
                    capped_down = (capped_down * scale).quantize(Decimal("0.01"))
        
        return CappedTarget(
            market_id=market_id,
            original_up_dollars=target_up,
            original_down_dollars=target_down,
            capped_up_dollars=capped_up,
            capped_down_dollars=capped_down,
            caps_applied=caps_applied,
        )
    
    def check_minimum(
        self,
        amount_dollars: Decimal,
        amount_shares: Optional[Decimal] = None,
        is_market_order: bool = True,
    ) -> Dict[str, Any]:
        """Check if an order meets minimum requirements.
        
        Args:
            amount_dollars: Order amount in dollars
            amount_shares: Order amount in shares (if known)
            is_market_order: Whether this is a market order
            
        Returns:
            Dict with 'meets_minimum', 'min_required', 'shortfall'
        """
        if is_market_order:
            min_required = self.config.market_min_dollars
            meets = amount_dollars >= min_required
            return {
                "meets_minimum": meets,
                "type": "market_dollars",
                "value": str(amount_dollars),
                "min_required": str(min_required),
                "shortfall": str(max(Decimal("0"), min_required - amount_dollars)),
            }
        else:
            # Limit order - check shares
            if amount_shares is None:
                return {
                    "meets_minimum": False,
                    "type": "limit_shares",
                    "error": "shares_unknown",
                }
            
            min_required = self.config.limit_min_shares
            meets = amount_shares >= min_required
            return {
                "meets_minimum": meets,
                "type": "limit_shares",
                "value": str(amount_shares),
                "min_required": str(min_required),
                "shortfall": str(max(Decimal("0"), min_required - amount_shares)),
            }
    
    def compute_delta_order(
        self,
        target: CappedTarget,
        current: Exposure,
        side: Side,
    ) -> Dict[str, Any]:
        """Compute the order needed to reach target from current.
        
        Args:
            target: Capped target
            current: Current exposure
            side: Which side to compute
            
        Returns:
            Dict with order details and whether it meets minimums
        """
        if side == Side.UP:
            target_dollars = target.capped_up_dollars
            current_dollars = current.up_dollars
        else:
            target_dollars = target.capped_down_dollars
            current_dollars = current.down_dollars
        
        delta = target_dollars - current_dollars
        
        result = {
            "side": str(side),
            "target": str(target_dollars),
            "current": str(current_dollars),
            "delta": str(delta),
        }
        
        if delta > 0:
            # Need to buy
            result["action"] = "BUY"
            result["amount"] = str(delta)
            min_check = self.check_minimum(delta, is_market_order=True)
            result["min_check"] = min_check
        elif delta < 0:
            # Need to sell
            result["action"] = "SELL"
            result["amount"] = str(abs(delta))
            min_check = self.check_minimum(abs(delta), is_market_order=True)
            result["min_check"] = min_check
        else:
            result["action"] = "NONE"
            result["min_check"] = {"meets_minimum": True, "reason": "no_change"}
        
        return result
