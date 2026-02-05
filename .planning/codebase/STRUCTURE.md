# Codebase Structure

**Analysis Date:** 2026-02-05

## Directory Layout

```
polybotestv2/
├── main.py                  # Primary CLI entry point
├── requirements.txt         # Python dependencies
├── README.md               # Project documentation
├── .env.example            # Environment variable template
├── config/                 # Configuration directory
│   ├── config.yaml         # Main bot configuration (user-edited)
│   └── config.example.yaml # Configuration template
├── data/                   # Runtime data directory
│   ├── state/              # Persistent state files
│   │   ├── seen_hashes.json
│   │   └── portfolio_state.json
│   └── sessions/           # Recorded session data (JSON)
├── logs/                   # Log files (generated at runtime)
├── src/                    # Main source code
│   ├── framework/          # Core orchestration (runner, replay, recording)
│   ├── strategies/         # Strategy implementations
│   ├── execution/          # Execution adapters (live vs dry-run)
│   ├── data/               # Data sources (APIs, blockchain, websocket)
│   ├── core/               # Core services (portfolio, config, types)
│   ├── analysis/           # Post-session analysis
│   ├── comparison/         # Strategy comparison tools
│   ├── simulation/         # Optimization and testing
│   ├── statistics/         # Statistical utilities
│   ├── tools/              # Utility scripts
│   ├── validation/         # Data validation
│   └── __init__.py
├── tests/                  # Unit tests
│   └── unit/               # Unit test files
├── docs/                   # Generated documentation
└── .planning/              # GSD planning documents
```

## Directory Purposes

**Root level scripts:**
- `main.py`: Primary entry point, handles CLI routing to live/dry-run/replay/optimize modes
- `run_test.py`, `run_comparison.py`, `check_profit_taker.py`, `optimize_profit_taker.py`: Utility scripts for testing specific strategies

