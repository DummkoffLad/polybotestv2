# Architecture

**Analysis Date:** 2026-02-05

## Pattern Overview

**Overall:** Event-driven copy-trading bot following a data flow pipeline architecture where leader trades trigger strategy decisions that flow through an execution layer.

**Key Characteristics:**
- Decoupled strategy layer from execution (pluggable execution adapters)
- Blockchain event detection feeding into price-aware decision making
- Pluggable strategy framework with multiple concrete implementations
- Session recording and replay for optimization and testing
- State persistence across restarts

## Layers

**Data Source Layer:**
- Purpose: Fetch leader trades and position data from Polymarket APIs
- Location: `src/data/live_source.py`, `src/data/blockchain_detector.py`, `src/data/ws_price.py`
- Contains: LiveDataSource (HTTP API client), BlockchainDetector (blockchain event polling), WebSocketPriceService (real-time prices)
- Depends on: HTTP client, blockchain RPC, WebSocket connection
- Used by: UniversalRunner (orchestrator)

**Strategy Layer:**
- Purpose: Consume MarketEvents and generate trading decisions
- Location: `src/strategies/base.py` (base interface), `src/strategies/mirror/`, `src/strategies/conservative/`, `src/strategies/aggressive/`, etc.
- Contains: Strategy base class, 9+ concrete strategy implementations
- Depends on: MarketEvent models, StrategyConfig
- Used by: UniversalRunner for decision making

**Execution Layer:**
- Purpose: Convert strategy decisions into actual orders on Polymarket
- Location: `src/execution/base.py`, `src/execution/dry_run.py`, `src/execution/live.py`, `src/execution/hybrid.py`
- Contains: ExecutionAdapter interface, NullExecutionAdapter (dry-run), LiveExecutionAdapter (real API)
- Depends on: OrderRequest/OrderResponse models, Polymarket API
- Used by: UniversalRunner to place orders

**Framework Layer:**
- Purpose: Orchestrate the event loop and manage runner lifecycle
- Location: `src/framework/runner.py`, `src/framework/replay.py`, `src/framework/recorder.py`
- Contains: UniversalRunner (main loop), SessionReplayer (replays recorded sessions), SessionRecorder (records events)
- Depends on: Data source, strategy, execution layers
- Used by: main.py entry point

**Core Services:**
- Purpose: Cross-cutting trading logic (portfolio tracking, capital management, risk sizing)
- Location: `src/core/portfolio.py`, `src/core/capital_manager.py`, `src/core/kelly_engine.py`, `src/core/config.py`
- Contains: Portfolio tracking, position management, capital allocation, Kelly criterion sizing
- Depends on: Type definitions, Decimal math
- Used by: Strategies and execution layer

**Analysis & Reporting:**
- Purpose: Post-session analysis and metrics computation
- Location: `src/analysis/drawdown.py`, `src/analysis/reports.py`, `src/analysis/equity_tracker.py`, `src/analysis/slippage.py`
- Contains: Equity curve tracking, drawdown computation, P&L attribution, slippage analysis
- Depends on: Session data, position history
- Used by: Replay system for optimization results

**Simulation & Optimization:**
- Purpose: Test and optimize strategies against recorded sessions
- Location: `src/simulation/optimizer.py`, `src/simulation/full_optimizer.py`
- Contains: Grid search optimization, strategy variant testing, full Cartesian product testing
- Depends on: SessionReplayer, all strategies, analysis modules
- Used by: main.py --optimize flags

## Data Flow

**Live Trading Flow (--mode live):**

1. UniversalRunner initializes in `_init()`: fetches leader snapshot, discovers markets, starts data services
2. BlockchainDetector polls for leader transaction logs on Polygon
3. For each trade found, BlockchainDetector enriches with market data and creates LeaderTrade
4. LeaderTrade wrapped in MarketEvent with price snapshot from WebSocketPriceService
5. Strategy.on_event(event) processes event, returns TradeDecision
6. UniversalRunner validates decision against Polymarket constraints (minimum dollar amounts, etc)
7. ExecutionAdapter.place_order(OrderRequest) sends to Polymarket API
8. Strategy.on_fill() updates internal state with fill confirmation
9. Portfolio tracks position changes, calculates PnL
10. State persisted to disk on shutdown or hourly transitions

**Dry-Run Flow (--mode dry-run):**

Same as live except ExecutionAdapter.place_order() returns SIMULATED status instead of hitting real API.

