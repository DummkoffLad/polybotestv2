"""Visualize trading session with price snapshots and portfolio tracking.

Creates two types of visualizations:
1. Per-hour charts: Price of each market with buy/sell markers
2. Summary chart: Portfolio value, cash, realized & unrealized P&L over time

Usage:
    python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
"""
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from zoneinfo import ZoneInfo
from dataclasses import dataclass

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.strategies import get_strategy
from src.framework.replay import SessionReplayer
from src.strategies.base import DecisionAction, StrategyConfig
from src.data.models import PriceSnapshot


@dataclass
class PortfolioState:
    """State at a point in time."""
    timestamp: datetime
    portfolio_value: float  # Sum of position values at current prices
    cash_spent: float       # Cumulative cash spent on buys
    cash_received: float    # Cumulative cash from sells
    realized_pnl: float
    unrealized_pnl: float
    position_count: int


@dataclass
class Trade:
    """A buy or sell trade."""
    timestamp: datetime
    token_id: str
    action: str  # 'BUY' or 'SELL'
    shares: float
    price: float
    dollars: float


def load_session_events(session_path: Path) -> Tuple[List[dict], List[dict]]:
    """Load and separate price_snapshot and leader_trade events."""
    snapshots = []
    trades = []

    with open(session_path, 'r') as f:
        for line in f:
            data = json.loads(line)
            event_type = data.get('type')
            if event_type == 'price_snapshot':
                snapshots.append(data)
            elif event_type == 'leader_trade':
                trades.append(data)

    return snapshots, trades


def run_tracking(session_path: Path, strategy_name: str) -> Tuple[
    List[PortfolioState],  # State at each snapshot
    List[Trade],           # Our executed trades
    Dict[str, List[Tuple[datetime, float]]],  # Price history per token
]:
    """Run strategy and track portfolio state at each price snapshot."""

    # Load events
    snapshots_raw, trades_raw = load_session_events(session_path)
    print(f"  Loaded {len(snapshots_raw)} price snapshots, {len(trades_raw)} trades")

    # Setup strategy via replayer (for config)
    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(session_path, strategy)
    replayer.load()

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    # Track state
    states: List[PortfolioState] = []
    our_trades: List[Trade] = []
    price_history: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

    cash_spent = Decimal("0")
    cash_received = Decimal("0")

    # Current prices from latest snapshot
    current_prices: Dict[str, Decimal] = {}

    # Process events in order
    trade_idx = 0

    for snap in snapshots_raw:
        snap_time = datetime.fromisoformat(snap['timestamp'])

        # Update current prices from snapshot
        for token_id, price_data in snap.get('prices', {}).items():
            bid = price_data.get('bid')
            if bid and bid != '0':
                price = Decimal(bid)
                current_prices[token_id] = price
                price_history[token_id].append((snap_time, float(price)))

        # Process any trades that happened before this snapshot
        while trade_idx < len(trades_raw):
            trade_data = trades_raw[trade_idx]
            trade_time = datetime.fromisoformat(trade_data['timestamp'])

            if trade_time > snap_time:
                break  # This trade is after the snapshot

            # Process this trade through strategy
            event = replayer.events[trade_idx] if trade_idx < len(replayer.events) else None
            if event:
                # Populate all_prices for strategy
                event.context['all_prices'] = {
                    tid: PriceSnapshot(token_id=tid, bid=p, ask=p, spread_pct=Decimal("0"))
                    for tid, p in current_prices.items()
                }

                decision = strategy.on_event(event)

                if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                    strategy.on_fill(event, decision)

                    # Record our trade
                    meta = decision.metadata
                    token_id = meta.get('token_id', event.trade.token_id)

                    our_trades.append(Trade(
                        timestamp=trade_time,
                        token_id=token_id,
                        action='BUY' if decision.action == DecisionAction.BUY else 'SELL',
                        shares=float(decision.shares or 0),
                        price=float(decision.price or 0),
                        dollars=float(decision.dollars or 0)
                    ))

                    if decision.action == DecisionAction.BUY:
                        cash_spent += decision.dollars or Decimal("0")
                    else:
                        cash_received += decision.dollars or Decimal("0")

            trade_idx += 1

        # Calculate portfolio state at this snapshot
        portfolio_value = Decimal("0")
        cost_basis = Decimal("0")
        position_count = 0

        for token_id, pos in strategy.portfolio._positions.items():
            if pos.shares > 0:
                position_count += 1
                cost_basis += pos.cost_basis
                price = current_prices.get(token_id)
                if price:
                    portfolio_value += pos.shares * price
                else:
                    portfolio_value += pos.cost_basis

        unrealized = portfolio_value - cost_basis

        states.append(PortfolioState(
            timestamp=snap_time,
            portfolio_value=float(portfolio_value),
            cash_spent=float(cash_spent),
            cash_received=float(cash_received),
            realized_pnl=float(strategy.portfolio.realized_pnl),
            unrealized_pnl=float(unrealized),
            position_count=position_count
        ))

    return states, our_trades, dict(price_history)


