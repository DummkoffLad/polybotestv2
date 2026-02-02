"""Live execution adapter for LIVE mode using py-clob-client SDK."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Any, TYPE_CHECKING
import os
import logging

if TYPE_CHECKING:
    from ..data.live_source import LiveDataSource

from ..core.types import ExecutionMode, OrderRequest, OrderResponse, OrderStatus, OrderType, Side
from .base import ExecutionAdapter

logger = logging.getLogger(__name__)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType as ClobOrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    HAS_CLOB = True
except ImportError:
    HAS_CLOB = False
    ClobClient = None


class LiveExecutionAdapter(ExecutionAdapter):
    """Live execution using py-clob-client SDK."""
    
    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137
    
    def __init__(self, private_key: str = None, funder_address: str = None, signature_type: int = 2):
        self._private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        self._funder_address = funder_address or os.getenv("POLYMARKET_FUNDER_ADDRESS")
        self._signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", str(signature_type)))
        self._armed = False
        self._preflight_passed = False
        self._client: Optional[Any] = None
        self._data_source: Optional["LiveDataSource"] = None
    
    def set_data_source(self, data_source: "LiveDataSource") -> None:
        self._data_source = data_source
    
    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.LIVE
    
    @property
    def can_place_orders(self) -> bool:
        return self._armed and self._preflight_passed and self._client is not None
    
    @property
    def is_armed(self) -> bool:
        return self._armed
    
    def set_preflight_passed(self, passed: bool) -> None:
        self._preflight_passed = passed
    
    def arm(self) -> bool:
        if not self._preflight_passed or not self._private_key or not self._funder_address:
            return False
        if not self._init_client():
            return False
        self._armed = True
        print(f"LIVE EXECUTION ARMED - Funder: {self._funder_address}")
        return True
    
    def disarm(self) -> None:
        self._armed = False
        print("LIVE EXECUTION DISARMED")
    
    def _init_client(self) -> bool:
        if not HAS_CLOB:
            logger.error("py-clob-client not installed")
            return False
        try:
            self._client = ClobClient(
                host=self.HOST, key=self._private_key, chain_id=self.CHAIN_ID,
                signature_type=self._signature_type, funder=self._funder_address,
            )
            if not self._client.get_ok():
                return False
            api_creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(api_creds)
            logger.info("CLOB client initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Client init failed: {e}")
            return False
    
    def place_order(self, request: OrderRequest) -> OrderResponse:
        # SAFETY: Must be armed
        if not self.can_place_orders:
            logger.warning("Order rejected: adapter not armed")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error="Not armed")
        
        # SAFETY: Validate request has required fields
        if not request.market_id or not request.token_id:
            logger.error(f"Order rejected: missing market_id or token_id")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error="Missing market/token ID")
        
        # SAFETY: Validate amounts are positive
        if request.action == "BUY" and (not request.amount_dollars or request.amount_dollars <= 0):
            logger.error(f"BUY order rejected: invalid amount {request.amount_dollars}")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error="Invalid buy amount")
        if request.action == "SELL" and (not request.shares or request.shares <= 0):
            logger.error(f"SELL order rejected: invalid shares {request.shares}")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error="Invalid sell shares")
        
        token_id = self._get_token_id(request.market_id, request.side)
        if not token_id:
            logger.error(f"Order rejected: no token_id for market {request.market_id} side {request.side}")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error="No token ID")
        
        try:
            mid = float(self._client.get_midpoint(token_id).get("mid", 0.5))
            
            # SAFETY: Validate mid price is reasonable
            if not (0.01 <= mid <= 0.99):
                logger.warning(f"Suspicious midpoint: {mid} for {token_id}")
            
            if request.action == "BUY":
                price = min(0.99, mid + 0.05)
                size = float(request.amount_dollars)
                side = BUY
            else:
                price = max(0.01, mid - 0.05)
                size = float(request.shares)
                side = SELL
            
            # SAFETY: Log order details before submission
            logger.info(f"SUBMITTING ORDER: {request.action} {size:.2f} @{price:.4f} token={token_id[:16]}...")
            
            order_args = OrderArgs(price=round(price / 0.01) * 0.01, size=size, side=side, token_id=token_id)
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, ClobOrderType.FOK)
            
            success = resp.get("success", False)
            order_id = resp.get("orderID", resp.get("orderId", ""))
            status = OrderStatus.FILLED if success else OrderStatus.REJECTED
            error_msg = resp.get("errorMsg")
            
            # SAFETY: Log outcome
            if success:
                logger.info(f"ORDER FILLED: id={order_id}")
            else:
                logger.warning(f"ORDER REJECTED: {error_msg}")
            
            return OrderResponse(order_id=order_id, status=status, error=error_msg)
        except Exception as e:
            logger.error(f"ORDER EXCEPTION: {e}")
            return OrderResponse(order_id="", status=OrderStatus.REJECTED, error=str(e))
    
    def _get_token_id(self, market_id: str, side: Side) -> Optional[str]:
        if self._data_source:
            return self._data_source.get_token_id(market_id, side)
        return None