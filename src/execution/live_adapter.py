"""Live execution adapter for LIVE mode.

Uses py-clob-client SDK for order placement.
This is the ONLY module that should contain real API calls.

COPY-TRADING DESIGN:
- Token IDs come from the data source (discovered from leader activity)
- We do NOT hardcode or pre-configure markets
- All market info is passed via set_data_source() or per-order

Authentication flow:
1. Initialize with private key + funder address
2. Derive API credentials (L2 auth)
3. Place orders using SDK

Order types:
- FOK (Fill-or-Kill): Market order - buy in $, sell in shares
- GTC (Good-til-Cancelled): Limit order - rests on book

Minimum order sizes (from Polymarket):
- Market orders: $1 minimum
- Limit orders: 5 shares minimum
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Any, TYPE_CHECKING
import os
import logging

if TYPE_CHECKING:
    from ..data.live_source import LiveDataSource

from ..core.types import (
    ExecutionMode,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Side,
)
from .base import ExecutionAdapter

logger = logging.getLogger(__name__)

# Minimum order sizes (confirmed by user)
MARKET_ORDER_MIN_DOLLARS = Decimal("1.0")
LIMIT_ORDER_MIN_SHARES = Decimal("5.0")

# Try to import py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType as ClobOrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    HAS_CLOB_CLIENT = True
except ImportError:
    HAS_CLOB_CLIENT = False
    ClobClient = None


class LiveExecutionAdapter(ExecutionAdapter):
    """Live execution adapter using py-clob-client SDK.
    
    This adapter:
    - Places real orders on Polymarket
    - Requires explicit ARM command before placing orders
    - Requires preflight checks to pass
    - Gets token IDs from data source (discovered from leader activity)
    
    Configuration via environment variables:
    - POLYMARKET_PRIVATE_KEY: Wallet private key (required)
    - POLYMARKET_FUNDER_ADDRESS: Proxy wallet address (required)
    - POLYMARKET_SIGNATURE_TYPE: 0=EOA, 1=Magic, 2=Gnosis (default: 2)
    """
    
    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137  # Polygon mainnet
    
    def __init__(
        self,
        private_key: Optional[str] = None,
        funder_address: Optional[str] = None,
        signature_type: int = 2,  # Default to Gnosis Safe
    ):
        """Initialize live adapter.
        
        Args:
            private_key: Wallet private key (or from env)
            funder_address: Proxy wallet address that holds funds
            signature_type: 0=EOA, 1=POLY_PROXY (Magic), 2=GNOSIS_SAFE
        """
        self._private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        self._funder_address = funder_address or os.getenv("POLYMARKET_FUNDER_ADDRESS")
        self._signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", str(signature_type)))
        
        # State
        self._armed = False
        self._preflight_passed = False
        self._client: Optional[Any] = None
        self._api_creds_set = False
        
        # Data source for token ID lookup (set via set_data_source)
        self._data_source: Optional["LiveDataSource"] = None
        
        # Order tracking
        self._pending_orders: Dict[str, OrderStatus] = {}
    
    def set_data_source(self, data_source: "LiveDataSource") -> None:
        """Set the data source for token ID lookup.
        
        Token IDs are discovered from leader activity, not hardcoded.
        The data source tracks all markets seen in leader positions/trades.
        """
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
        """Set whether preflight checks passed."""
        self._preflight_passed = passed
    
    def _init_client(self) -> bool:
        """Initialize the CLOB client.
        
        Returns:
            True if client initialized successfully
        """
        if not HAS_CLOB_CLIENT:
            logger.error("py-clob-client not installed. Run: pip install py-clob-client")
            return False
        
        if not self._private_key:
            logger.error("Private key not configured")
            return False
        
        if not self._funder_address:
            logger.error("Funder address not configured")
            return False
        
        try:
            # Initialize client with credentials
            self._client = ClobClient(
                host=self.HOST,
                key=self._private_key,
                chain_id=self.CHAIN_ID,
                signature_type=self._signature_type,
                funder=self._funder_address,
            )
            
            # Test connectivity
            ok = self._client.get_ok()
            if not ok:
                logger.error("Failed to connect to CLOB API")
                return False
            
            # Create or derive API credentials
            api_creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(api_creds)
            self._api_creds_set = True
            
            logger.info("CLOB client initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            self._client = None
            return False
    
    def arm(self) -> bool:
        """Arm the adapter for live trading.
        
        Returns:
            True if arming succeeded
        """
        if not self._preflight_passed:
            print("ERROR: Cannot ARM - preflight checks have not passed")
            return False
        
        if not self._private_key:
            print("ERROR: Cannot ARM - POLYMARKET_PRIVATE_KEY not set")
            return False
        
        if not self._funder_address:
            print("ERROR: Cannot ARM - POLYMARKET_FUNDER_ADDRESS not set")
            return False
        
        # Initialize client
        if not self._init_client():
            print("ERROR: Cannot ARM - failed to initialize CLOB client")
            return False
        
        self._armed = True
        print("=" * 60)
        print("  LIVE EXECUTION ARMED")
        print("  Real orders will be placed!")
        print(f"  Funder: {self._funder_address}")
        print(f"  Signature Type: {self._signature_type}")
        print("=" * 60)
        return True
    
    def disarm(self) -> None:
        """Immediately disarm the adapter (KILL switch)."""
        self._armed = False
        print("=" * 60)
        print("  LIVE EXECUTION DISARMED")
        print("  No orders will be placed.")
        print("=" * 60)
    
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place a real order on Polymarket.
        
        For market orders:
        - Uses FOK (Fill-or-Kill) order type
        - Buy orders specify dollar amount
        - Sell orders specify share amount
        
        For limit orders:
        - Uses GTC (Good-til-Cancelled) order type
        - Specifies price and size in shares
        
        CRITICAL: This places REAL orders with REAL money!
        """
        # SAFETY: Validate request before sending
        if request.dollars is not None and request.dollars <= 0:
            return OrderResponse(
                success=False,
                order_id=None,
                status=OrderStatus.REJECTED,
                error_message=f"Invalid order: dollars must be positive, got {request.dollars}",
                correlation_id=request.correlation_id,
            )
        
        if request.shares is not None and request.shares <= 0:
            return OrderResponse(
                success=False,
                order_id=None,
                status=OrderStatus.REJECTED,
                error_message=f"Invalid order: shares must be positive, got {request.shares}",
                correlation_id=request.correlation_id,
            )
        
        if not self.can_place_orders:
            logger.error("Attempted to place order while not armed!")
            return OrderResponse(
                success=False,
                order_id=None,
                status=OrderStatus.REJECTED,
                error_message="Live adapter not armed or not initialized - CANNOT place orders",
                correlation_id=request.correlation_id,
            )
        
        try:
            # Get token ID for the market/side
            token_id = self._get_token_id(request.market_id, request.side)
            if not token_id:
                return OrderResponse(
                    success=False,
                    order_id=None,
                    status=OrderStatus.REJECTED,
                    error_message=f"Could not resolve token ID for {request.market_id}/{request.side}",
                    correlation_id=request.correlation_id,
                )
            
            # Get tick size for the market
            tick_size = self._get_tick_size(request.market_id)
            neg_risk = self._is_neg_risk(request.market_id)
            
            # Determine order side
            side = BUY if request.dollars and request.dollars > 0 else SELL
            
            if request.order_type == OrderType.MARKET:
                # Market order - use FOK
                return self._place_market_order(
                    request=request,
                    token_id=token_id,
                    side=side,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                )
            else:
                # Limit order - use GTC
                return self._place_limit_order(
                    request=request,
                    token_id=token_id,
                    side=side,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                )
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return OrderResponse(
                success=False,
                order_id=None,
                status=OrderStatus.REJECTED,
                error_message=str(e),
                correlation_id=request.correlation_id,
            )
    
    def _place_market_order(
        self,
        request: OrderRequest,
        token_id: str,
        side: str,
        tick_size: str,
        neg_risk: bool,
    ) -> OrderResponse:
        """Place a market order using FOK.
        
        For buys: uses dollar amount
        For sells: uses share amount
        """
        # Get current price to make order marketable
        try:
            mid = self._client.get_midpoint(token_id)
            mid_price = float(mid.get("mid", 0.5))
        except:
            mid_price = 0.5
        
        # Set price to be marketable (buy high, sell low)
        if side == BUY:
            # Buy at higher price to ensure fill
            price = min(0.99, mid_price + 0.05)
            # Size is in shares, but for FOK buy we want dollar amount
            # SDK handles this via amount parameter
            size = float(request.dollars) if request.dollars else 1.0
        else:
            # Sell at lower price to ensure fill
            price = max(0.01, mid_price - 0.05)
            size = float(request.shares) if request.shares else 1.0
        
        # Round price to tick size
        tick = float(tick_size)
        price = round(price / tick) * tick
        
        # Create order args
        order_args = OrderArgs(
            price=price,
            size=size,
            side=side,
            token_id=token_id,
        )
        
        # Create and sign order
        signed_order = self._client.create_order(order_args)
        
        # Post as FOK (Fill-or-Kill)
        response = self._client.post_order(signed_order, ClobOrderType.FOK)
        
        return self._parse_order_response(response, request.correlation_id)
    
    def _place_limit_order(
        self,
        request: OrderRequest,
        token_id: str,
        side: str,
        tick_size: str,
        neg_risk: bool,
    ) -> OrderResponse:
        """Place a limit order using GTC."""
        if request.limit_price is None:
            return OrderResponse(
                success=False,
                order_id=None,
                status=OrderStatus.REJECTED,
                error_message="Limit order requires limit_price",
                correlation_id=request.correlation_id,
            )
        
        price = float(request.limit_price)
        size = float(request.shares) if request.shares else 5.0  # Min 5 shares
        
        # Round price to tick size
        tick = float(tick_size)
        price = round(price / tick) * tick
        
        # Create order args
        order_args = OrderArgs(
            price=price,
            size=size,
            side=side,
            token_id=token_id,
        )
        
        # Create and sign order
        signed_order = self._client.create_order(order_args)
        
        # Post as GTC (Good-til-Cancelled)
        response = self._client.post_order(signed_order, ClobOrderType.GTC)
        
        return self._parse_order_response(response, request.correlation_id)
    
    def _parse_order_response(
        self,
        response: Dict[str, Any],
        correlation_id: str,
    ) -> OrderResponse:
        """Parse SDK order response into OrderResponse.
        
        Also extracts helpful info from error messages (like actual minimums).
        """
        success = response.get("success", False)
        order_id = response.get("orderID", response.get("orderId"))
        error_msg = response.get("errorMsg", "")
        
        # Parse error message for useful info
        if error_msg:
            error_msg = self._enhance_error_message(error_msg)
        
        # Determine status from response
        if success and order_id:
            # Check if it was matched or placed
            status_str = response.get("status", "live")
            if status_str == "matched":
                status = OrderStatus.FILLED
            elif status_str in ("live", "delayed"):
                status = OrderStatus.PENDING
                self._pending_orders[order_id] = status
            else:
                status = OrderStatus.PENDING
        else:
            status = OrderStatus.REJECTED
        
        return OrderResponse(
            success=success,
            order_id=order_id,
            status=status,
            filled_shares=Decimal("0"),  # Would need to query
            filled_dollars=Decimal("0"),
            error_message=error_msg if not success else None,
            correlation_id=correlation_id,
        )
    
    def _enhance_error_message(self, error_msg: str) -> str:
        """Enhance error message with helpful context.
        
        Polymarket errors like INVALID_ORDER_MIN_SIZE should tell you
        what the actual minimum is.
        """
        error_upper = error_msg.upper()
        
        if "MIN_SIZE" in error_upper or "MINIMUM" in error_upper:
            return (
                f"{error_msg} | "
                f"Minimums: Market orders=${MARKET_ORDER_MIN_DOLLARS}, "
                f"Limit orders={LIMIT_ORDER_MIN_SHARES} shares"
            )
        
        if "MIN_TICK" in error_upper:
            return f"{error_msg} | Price must be multiple of 0.01 (1 cent)"
        
        if "BALANCE" in error_upper or "ALLOWANCE" in error_upper:
            return f"{error_msg} | Check USDC balance in funder wallet"
        
        return error_msg
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        if not self.can_place_orders:
            return False
        
        try:
            response = self._client.cancel(order_id=order_id)
            canceled = response.get("canceled", [])
            
            if order_id in canceled:
                self._pending_orders.pop(order_id, None)
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error canceling order {order_id}: {e}")
            return False
    
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get status of an order."""
        if not self._client or not self._api_creds_set:
            return self._pending_orders.get(order_id, OrderStatus.UNKNOWN)
        
        try:
            order = self._client.get_order(order_id)
            if not order:
                return OrderStatus.UNKNOWN
            
            status_str = order.get("status", "").lower()
            
            if status_str == "matched":
                return OrderStatus.FILLED
            elif status_str in ("live", "open"):
                return OrderStatus.PENDING
            elif status_str == "cancelled":
                return OrderStatus.CANCELLED
            else:
                return OrderStatus.UNKNOWN
                
        except Exception as e:
            logger.error(f"Error getting order status for {order_id}: {e}")
            return self._pending_orders.get(order_id, OrderStatus.UNKNOWN)
    
    def get_pending_orders(self) -> List[str]:
        """Get list of pending order IDs."""
        return [
            oid for oid, status in self._pending_orders.items()
            if status == OrderStatus.PENDING
        ]
    
    def _get_token_id(self, market_id: str, side: Side) -> Optional[str]:
        """Get token ID for a market/side from discovered markets.
        
        Token IDs come from leader activity via the data source.
        We do NOT hardcode markets.
        """
        if self._data_source:
            return self._data_source.get_token_id(market_id, side)
        
        logger.warning(f"No data source set - cannot resolve token ID for {market_id}/{side}")
        return None
    
    def _get_tick_size(self, market_id: str) -> str:
        """Get tick size for a market.
        
        Default to 0.01 (1 cent) - most markets use this.
        """
        # TODO: Could fetch from GAMMA API if needed
        return "0.01"
    
    def _is_neg_risk(self, market_id: str) -> bool:
        """Check if market uses negative risk.
        
        Could check discovered market info if available.
        """
        # For hourly markets, this is typically False
        return False
    
    def validate_credentials(self) -> bool:
        """Validate that credentials are configured."""
        return bool(self._private_key and self._funder_address)
    
    def test_connectivity(self) -> bool:
        """Test API connectivity."""
        if not HAS_CLOB_CLIENT:
            return False
        
        try:
            # Quick test without full init
            from py_clob_client.client import ClobClient
            client = ClobClient(host=self.HOST, chain_id=self.CHAIN_ID)
            result = client.get_ok()
            return bool(result)
        except:
            return False
