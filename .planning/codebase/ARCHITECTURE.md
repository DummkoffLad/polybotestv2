# Architecture

**Analysis Date:** 2026-01-30

## Pattern Overview

**Overall:** Layered event-driven architecture with pluggable execution adapters and strategies.

The system follows a classic **separation of concerns** model:
- **Data Layer**: Fetches leader trades from Polymarket APIs and blockchain
- **Strategy Layer**: Consumes events and makes trading decisions
- **Execution Layer**: Abstract adapters for order placement (dry-run vs live)
- **Recording/Replay**: Framework for session persistence and backtesting

**Key Characteristics:**
- Event-driven: Core loop polls for leader trades, emits `MarketEvent` to strategy
- Adapter pattern: Execution abstraction allows DRY_RUN and LIVE modes without code change
- Deterministic replay: All trades and prices recorded for offline testing
- Safety-first: Validation at multiple layers, invariant checks in portfolio tracking

## Layers

**Data Layer:**
- Purpose: Fetch leader activity and market data from Polymarket
- Location: `src/data/`
- Contains:
  - `live_source.py` - Polymarket API client (positions, trades, market discovery)
  - `blockchain_detector.py` - Polygon blockchain event listener for leader transactions
  - `ws_price.py` - WebSocket price feed for bid/ask quotes
  - `models.py` - Data classes (MarketEvent, LeaderTrade, PriceSnapshot, PolymarketPosition)
- Depends on: httpx (HTTP), websocket (price feeds)
- Used by: Framework runner, recorder, replay system

**Strategy Layer:**
- Purpose: Decide whether to buy/sell when leader trades
- Location: `src/strategies/`
- Contains:
  - `base.py` - Abstract Strategy interface with StrategyConfig
  - Implementations: `mirror/`, `momentum/`, `conservative/`, `aggressive/`, `spread_aware/`, `velocity/`, `price_level/`, `hybrid_conservative/`
  - Each strategy implements `on_event(MarketEvent) -> TradeDecision`
  - Strategies maintain local portfolio state via `Portfolio` class
- Depends on: `src/core/portfolio.py` (position tracking)
- Used by: UniversalRunner, replay system

**Core Layer:**
- Purpose: Shared types, configuration, utilities
- Location: `src/core/`
- Contains:
  - `types.py` - Enums (ExecutionMode, Side, OrderStatus) and dataclasses (OrderRequest, OrderResponse, Exposure)
  - `config.py` - BotConfig loaded from YAML (leader/trader addresses, scaling, budget)
  - `portfolio.py` - Portfolio tracking with realized/unrealized PnL calculations
  - `clock.py` - Abstraction for time (SystemClock for live, SimulatedClock for replay)
- Depends on: pyyaml, python-dotenv
- Used by: All layers

**Execution Layer:**
- Purpose: Place orders via appropriate adapter
- Location: `src/execution/`
- Contains:
  - `base.py` - ExecutionAdapter abstract interface
  - `dry_run.py` - NullExecutionAdapter (logs what would execute, DRY_RUN mode)
  - `live.py` - LiveExecutionAdapter (actual order placement via py-clob-client)
  - `hybrid.py` - Alternative multi-mode adapter (not currently used)
- Depends on: py-clob-client SDK (live only)
- Used by: UniversalRunner

**Framework Layer:**
- Purpose: Main run loop and replay infrastructure
- Location: `src/framework/`
- Contains:
  - `runner.py` - UniversalRunner: polls blockchain, emits events to strategy, executes orders
  - `recorder.py` - SessionRecorder: writes events and price snapshots to JSONL for replay
  - `replay.py` - SessionReplayer: reads recorded session, replays through any strategy
- Depends on: All layers below
- Used by: main.py

**Simulation Layer:**
- Purpose: Strategy optimization and backtesting
- Location: `src/simulation/`
- Contains:
  - `optimizer.py` - Grid search over strategy parameters
  - `full_optimizer.py` - Tests all strategies × all execution modes
  - `limit_order_sim.py` - Limit order fill simulation
  - `follow_metrics.py` - Metrics for comparing strategy vs leader

## Data Flow

**Live Execution Flow (--mode live):**

1. **Initialization** (`UniversalRunner._init()`)
   - Load BotConfig from YAML
   - Initialize LiveDataSource → fetch leader positions/trades
   - Initialize BlockchainDetector → poll Polygon for leader txs
   - Initialize WebSocketPriceService → subscribe to token prices
   - Initialize LiveExecutionAdapter with private key
   - Initialize Strategy with StrategyConfig

2. **Main Loop** (`UniversalRunner.run()`)
   - Poll blockchain for new leader trades every N seconds
   - For each trade:
     - Extract trade details → create LeaderTrade
     - Fetch current bid/ask prices → create PriceSnapshot
     - Emit MarketEvent to strategy
     - Strategy decides BUY/SELL/SKIP → TradeDecision
     - If BUY/SELL: create OrderRequest → pass to execution adapter
     - Adapter places order → returns OrderResponse
     - Strategy updates local portfolio on fill
     - Record event if SessionRecorder enabled

