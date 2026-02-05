"""Drawdown analysis for equity curves.

Provides DrawdownAnalyzer to calculate maximum drawdown, duration,
and recovery time from equity time series data.
"""

from decimal import Decimal
from typing import Dict, Optional
import pandas as pd


class DrawdownAnalyzer:
    """Analyze drawdown metrics from equity curve."""

    def analyze(self, equity_df: pd.DataFrame) -> Dict:
        """Calculate drawdown statistics.

        Args:
            equity_df: DataFrame with 'equity' column and datetime index

        Returns:
            Dict with:
                - max_drawdown_pct (float, negative): Maximum percentage drawdown
                - max_drawdown_value (float): Dollar amount of max drawdown
                - drawdown_start (datetime): When peak occurred before drawdown
                - drawdown_bottom (datetime): Timestamp of lowest point
                - drawdown_duration_min (float): Minutes from peak to trough
                - recovery_time_min (Optional[float]): Minutes from trough to recovery
                - current_drawdown_pct (float): Current drawdown from last peak

        Raises:
            ValueError: If equity_df is empty
        """
        if len(equity_df) == 0:
            raise ValueError("Empty equity DataFrame")

        # Handle single-row DataFrame
        if len(equity_df) == 1:
            return {
                'max_drawdown_pct': 0.0,
                'max_drawdown_value': 0.0,
                'drawdown_start': equity_df.index[0],
                'drawdown_bottom': equity_df.index[0],
                'drawdown_duration_min': 0.0,
                'recovery_time_min': 0.0,
                'current_drawdown_pct': 0.0,
            }

        # Calculate running peak (cummax)
        peak = equity_df['equity'].cummax()

        # Calculate drawdown series: (equity - peak) / peak
        drawdown = (equity_df['equity'] - peak) / peak

        # Find max drawdown (most negative value)
        max_dd = drawdown.min()
        max_dd_pct = float(max_dd * 100)

        # If no drawdown occurred (monotonic increase or constant)
        if max_dd >= 0:
            return {
                'max_drawdown_pct': 0.0,
                'max_drawdown_value': 0.0,
                'drawdown_start': equity_df.index[0],
                'drawdown_bottom': equity_df.index[0],
                'drawdown_duration_min': 0.0,
                'recovery_time_min': 0.0,
                'current_drawdown_pct': float(drawdown.iloc[-1] * 100),
            }

        # Find timestamp of max drawdown
        dd_idx = drawdown.idxmin()

        # Find peak before max drawdown
        peak_before_dd = peak.loc[:dd_idx]
        drawdown_start = peak_before_dd.idxmax()

        # Calculate duration from peak to trough
        duration_seconds = (dd_idx - drawdown_start).total_seconds()
        duration_min = float(duration_seconds / 60)

        # Calculate max drawdown dollar value
        peak_value = peak.loc[dd_idx]
        trough_value = equity_df.loc[dd_idx, 'equity']
        # Handle case where loc returns Series (multiple values at same index)
        if hasattr(peak_value, 'iloc'):
            peak_value = peak_value.iloc[0]
        if hasattr(trough_value, 'iloc'):
            trough_value = trough_value.iloc[0]
        max_dd_value = float(trough_value - peak_value)

        # Calculate recovery time
        recovery_time_min = None
        after_dd = equity_df.loc[dd_idx:]
        recovered = after_dd[after_dd['equity'] >= peak_value]
        if len(recovered) > 0:
            recovery_idx = recovered.index[0]
            recovery_seconds = (recovery_idx - dd_idx).total_seconds()
            recovery_time_min = float(recovery_seconds / 60)

        # Current drawdown
        current_dd_pct = float(drawdown.iloc[-1] * 100)

        return {
            'max_drawdown_pct': max_dd_pct,
            'max_drawdown_value': max_dd_value,
            'drawdown_start': drawdown_start,
            'drawdown_bottom': dd_idx,
            'drawdown_duration_min': duration_min,
            'recovery_time_min': recovery_time_min,
            'current_drawdown_pct': current_dd_pct,
        }

    def get_drawdown_series(self, equity_df: pd.DataFrame) -> pd.Series:
        """Return the full drawdown series (percentage from peak at each point).

        Used by chart generator to plot drawdown over time.

        Args:
            equity_df: DataFrame with 'equity' column and datetime index

        Returns:
            pd.Series with drawdown percentage at each timestamp
        """
        if len(equity_df) == 0:
            return pd.Series([], dtype=float)

        peak = equity_df['equity'].cummax()
        drawdown = (equity_df['equity'] - peak) / peak * 100  # As percentage

        return drawdown
