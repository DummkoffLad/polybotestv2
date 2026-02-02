"""
Passive Limit Order Simulator

Simulates limit order execution with realistic fill assumptions:
- Buy limit fills only when ask drops to or below the limit price within TTL
- Sell limit fills only when bid rises to or above the limit price within TTL
- TTL (time-to-live) is typically 6-10 seconds for fast-moving markets
- Orders that don't fill within TTL are cancelled

This is more conservative than assuming instant fills at current prices.

Key differences from market order assumptions:
1. Market orders: fill immediately at ask (buy) or bid (sell)
2. Limit orders: only fill if price moves favorably within TTL window

For Polymarket hourly markets that move quickly, this simulates the reality
that limit orders often don't get filled before the market moves away.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    PENDING = "PENDING"       # Waiting for price to touch
    FILLED = "FILLED"         # Successfully executed
    EXPIRED = "EXPIRED"       # TTL elapsed without fill
    CANCELLED = "CANCELLED"   # Manually cancelled


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class LimitOrder:
    """A pending limit order."""
    order_id: str
    token_id: str
    market_id: str
    side: OrderSide
    limit_price: Decimal
    shares: Decimal
    created_at: datetime
    ttl_seconds: float
    status: OrderStatus = OrderStatus.PENDING
    filled_at: Optional[datetime] = None
    fill_price: Optional[Decimal] = None
    
    # For tracking
    leader_sequence: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=self.ttl_seconds)
    
    def is_expired(self, current_time: datetime) -> bool:
        return current_time >= self.expires_at
    
    def would_fill_at(self, bid: Optional[Decimal], ask: Optional[Decimal]) -> bool:
        """Check if this order would fill at the given prices.
        
        Buy limit fills when ask <= limit_price (we can buy at or below our limit)
        Sell limit fills when bid >= limit_price (we can sell at or above our limit)
        """
        if self.side == OrderSide.BUY:
            if ask is None:
                return False
            return ask <= self.limit_price
        else:  # SELL
            if bid is None:
                return False
            return bid >= self.limit_price
    
    def fill(self, fill_time: datetime, fill_price: Decimal) -> None:
        """Mark order as filled."""
        self.status = OrderStatus.FILLED
        self.filled_at = fill_time
        self.fill_price = fill_price
    
    def expire(self) -> None:
        """Mark order as expired."""
        self.status = OrderStatus.EXPIRED


@dataclass
class LimitOrderResult:
    """Result of limit order simulation."""
    orders_placed: int = 0
    orders_filled: int = 0
    orders_expired: int = 0
    
    # Breakdown by side
    buy_orders: int = 0
    buy_filled: int = 0
    sell_orders: int = 0
    sell_filled: int = 0
    
    # Value stats
    total_buy_value: Decimal = Decimal("0")
    total_sell_value: Decimal = Decimal("0")
    
    # Fill rate
    fill_rate_pct: float = 0.0
    buy_fill_rate_pct: float = 0.0
    sell_fill_rate_pct: float = 0.0
    
    # Average wait time for fills
    avg_wait_time_ms: Optional[float] = None
    
    # Filled orders for detailed analysis
    filled_orders: List[LimitOrder] = field(default_factory=list)
    expired_orders: List[LimitOrder] = field(default_factory=list)


class LimitOrderSimulator:
    """
    Simulates passive limit order execution during replay.
    
    Usage:
        sim = LimitOrderSimulator(default_ttl=8.0)
        
        # Place orders as you process events
        order = sim.place_order(
            token_id="abc", market_id="xyz", side=OrderSide.BUY,
            limit_price=Decimal("0.50"), shares=Decimal("10"),
            timestamp=event_time, leader_seq=123
        )
        
        # Check for fills as prices update
        fills = sim.check_fills(timestamp, {token: price_snapshot})
        
        # Get final results
        result = sim.get_results()
    """
    
    def __init__(self, default_ttl: float = 8.0, 
                 price_buffer_pct: Decimal = Decimal("0.5")):
        """
        Args:
            default_ttl: Default time-to-live in seconds
            price_buffer_pct: Buffer to add to limit prices for more conservative fills.
                              0.5% means buy at ask - 0.5%, sell at bid + 0.5%
        """
        self.default_ttl = default_ttl
        self.price_buffer_pct = price_buffer_pct
        
        # Active orders by token
        self.pending_orders: Dict[str, List[LimitOrder]] = {}
        self._order_counter = 0
        
        # Completed orders
        self.filled_orders: List[LimitOrder] = []
        self.expired_orders: List[LimitOrder] = []
        
        # Price history for checking touchs
        self._price_history: Dict[str, List[Tuple[datetime, Decimal, Decimal]]] = {}
    
    def place_order(self, token_id: str, market_id: str, side: OrderSide,
                   limit_price: Decimal, shares: Decimal, timestamp: datetime,
                   leader_seq: Optional[int] = None,
                   ttl_seconds: Optional[float] = None,
                   metadata: Dict[str, Any] = None) -> LimitOrder:
        """
        Place a new limit order.
        
        Args:
            token_id: Token to trade
            market_id: Market ID
            side: BUY or SELL
            limit_price: The price limit
            shares: Number of shares
            timestamp: When the order is placed
            leader_seq: Optional sequence number from leader trade
            ttl_seconds: Custom TTL (or use default)
            metadata: Additional info to store
            
        Returns:
            The created LimitOrder
        """
        self._order_counter += 1
        order = LimitOrder(
            order_id=f"LO_{self._order_counter}",
            token_id=token_id,
            market_id=market_id,
            side=side,
            limit_price=limit_price,
            shares=shares,
            created_at=timestamp,
            ttl_seconds=ttl_seconds or self.default_ttl,
            leader_sequence=leader_seq,
            metadata=metadata or {}
        )
        
        if token_id not in self.pending_orders:
            self.pending_orders[token_id] = []
        self.pending_orders[token_id].append(order)
        
        logger.debug(f"Placed {side.value} limit order at {limit_price} for {shares} shares of {token_id[:16]}...")
        return order
    
    def record_price(self, token_id: str, timestamp: datetime, 
                    bid: Decimal, ask: Decimal) -> None:
        """Record a price snapshot for fill checking."""
        if token_id not in self._price_history:
            self._price_history[token_id] = []
        self._price_history[token_id].append((timestamp, bid, ask))
    
    def check_fills(self, current_time: datetime, 
                   current_prices: Dict[str, Tuple[Decimal, Decimal]]) -> List[LimitOrder]:
        """
        Check if any pending orders should fill at current prices.
        
        Args:
            current_time: Current timestamp
            current_prices: Dict mapping token_id -> (bid, ask)
            
        Returns:
            List of orders that filled
        """
        fills = []
        
        for token_id, orders in list(self.pending_orders.items()):
            prices = current_prices.get(token_id)
            if not prices:
                continue
            
            bid, ask = prices
            
            remaining = []
            for order in orders:
                if order.status != OrderStatus.PENDING:
                    continue
                
                # Check expiration first
                if order.is_expired(current_time):
                    order.expire()
                    self.expired_orders.append(order)
                    logger.debug(f"Order {order.order_id} expired (TTL)")
                    continue
                
                # Check if price touches our limit
                if order.would_fill_at(bid, ask):
                    fill_price = ask if order.side == OrderSide.BUY else bid
                    order.fill(current_time, fill_price)
                    self.filled_orders.append(order)
                    fills.append(order)
                    logger.debug(f"Order {order.order_id} filled at {fill_price}")
                else:
                    remaining.append(order)
            
            self.pending_orders[token_id] = remaining
        
        return fills
    
    def expire_all_pending(self) -> None:
        """Expire all remaining pending orders (end of simulation)."""
        for token_id, orders in self.pending_orders.items():
            for order in orders:
                if order.status == OrderStatus.PENDING:
                    order.expire()
                    self.expired_orders.append(order)
        self.pending_orders.clear()
    
    def get_results(self) -> LimitOrderResult:
        """Calculate and return simulation results."""
        # Expire any remaining pending orders
        self.expire_all_pending()
        
        result = LimitOrderResult()
        
        # Aggregate stats
        buy_orders = [o for o in self.filled_orders + self.expired_orders 
                     if o.side == OrderSide.BUY]
        sell_orders = [o for o in self.filled_orders + self.expired_orders 
                      if o.side == OrderSide.SELL]
        
        result.orders_placed = len(self.filled_orders) + len(self.expired_orders)
        result.orders_filled = len(self.filled_orders)
        result.orders_expired = len(self.expired_orders)
        
        result.buy_orders = len(buy_orders)
        result.buy_filled = sum(1 for o in buy_orders if o.status == OrderStatus.FILLED)
        result.sell_orders = len(sell_orders)
        result.sell_filled = sum(1 for o in sell_orders if o.status == OrderStatus.FILLED)
        
        # Value calculations
        for order in self.filled_orders:
            value = order.shares * (order.fill_price or Decimal("0"))
            if order.side == OrderSide.BUY:
                result.total_buy_value += value
            else:
                result.total_sell_value += value
        
        # Fill rates
        if result.orders_placed > 0:
            result.fill_rate_pct = (result.orders_filled / result.orders_placed) * 100
        if result.buy_orders > 0:
            result.buy_fill_rate_pct = (result.buy_filled / result.buy_orders) * 100
        if result.sell_orders > 0:
            result.sell_fill_rate_pct = (result.sell_filled / result.sell_orders) * 100
        
        # Average wait time
        wait_times = []
        for order in self.filled_orders:
            if order.filled_at and order.created_at:
                wait_ms = (order.filled_at - order.created_at).total_seconds() * 1000
                wait_times.append(wait_ms)
        if wait_times:
            result.avg_wait_time_ms = sum(wait_times) / len(wait_times)
        
        result.filled_orders = self.filled_orders.copy()
        result.expired_orders = self.expired_orders.copy()
        
        return result
    
    def print_summary(self) -> None:
        """Print a summary of the simulation."""
        result = self.get_results()
        
        print()
        print("=" * 70)
        print("  LIMIT ORDER SIMULATION RESULTS")
        print("=" * 70)
        print()
        print(f"  TTL: {self.default_ttl}s")
        print()
        print(f"  Orders placed:  {result.orders_placed}")
        print(f"  Orders filled:  {result.orders_filled} ({result.fill_rate_pct:.1f}%)")
        print(f"  Orders expired: {result.orders_expired}")
        print()
        print(f"  Buy orders:  {result.buy_orders} placed, {result.buy_filled} filled ({result.buy_fill_rate_pct:.1f}%)")
        print(f"  Sell orders: {result.sell_orders} placed, {result.sell_filled} filled ({result.sell_fill_rate_pct:.1f}%)")
        print()
        print(f"  Total buy value:  ${result.total_buy_value:.2f}")
        print(f"  Total sell value: ${result.total_sell_value:.2f}")
        if result.avg_wait_time_ms:
            print(f"  Avg wait time:    {result.avg_wait_time_ms:.0f}ms")
        print("=" * 70)


def simulate_with_limit_orders(session_path: str, ttl_seconds: float = 8.0) -> LimitOrderResult:
    """
    Run a session replay using limit order simulation instead of market orders.
    
    This is a standalone function that can be used to compare limit vs market
    order performance on the same session.
    """
    # TODO: Integrate with SessionReplayer using limit order execution mode
    pass
