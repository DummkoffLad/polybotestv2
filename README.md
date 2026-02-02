# Polymarket Copy-Trading Bot V2

A safe, debuggable copy-trading bot for Polymarket hourly markets.

## Features

- **Per-market state machine** with BURST/FOLLOW/RESYNC modes
- **Strict mode separation**: DRY-RUN, PAPER, LIVE modes with impossible-to-violate boundaries
- **Decision tracing** for verification and comparison
- **Data collection** for replay simulation
- **Circuit breakers** and risk caps
- **ARM/DISARM safety** for live trading

## Quick Start

### 1. Setup

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Copy configuration
copy config\config.example.yaml config\config.yaml
copy .env.example .env

# Edit config.yaml with your settings
# Edit .env with your API credentials (for LIVE mode)
```

### 2. Run Preflight Checks

```bash
python main.py --preflight
```

### 3. Run DRY-RUN (No Orders)

```bash
# Interactive menu
python main.py

# Or directly
python main.py --mode dry-run
```

### 4. Run Data Collector

```bash
python main.py --collect
```

### 5. Run Simulation Replay

```bash
python main.py --replay data/collected/
```

### 6. Compare Decision Traces

```bash
python main.py --compare data/traces/dryrun.jsonl data/traces/replay.jsonl
```

## Project Structure

```
polybotestv2/
├── config/                   # Configuration files
│   ├── config.yaml          # Your config (gitignored)
│   └── config.example.yaml  # Template
├── src/
│   ├── core/                # Core types and logic
│   ├── execution/           # Execution adapters (strict separation)
│   ├── strategies/          # Trading strategies (NEW universal framework)
│   │   ├── base.py         # Strategy ABC, MarketEvent, TradeDecision
│   │   └── mirror/         # Mirror strategy implementation
│   ├── framework/           # Universal runner/recorder/replayer
│   │   ├── runner.py       # Universal async runner
│   │   ├── recorder.py     # Strategy-agnostic session recorder
│   │   └── replay.py       # Session replayer for any strategy
│   ├── strategy/            # (Legacy) Trading strategy
│   ├── collector/           # Data collection
│   ├── simulator/           # Replay simulation
│   ├── logging/             # Structured logging & tracing
│   ├── tools/               # Preflight, compare tools
│   └── cli/                 # Operator CLI
├── tests/                   # Unit and integration tests
├── data/                    # Data directory (gitignored)
│   ├── collected/          # Collected leader/market data
│   ├── sessions/           # Recorded sessions for replay
│   ├── traces/             # Decision traces
│   └── state/              # Persisted state
├── main.py                 # Entry point
└── requirements.txt
```

## Universal Strategy Framework

The bot uses a universal strategy framework that allows:

1. **Adding new strategies by writing a single file** - implement the `Strategy` interface
2. **Universal session recording** - works with any strategy
3. **Universal replay/backtesting** - test any strategy against recorded sessions
4. **Same interface for live and replay** - strategies don't know the difference

### Creating a New Strategy

```python
from src.strategies import Strategy, StrategyConfig, MarketEvent, TradeDecision, register_strategy

@register_strategy
class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "mystrategy"
    
    def initialize(self, config: StrategyConfig) -> None:
        # Set up strategy state
        pass
    
    def on_event(self, event: MarketEvent) -> TradeDecision:
        # Core logic: receive event, return decision
        return TradeDecision.skip("not implemented")
    
    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        # Update state after execution
        pass
    
    def get_state(self) -> Dict[str, Any]:
        return {}
```

### Replaying Sessions

```bash
# Replay a session with any strategy
python main.py --replay-session data/sessions/session_xxx.jsonl --strategy mirror
```

## Execution Modes

### DRY-RUN
- Real data reads from Polymarket
- NO order placement
- Uses `NullExecutionAdapter` (cannot place orders)

### LIVE
- Real data reads
- Real order placement
- Requires preflight + ARM command
- Uses `LiveExecutionAdapter`

## Strategy Overview

### State Machine (per market)

1. **BURST** (first 30-90s after market open):
   - Poll leader exposure every 1-2s
   - Wait for stabilization
   - Enter with 1-3 market orders toward scaled target

2. **FOLLOW** (after burst):
   - Copy leader trades with scaling and caps
   - Micro-batch bursty trades (1-3s windows)

3. **RESYNC** (every 30-120s):
   - Compare our state vs leader
   - Correct drift with at most 1 order per cycle

### Scaling

```
our_target = leader_exposure × (our_capital / leader_capital) × k
```

- `k` = conservative factor (0.5-0.8)
- `leader_capital` = estimated from total assets
- Hourly budget limits total new exposure per hour

### Risk Caps

- Per-market gross (UP + DOWN): configurable (default $8)
- Per-side: configurable (default $5)
- Global capital: configurable (default $40)
- Minimum trade size: $1 market orders, 5 shares limit orders

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/unit/test_scaling.py -v

# Run with coverage
pytest tests/ --cov=src
```

## Configuration

See `config/config.example.yaml` for all options.

Key settings:
- `mode`: DRY_RUN | PAPER | LIVE
- `leader.address`: Leader wallet to copy
- `scaling.k_factor`: Conservative factor (0.5-0.8)
- `scaling.our_capital`: Your starting capital
- `caps.*`: Risk limits

## Safety Features

### Mode Separation
- `NullExecutionAdapter`: Pure no-op, no network imports
- `LiveExecutionAdapter`: Isolated module with real API

### Live Trading Safety
1. Preflight checks must pass
2. Explicit ARM command required
3. KILL switch to instantly DISARM
4. Circuit breakers for errors/rate limits

## TODO Before LIVE

Before enabling LIVE mode, confirm with Polymarket API docs:

- [ ] Exact endpoint URLs for placing orders
- [ ] Authentication method (API key vs wallet signing)
- [ ] Order format and required fields
- [ ] Minimum order constraints (shares vs dollars)
- [ ] Fill status polling / websocket updates
- [ ] Rate limits
- [ ] Sell order format (dollars or shares)

## License

Private - for authorized use only.
