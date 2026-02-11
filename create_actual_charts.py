"""Create charts showing ACTUAL replay trades, not recorded decisions."""
import requests
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from collections import defaultdict
import plotly.graph_objects as go

from src.strategies.profit_taker.strategy import ProfitTakerStrategy
from src.framework.replay import SessionReplayer
from src.strategies.base import DecisionAction

def get_name(market_id):
    try:
        resp = requests.get(f'https://clob.polymarket.com/markets/{market_id}', timeout=5)
        if resp.status_code == 200:
            n = resp.json().get('question', '?')
            return n.split(' Up or Down')[0] if 'Up or Down' in n else n[:15]
    except:
        pass
    return '?'


class TrackedStrategy(ProfitTakerStrategy):
    """Strategy that tracks all executed trades."""

    def __init__(self):
        super().__init__()
        self.executed_trades = []
        self._position_tracker = defaultdict(lambda: {'shares': Decimal('0'), 'cost': Decimal('0')})

    def on_fill(self, event, decision):
        super().on_fill(event, decision)

        trade = event.trade
        token_id = decision.metadata.get('token_id', trade.token_id)
        market_id = decision.metadata.get('market_id', trade.market_id)
        side = decision.metadata.get('side', None)
        if side is None:
            from src.strategies.utils import to_side
            side = to_side(trade.side)

        price = float(decision.price or 0)
        shares = float(decision.shares or 0)
        dollars = float(decision.dollars or 0)

        pnl = None
        if decision.action == DecisionAction.BUY:
            self._position_tracker[token_id]['shares'] += Decimal(str(shares))
            self._position_tracker[token_id]['cost'] += Decimal(str(dollars))
        elif decision.action == DecisionAction.SELL:
            pos = self._position_tracker[token_id]
            if pos['shares'] > 0:
                avg_cost = float(pos['cost'] / pos['shares'])
                cost_of_sold = shares * avg_cost
                pnl = dollars - cost_of_sold
                pos['shares'] -= Decimal(str(shares))
                pos['cost'] -= Decimal(str(cost_of_sold))

        self.executed_trades.append({
            'time': trade.timestamp,
            'token_id': token_id,
            'market_id': market_id,
            'side': side.value if hasattr(side, 'value') else str(side),
            'action': decision.action.value,
            'price': price,
            'shares': shares,
            'dollars': dollars,
            'pnl': pnl,
        })


def get_price_history(session_path):
    """Extract price history from session file."""
    price_history = defaultdict(list)
    token_markets = {}

    with open(session_path) as f:
        for line in f:
            event = json.loads(line)
            if event.get('type') != 'leader_trade':
                continue

            ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            lt = event.get('leader_trade', {})
            token_id = lt.get('token_id', '')
            market_id = lt.get('market_id', '')
            side = lt.get('side', '')

            if token_id and market_id:
                token_markets[token_id] = {'market_id': market_id, 'side': side}

            pc = event.get('price_context', {})
            if pc:
                bid = float(pc.get('bid', 0) or 0)
                ask = float(pc.get('ask', 0) or 0)
                if bid > 0 and ask > 0:
                    price_history[token_id].append((ts, bid, ask))

    return price_history, token_markets


