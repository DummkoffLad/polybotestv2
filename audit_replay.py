"""Audit what profit_taker ACTUALLY does during replay."""
import requests
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

def get_name(market_id):
    try:
        resp = requests.get(f'https://clob.polymarket.com/markets/{market_id}', timeout=5)
        if resp.status_code == 200:
            n = resp.json().get('question', '?')
            return n.split(' Up or Down')[0] if 'Up or Down' in n else n[:15]
    except:
        pass
    return '?'

# Custom strategy that tracks every trade
from src.strategies.profit_taker.strategy import ProfitTakerStrategy
from src.framework.replay import SessionReplayer
from src.strategies.base import DecisionAction

class TrackedProfitTaker(ProfitTakerStrategy):
    def __init__(self):
        super().__init__()
        self.trade_log = []
        self.position_tracker = defaultdict(lambda: {'shares': Decimal('0'), 'cost': Decimal('0')})

    def on_fill(self, event, decision):
        # Call parent
        super().on_fill(event, decision)

        # Track the trade
        trade = event.trade
        token_id = decision.metadata.get('token_id', trade.token_id)
        market_id = decision.metadata.get('market_id', trade.market_id)
        side = decision.metadata.get('side', None)
        if side is None:
            from src.strategies.utils import to_side
            side = to_side(trade.side)

        price = decision.price or Decimal('0')
        shares = decision.shares or Decimal('0')
        dollars = decision.dollars or Decimal('0')

        pnl = None
        if decision.action == DecisionAction.BUY:
            self.position_tracker[token_id]['shares'] += shares
            self.position_tracker[token_id]['cost'] += dollars
        elif decision.action == DecisionAction.SELL:
            if self.position_tracker[token_id]['shares'] > 0:
                avg_cost = self.position_tracker[token_id]['cost'] / self.position_tracker[token_id]['shares']
                cost_of_sold = shares * avg_cost
                pnl = dollars - cost_of_sold
                self.position_tracker[token_id]['shares'] -= shares
                self.position_tracker[token_id]['cost'] -= cost_of_sold

        self.trade_log.append({
            'time': trade.timestamp,
            'token_id': token_id,
            'market_id': market_id,
            'side': side.value if hasattr(side, 'value') else str(side),
            'action': decision.action.value,
            'price': price,
            'shares': shares,
            'dollars': dollars,
            'pnl': pnl,
            'pos_shares': self.position_tracker[token_id]['shares'],
        })


def audit_replay(session_path, title):
    print()
    print('=' * 110)
    print(f'{title} - ACTUAL REPLAY TRADES')
    print('=' * 110)

    strategy = TrackedProfitTaker()
    replayer = SessionReplayer(Path(session_path), strategy, {'scaling.our_capital': '30'})
    replayer.load()
    result = replayer.run()

    # Get market names
    market_names = {}
    for t in strategy.trade_log:
        mid = t['market_id']
        if mid and mid not in market_names:
            market_names[mid] = get_name(mid)

    print()
    print(f"{'Time':<10} {'Market':<12} {'Side':<5} {'Action':<5} {'Price':>7} {'Shares':>8} {'$':>8} {'P&L':>10} {'Position':>10}")
    print('-' * 110)

    from datetime import timedelta
    total_pnl = Decimal('0')

    for t in strategy.trade_log:
        name = market_names.get(t['market_id'], '?')[:10]
        et = t['time'] - timedelta(hours=5)
        time_str = et.strftime('%H:%M:%S')

        pnl_str = ''
        if t['pnl'] is not None:
            pnl_str = f"${t['pnl']:+.2f}"
            total_pnl += t['pnl']

        print(f"{time_str:<10} {name:<12} {t['side']:<5} {t['action']:<5} ${float(t['price']):>5.2f}  {float(t['shares']):>7.2f}  ${float(t['dollars']):>6.2f}  {pnl_str:>10} {float(t['pos_shares']):>8.2f}sh")

    print('-' * 110)
    print(f"Total trades: {len(strategy.trade_log)}")
    print(f"Realized P&L from trades: ${total_pnl:+.2f}")
    print(f"Strategy reported P&L: ${result.total_pnl:+.2f}")
    print(f"  Realized: ${result.realized_pnl:+.2f}")
    print(f"  Unrealized: ${result.unrealized_pnl:+.2f}")

    # Check for remaining positions
    positions = strategy.portfolio.get_positions()
    open_pos = [(tid, p) for tid, p in positions.items() if p.shares > 0]
    if open_pos:
        print(f"\nOpen positions at end:")
        for tid, p in open_pos:
            name = market_names.get(p.market_id, '?')
            fp = replayer.loader.final_prices.get(tid)
            value = p.shares * fp.bid if fp and fp.bid else Decimal('0')
            print(f"  {name} {p.side.value}: {float(p.shares):.2f}sh @ ${float(p.avg_price):.3f} = ${float(p.cost_basis):.2f} cost, ${float(value):.2f} value")


audit_replay('data/sessions/2026-02-04/02-35_hour_09.jsonl', 'WINNER (+$68.75)')
print('\n' * 2)
audit_replay('data/sessions/2026-02-05/03-58_hour_02.jsonl', 'LOSER (-$25.09)')
