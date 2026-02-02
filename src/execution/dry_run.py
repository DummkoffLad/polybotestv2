"""Null execution adapter for DRY_RUN mode.

SAFETY: This adapter is PURE - it cannot place any orders.
It has NO network capabilities, NO API imports, NO signing code.
"""

from __future__ import annotations

from typing import List

from ..core.types import (
    ExecutionMode,
    OrderRequest,
    OrderResponse,
    OrderStatus,
)
from .base import ExecutionAdapter


class NullExecutionAdapter(ExecutionAdapter):
    """Null adapter for DRY_RUN mode.
    
    This adapter:
    - Cannot place any orders (always returns success=False with reason)
    - Has no network/API capabilities
    - Is always "armed" (but cannot do anything)
    - Logs what would have been done
    
    SAFETY GUARANTEE: This class has NO imports of network, signing,
    or API code. It is impossible for this adapter to place real orders.
    """
    
    def __init__(self):
        """Initialize the null adapter."""
        self._order_log: List[OrderRequest] = []
    
    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.DRY_RUN
    
    @property
    def can_place_orders(self) -> bool:
        """Null adapter cannot place orders."""
        return False
    
    @property
    def is_armed(self) -> bool:
        """Always armed (but cannot do anything)."""
        return True
    
    def arm(self) -> bool:
        """Arming is a no-op for null adapter."""
        return True
    
    def disarm(self) -> None:
        """Disarming is a no-op for null adapter."""
        pass
    
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Record the order request but do not execute.
        
        Returns a simulated success response so we can see what would happen.
        """
        # Log the request for later review
        self._order_log.append(request)
        
        # Return a simulated success (so portfolio tracks what would happen)
        return OrderResponse(
            order_id=f"DRY-{len(self._order_log)}",
            status=OrderStatus.SIMULATED,
            filled_shares=request.shares,
            filled_price=request.price,
            error=None,
            correlation_id=request.correlation_id,
        )
    
    def cancel_order(self, order_id: str) -> bool:
        """No orders to cancel in DRY_RUN mode."""
        return True
    
    def get_order_status(self, order_id: str) -> OrderStatus:
        """No orders exist in DRY_RUN mode."""
        return OrderStatus.UNKNOWN
    
    def get_pending_orders(self) -> List[str]:
        """No pending orders in DRY_RUN mode."""
        return []
    
    def get_order_log(self) -> List[OrderRequest]:
        """Get the log of orders that would have been placed.
        
        Returns:
            List of OrderRequest objects that were "submitted"
        """
        return list(self._order_log)
    
    def clear_order_log(self) -> None:
        """Clear the order log."""
        self._order_log.clear()
