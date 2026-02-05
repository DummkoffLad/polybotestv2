"""Trade visualization tool - price chart with entry/exit markers.

Generates interactive HTML charts showing price movement with trade markers
for visual verification that trades happen at expected times and prices.

Usage:
    python -m src.tools.visualize_trades data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker

Output:
    Creates: data/reports/trade_viz_{session}_{strategy}.html
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..framework.replay import SessionReplayer
from ..strategies import get_strategy


def extract_price_and_trade_data(replayer: SessionReplayer, result) -> Tuple[Dict, Dict]:
    """Extract price history and trade markers from replay.

    Args:
        replayer: SessionReplayer with loaded events
        result: ReplayResult with executed trades

    Returns:
        (price_history, trade_markers)
        price_history: {token_id: [(timestamp, bid_price), ...]}
        trade_markers: {token_id: {'buys': [(ts, price, shares, profit_target)], 'sells': [(ts, price, shares, profit%)]}}
    """
    price_history: Dict[str, List[Tuple[datetime, Decimal]]] = defaultdict(list)
    trade_markers: Dict[str, Dict] = defaultdict(lambda: {'buys': [], 'sells': []})

    # Track entry prices for profit calculation on sells
    entry_prices: Dict[str, Decimal] = {}

    # Extract price history from price snapshots
    for snapshot in replayer._price_snapshots:
        for token_id, prices in snapshot.prices.items():
            if prices.bid and prices.bid > 0:
                price_history[token_id].append((snapshot.timestamp, prices.bid))

    # Extract trade markers from result.trades
    if hasattr(result, 'trades') and result.trades:
        for trade in result.trades:
            token_id = trade.token_id

            if trade.action == "BUY":
                # Calculate profit target based on entry price
                # (matches profit_taker strategy logic)
                entry_price = trade.our_price
                if entry_price < Decimal("0.30"):
                    profit_target = entry_price * Decimal("1.35")  # 35% target
                elif entry_price < Decimal("0.60"):
                    profit_target = entry_price * Decimal("1.20")  # 20% target
                else:
                    profit_target = entry_price * Decimal("1.12")  # 12% target

                trade_markers[token_id]['buys'].append(
                    (trade.timestamp, float(trade.our_price), float(trade.our_shares), float(profit_target))
                )
                entry_prices[token_id] = trade.our_price

            elif trade.action == "SELL":
                # Calculate profit %
                entry = entry_prices.get(token_id, trade.our_price)
                if entry > 0:
                    profit_pct = ((trade.our_price - entry) / entry) * 100
                else:
                    profit_pct = 0

                trade_markers[token_id]['sells'].append(
                    (trade.timestamp, float(trade.our_price), float(trade.our_shares), float(profit_pct))
                )

    return price_history, trade_markers


def create_trade_visualization(
    price_history: Dict[str, List[Tuple[datetime, Decimal]]],
    trade_markers: Dict[str, Dict],
    max_tokens: int = 10
) -> go.Figure:
    """Create interactive trade visualization chart.

    Args:
        price_history: Price data per token
        trade_markers: Trade markers per token
        max_tokens: Maximum number of tokens to display (to avoid overwhelming chart)

    Returns:
        Plotly Figure with price lines and trade markers
    """
    # Filter to tokens with trades (ignore price-only tokens)
    traded_tokens = [tid for tid in trade_markers.keys()
                     if trade_markers[tid]['buys'] or trade_markers[tid]['sells']]

    # Sort by trade count, take top N
    traded_tokens = sorted(
        traded_tokens,
        key=lambda tid: len(trade_markers[tid]['buys']) + len(trade_markers[tid]['sells']),
        reverse=True
    )[:max_tokens]

    if not traded_tokens:
        # Create empty figure with message
        fig = go.Figure()
        fig.add_annotation(
            text="No trades executed during this session",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=20)
        )
        return fig

    # Create subplots (one per token)
    num_tokens = len(traded_tokens)
    fig = make_subplots(
        rows=num_tokens, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        subplot_titles=[f"Token: {tid[:12]}..." for tid in traded_tokens]
    )

    for idx, token_id in enumerate(traded_tokens, start=1):
        row = idx

        # Add price line (Scattergl for performance)
        if token_id in price_history:
            times, prices = zip(*price_history[token_id])
            fig.add_trace(
                go.Scattergl(
                    x=times,
                    y=prices,
                    name=f"Price",
                    mode='lines',
                    line=dict(color='#2E86AB', width=1),
                    hovertemplate='%{y:.4f}<extra>Price</extra>',
                    showlegend=(idx == 1)  # Only show legend on first subplot
                ),
                row=row, col=1
            )

        # Add BUY markers (green triangles)
        markers = trade_markers[token_id]
        if markers['buys']:
            buy_times, buy_prices, buy_shares, profit_targets = zip(*markers['buys'])
            fig.add_trace(
                go.Scatter(
                    x=buy_times,
                    y=buy_prices,
                    name="BUY",
                    mode='markers',
                    marker=dict(
                        symbol='triangle-up',
                        size=12,
                        color='green',
                        line=dict(width=1, color='darkgreen')
                    ),
                    hovertemplate='BUY: $%{y:.4f}<br>Shares: %{customdata[0]:.2f}<br>Target: $%{customdata[1]:.4f}<extra></extra>',
                    customdata=list(zip(buy_shares, profit_targets)),
                    showlegend=(idx == 1)
                ),
                row=row, col=1
            )

            # Add profit target lines
            for buy_time, buy_price, _, profit_target in markers['buys']:
                fig.add_shape(
                    type="line",
                    x0=buy_time, x1=buy_time,  # Vertical line at entry
                    y0=buy_price, y1=profit_target,
                    line=dict(color="green", width=1, dash="dot"),
                    row=row, col=1
                )

        # Add SELL markers (red triangles)
        if markers['sells']:
            sell_times, sell_prices, sell_shares, profit_pcts = zip(*markers['sells'])
            fig.add_trace(
                go.Scatter(
                    x=sell_times,
                    y=sell_prices,
                    name="SELL",
                    mode='markers',
                    marker=dict(
                        symbol='triangle-down',
                        size=12,
                        color='red',
                        line=dict(width=1, color='darkred')
                    ),
                    hovertemplate='SELL: $%{y:.4f}<br>Shares: %{customdata[0]:.2f}<br>Profit: %{customdata[1]:+.1f}%<extra></extra>',
                    customdata=list(zip(sell_shares, profit_pcts)),
                    showlegend=(idx == 1)
                ),
                row=row, col=1
            )

        # Update y-axis label
        fig.update_yaxes(title_text="Price ($)", row=row, col=1)

    # Update x-axis label (only bottom subplot)
    fig.update_xaxes(title_text="Time", row=num_tokens, col=1)

    # Layout
    fig.update_layout(
        height=300 * num_tokens,
        hovermode='closest',
        title_text=f"Trade Visualization ({num_tokens} tokens)",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0
        )
    )

    return fig


def main():
    parser = argparse.ArgumentParser(
        description="Generate trade visualization for strategy replay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.tools.visualize_trades data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
  python -m src.tools.visualize_trades data/sessions/2026-02-04/02-35.jsonl --strategy conservative_mirror

Output:
  Creates: data/reports/trade_viz_{session}_{strategy}.html
        """
    )
    parser.add_argument('session_file', type=Path, help='Path to session JSONL file')
    parser.add_argument('--strategy', required=True, help='Strategy name (e.g., profit_taker)')
    parser.add_argument('--output-dir', type=Path, default=Path('data/reports'),
                       help='Output directory for HTML file (default: data/reports)')
    parser.add_argument('--max-tokens', type=int, default=10,
                       help='Maximum tokens to display (default: 10)')

    args = parser.parse_args()

    session_path: Path = args.session_file
    if not session_path.exists():
        print(f"ERROR: Session file not found: {session_path}")
        sys.exit(1)

    print(f"Generating trade visualization...")
    print(f"  Session: {session_path}")
    print(f"  Strategy: {args.strategy}")
    print()

    try:
        # Load strategy
        strategy = get_strategy(args.strategy)
        if not strategy:
            print(f"ERROR: Strategy '{args.strategy}' not found")
            sys.exit(1)

        # Run replay with trade collection
        replayer = SessionReplayer(session_path, strategy)
        event_count = replayer.load()
        print(f"Loaded {event_count} events")

        result = replayer.run(collect_trades=True, track_analysis=False)
        print(f"Executed {result.buys_executed} buys, {result.sells_executed} sells")
        print()

        # Extract data
        price_history, trade_markers = extract_price_and_trade_data(replayer, result)
        print(f"Found price data for {len(price_history)} tokens")
        print(f"Found trades on {len(trade_markers)} tokens")
        print()

        # Create visualization
        fig = create_trade_visualization(price_history, trade_markers, args.max_tokens)

        # Save to file
        session_name = session_path.stem
        output_path = args.output_dir / f"trade_viz_{session_name}_{args.strategy}.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fig.write_html(
            str(output_path),
            include_plotlyjs='cdn'  # CDN reference for smaller file size
        )

        print(f"SUCCESS: Trade visualization saved to {output_path}")
        print(f"  File size: {output_path.stat().st_size / 1024:.1f} KB")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
