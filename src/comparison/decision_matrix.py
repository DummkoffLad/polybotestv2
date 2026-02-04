"""Decision matrix for trade-level comparison visibility.

Per CONTEXT.md requirements:
- Show position size + resulting PnL per cell
- Highlight rows where strategies diverged (yellow)
- No drill-down (summary view only)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .comparator import ComparisonResult, StrategyResult


def create_decision_matrix(comparison: "ComparisonResult") -> pd.io.formats.style.Styler:
    """Build event x strategy matrix showing decisions + PnL.

    Per CONTEXT.md:
    - Show position size + resulting PnL per cell
    - Highlight rows where strategies diverged (yellow)
    - No drill-down (summary view only)

    Args:
        comparison: ComparisonResult from StrategyComparator

    Returns:
        Styled DataFrame with divergence highlighting
    """
    # Collect all unique events across all strategies
    # Event key: (timestamp, market_id, token_id)
    all_events: Set[Tuple[datetime, str, str]] = set()
    strategy_trades: Dict[str, Dict[Tuple[datetime, str, str], Dict]] = {}

    for result in comparison.strategy_results:
        strategy_name = result.strategy_name
        strategy_trades[strategy_name] = {}

        # Get attributed trades from analysis if available
        # attributed_trades is a flat list of AttributedTrade objects
        if result.replay_result.analysis and 'attributed_trades' in result.replay_result.analysis:
            trades = result.replay_result.analysis['attributed_trades']
            for trade in trades:
                event_key = (trade.entry_timestamp, trade.market_id, trade.token_id)
                all_events.add(event_key)
                strategy_trades[strategy_name][event_key] = {
                    'shares': trade.entry_shares,
                    'pnl': trade.realized_pnl,
                    'action': trade.action,
                    'status': trade.status,
                }
        # Also check replay_result.trades for simpler access
        elif result.replay_result.trades:
            for trade in result.replay_result.trades:
                event_key = (trade.timestamp, trade.market_id, trade.token_id)
                all_events.add(event_key)
                # Note: ExecutedTrade doesn't have realized_pnl directly
                # We'd need to calculate it from buy/sell pairs
                strategy_trades[strategy_name][event_key] = {
                    'shares': trade.our_shares,
                    'pnl': Decimal("0"),  # PnL not directly available
                    'action': trade.action,
                    'status': 'executed',
                }

    # Handle empty case
    if not all_events:
        empty_df = pd.DataFrame()
        return empty_df.style

    # Build matrix data
    matrix_data: Dict[str, Dict[str, str]] = {}
    sorted_events = sorted(all_events, key=lambda e: e[0])  # Sort by timestamp

    for event_key in sorted_events:
        timestamp, market_id, token_id = event_key
        # Create row label (readable format)
        row_label = f"{timestamp.strftime('%H:%M:%S')} | {market_id[:16]}... | {token_id[:16]}..."

        for strategy_name, trades_dict in strategy_trades.items():
            if strategy_name not in matrix_data:
                matrix_data[strategy_name] = {}

            if event_key in trades_dict:
                trade_info = trades_dict[event_key]
                shares = trade_info['shares']
                pnl = trade_info['pnl']
                # Format: "shares -> $PnL"
                cell_value = f"{float(shares):.1f} -> ${float(pnl):+.2f}"
            else:
                cell_value = "SKIP"

            matrix_data[strategy_name][row_label] = cell_value

    # Create DataFrame with events as rows, strategies as columns
    df = pd.DataFrame(matrix_data)
    df.index.name = "Event"

    # Apply styling
    def style_cell(val):
        """Style individual cells based on content."""
        if val == "SKIP":
            return "color: #808080"  # Gray for SKIP
        if isinstance(val, str) and "->" in val:
            # Extract PnL value from cell
            try:
                pnl_str = val.split("$")[1] if "$" in val else "0"
                pnl_val = float(pnl_str)
                if pnl_val > 0:
                    return "color: green"  # Green for profit
                elif pnl_val < 0:
                    return "color: red"  # Red for loss
            except (IndexError, ValueError):
                pass
        return ""

    def highlight_divergence(row):
        """Highlight rows where strategies diverged."""
        took_count = sum(1 for val in row if val != "SKIP")
        total = len(row)

        # Divergence: some took, some skipped (not all or none)
        if 0 < took_count < total:
            return ["background-color: #FFFFCC"] * len(row)  # Yellow
        return [""] * len(row)

    styled = df.style.map(style_cell).apply(highlight_divergence, axis=1)
    return styled


def get_trade_listing(comparison: "ComparisonResult") -> pd.DataFrame:
    """Create sortable/filterable trade-by-trade listing.

    Per COMP-03: sortable by outcome, strategy, market

    Args:
        comparison: ComparisonResult from StrategyComparator

    Returns:
        DataFrame with columns: timestamp, market_id, token_id, strategy_name, action, shares, pnl
    """
    rows: List[Dict] = []

    for result in comparison.strategy_results:
        strategy_name = result.strategy_name

        # Get trades from analysis (attributed_trades) if available
        # attributed_trades is a flat list of AttributedTrade objects
        if result.replay_result.analysis and 'attributed_trades' in result.replay_result.analysis:
            trades = result.replay_result.analysis['attributed_trades']
            for trade in trades:
                rows.append({
                    'timestamp': trade.entry_timestamp,
                    'market_id': trade.market_id,
                    'token_id': trade.token_id,
                    'strategy_name': strategy_name,
                    'action': trade.action,
                    'shares': float(trade.entry_shares),
                    'pnl': float(trade.realized_pnl),
                })
        # Fallback to replay_result.trades
        elif result.replay_result.trades:
            for trade in result.replay_result.trades:
                rows.append({
                    'timestamp': trade.timestamp,
                    'market_id': trade.market_id,
                    'token_id': trade.token_id,
                    'strategy_name': strategy_name,
                    'action': trade.action,
                    'shares': float(trade.our_shares),
                    'pnl': 0.0,  # PnL not directly available in ExecutedTrade
                })

    # Create DataFrame
    if not rows:
        # Return empty DataFrame with expected columns
        return pd.DataFrame(columns=[
            'timestamp', 'market_id', 'token_id', 'strategy_name', 'action', 'shares', 'pnl'
        ])

    df = pd.DataFrame(rows)
    return df
