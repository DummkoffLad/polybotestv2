"""Decision matrix for trade-level comparison visibility.

Per CONTEXT.md requirements:
- Show position size + resulting PnL per cell
- Highlight rows where strategies diverged (yellow)
- No drill-down (summary view only)
"""

from __future__ import annotations

import pandas as pd
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .comparator import ComparisonResult


def create_decision_matrix(comparison: "ComparisonResult") -> pd.io.formats.style.Styler:
    """Build event x strategy matrix showing decisions + PnL.

    Args:
        comparison: ComparisonResult from StrategyComparator

    Returns:
        Styled DataFrame with divergence highlighting
    """
    raise NotImplementedError("TDD RED phase - implement in GREEN phase")


def get_trade_listing(comparison: "ComparisonResult") -> pd.DataFrame:
    """Create sortable/filterable trade-by-trade listing.

    Per COMP-03: sortable by outcome, strategy, market

    Args:
        comparison: ComparisonResult from StrategyComparator

    Returns:
        DataFrame with columns: timestamp, market_id, token_id, strategy_name, action, shares, pnl
    """
    raise NotImplementedError("TDD RED phase - implement in GREEN phase")
