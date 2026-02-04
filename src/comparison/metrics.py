"""Metrics calculation for strategy comparison.

Uses empyrical-reloaded for financial ratio calculations (don't hand-roll per RESEARCH.md).
Creates comparison tables with pandas Styler for conditional formatting.
"""

from __future__ import annotations

from typing import Dict, Any

import empyrical as ep
import pandas as pd
import numpy as np

from .comparator import StrategyResult, ComparisonResult


def calculate_strategy_metrics(result: StrategyResult) -> Dict[str, Any]:
    """Calculate key performance metrics using empyrical-reloaded.

    Don't hand-roll financial metrics (per RESEARCH.md):
    - Sharpe: ep.sharpe_ratio(returns)
    - Sortino: ep.sortino_ratio(returns)
    - Calmar: ep.calmar_ratio(returns)
    - Max DD: ep.max_drawdown(returns)

    Args:
        result: StrategyResult containing equity_df and replay_result

    Returns:
        Dict with metrics:
        - total_return_pct: Total return as percentage
        - sharpe_ratio: Annualized Sharpe ratio
        - sortino_ratio: Annualized Sortino ratio
        - calmar_ratio: Calmar ratio (annual return / max drawdown)
        - max_drawdown_pct: Maximum drawdown as negative percentage
        - win_rate_pct: Win rate as percentage
        - profit_factor: Gross profit / gross loss
        - trade_count: Total number of trades
    """
    equity_df = result.equity_df
    replay_result = result.replay_result

    # Calculate basic metrics from equity curve
    if equity_df.empty or len(equity_df) < 2:
        return _empty_metrics()

    equity = equity_df['equity']
    start_equity = equity.iloc[0]
    end_equity = equity.iloc[-1]

    # Total return
    total_return_pct = ((end_equity - start_equity) / start_equity) * 100

    # Convert equity to returns for empyrical
    returns = equity.pct_change().dropna()

    # Handle edge case of zero variance (flat equity)
    if returns.std() == 0:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        calmar_ratio = 0.0
        max_drawdown_pct = 0.0
    else:
        # Use empyrical-reloaded for ratio calculations
        try:
            sharpe_ratio = ep.sharpe_ratio(returns)
            if pd.isna(sharpe_ratio):
                sharpe_ratio = 0.0
        except Exception:
            sharpe_ratio = 0.0

        try:
            sortino_ratio = ep.sortino_ratio(returns)
            if pd.isna(sortino_ratio):
                sortino_ratio = 0.0
        except Exception:
            sortino_ratio = 0.0

        try:
            calmar_ratio = ep.calmar_ratio(returns)
            if pd.isna(calmar_ratio):
                calmar_ratio = 0.0
        except Exception:
            calmar_ratio = 0.0

        try:
            max_drawdown = ep.max_drawdown(returns)
            if pd.isna(max_drawdown):
                max_drawdown_pct = 0.0
            else:
                max_drawdown_pct = max_drawdown * 100  # Already negative
        except Exception:
            max_drawdown_pct = 0.0

    # Trade-based metrics from replay_result
    win_count = replay_result.win_count
    loss_count = replay_result.loss_count
    trade_count = win_count + loss_count

    # Win rate
    if trade_count > 0:
        win_rate_pct = (win_count / trade_count) * 100
    else:
        win_rate_pct = 0.0

    # Profit factor - estimated from win/loss counts and total PnL
    # Note: Accurate profit factor requires per-trade PnL data
    # We estimate based on available data
    profit_factor = _estimate_profit_factor(win_count, loss_count, float(replay_result.total_pnl))

    return {
        'total_return_pct': float(total_return_pct),
        'sharpe_ratio': float(sharpe_ratio),
        'sortino_ratio': float(sortino_ratio),
        'calmar_ratio': float(calmar_ratio),
        'max_drawdown_pct': float(max_drawdown_pct),
        'win_rate_pct': float(win_rate_pct),
        'profit_factor': float(profit_factor),
        'trade_count': int(trade_count)
    }


