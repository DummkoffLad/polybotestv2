# Context: Profit Taker Visualization Work

## Session Context
This document captures the state of ongoing work on profit_taker visualization tools. Use this to continue in a new chat.

## What Was Built

### 1. Profit Taker Strategy (`src/strategies/profit_taker/`)
A strategy that copies leader buys but exits smarter:
- **Dynamic profit targets**: 35% for low prices (<$0.30), 20% for mid ($0.30-0.60), 12% for high (>$0.60)
- **All-position checking**: Monitors ALL positions on every event using `all_prices` context
- **Loss protection**: Won't sell at loss if leader is selling at profit
- **Mini-sell filtering**: Ignores small leader sells (<10% of their position)

**Performance**: +$16.30 vs conservative's +$4.32 on 2 sessions (277% better)

**Key files**:
- `src/strategies/profit_taker/strategy.py` - Main strategy code
- `docs/strategies/profit_taker.md` - Full documentation

### 2. Session Splitting Tool (`src/tools/split_session_hourly.py`)
Splits session JSONL files by hour (ET timezone).
```bash
python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl
```
Creates: `05-56_hour_00.jsonl`, `05-56_hour_01.jsonl`, etc.

### 3. Trade Visualization Tool (`src/tools/visualize_trades.py`)
Shows price charts with buy/sell markers.
```bash
python -m src.tools.visualize_trades data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
```

### 4. Portfolio Visualization Tool (`src/tools/visualize_portfolio.py`)
Shows portfolio value, cash flow, realized & unrealized P&L over time.
```bash
python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
```
Current output shows:
- Portfolio value with buy/sell markers
- Cash spent vs cash received
- Realized P&L
- Unrealized P&L with max envelope (peak potential profit)

### 5. Replayer Enhancement (`src/framework/replay.py`)
Added `all_prices` to event context during replay:
```python
event.context['all_prices'] = self.get_all_prices_at_time(event.trade.timestamp)
```

## What Needs To Be Done

### The Core Issue
The visualizations don't properly show **per-market price charts** for each hour.

**Key understanding about the data**:
- These are **hourly prediction markets** that resolve at the END of each hour
- Each hour has **4 active markets** with **8 tokens** (UP and DOWN for each market)
- The 46 "markets" seen in full session = different markets across multiple hours
- Within a single hour, there are only 4 markets active

### Requested Visualization
For each active hour (or the most active hours), create a chart showing:
1. **Per-market subplots**: 4 markets per hour
2. **Both tokens per market**: UP price line + DOWN price line
3. **Our trades**: Buy markers (green up triangle), Sell markers (red down triangle)
4. **Price snapshots**: Use price_snapshot events (11,457 per session) for accurate price history

Then a **summary chart** across all hours showing:
- Portfolio value over time
- Current cash at each snapshot
- Realized P&L
- Unrealized P&L

### Data Structure
Session JSONL contains:
- `price_snapshot` events: Prices for all tokens at that moment (~11,457 per session)
- `leader_trade` events: Leader's trades (~6,296 per session)

Each `price_snapshot` has:
```json
{
  "type": "price_snapshot",
  "timestamp": "2026-02-03T06:02:33.753047+00:00",
  "prices": {
    "<token_id>": {"bid": "0.55", "ask": "0.57", "spread_pct": "3.5"}
  },
  "token_count": 19
}
```

Each `leader_trade` has:
```json
{
  "type": "leader_trade",
  "leader_trade": {
    "market_id": "0x...",
    "token_id": "...",
    "side": "UP" or "DOWN",
    "action": "BUY" or "SELL",
    "shares": 100,
    "dollars": 50.00,
    "price": 0.50
  }
}
```

### Grouping Logic Needed
To create per-hour, per-market charts:
1. Group events by hour (ET timezone)
2. Within each hour, identify the 4 unique markets (by market_id)
3. For each market, plot both UP and DOWN token prices
4. Overlay our buy/sell trades on the correct token's price line

### Files to Modify
- `src/tools/visualize_portfolio.py` - Add per-market hourly charts

### Sessions Available
- `data/sessions/2026-02-03/05-56.jsonl` - Full session
- `data/sessions/2026-02-03/05-56_hour_XX.jsonl` - Pre-split hourly files
- `data/sessions/2026-02-04/02-35.jsonl` - Second session
- `data/sessions/2026-02-04/02-35_hour_XX.jsonl` - Pre-split hourly files

## Commands to Run

```bash
# Test current profit_taker performance
python -c "
from pathlib import Path
from src.strategies import get_strategy
from src.framework.replay import SessionReplayer

for session in [Path('data/sessions/2026-02-03/05-56.jsonl'), Path('data/sessions/2026-02-04/02-35.jsonl')]:
    for name in ['conservative_mirror', 'profit_taker']:
        s = get_strategy(name)
        r = SessionReplayer(session, s)
        r.load()
        result = r.run(track_analysis=False)
        print(f'{session.stem} {name}: \${float(result.total_pnl):+.2f}')
"

# Generate current portfolio visualization
python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker

# Split sessions by hour
python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl
```

## Prompt to Continue

Copy this to start a new chat:

---

I'm working on trading bot visualization tools. Read `.planning/CONTEXT-profit-taker-viz.md` for full context.

**Quick summary**: I have a `profit_taker` strategy that beats the baseline. I need to create proper visualizations showing:

1. **Per-hour, per-market price charts**: These are hourly prediction markets (4 markets per hour, 8 tokens - UP/DOWN each). Show price lines for both tokens in each market, with our buy/sell markers overlaid.

2. **Summary chart**: Portfolio value, cash, realized P&L, unrealized P&L over time (this mostly works already).

The current `src/tools/visualize_portfolio.py` has the summary chart working but doesn't properly show per-market breakdowns for each hour.

Please update the visualization to show per-market price charts grouped by hour, with our trades marked on them.

---

## Related Files
- `.planning/quick/005-profit-taker-hourly-viz-docs/` - Quick task docs
- `docs/strategies/profit_taker.md` - Strategy documentation
- `data/reports/` - Generated visualization HTML files
