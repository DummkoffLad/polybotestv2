"""Base execution adapter interface.

All execution adapters must implement this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, List

from ..core.types import (
    ExecutionMode,
    OrderRequest,
    OrderResponse,
    OrderStatus,
)


class ExecutionAdapter(ABC):
    """Abstract base class for execution adapters.
    
    Each adapter handles order placement differently:
    - NullExecutionAdapter: No-op (DRY_RUN)
    - LiveExecutionAdapter: Real API calls (LIVE)
    """
    
    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """Return the execution mode this adapter is for."""
        pass
    
    @property
    @abstractmethod
    def can_place_orders(self) -> bool:
        """Return True if this adapter can actually place orders."""
        pass
    
    @property
    @abstractmethod
    def is_armed(self) -> bool:
        """Return True if the adapter is armed (ready to place orders)."""
        pass
    
    @abstractmethod
    def arm(self) -> bool:
        """Arm the adapter for order placement.
        
        Returns True if arming succeeded.
        Only LiveExecutionAdapter requires explicit arming.
        """
        pass
    
    @abstractmethod
    def disarm(self) -> None:
        """Disarm the adapter (stop placing orders)."""
        pass
    
    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order.
        
        Args:
            request: The order to place
            
        Returns:
            OrderResponse with status and details
        """
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.
        
        Args:
            order_id: The order to cancel
            
        Returns:
            True if cancellation was successful
        """
        pass
    
    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get the current status of an order.
        
        Args:
            order_id: The order to check
            
        Returns:
            Current OrderStatus
        """
        pass
    
    @abstractmethod
    def get_pending_orders(self) -> List[str]:
        """Get list of pending order IDs."""
        pass
    
    def validate_for_mode(self, expected_mode: ExecutionMode) -> None:
        """Validate that this adapter matches the expected mode.
        
        Raises:
            RuntimeError: If mode doesn't match
        """
        if self.mode != expected_mode:
            raise RuntimeError(
                f"Execution adapter mode mismatch: "
                f"expected {expected_mode}, got {self.mode}"
            )