**Replay Flow (--replay-session PATH):**

1. SessionReplayer loads recorded session JSON (events, prices, trades)
2. For each recorded MarketEvent, feeds to strategy in same sequence
3. Replayer computes execution without hitting network
4. Strategy decisions evaluated against recorded prices (deterministic)
5. Final portfolio and P&L metrics computed
6. Results used for optimization or analysis

**Hourly Market Transition:**

1. UniversalRunner detects when within 30 seconds of hour boundary
2. Calls _hourly_cleanup(): attempts to sell high positions, accepts losses on low prices
3. Stops WebSocket and BlockchainDetector
4. Waits for hour boundary + 30 second delay
5. Clears internal state (seen trades, discovered markets)
6. Calls _init() to reinitialize with fresh state for new hourly market

**State Management:**

- Session state: In-memory in UniversalRunner and Strategy instances
- Persistence: `data/state/seen_hashes.json` (deduplication), `data/state/portfolio_state.json` (positions/summary)
- Recording: SessionRecorder writes events to `data/sessions/[timestamp]_[strategy]_session.json`
- Positions: Tracked in strategy internal state, fetched from exchange every 5 minutes for reconciliation

## Key Abstractions

**Strategy Interface:**
- Purpose: Pluggable decision-making logic
- Examples: `src/strategies/mirror/strategy.py`, `src/strategies/conservative/strategy.py`, `src/strategies/profit_taker/strategy.py`
- Pattern: All extend Strategy base class, implement on_event(MarketEvent) -> TradeDecision

**ExecutionAdapter Interface:**
- Purpose: Pluggable order execution backend
- Examples: `src/execution/dry_run.py` (NullExecutionAdapter), `src/execution/live.py` (LiveExecutionAdapter)
- Pattern: All implement abstract methods (place_order, cancel_order, arm/disarm)

**MarketEvent:**
- Purpose: Immutable event packet containing trade + prices
- Location: `src/data/models.py`
- Pattern: Passed through entire pipeline, strategies are stateless with respect to event history

**Configuration Objects:**
- Purpose: Centralized config without globals
- Examples: `BotConfig`, `StrategyConfig`, `LeaderConfig`, `TraderConfig`
- Pattern: Loaded from YAML, passed through dependency injection

## Entry Points

**main.py (CLI) - primary:**
- Location: `main.py`
- Triggers: User runs `python main.py [flags]`
- Responsibilities: Parse CLI args, load config, route to appropriate mode (live/dry-run/replay/optimize)

**UniversalRunner.run():**
- Location: `src/framework/runner.py`
- Triggers: Called from main.py after setup
- Responsibilities: Main event loop, polling blockchain, feeding events to strategy, coordinating execution

**SessionReplayer.replay():**
- Location: `src/framework/replay.py`
- Triggers: Called from main.py with --replay-session flag
- Responsibilities: Load JSON session file, replay events deterministically, compute final metrics

**Optimizer.run_optimization():**
- Location: `src/simulation/optimizer.py`
- Triggers: Called from main.py with --optimize flag
- Responsibilities: Grid search over strategy configs, run replayer for each variant, rank results

## Error Handling

**Strategy:** Strategies can return TradeDecision.skip() with reason if market conditions don't warrant a trade

**Execution:** Orders validated before submission; if rejected by API, logged and stats incremented, portfolio NOT updated

**Blockchain Detection:** Failures logged but don't crash runner; next poll attempt retries

**API Rate Limiting:** LiveDataSource includes rate limiting with exponential backoff

**Portfolio Invariants:** Portfolio raises PortfolioInvariantError if shares go negative or other violations detected

## Cross-Cutting Concerns

**Logging:** All modules use standard logging (handlers configured in runner initialization)

**Validation:**
- TradeDecision validates against Polymarket minimums (market order >= $1, limit order >= 5 shares)
- Portfolio validates shares never go negative, prices in valid range (0, 1)

**Authentication:**
- Live mode requires private key from environment variable
- Signature type (default 2) configured in TraderConfig

**Deduplication:**
- Content-based keys prevent replay of same trade twice: `{tx_hash}_{token_id}_{action}_{dollar_value}`
- Seen hashes persisted across restarts (24-hour window)

**Rate Limiting:**
- LiveDataSource implements token bucket rate limiting on API calls
- WebSocket auto-reconnects with exponential backoff on disconnect