def create_charts(session_path, title, output_dir):
    """Create one chart per token showing actual replay trades."""

    print(f"\nProcessing: {title}")

    # Run replay to get actual trades
    strategy = TrackedStrategy()
    replayer = SessionReplayer(Path(session_path), strategy, {'scaling.our_capital': '30'})
    replayer.load()
    result = replayer.run()

    # Get price history
    price_history, token_markets = get_price_history(session_path)

    # Get market names
    market_names = {}
    for tid, info in token_markets.items():
        mid = info['market_id']
        if mid not in market_names:
            market_names[mid] = get_name(mid)

    # Group trades by token
    trades_by_token = defaultdict(list)
    for t in strategy.executed_trades:
        trades_by_token[t['token_id']].append(t)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create one chart per token
    for token_id, prices in price_history.items():
        info = token_markets.get(token_id, {})
        market_id = info.get('market_id', '')
        side = info.get('side', '?')
        name = market_names.get(market_id, '?')

        prices = sorted(prices, key=lambda x: x[0])
        if not prices:
            continue

        # Convert to ET
        times = [p[0] - timedelta(hours=5) for p in prices]
        bids = [p[1] for p in prices]
        asks = [p[2] for p in prices]

        fig = go.Figure()

        # Price spread
        fig.add_trace(go.Scatter(
            x=times + times[::-1],
            y=bids + asks[::-1],
            fill='toself',
            fillcolor='rgba(100, 149, 237, 0.15)',
            line=dict(color='rgba(0,0,0,0)'),
            name='Bid-Ask',
            hoverinfo='skip',
        ))

        # Bid line
        fig.add_trace(go.Scatter(
            x=times, y=bids,
            mode='lines',
            name='Price (Bid)',
            line=dict(color='steelblue', width=2),
            hovertemplate='%{x|%H:%M:%S}<br>Bid: $%{y:.3f}<extra></extra>',
        ))

        # Get trades for this token
        token_trades = trades_by_token.get(token_id, [])

        # Separate buys and sells
        buys = [t for t in token_trades if t['action'] == 'BUY']
        sells = [t for t in token_trades if t['action'] == 'SELL']

        # Calculate totals
        buy_total = sum(t['dollars'] for t in buys)
        sell_total = sum(t['dollars'] for t in sells)
        realized_pnl = sum(t['pnl'] for t in sells if t['pnl'] is not None)

        # Plot buys
        if buys:
            buy_times = [t['time'] - timedelta(hours=5) for t in buys]
            buy_prices = [t['price'] for t in buys]
            buy_hover = [f"BUY ${t['dollars']:.2f}<br>{t['shares']:.1f} shares @ ${t['price']:.3f}" for t in buys]

            fig.add_trace(go.Scatter(
                x=buy_times,
                y=buy_prices,
                mode='markers',
                name=f'BUY (${buy_total:.0f})',
                marker=dict(
                    color='lime',
                    size=12,
                    symbol='triangle-up',
                    line=dict(width=2, color='darkgreen')
                ),
                text=buy_hover,
                hovertemplate='<b>%{text}</b><extra></extra>',
            ))

        # Plot sells with P&L coloring
        if sells:
            sell_times = [t['time'] - timedelta(hours=5) for t in sells]
            sell_prices = [t['price'] for t in sells]
            sell_colors = ['green' if (t['pnl'] or 0) >= 0 else 'red' for t in sells]
            sell_hover = [
                f"SELL ${t['dollars']:.2f}<br>{t['shares']:.1f} shares @ ${t['price']:.3f}<br>P&L: ${t['pnl']:+.2f}"
                if t['pnl'] is not None else f"SELL ${t['dollars']:.2f}"
                for t in sells
            ]

            fig.add_trace(go.Scatter(
                x=sell_times,
                y=sell_prices,
                mode='markers',
                name=f'SELL (${sell_total:.0f})',
                marker=dict(
                    color=sell_colors,
                    size=12,
                    symbol='triangle-down',
                    line=dict(width=2, color='darkred')
                ),
                text=sell_hover,
                hovertemplate='<b>%{text}</b><extra></extra>',
            ))

        # Title with stats
        net = sell_total - buy_total
        fig.update_layout(
            title=dict(
                text=f"<b>{name} {side}</b> | {title}<br>" +
                     f"<sup>Bought: ${buy_total:.0f} ({len(buys)} trades) | " +
                     f"Sold: ${sell_total:.0f} ({len(sells)} trades) | " +
                     f"Realized P&L: ${realized_pnl:+.2f}</sup>",
                font=dict(size=14),
            ),
            xaxis=dict(
                title='Time (ET)',
                tickformat='%H:%M',
            ),
            yaxis=dict(
                title='Price ($)',
                range=[0, 1.05],
                tickformat='$.2f',
            ),
            height=500,
            width=1000,
            hovermode='closest',
            legend=dict(
                orientation='h',
                yanchor='bottom',
                y=1.02,
                xanchor='center',
                x=0.5
            ),
        )

        # Save
        safe_name = f"{name}_{side}".replace(' ', '_').replace('/', '_')
        output_file = output_dir / f"{safe_name}.html"
        fig.write_html(output_file, include_plotlyjs='cdn')
        print(f"  {output_file.name}: {len(buys)} buys, {len(sells)} sells, P&L ${realized_pnl:+.2f}")

    print(f"  Total strategy P&L: ${result.total_pnl:+.2f}")
    return result


# Create charts for both sessions
print("Creating ACTUAL trade charts...")

create_charts(
    'data/sessions/2026-02-04/02-35_hour_09.jsonl',
    'WINNER +$68.75',
    'data/reports/actual_winner'
)

create_charts(
    'data/sessions/2026-02-05/03-58_hour_02.jsonl',
    'LOSER -$25.09',
    'data/reports/actual_loser'
)

print("\nDone! Charts saved to data/reports/actual_winner/ and data/reports/actual_loser/")
