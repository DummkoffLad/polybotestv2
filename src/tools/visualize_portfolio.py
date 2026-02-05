"""Visualize portfolio and cash over time during strategy replay.

Shows:
1. Portfolio value (total position value) over time
2. Cash spent (cumulative buys) over time
3. Hourly boundaries to verify positions are managed per market hour

Usage:
    python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
"""
import argparse
import json
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.strategies import get_strategy
from src.framework.replay import SessionReplayer
from src.strategies.base import DecisionAction


def load_price_history(session_path: Path) -> Dict[str, List[Tuple[datetime, Decimal]]]:
    """Load price history from price_snapshot events."""
    prices: Dict[str, List[Tuple[datetime, Decimal]]] = {}

    with open(session_path, 'r') as f:
        for line in f:
            data = json.loads(line)
            if data.get('type') == 'price_snapshot':
                ts = datetime.fromisoformat(data['timestamp'])
                for token_id, price_data in data.get('prices', {}).items():
                    bid = price_data.get('bid')
                    if bid and bid != '0':
                        if token_id not in prices:
                            prices[token_id] = []
                        prices[token_id].append((ts, Decimal(bid)))

    return prices


def run_replay_with_tracking(session_path: Path, strategy_name: str) -> Tuple[
    List[datetime],  # timestamps
    List[float],     # portfolio values
    List[float],     # cash spent (cumulative)
    List[float],     # realized pnl
    List[Tuple[datetime, str, float]],  # buys: (time, token_id[:8], dollars)
    List[Tuple[datetime, str, float]],  # sells: (time, token_id[:8], dollars)
]:
    """Replay strategy and track portfolio/cash over time."""
    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(session_path, strategy)
    replayer.load()

    # Run replay manually to track state at each event
    config = replayer._merge_config()
    from src.strategies.base import StrategyConfig
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    timestamps = []
    portfolio_values = []
    cash_spent = []
    realized_pnls = []
    buys = []
    sells = []

    cumulative_spent = Decimal("0")

    for event in replayer.events:
        decision = strategy.on_event(event)

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

            if decision.action == DecisionAction.BUY:
                cumulative_spent += decision.dollars or Decimal("0")
                buys.append((
                    event.trade.timestamp,
                    event.trade.token_id[:8],
                    float(decision.dollars or 0)
                ))
            else:
                sells.append((
                    event.trade.timestamp,
                    event.trade.token_id[:8],
                    float(decision.dollars or 0)
                ))

        # Calculate current portfolio value
        all_prices = event.context.get('all_prices', {})
        portfolio_value = Decimal("0")
        for token_id, pos in strategy.portfolio.get_positions().items():
            if pos.shares > 0:
                price = all_prices.get(token_id)
                if price and price.bid:
                    portfolio_value += pos.shares * price.bid
                else:
                    # Use cost basis if no current price
                    portfolio_value += pos.cost_basis

        timestamps.append(event.trade.timestamp)
        portfolio_values.append(float(portfolio_value))
        cash_spent.append(float(cumulative_spent))
        realized_pnls.append(float(strategy.portfolio.realized_pnl))

    return timestamps, portfolio_values, cash_spent, realized_pnls, buys, sells