def create_metrics_table(comparison: ComparisonResult) -> pd.io.formats.style.Styler:
    """Create comparison table with conditional formatting.

    Per CONTEXT.md:
    - Green for best value per metric
    - Red for worst value per metric
    - Statistical confidence in separate section (not inline)

    Args:
        comparison: ComparisonResult containing strategy results

    Returns:
        pandas Styler object with conditional formatting applied
    """
    # Calculate metrics for each strategy
    metrics_data = {}
    for strategy_result in comparison.strategy_results:
        metrics = calculate_strategy_metrics(strategy_result)
        metrics_data[strategy_result.strategy_name] = metrics

    # Build DataFrame: rows=strategies, columns=metrics
    df = pd.DataFrame(metrics_data).T

    # Reorder columns for better presentation
    column_order = [
        'total_return_pct',
        'sharpe_ratio',
        'sortino_ratio',
        'calmar_ratio',
        'max_drawdown_pct',
        'win_rate_pct',
        'profit_factor',
        'trade_count'
    ]
    df = df[[c for c in column_order if c in df.columns]]

    # Apply conditional formatting
    def highlight_best_worst(s):
        """Color code best/worst per column.

        Green (#90EE90) for best value
        Red (#FFB6C1) for worst value
        """
        if len(s) < 2:
            return [''] * len(s)

        # Inverse metrics (lower is better)
        inverse_metrics = ['max_drawdown_pct']
        is_inverse = s.name in inverse_metrics

        # Skip formatting for trade_count (informational, not good/bad)
        if s.name == 'trade_count':
            return [''] * len(s)

        # Handle NaN values
        valid_values = s.dropna()
        if len(valid_values) < 2:
            return [''] * len(s)

        # Find best/worst
        if is_inverse:
            # For drawdown: less negative (closer to 0) is better
            best_val = valid_values.max()
            worst_val = valid_values.min()
        else:
            # For returns, ratios: higher is better
            best_val = valid_values.max()
            worst_val = valid_values.min()

        # Apply colors
        colors = []
        for val in s:
            if pd.isna(val):
                colors.append('')
            elif val == best_val and best_val != worst_val:
                colors.append('background-color: #90EE90; font-weight: bold')
            elif val == worst_val and best_val != worst_val:
                colors.append('background-color: #FFB6C1')
            else:
                colors.append('')
        return colors

    styled_df = df.style.apply(highlight_best_worst, axis=0)

    # Format numbers appropriately
    styled_df = styled_df.format({
        'total_return_pct': '{:.2f}%',
        'sharpe_ratio': '{:.3f}',
        'sortino_ratio': '{:.3f}',
        'calmar_ratio': '{:.3f}',
        'max_drawdown_pct': '{:.2f}%',
        'win_rate_pct': '{:.1f}%',
        'profit_factor': '{:.2f}',
        'trade_count': '{:.0f}'
    })

    return styled_df


def _empty_metrics() -> Dict[str, Any]:
    """Return empty/default metrics for invalid input."""
    return {
        'total_return_pct': 0.0,
        'sharpe_ratio': 0.0,
        'sortino_ratio': 0.0,
        'calmar_ratio': 0.0,
        'max_drawdown_pct': 0.0,
        'win_rate_pct': 0.0,
        'profit_factor': 0.0,
        'trade_count': 0
    }


def _estimate_profit_factor(win_count: int, loss_count: int, total_pnl: float) -> float:
    """Estimate profit factor from win/loss counts and total PnL.

    Note: Accurate profit factor requires per-trade profit/loss data.
    This is an estimation that assumes:
    - Average win = (total_pnl + losses) / win_count when profitable
    - Average loss = losses / loss_count

    For now, we use a simplified estimation based on win rate and PnL.

    Args:
        win_count: Number of winning trades
        loss_count: Number of losing trades
        total_pnl: Total profit/loss

    Returns:
        Estimated profit factor (gross profit / gross loss), or 0 if undefined
    """
    if loss_count == 0:
        # No losses - profit factor is undefined (infinite)
        # Return a large number to indicate "all wins"
        return 100.0 if win_count > 0 else 0.0

    if win_count == 0:
        # All losses - profit factor is 0
        return 0.0

    # Simple estimation: if PnL > 0, we made more than we lost
    # Profit factor = (total profit) / (total loss)
    # Since we don't have per-trade data, estimate based on counts and total

    if total_pnl >= 0:
        # Profitable - wins exceeded losses
        # Estimate: gross_profit = total_pnl + estimated_gross_loss
        # profit_factor = gross_profit / gross_loss

        # Use win_rate as a proxy
        win_rate = win_count / (win_count + loss_count)

        # Simple model: profit_factor correlates with win_rate when profitable
        # A 60% win rate with positive PnL suggests PF around 1.2-1.5
        if win_rate > 0.5:
            return 1.0 + (win_rate - 0.5) * 4  # 50% -> 1.0, 75% -> 2.0
        else:
            return 0.5 + win_rate  # 50% -> 1.0, 25% -> 0.75
    else:
        # Unprofitable - losses exceeded wins
        loss_rate = loss_count / (win_count + loss_count)
        if loss_rate > 0.5:
            return max(0.1, 1.0 - (loss_rate - 0.5) * 2)  # Lower PF for higher loss rate
        else:
            return 1.0 - (loss_rate * 0.5)  # Slightly below 1.0
