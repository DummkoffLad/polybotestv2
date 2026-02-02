"""Compare original session decisions with replayer decisions trade by trade."""
import json
from decimal import Decimal
from pathlib import Path
from src.strategies import get_strategy
from src.framework.replay import SessionReplayer
from src.strategies.base import DecisionAction, StrategyConfig

# Load original session
data = [json.loads(l) for l in open('data/sessions/session_20260129_191609.jsonl')]
trades = [d for d in data if d.get('type') == 'leader_trade']

# Setup replayer  
strategy = get_strategy('mirror')
replayer = SessionReplayer(Path('data/sessions/session_20260129_191609.jsonl'), strategy)
replayer.load()
config = replayer._merge_config()

strategy_config = StrategyConfig.from_dict(config)
strategy.initialize(strategy_config)
strategy.on_session_start()

# Compare each trade
mismatches = []
for i, (event, orig) in enumerate(zip(replayer.events, trades)):
    orig_dec = orig['decision']
    orig_action = orig_dec['action']
    orig_reason = orig_dec.get('skip_reason')
    
    replay_dec = strategy.on_event(event)
    replay_action = replay_dec.action.name
    replay_reason = replay_dec.skip_reason
    
    if replay_dec.action in (DecisionAction.BUY, DecisionAction.SELL):
        strategy.on_fill(event, replay_dec)
    
    if orig_action != replay_action or orig_reason != replay_reason:
        mismatches.append({
            'seq': i + 1,
            'leader': orig['leader_trade']['action'],
            'orig_action': orig_action,
            'orig_reason': orig_reason,
            'replay_action': replay_action,
            'replay_reason': replay_reason,
        })

print(f'Found {len(mismatches)} mismatches out of {len(trades)} trades')
if mismatches:
    print()
    print('SEQ | LEADER | ORIGINAL         | REPLAY')
    print('-' * 60)
    for m in mismatches:
        orig_str = f"{m['orig_action']}({m['orig_reason'] or '-'})"
        replay_str = f"{m['replay_action']}({m['replay_reason'] or '-'})"
        print(f"{m['seq']:3} | {m['leader']:6} | {orig_str:16} | {replay_str}")
else:
    print('ALL TRADES MATCH!')

# Summary stats
print()
print('=== FINAL STATS ===')
print(f'Replayer: buys={strategy.buys}, sells={strategy.sells}, skips={strategy.skips}')
print(f'Skip reasons: {strategy.skip_reasons}')
print(f'Realized PnL: ${strategy.portfolio.realized_pnl:.2f}')
print(f'Total bought: ${strategy.portfolio.total_bought:.2f}')
print(f'Total sold: ${strategy.portfolio.total_sold:.2f}')