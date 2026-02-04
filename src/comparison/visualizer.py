"""Equity curve visualization for strategy comparison.

Creates interactive Plotly charts with equity curves overlay and drawdown shading.
Per CONTEXT.md: No trade markers on equity curve (keep it clean).
Per RESEARCH.md: Use WebGL (Scattergl) for performance, CDN for smaller HTML files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# Color palette for strategy traces
STRATEGY_COLORS = ['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#6A994E', '#7B2D8E', '#3D5A80']


def create_equity_comparison(equity_data: Dict[str, pd.DataFrame]) -> go.Figure:
    """Create comparison chart with equity curves + drawdown overlay.

    Args:
        equity_data: {strategy_name: DataFrame with 'equity' column, indexed by timestamp}

    Returns:
        Plotly Figure with:
        - Top panel (70% height): overlaid equity curves per strategy
        - Bottom panel (30% height): drawdown as shaded zones (per CONTEXT.md)
        - No trade markers (per CONTEXT.md)

    Raises:
        ValueError: If equity_data is empty or contains empty DataFrames
    """
    # Validate input
    if not equity_data:
        raise ValueError("equity_data cannot be empty")

    for name, df in equity_data.items():
        if df.empty or 'equity' not in df.columns:
            raise ValueError(f"Strategy '{name}' has empty or invalid DataFrame")
        if len(df) < 2:
            raise ValueError(f"Strategy '{name}' needs at least 2 data points")

    # Create subplots: equity (70% height) + drawdown (30%)
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=('Equity Curves', 'Drawdown from Peak (%)'),
        row_heights=[0.7, 0.3]
    )

    # Add traces for each strategy
    for idx, (strategy_name, df) in enumerate(equity_data.items()):
        color = STRATEGY_COLORS[idx % len(STRATEGY_COLORS)]

        # Prepare data - potentially downsample for performance
        df_plot = _prepare_data_for_plotting(df)

        # Top panel: Equity curve (use Scattergl for WebGL performance)
        fig.add_trace(
            go.Scattergl(
                x=df_plot.index,
                y=df_plot['equity'],
                name=strategy_name,
                mode='lines',  # No markers per CONTEXT.md
                line=dict(color=color, width=2),
                hovertemplate='%{y:.2f}<extra>' + strategy_name + '</extra>'
            ),
            row=1, col=1
        )

        # Calculate drawdown: (equity - cummax) / cummax * 100
        cummax = df_plot['equity'].expanding().max()
        drawdown_pct = ((df_plot['equity'] - cummax) / cummax * 100)

        # Bottom panel: Drawdown with shaded fill
        fig.add_trace(
            go.Scatter(
                x=df_plot.index,
                y=drawdown_pct,
                name=f"{strategy_name} DD",
                mode='lines',
                fill='tozeroy',  # Shaded zone per CONTEXT.md
                line=dict(color=color, width=1),
                fillcolor=_with_opacity(color, 0.3),
                hovertemplate='%{y:.2f}%<extra>' + strategy_name + '</extra>',
                showlegend=False
            ),
            row=2, col=1
        )

    # Styling
    fig.update_xaxes(title_text="Time", row=2, col=1)
    fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown (%)", row=2, col=1)

    fig.update_layout(
        height=800,
        hovermode='x unified',
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(t=80, b=40, l=60, r=40)
    )

    return fig


def save_equity_html(fig: go.Figure, output_path: Path) -> Path:
    """Save figure to HTML with CDN reference.

    Uses include_plotlyjs='cdn' for ~3MB smaller files (per RESEARCH.md).
    Requires internet connection to view.

    Args:
        fig: Plotly Figure to save
        output_path: Path for output HTML file

    Returns:
        Path to saved file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig.write_html(
        str(output_path),
        include_plotlyjs='cdn'  # CDN reference for smaller file size
    )

    return output_path


def _prepare_data_for_plotting(df: pd.DataFrame, max_points: int = 5000) -> pd.DataFrame:
    """Prepare data for plotting, potentially downsampling large datasets.

    Args:
        df: DataFrame with equity data
        max_points: Maximum points to plot (for performance)

    Returns:
        DataFrame ready for plotting (possibly downsampled)
    """
    if len(df) <= max_points:
        return df

    # Downsample to ~max_points while preserving important features
    step = len(df) // max_points
    return df.iloc[::step]


def _with_opacity(hex_color: str, opacity: float) -> str:
    """Convert hex color to rgba with opacity.

    Args:
        hex_color: Hex color string like '#2E86AB'
        opacity: Opacity value 0-1

    Returns:
        RGBA color string like 'rgba(46, 134, 171, 0.3)'
    """
    # Remove # if present
    hex_color = hex_color.lstrip('#')

    # Parse RGB values
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    return f'rgba({r}, {g}, {b}, {opacity})'