**config/**
- Purpose: Store user configuration and examples
- Contains: YAML configuration files
- Key files: `config/config.yaml` (user creates from example), `config/config.example.yaml`
- Note: config.yaml is gitignored, each user/environment maintains their own

**data/**
- Purpose: Runtime-generated data storage
- Contains: State persistence, session recordings, logs
- `data/state/`: Persistent data across runs (seen trade hashes, portfolio snapshots)
- `data/sessions/`: Recorded session files (JSON) for replay and analysis
- Note: Directory auto-created at startup if missing

**src/framework/**
- Purpose: Core orchestration and lifecycle management
- Contains: UniversalRunner (main event loop), SessionReplayer (replay engine), SessionRecorder (session persistence)
- Key files:
  - `src/framework/runner.py`: Main loop, handles data polling, strategy calls, execution
  - `src/framework/replay.py`: Replays sessions deterministically
  - `src/framework/recorder.py`: Records events and prices for later analysis

**src/strategies/**
- Purpose: Pluggable strategy implementations
- Contains: Base class and 9+ concrete strategies
- Directory structure: Each strategy in its own directory with `strategy.py` and optional `config.py`
  - `src/strategies/mirror/`: Copy leader exactly
  - `src/strategies/conservative/`: Size down conservative positions
  - `src/strategies/aggressive/`: Scale up with confidence
  - `src/strategies/profit_taker/`: Take profits at thresholds
  - `src/strategies/momentum/`: Follow trend momentum
  - `src/strategies/velocity/`: Based on position velocity
  - `src/strategies/spread_aware/`: Adjust for market spread
  - `src/strategies/price_level/`: Entry/exit based on price levels
  - `src/strategies/hybrid_conservative/`: Mixed approach
  - `src/strategies/simple_follow/`: Basic following with stops
- Key files:
  - `src/strategies/base.py`: Strategy interface and TradeDecision class
  - `src/strategies/__init__.py`: Strategy registry and get_strategy() factory

**src/execution/**
- Purpose: Pluggable execution backends
- Contains: ExecutionAdapter interface, dry-run and live implementations
- Key files:
  - `src/execution/base.py`: Abstract ExecutionAdapter interface
  - `src/execution/dry_run.py`: NullExecutionAdapter (returns SIMULATED status, no real orders)
  - `src/execution/live.py`: LiveExecutionAdapter (hits Polymarket API, requires private key)
  - `src/execution/hybrid.py`: Hybrid execution mode
  - `src/execution/__init__.py`: Exports adapter classes

**src/data/**
- Purpose: External data sources
- Contains: Polymarket API client, blockchain detector, WebSocket price service
- Key files:
  - `src/data/live_source.py`: HTTP client for Polymarket REST API (positions, trades)
  - `src/data/blockchain_detector.py`: Polls Polygon blockchain for leader transactions
  - `src/data/ws_price.py`: WebSocket connection to Polymarket for real-time bid/ask
  - `src/data/models.py`: Data model definitions (MarketEvent, LeaderTrade, etc)
  - `src/data/__init__.py`: Re-exports models

**src/core/**
- Purpose: Core trading logic and configuration
- Contains: Portfolio tracking, capital management, configuration, type definitions
- Key files:
  - `src/core/portfolio.py`: Portfolio class (tracks positions, computes PnL)
  - `src/core/capital_manager.py`: Capital allocation logic
  - `src/core/kelly_engine.py`: Kelly criterion position sizing
  - `src/core/config.py`: Config loading from YAML
  - `src/core/types.py`: OrderRequest, OrderResponse, Side, ExecutionMode
  - `src/core/sizing.py`: Position sizing utilities
  - `src/core/adaptive_sizer.py`: Adaptive sizing based on edge
  - `src/core/edge_tracker.py`: Track realized edges
  - `src/core/conviction.py`: Conviction scoring
  - `src/core/trade_filter.py`: Trade filtering logic
  - `src/core/trade_ranker.py`: Rank trades by quality
  - `src/core/clock.py`: Simulated clock for testing

**src/analysis/**
- Purpose: Post-trade analysis and metrics
- Contains: Equity curve tracking, P&L attribution, risk metrics
- Key files:
  - `src/analysis/equity_tracker.py`: Track portfolio equity over time
  - `src/analysis/drawdown.py`: Compute max drawdown, recovery analysis
  - `src/analysis/slippage.py`: Measure execution slippage vs decision price
  - `src/analysis/attribution.py`: P&L attribution to strategy decisions
  - `src/analysis/reports.py`: Generate summary reports from session data

**src/comparison/**
- Purpose: Compare multiple strategy runs
- Contains: Metrics computation, tear sheets, decision matrices
- Key files:
  - `src/comparison/metrics.py`: Compute returns, Sharpe, etc
  - `src/comparison/tear_sheets.py`: Generate HTML tear sheets
  - `src/comparison/visualizer.py`: Plot strategy comparisons
  - `src/comparison/comparator.py`: Compare multiple runs

**src/simulation/**
- Purpose: Strategy optimization and testing
- Contains: Parameter optimization, full testing harness
- Key files:
  - `src/simulation/optimizer.py`: Grid search over strategy parameters
  - `src/simulation/full_optimizer.py`: Exhaustive Cartesian product testing

**src/statistics/**
- Purpose: Statistical utilities
- Contains: Distribution fitting, correlation analysis, etc

**src/tools/**
- Purpose: Standalone utility tools
- Contains: Portfolio visualization, trade analysis tools

**src/validation/**
- Purpose: Data validation and sanity checks
- Contains: Event validation, trade validation

**tests/unit/**
- Purpose: Unit tests
- Contains: Tests for individual components
- Key files:
  - `tests/unit/test_strategies.py`: Strategy behavior tests
  - `tests/unit/test_simple_follow.py`: Simple follow strategy tests

## Key File Locations

**Entry Points:**
- `main.py`: Main CLI orchestrator, route to all modes
- `src/framework/runner.py`: UniversalRunner.run() - main event loop

**Configuration:**
- `src/core/config.py`: BotConfig, load_config() function
- `config/config.yaml`: User configuration (not in git)

**Core Logic:**
- `src/strategies/base.py`: Strategy base class, TradeDecision
- `src/core/portfolio.py`: Portfolio state tracking
- `src/execution/base.py`: ExecutionAdapter interface

**Data Models:**
- `src/data/models.py`: MarketEvent, LeaderTrade, PriceSnapshot
- `src/core/types.py`: OrderRequest, OrderResponse, Side, ExecutionMode

**Testing & Optimization:**
- `src/framework/replay.py`: SessionReplayer for deterministic replay
- `src/simulation/optimizer.py`: Grid search optimization
- `src/analysis/equity_tracker.py`: P&L computation for results

## Naming Conventions

**Files:**
- Module names: lowercase with underscores (`config.py`, `live_source.py`, `kelly_engine.py`)
- Strategy implementations: One strategy per directory, main file is `strategy.py`
- Test files: Match source module name with `test_` prefix (`test_strategies.py`)

**Directories:**
- Functional grouping: `src/strategies/`, `src/execution/`, `src/data/`, `src/core/`
- One strategy per subdirectory: `src/strategies/mirror/`, `src/strategies/conservative/`

**Classes:**
- Pascal case for classes: `UniversalRunner`, `LiveDataSource`, `SessionRecorder`
- Exception classes end with Error: `PortfolioInvariantError`
- Dataclass models: `MarketEvent`, `LeaderTrade`, `OrderRequest`, `PortfolioPosition`

**Functions/Methods:**
- Snake case: `on_event()`, `place_order()`, `fetch_positions()`
- Private methods start with underscore: `_init()`, `_cycle()`, `_save_state()`
- Magic methods double underscore: `__post_init__()` (dataclass hook)

**Constants:**
- All caps: `MIN_MARKET_ORDER_DOLLARS`, `HOUR_END_THRESHOLD_SEC`, `EXTREME_HIGH_PRICE`
- Grouped at module top after imports

## Where to Add New Code

**New Strategy:**
1. Create directory: `src/strategies/[strategy_name]/`
2. Create file: `src/strategies/[strategy_name]/strategy.py` extending Strategy base
3. Register in: `src/strategies/__init__.py` with import and optional register_strategy() call
4. Add tests: `tests/unit/test_[strategy_name].py`

**New Execution Mode:**
1. Create file: `src/execution/[mode].py`
2. Implement ExecutionAdapter interface from `src/execution/base.py`
3. Export from: `src/execution/__init__.py`
4. Route from: `main.py` when user selects mode

**New Analysis Metric:**
1. Add to: `src/analysis/[metric_name].py` (or add function to existing file)
2. Import in: `src/analysis/__init__.py`
3. Call from: SessionReplayer or ReplayResult processing in `src/framework/replay.py`

**New Data Source:**
1. Create file: `src/data/[source_name].py`
2. Follow LiveDataSource pattern (rate limiting, error handling, caching)
3. Wire into: UniversalRunner._init() in `src/framework/runner.py`

**New Core Service:**
1. Create file: `src/core/[service].py`
2. Dependency inject into: UniversalRunner or strategies
3. Add configuration if needed: Add to BotConfig in `src/core/config.py`

**Utility Scripts:**
1. Root level Python files: `src_[feature].py`
2. Import from src/ modules normally
3. Entry point: if __name__ == "__main__": main()

## Special Directories

**data/state/:**
- Purpose: Persistent runtime state across restarts
- Generated: Yes (auto-created at startup)
- Committed: No (in .gitignore)
- Contents: JSON files with trade hashes and portfolio snapshots

**data/sessions/:**
- Purpose: Recorded session data for replay and analysis
- Generated: Yes (when --record flag used)
- Committed: No (in .gitignore)
- Contents: JSON files with event sequences, prices, decisions for each session

**config/**
- Purpose: Configuration files
- Generated: No (user-created from examples)
- Committed: Only .example.yaml files; actual config.yaml is gitignored
- Note: Users must copy config.example.yaml to config.yaml and customize

**logs/**
- Purpose: Application logs
- Generated: Yes (if logging configured to write files)
- Committed: No (in .gitignore)
- Rotation: Not currently implemented, files grow indefinitely
