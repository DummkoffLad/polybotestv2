"""Universal portfolio tracking."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from .types import Side

logger = logging.getLogger(__name__)

class PortfolioInvariantError(Exception):
    """Raised when a critical portfolio invariant is violated."""
    pass

@dataclass
class PortfolioPosition:
    token_id: str
    market_id: str
    side: Side
    shares: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    last_update: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {"token_id": self.token_id, "market_id": self.market_id, 
                "side": self.side.value if hasattr(self.side, 'value') else str(self.side),
                "shares": str(self.shares), "avg_price": str(self.avg_price), "cost_basis": str(self.cost_basis)}

class Portfolio:
    def __init__(self):
        self._positions: Dict[str, PortfolioPosition] = {}
        self.realized_pnl: Decimal = Decimal("0")
        self.total_bought: Decimal = Decimal("0")
        self.total_sold: Decimal = Decimal("0")

    @staticmethod
    def _position_key(token_id: str, market_id: str, side: Side) -> str:
        """Create composite key from token_id, market_id, and side."""
        side_str = side.value if hasattr(side, 'value') else str(side)
        return f"{token_id}|{market_id}|{side_str}"

    def get(self, token_id: str, market_id: str = "", side: Side = Side.UP) -> PortfolioPosition:
        key = self._position_key(token_id, market_id, side)
        if key not in self._positions:
            self._positions[key] = PortfolioPosition(token_id, market_id, side)
        return self._positions[key]

    def has_position(self, token_id: str, market_id: str, side: Side) -> bool:
        key = self._position_key(token_id, market_id, side)
        pos = self._positions.get(key)
        return pos is not None and pos.shares > 0
    
    def apply_buy(self, token_id: str, market_id: str, side: Side, shares: Decimal, 
                  price: Decimal, timestamp: Optional[datetime] = None) -> PortfolioPosition:
        # SAFETY: Validate inputs
        if shares <= 0:
            raise PortfolioInvariantError(f"BUY shares must be positive: {shares}")
        if price <= 0 or price >= 1:
            raise PortfolioInvariantError(f"BUY price must be in (0,1): {price}")
        
        pos = self.get(token_id, market_id, side)
        cost = shares * price
        total_cost = pos.cost_basis + cost
        total_shares = pos.shares + shares
        if total_shares > 0:
            pos.avg_price = (total_cost / total_shares).quantize(Decimal("0.0001"))
        pos.shares = total_shares
        pos.cost_basis = total_cost
        pos.last_update = timestamp
        self.total_bought += cost

        logger.debug(f"BUY applied: {token_id} +{shares} @{price} -> total {pos.shares}")
        return pos
    
    def apply_sell(self, token_id: str, market_id: str, side: Side, shares: Decimal,
                   price: Decimal, timestamp: Optional[datetime] = None) -> PortfolioPosition:
        # SAFETY: Validate inputs
        if shares <= 0:
            raise PortfolioInvariantError(f"SELL shares must be positive: {shares}")
        if price <= 0 or price >= 1:
            raise PortfolioInvariantError(f"SELL price must be in (0,1): {price}")
        
        pos = self.get(token_id, market_id, side)

        # SAFETY: Never sell more than we own
        if shares > pos.shares:
            logger.warning(f"SELL clamped: requested {shares}, have {pos.shares}")
        sell_shares = min(shares, pos.shares)

        # Calculate realized PnL before modifying cost_basis
        proceeds = sell_shares * price
        if pos.shares > 0 and sell_shares > 0:
            cost_of_sold = pos.cost_basis * (sell_shares / pos.shares)
            self.realized_pnl += proceeds - cost_of_sold
            pos.cost_basis -= cost_of_sold
        pos.shares -= sell_shares
        self.total_sold += proceeds

        # Clean up dust
        if pos.shares < Decimal("0.001"):
            pos.shares = pos.cost_basis = pos.avg_price = Decimal("0")

        # SAFETY: Shares must never go negative
        if pos.shares < 0:
            raise PortfolioInvariantError(f"Shares went negative: {pos.shares}")

        pos.last_update = timestamp
        logger.debug(f"SELL applied: {token_id} -{sell_shares} @{price} -> remaining {pos.shares}")
        return pos
    
    def get_total_deployed(self) -> Decimal:
        return sum(p.cost_basis for p in self._positions.values() if p.shares > 0)
    
    def get_market_exposure(self, market_id: str) -> Decimal:
        return sum(p.cost_basis for p in self._positions.values() if p.market_id == market_id and p.shares > 0)
    
    def get_side_exposure(self, market_id: str, side: Side) -> Decimal:
        return sum(p.cost_basis for p in self._positions.values() if p.market_id == market_id and p.side == side and p.shares > 0)
    
    def get_positions(self) -> Dict[str, PortfolioPosition]:
        return {k: v for k, v in self._positions.items() if v.shares > 0}

    def calculate_pnl(self, current_prices: Dict[str, Decimal]) -> tuple[Decimal, Decimal]:
        unrealized = sum(pos.shares * current_prices.get(pos.token_id, pos.avg_price) - pos.cost_basis
                        for pos in self._positions.values() if pos.shares > 0)
        return self.realized_pnl, unrealized

    def to_dict(self) -> Dict[str, Any]:
        return {key: pos.to_dict() for key, pos in self._positions.items() if pos.shares > 0}
    
    def clear(self) -> None:
        self._positions.clear()
        self.realized_pnl = Decimal("0")
        self.total_bought = Decimal("0")
        self.total_sold = Decimal("0")