def create_hourly_charts(
    price_history: Dict[str, List[Tuple[datetime, float]]],
    trades: List[Trade],
    session_name: str,
    output_dir: Path
) -> List[Path]:
    """Create per-hour price charts with trade markers."""
    et = ZoneInfo('America/New_York')

    # Group by hour
    hours: Dict[int, Dict[str, List[Tuple[datetime, float]]]] = defaultdict(lambda: defaultdict(list))
    trades_by_hour: Dict[int, List[Trade]] = defaultdict(list)

    for token_id, history in price_history.items():
        for ts, price in history:
            hour = ts.astimezone(et).hour
            hours[hour][token_id].append((ts, price))

    for trade in trades:
        hour = trade.timestamp.astimezone(et).hour
        trades_by_hour[hour].append(trade)

    created_files = []

    for hour in sorted(hours.keys()):
        tokens_data = hours[hour]
        hour_trades = trades_by_hour[hour]

        # Get top 6 most active tokens
        sorted_tokens = sorted(tokens_data.items(), key=lambda x: len(x[1]), reverse=True)[:6]

        if not sorted_tokens:
            continue

        # Create subplot for each token
        n_tokens = len(sorted_tokens)
        fig = make_subplots(
            rows=n_tokens, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            subplot_titles=[f"Token {tid[:12]}..." for tid, _ in sorted_tokens]
        )

        for row, (token_id, history) in enumerate(sorted_tokens, 1):
            times = [ts.astimezone(et) for ts, _ in history]
            prices = [p for _, p in history]

            # Price line
            fig.add_trace(
                go.Scattergl(
                    x=times,
                    y=prices,
                    mode='lines',
                    name=f'Price',
                    line=dict(width=1),
                    showlegend=(row == 1)
                ),
                row=row, col=1
            )

            # Add trades for this token
            token_trades = [t for t in hour_trades if t.token_id == token_id]

            buys = [t for t in token_trades if t.action == 'BUY']
            sells = [t for t in token_trades if t.action == 'SELL']

            if buys:
                fig.add_trace(
                    go.Scatter(
                        x=[t.timestamp.astimezone(et) for t in buys],
                        y=[t.price for t in buys],
                        mode='markers',
                        name='BUY',
                        marker=dict(symbol='triangle-up', size=10, color='green'),
                        showlegend=(row == 1),
                        hovertemplate='BUY %{y:.4f}<extra></extra>'
                    ),
                    row=row, col=1
                )

            if sells:
                fig.add_trace(
                    go.Scatter(
                        x=[t.timestamp.astimezone(et) for t in sells],
                        y=[t.price for t in sells],
                        mode='markers',
                        name='SELL',
                        marker=dict(symbol='triangle-down', size=10, color='red'),
                        showlegend=(row == 1),
                        hovertemplate='SELL %{y:.4f}<extra></extra>'
                    ),
                    row=row, col=1
                )

        fig.update_layout(
            title=f'{session_name} - Hour {hour:02d} ET',
            height=200 * n_tokens,
            showlegend=True
        )

        output_path = output_dir / f'{session_name}_hour_{hour:02d}.html'
        fig.write_html(output_path, include_plotlyjs='cdn')
        created_files.append(output_path)

    return created_files


