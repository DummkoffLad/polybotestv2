"""Shared PnL calculation logic for strategies.

Centralizes the common calculate_pnl logic that was duplicated across all strategies.
This prevents bugs when PnL calculation needs to change and ensures consistency.
"""
from decimal import Decimal
from typing import Dict, Tuple

from ..core.portfolio import Portfolio
from ..data.models import PriceSnapshot


def calculate_strategy_pnl(
    portfolio: Portfolio,
    final_prices: Dict[str, PriceSnapshot]
) -> Tuple[Decimal, Decimal]:
    """Calculate realized and unrealized PnL for a portfolio.

    Args:
        portfolio: Portfolio instance with positions and realized PnL
        final_prices: Dict mapping token_id to final PriceSnapshot

    Returns:
        Tuple of (realized_pnl, unrealized_pnl)

    Example:
        >>> portfolio = Portfolio()
        >>> portfolio.apply_buy("token1", "market1", Side.YES, Decimal("10"), Decimal("0.50"))
        >>> final_prices = {"token1": PriceSnapshot(bid=Decimal("0.60"), ask=Decimal("0.61"))}
        >>> realized, unrealized = calculate_strategy_pnl(portfolio, final_prices)
        >>> # realized = 0 (no sells yet)
        >>> # unrealized = 10 * 0.60 - 5.00 = 1.00 (profit on position)

    Edge cases handled:
        - Token not in final_prices: ignored (no value)
        - final_prices[tid].bid is None or 0: ignored (no valid price)
        - Empty portfolio: returns (realized_pnl, 0)
    """
    unrealized = Decimal("0")

    for tid, position in portfolio.get_positions().items():
        # Skip if no price available for this token
        if tid not in final_prices:
            continue

        price_snapshot = final_prices[tid]
        if not price_snapshot.bid or price_snapshot.bid <= 0:
            continue

        # Calculate unrealized PnL: (shares * current_price) - cost_basis
        position_value = position.shares * price_snapshot.bid
        unrealized += position_value - position.cost_basis

    return portfolio.realized_pnl, unrealized