def create_portfolio_chart(
    timestamps: List[datetime],
    portfolio_values: List[float],
    cash_spent: List[float],
    realized_pnls: List[float],
    buys: List[Tuple[datetime, str, float]],
    sells: List[Tuple[datetime, str, float]],
    strategy_name: str,
    session_name: str,
) -> go.Figure:
    """Create interactive portfolio/cash chart with hourly markers."""

    # Convert to ET timezone for display
    et = ZoneInfo('America/New_York')
    timestamps_et = [ts.astimezone(et) for ts in timestamps]

    # Create figure with 3 subplots
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            'Portfolio Value (Position Holdings)',
            'Cumulative Cash Spent',
            'Realized P&L'
        ),
        row_heights=[0.4, 0.3, 0.3]
    )

    # Plot 1: Portfolio value
    fig.add_trace(
        go.Scattergl(
            x=timestamps_et,
            y=portfolio_values,
            mode='lines',
            name='Portfolio Value',
            line=dict(color='blue', width=1.5),
            hovertemplate='%{x}<br>Value: $%{y:.2f}<extra></extra>'
        ),
        row=1, col=1
    )

    # Add buy markers on portfolio chart
    buy_times = [b[0].astimezone(et) for b in buys]
    buy_values = []
    for b in buys:
        # Find closest portfolio value at buy time
        idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - b[0]))
        buy_values.append(portfolio_values[idx])

    if buy_times:
        fig.add_trace(
            go.Scattergl(
                x=buy_times,
                y=buy_values,
                mode='markers',
                name='Buy',
                marker=dict(
                    symbol='triangle-up',
                    size=8,
                    color='green',
                    line=dict(width=1, color='darkgreen')
                ),
                hovertemplate='BUY<br>%{x}<br>Portfolio: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )

    # Add sell markers on portfolio chart
    sell_times = [s[0].astimezone(et) for s in sells]
    sell_values = []
    for s in sells:
        idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - s[0]))
        sell_values.append(portfolio_values[idx])

    if sell_times:
        fig.add_trace(
            go.Scattergl(
                x=sell_times,
                y=sell_values,
                mode='markers',
                name='Sell',
                marker=dict(
                    symbol='triangle-down',
                    size=8,
                    color='red',
                    line=dict(width=1, color='darkred')
                ),
                hovertemplate='SELL<br>%{x}<br>Portfolio: $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )

    # Plot 2: Cash spent
    fig.add_trace(
        go.Scattergl(
            x=timestamps_et,
            y=cash_spent,
            mode='lines',
            name='Cash Spent',
            line=dict(color='orange', width=1.5),
            hovertemplate='%{x}<br>Spent: $%{y:.2f}<extra></extra>'
        ),
        row=2, col=1
    )

    # Plot 3: Realized P&L
    fig.add_trace(
        go.Scattergl(
            x=timestamps_et,
            y=realized_pnls,
            mode='lines',
            name='Realized P&L',
            line=dict(color='purple', width=1.5),
            hovertemplate='%{x}<br>P&L: $%{y:.2f}<extra></extra>'
        ),
        row=3, col=1
    )

    # Add zero line for P&L
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=3, col=1)

    # Add hourly vertical lines
    if timestamps_et:
        start_hour = timestamps_et[0].replace(minute=0, second=0, microsecond=0)
        end_time = timestamps_et[-1]

        current = start_hour
        while current <= end_time:
            for row in [1, 2, 3]:
                fig.add_vline(
                    x=current,
                    line_dash="dot",
                    line_color="lightgray",
                    line_width=1,
                    row=row, col=1
                )
            # Add hour label at top
            fig.add_annotation(
                x=current,
                y=1.02,
                yref="paper",
                text=current.strftime("%H:00"),
                showarrow=False,
                font=dict(size=9, color="gray"),
            )
            from datetime import timedelta
            current = current + timedelta(hours=1)

    # Update layout
    fig.update_layout(
        title=dict(
            text=f'Portfolio & Cash: {strategy_name} on {session_name}',
            font=dict(size=16)
        ),
        height=800,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        hovermode='x unified'
    )

    # Update axes
    fig.update_xaxes(title_text="Time (ET)", row=3, col=1)
    fig.update_yaxes(title_text="$ Value", row=1, col=1)
    fig.update_yaxes(title_text="$ Spent", row=2, col=1)
    fig.update_yaxes(title_text="$ P&L", row=3, col=1)

    return fig


def main():
    parser = argparse.ArgumentParser(
        description='Visualize portfolio and cash over time during strategy replay',
        epilog='Example: python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker'
    )
    parser.add_argument('session_path', help='Path to session JSONL file')
    parser.add_argument('--strategy', '-s', default='profit_taker',
                        help='Strategy name (default: profit_taker)')
    parser.add_argument('--output', '-o', help='Output HTML file path')

    args = parser.parse_args()
    session_path = Path(args.session_path)

    if not session_path.exists():
        print(f"Error: Session file not found: {session_path}")
        return

    print(f"Loading session: {session_path.name}")
    print(f"Strategy: {args.strategy}")

    # Run replay with tracking
    print("Running replay with portfolio tracking...")
    timestamps, portfolio_values, cash_spent, realized_pnls, buys, sells = \
        run_replay_with_tracking(session_path, args.strategy)

    print(f"  Events: {len(timestamps)}")
    print(f"  Buys: {len(buys)}")
    print(f"  Sells: {len(sells)}")
    print(f"  Final portfolio value: ${portfolio_values[-1]:.2f}" if portfolio_values else "  No data")
    print(f"  Total cash spent: ${cash_spent[-1]:.2f}" if cash_spent else "  No data")
    print(f"  Realized P&L: ${realized_pnls[-1]:.2f}" if realized_pnls else "  No data")

    # Create chart
    print("Creating visualization...")
    fig = create_portfolio_chart(
        timestamps, portfolio_values, cash_spent, realized_pnls,
        buys, sells, args.strategy, session_path.stem
    )

    # Save to file
    output_dir = Path('data/reports')
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = output_dir / f'portfolio_{session_path.stem}_{args.strategy}.html'

    fig.write_html(
        output_path,
        include_plotlyjs='cdn',
        full_html=True
    )

    print(f"\nSaved: {output_path}")
    print(f"  Size: {output_path.stat().st_size / 1024:.1f} KB")


if __name__ == '__main__':
    main()