def create_summary_chart(
    states: List[PortfolioState],
    trades: List[Trade],
    strategy_name: str,
    session_name: str,
) -> go.Figure:
    """Create summary chart with portfolio value, cash, and P&L."""
    et = ZoneInfo('America/New_York')

    times = [s.timestamp.astimezone(et) for s in states]
    portfolio_values = [s.portfolio_value for s in states]
    cash_spent = [s.cash_spent for s in states]
    cash_received = [s.cash_received for s in states]
    net_cash = [s.cash_received - s.cash_spent for s in states]
    realized_pnls = [s.realized_pnl for s in states]
    unrealized_pnls = [s.unrealized_pnl for s in states]
    total_pnls = [s.realized_pnl + s.unrealized_pnl for s in states]

    # Create 4 subplots
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=(
            'Portfolio Value (Position Holdings)',
            'Cash Flow (Spent vs Received)',
            'Realized P&L',
            'Unrealized P&L (Could Have Profited More?)'
        ),
        row_heights=[0.25, 0.25, 0.25, 0.25]
    )

    # Plot 1: Portfolio value
    fig.add_trace(
        go.Scattergl(x=times, y=portfolio_values, mode='lines',
                    name='Portfolio Value', line=dict(color='blue', width=1.5)),
        row=1, col=1
    )

    # Add trade markers on portfolio
    buy_trades = [t for t in trades if t.action == 'BUY']
    sell_trades = [t for t in trades if t.action == 'SELL']

    # Find portfolio values at trade times
    def find_value_at_time(trade):
        trade_ts = trade.timestamp
        for i, s in enumerate(states):
            if s.timestamp >= trade_ts:
                return portfolio_values[max(0, i-1)]
        return portfolio_values[-1] if portfolio_values else 0

    if buy_trades:
        buy_times = [t.timestamp.astimezone(et) for t in buy_trades]
        buy_values = [find_value_at_time(t) for t in buy_trades]
        fig.add_trace(
            go.Scatter(x=buy_times, y=buy_values, mode='markers', name='Buy',
                      marker=dict(symbol='triangle-up', size=8, color='green')),
            row=1, col=1
        )

    if sell_trades:
        sell_times = [t.timestamp.astimezone(et) for t in sell_trades]
        sell_values = [find_value_at_time(t) for t in sell_trades]
        fig.add_trace(
            go.Scatter(x=sell_times, y=sell_values, mode='markers', name='Sell',
                      marker=dict(symbol='triangle-down', size=8, color='red')),
            row=1, col=1
        )

    # Plot 2: Cash flow
    fig.add_trace(
        go.Scattergl(x=times, y=cash_spent, mode='lines',
                    name='Cash Spent', line=dict(color='orange', width=1.5)),
        row=2, col=1
    )
    fig.add_trace(
        go.Scattergl(x=times, y=cash_received, mode='lines',
                    name='Cash Received', line=dict(color='green', width=1.5)),
        row=2, col=1
    )

    # Plot 3: Realized P&L
    fig.add_trace(
        go.Scattergl(x=times, y=realized_pnls, mode='lines',
                    name='Realized P&L', line=dict(color='purple', width=1.5)),
        row=3, col=1
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=3, col=1)

    # Plot 4: Unrealized P&L with max envelope
    fig.add_trace(
        go.Scattergl(x=times, y=unrealized_pnls, mode='lines',
                    name='Unrealized P&L', line=dict(color='teal', width=1.5)),
        row=4, col=1
    )

    # Max unrealized envelope
    max_unrealized = []
    running_max = 0
    for u in unrealized_pnls:
        if u > running_max:
            running_max = u
        max_unrealized.append(running_max)

    fig.add_trace(
        go.Scattergl(x=times, y=max_unrealized, mode='lines',
                    name='Max Unrealized (Peak)', line=dict(color='gold', width=1, dash='dot')),
        row=4, col=1
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=4, col=1)

    # Add hourly lines
    if times:
        start_hour = times[0].replace(minute=0, second=0, microsecond=0)
        end_time = times[-1]
        current = start_hour
        while current <= end_time:
            for row in [1, 2, 3, 4]:
                fig.add_vline(x=current, line_dash="dot", line_color="lightgray",
                             line_width=1, row=row, col=1)
            current = current + timedelta(hours=1)

    fig.update_layout(
        title=f'Portfolio Summary: {strategy_name} on {session_name}',
        height=1000,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.5, xanchor="center"),
        hovermode='x unified'
    )

    fig.update_xaxes(title_text="Time (ET)", row=4, col=1)
    fig.update_yaxes(title_text="$ Value", row=1, col=1)
    fig.update_yaxes(title_text="$ Cash", row=2, col=1)
    fig.update_yaxes(title_text="$ Realized", row=3, col=1)
    fig.update_yaxes(title_text="$ Unrealized", row=4, col=1)

    return fig


