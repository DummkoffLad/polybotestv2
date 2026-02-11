"""Shared trade logic for simulation and live execution.

This module provides common trade operations that must behave identically
across simulation replay and live trading to ensure consistency.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Any

from .portfolio import Portfolio
from ..data.models import PriceSnapshot

logger = logging.getLogger(__name__)


@dataclass
class LiquidationResult:
    """Result of liquidating positions."""
    total_dollars: Decimal
    num_sells: int
    details: list[dict[str, Any]]  # Per-position details for debugging


def liquidate_positions_at_hour_boundary(
    portfolio: Portfolio,
    prices: Dict[str, PriceSnapshot],
    entry_prices: Optional[Dict[str, Decimal]] = None,
    our_entries: Optional[Dict[str, Decimal]] = None,
    high_water_marks: Optional[Dict[str, Decimal]] = None,
    cash_tracker: Optional[Any] = None,
    use_resolution_prices: bool = True,
) -> LiquidationResult:
    """Liquidate all open positions at hour boundary.

    This function handles two modes:
    1. Resolution price mode (use_resolution_prices=True):
       - For hourly markets that resolve at hour boundaries
       - Uses $0.99 if bid >= $0.50 (winning side), else $0.01 (losing side)
       - Used by strategies and live runner

    2. Actual price mode (use_resolution_prices=False):
       - For mid-session liquidation in simulations
       - Uses actual bid prices from orderbook
       - Used by replayer for forced liquidations

    Args:
        portfolio: Portfolio instance to liquidate
        prices: Dict mapping token_id to PriceSnapshot (current orderbook)
        entry_prices: Optional dict of entry prices (fallback when no price data)
        our_entries: Optional dict to clean up (removed after liquidation)
        high_water_marks: Optional dict to clean up (removed after liquidation)
        cash_tracker: Optional object with 'cash' attribute to increment
        use_resolution_prices: True = use $0.99/$0.01, False = use actual bid

    Returns:
        LiquidationResult with total dollars, number of sells, and details

    Note:
        Portfolio uses composite keys ("token_id|market_id|side") from Plan 08-01.
        To look up prices, we extract token_id from the composite key.
    """
    total_dollars = Decimal("0")
    num_sells = 0
    details = []

    positions = portfolio.get_positions()
    for composite_key, pos in list(positions.items()):
        if pos.shares <= 0:
            continue

        # Extract token_id from composite key (format: "token_id|market_id|side")
        token_id = composite_key.split("|")[0]

        # Get price snapshot for this token
        price_snap = prices.get(token_id)

        # Determine liquidation price based on mode
        if use_resolution_prices:
            # Resolution mode: determine winner/loser outcome
            if price_snap and price_snap.bid is not None:
                # Use actual bid — bid=0 means token lost (orderbook drained)
                last_bid = price_snap.bid
            else:
                # No price snapshot at all — use entry price as best guess
                last_bid = (entry_prices or {}).get(token_id, Decimal("0.50"))

            # Resolution price: winning side -> $0.99, losing side -> $0.01
            if last_bid >= Decimal("0.50"):
                liquidation_price = Decimal("0.99")
            else:
                liquidation_price = Decimal("0.01")

            log_msg = f"HOUR RESOLVE: {token_id} @{liquidation_price} (bid={last_bid})"
        else:
            # Actual price mode: use bid from orderbook
            if not price_snap or price_snap.bid is None or price_snap.bid <= 0:
                # Skip if no valid bid (replayer mode doesn't force-liquidate at fallback)
                continue

            liquidation_price = price_snap.bid
            log_msg = f"LIQUIDATE hour boundary: {token_id} {pos.shares} shares @{liquidation_price}"

        # Execute the sell
        dollars = pos.shares * liquidation_price
        portfolio.apply_sell(composite_key.split("|")[0], pos.market_id, pos.side, pos.shares, liquidation_price)

        # Update cash tracker if provided
        if cash_tracker is not None and hasattr(cash_tracker, 'cash'):
            cash_tracker.cash += dollars

        # Clean up tracking dicts if provided
        if our_entries is not None and token_id in our_entries:
            del our_entries[token_id]
        if high_water_marks is not None and token_id in high_water_marks:
            del high_water_marks[token_id]

        # Track results
        total_dollars += dollars
        num_sells += 1
        details.append({
            'token_id': token_id,
            'composite_key': composite_key,
            'shares': pos.shares,
            'price': liquidation_price,
            'dollars': dollars,
        })

        logger.info(f"{log_msg} = ${dollars:.2f}")

    return LiquidationResult(
        total_dollars=total_dollars,
        num_sells=num_sells,
        details=details,
    )