3. **Shutdown** (`UniversalRunner._shutdown()`)
   - Strategy.on_session_end() → summary stats
   - Persist seen transaction hashes and portfolio state
   - SessionRecorder.end_session() → closes JSONL file

**Dry-Run Execution Flow (--mode dry-run):**

Same as live, except:
- NullExecutionAdapter used instead of LiveExecutionAdapter
- Orders are logged but not placed
- Portfolio simulation happens client-side

**Session Replay Flow (--replay-session PATH):**

1. **Load Session** (`SessionReplayer.load()`)
   - Read JSONL file written by SessionRecorder
   - Parse events (leader_trade, price_snapshot)
   - Reconstruct session config and trade timeline

2. **Replay** (`SessionReplayer.replay(strategy)`)
   - For each recorded trade event:
     - Look up prices from nearest price_snapshot
     - Create MarketEvent and feed to strategy
     - Strategy decides action
     - Simulate fill and update portfolio
     - Calculate realized/unrealized PnL
   - Output ReplayResult with metrics

**State Management:**

- **Leader State**: Current positions tracked from Polymarket API snapshot
- **Our State**: Portfolio maintained in-memory by Strategy
- **Seen Trades**: Transaction hash deduplication set, persisted to `data/state/seen_hashes.json`
- **Portfolio State**: Realized PnL, open positions, persisted to `data/state/portfolio_state.json` on shutdown

## Key Abstractions

**Strategy (abstract base):**
- Purpose: Encapsulates trading logic
- Examples: `src/strategies/mirror/strategy.py`, `src/strategies/momentum/strategy.py`
- Pattern:
  - Initialize with config → set budget, scale ratio, caps
  - on_event(MarketEvent) → analyze trade and prices → return TradeDecision
  - on_fill(event, decision) → update local portfolio
  - get_state() → return dict for persistence
- All strategies share same interface; runner doesn't care which strategy is loaded

**ExecutionAdapter (abstract base):**
- Purpose: Decouple order placement from strategy logic
- Examples: `src/execution/dry_run.py` (NullExecutionAdapter), `src/execution/live.py` (LiveExecutionAdapter)
- Pattern:
  - place_order(OrderRequest) → OrderResponse
  - Implementations vary: null logs only, live calls py-clob-client SDK
  - Both implement same interface
- Allows switching modes without code change

**Portfolio:**
- Purpose: Track positions and calculate PnL
- Location: `src/core/portfolio.py`
- Pattern:
  - apply_buy/apply_sell: update position and cost basis
  - Invariant checks: shares never negative, prices in (0,1)
  - Tracks realized_pnl separately from unrealized
  - Used by both live and replay systems

**MarketEvent:**
- Purpose: Immutable snapshot of trade + prices at moment of decision
- Location: `src/data/models.py`
- Contains: LeaderTrade (who, what, when) + PriceSnapshot (bid/ask at that moment)
- Frozen dataclass ensures event semantics are preserved

## Entry Points

**main.py:**
- Location: `main.py`
- Triggers: Python script invocation with CLI args
- Responsibilities:
  - Parse arguments (--mode, --replay-session, --optimize, etc.)
  - Load config from YAML
  - Branch to appropriate execution path:
    - `--mode dry-run` → UniversalRunner with NullExecutionAdapter
    - `--mode live` → UniversalRunner with LiveExecutionAdapter (after arm/preflight)
    - `--replay-session PATH` → SessionReplayer.replay()
    - `--optimize SESSION` → Optimizer.optimize()

**UniversalRunner.run():**
- Location: `src/framework/runner.py`
- Triggers: Called from main.py for live or dry-run modes
- Responsibilities:
  - Initialize data sources and adapters
  - Poll blockchain in loop
  - Route trades to strategy
  - Execute orders
  - Handle graceful shutdown (Ctrl+C)

**SessionReplayer.replay():**
- Location: `src/framework/replay.py`
- Triggers: Called from main.py for --replay-session
- Responsibilities:
  - Load recorded session from disk
  - Play events sequentially through strategy
  - Simulate order fills and PnL
  - Output metrics

## Error Handling

**Strategy:**
- Errors logged, trade skipped, runner continues
- Invalid TradeDecision (e.g., negative shares) rejected before execution

**Execution:**
- OrderRequest validated before sending to adapter
- Adapter returns OrderResponse with status and error message
- Failed orders not applied to portfolio

**Data Source:**
- API timeouts/errors caught, empty results returned
- Blockchain polling failures logged, runner continues polling
- Price feed disconnects handled gracefully

## Cross-Cutting Concerns

**Logging:**
- stdlib logging configured at module level (`logger = logging.getLogger(__name__)`)
- Each layer logs at appropriate level (DEBUG: state updates, INFO: trades, WARNING: failures)
- No console output without logger

**Validation:**
- Portfolio: invariant checks (shares >= 0, prices in bounds)
- Order: minimum order size checks (Polymarket constraints)
- Strategy: capital budget and exposure caps

**Authentication:**
- Live mode: private key from environment (POLYMARKET_PRIVATE_KEY)
- Adapter checks armed status before order placement
- Explicit preflight check in main.py before live trading

---

*Architecture analysis: 2026-01-30*