def main():
    parser = argparse.ArgumentParser(
        description='Visualize trading session with price snapshots and portfolio tracking'
    )
    parser.add_argument('session_path', help='Path to session JSONL file')
    parser.add_argument('--strategy', '-s', default='profit_taker',
                        help='Strategy name (default: profit_taker)')
    parser.add_argument('--output', '-o', help='Output directory')
    parser.add_argument('--hourly', action='store_true',
                        help='Generate per-hour charts')

    args = parser.parse_args()
    session_path = Path(args.session_path)

    if not session_path.exists():
        print(f"Error: Session file not found: {session_path}")
        return

    print(f"Loading session: {session_path.name}")
    print(f"Strategy: {args.strategy}")

    # Run tracking
    print("Running replay with snapshot tracking...")
    states, trades, price_history = run_tracking(session_path, args.strategy)

    print(f"  Snapshots tracked: {len(states)}")
    print(f"  Our trades: {len(trades)} ({sum(1 for t in trades if t.action=='BUY')} buys, {sum(1 for t in trades if t.action=='SELL')} sells)")

    if states:
        final = states[-1]
        print(f"  Final portfolio value: ${final.portfolio_value:.2f}")
        print(f"  Cash spent: ${final.cash_spent:.2f}")
        print(f"  Cash received: ${final.cash_received:.2f}")
        print(f"  Realized P&L: ${final.realized_pnl:.2f}")
        print(f"  Unrealized P&L: ${final.unrealized_pnl:.2f}")
        print(f"  Max unrealized: ${max(s.unrealized_pnl for s in states):.2f}")

    # Output directory
    output_dir = Path(args.output) if args.output else Path('data/reports')
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create summary chart
    print("Creating summary chart...")
    fig = create_summary_chart(states, trades, args.strategy, session_path.stem)
    summary_path = output_dir / f'portfolio_{session_path.stem}_{args.strategy}.html'
    fig.write_html(summary_path, include_plotlyjs='cdn')
    print(f"  Saved: {summary_path}")

    # Create hourly charts if requested
    if args.hourly:
        print("Creating hourly charts...")
        hourly_dir = output_dir / f'{session_path.stem}_hourly'
        hourly_dir.mkdir(exist_ok=True)
        hourly_files = create_hourly_charts(price_history, trades, session_path.stem, hourly_dir)
        print(f"  Created {len(hourly_files)} hourly charts in {hourly_dir}")


if __name__ == '__main__':
    main()
