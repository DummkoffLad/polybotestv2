"""Decision computation and action generation.

Combines scaling, caps, and minimums to produce actionable orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
import uuid

from .types import (
    ExecutionMode,
    MarketPhase,
    MarketId,
    Side,
    OrderType,
    Exposure,
    LeaderSnapshot,
    MySnapshot,
    OrderRequest,
    DecisionTarget,
    Decision,
)
from .scaling import ScalingCalculator, ScalingConfig
from .caps import CapEnforcer, CapsConfig


@dataclass
class DecisionConfig:
    """Combined config for decision computation."""
    scaling: ScalingConfig
    caps: CapsConfig
    
    def compute_hash(self) -> str:
        """Compute hash of this config for tracing."""
        config_dict = {
            "scaling": {
                "our_capital": str(self.scaling.our_capital),
                "k_factor": str(self.scaling.k_factor),
                "leader_capital": str(self.scaling.leader_capital),
                "mode": self.scaling.mode,
                "hourly_budget": str(self.scaling.hourly_budget),
            },
            "caps": {
                "per_market_gross": str(self.caps.per_market_gross),
                "per_side": str(self.caps.per_side),
                "global_capital": str(self.caps.global_capital),
                "market_min_dollars": str(self.caps.market_min_dollars),
                "limit_min_shares": str(self.caps.limit_min_shares),
            },
        }
        return hashlib.sha256(
            json.dumps(config_dict, sort_keys=True).encode()
        ).hexdigest()[:12]


class DecisionEngine:
    """Computes trading decisions from leader and our state."""
    
    def __init__(self, config: DecisionConfig, mode: ExecutionMode):
        self.config = config
        self.mode = mode
        self.scaling_calc = ScalingCalculator(config.scaling)
        self.cap_enforcer = CapEnforcer(config.caps)
        self.config_hash = config.compute_hash()
    
    def compute_decision(
        self,
        market_id: MarketId,
        phase: MarketPhase,
        leader_snapshot: LeaderSnapshot,
        my_snapshot: MySnapshot,
        timestamp: datetime,
    ) -> Decision:
        """Compute a full decision for a market.
        
        Args:
            market_id: Market to decide for
            phase: Current market phase
            leader_snapshot: Current leader state
            my_snapshot: Current our state
            timestamp: Decision timestamp
            
        Returns:
            Decision object with targets, caps, and optional action
        """
        correlation_id = uuid.uuid4().hex[:12]
        
        # Get leader exposure for this market
        leader_exposure = leader_snapshot.exposures.get(
            market_id,
            Exposure(market_id=market_id)
        )
        
        # Get our exposure for this market
        my_exposure = my_snapshot.exposures.get(
            market_id,
            Exposure(market_id=market_id)
        )
        
        # Compute scaled target
        scaling_result = self.scaling_calc.compute_target(
            leader_exposure,
            leader_snapshot,
        )
        
        # Compute current global used capital
        global_used = sum(
            exp.gross_dollars for exp in my_snapshot.exposures.values()
        )
        
        # Compute remaining hourly budget
        hourly_remaining = (
            self.config.scaling.hourly_budget - my_snapshot.hourly_budget_used
        )
        
        # Enforce caps
        capped = self.cap_enforcer.enforce_caps(
            market_id=market_id,
            target_up=scaling_result.target_up_dollars,
            target_down=scaling_result.target_down_dollars,
            current_global_used=global_used,
            hourly_budget_remaining=hourly_remaining,
        )
        
        # Build decision target
        target = DecisionTarget(
            market_id=market_id,
            target_up_dollars=capped.capped_up_dollars,
            target_down_dollars=capped.capped_down_dollars,
            current_up_dollars=my_exposure.up_dollars,
            current_down_dollars=my_exposure.down_dollars,
        )
        
        # Determine action based on phase and deltas
        action, action_reason, min_checks = self._determine_action(
            phase=phase,
            target=target,
            my_exposure=my_exposure,
        )
        
        return Decision(
            timestamp=timestamp,
            correlation_id=correlation_id,
            mode=self.mode,
            market_id=market_id,
            phase=phase,
            leader_snapshot=leader_snapshot,
            my_snapshot=my_snapshot,
            config_hash=self.config_hash,
            target=target,
            caps_applied=capped.caps_applied,
            min_checks=min_checks,
            action=action,
            action_reason=action_reason,
        )
    
    def _determine_action(
        self,
        phase: MarketPhase,
        target: DecisionTarget,
        my_exposure: Exposure,
    ) -> tuple[Optional[OrderRequest], str, Dict[str, Any]]:
        """Determine what action to take.
        
        Returns:
            (action, reason, min_checks)
        """
        min_checks = {}
        
        # Don't trade in CLOSED phase
        if phase == MarketPhase.CLOSED:
            return None, "market_closed", min_checks
        
        # Calculate deltas
        delta_up = target.delta_up
        delta_down = target.delta_down
        
        # Find the larger delta to prioritize
        if abs(delta_up) >= abs(delta_down):
            primary_side = Side.UP
            primary_delta = delta_up
        else:
            primary_side = Side.DOWN
            primary_delta = delta_down
        
        # Check if delta is worth trading
        if primary_delta == 0:
            return None, "no_delta", min_checks
        
        # Determine direction (buy or sell)
        if primary_delta > 0:
            # Need to buy
            order_dollars = primary_delta
            min_check = self.cap_enforcer.check_minimum(
                order_dollars,
                is_market_order=True,
            )
            min_checks[f"{primary_side}_buy"] = min_check
            
            if not min_check["meets_minimum"]:
                return None, f"below_minimum_{primary_side}_buy", min_checks
            
            action = OrderRequest(
                market_id=target.market_id,
                side=primary_side,
                order_type=OrderType.MARKET,
                dollars=order_dollars,
            )
            return action, f"buy_{primary_side}_to_target", min_checks
        
        else:
            # Need to sell
            order_dollars = abs(primary_delta)
            min_check = self.cap_enforcer.check_minimum(
                order_dollars,
                is_market_order=True,
            )
            min_checks[f"{primary_side}_sell"] = min_check
            
            if not min_check["meets_minimum"]:
                return None, f"below_minimum_{primary_side}_sell", min_checks
            
            # For sells, we might need shares not dollars
            # TODO: Confirm sell order format with API
            action = OrderRequest(
                market_id=target.market_id,
                side=primary_side,
                order_type=OrderType.MARKET,
                dollars=order_dollars,  # TODO: May need to be shares
            )
            return action, f"sell_{primary_side}_to_target", min_checks
    
    def compute_error_metric(self, target: DecisionTarget) -> Decimal:
        """Compute error metric (distance from target).
        
        This is useful for RESYNC threshold checking.
        """
        return abs(target.delta_up) + abs(target.delta_down)
    
    def compute_decisions_for_all_markets(
        self,
        leader_snapshot: LeaderSnapshot,
        my_snapshot: MySnapshot,
        market_phases: Dict[MarketId, MarketPhase],
        timestamp: datetime,
    ) -> List[Decision]:
        """Compute decisions for all active markets.
        
        Args:
            leader_snapshot: Current leader state
            my_snapshot: Current our state
            market_phases: Phase for each market
            timestamp: Decision timestamp
            
        Returns:
            List of decisions (one per market with activity)
        """
        decisions = []
        
        # Get all markets leader has exposure in
        active_markets = set(leader_snapshot.exposures.keys())
        # Also include markets we have exposure in
        active_markets.update(my_snapshot.exposures.keys())
        
        for market_id in active_markets:
            phase = market_phases.get(market_id, MarketPhase.UNKNOWN)
            decision = self.compute_decision(
                market_id=market_id,
                phase=phase,
                leader_snapshot=leader_snapshot,
                my_snapshot=my_snapshot,
                timestamp=timestamp,
            )
            decisions.append(decision)
        
        return decisions
