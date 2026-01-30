# Codebase Structure

**Analysis Date:** 2026-01-30

## Directory Layout

```
polybotestv2/
├── main.py                 # Entry point: CLI argument parsing, mode dispatch
├── compare_trades.py       # Utility: compare leader vs follower trades
├── requirements.txt        # Python dependencies
├── README.md              # Project documentation
├── .env.example           # Environment variable template
├── config/                # Configuration files
│   └── config.example.yaml  # Example config (copy to config.yaml)
├── data/                  # Runtime data and state
│   ├── sessions/          # Recorded session JSONL files (created by --record)
│   ├── state/             # Persisted state (seen_hashes.json, portfolio_state.json)
│   ├── logs/              # Log files
│   ├── collected/         # Deprecated: old data collection output
│   └── traces/            # Execution traces
├── logs/                  # Separate logs directory
├── .planning/             # GSD planning documents
│   └── codebase/          # Architecture and structure analysis
├── tests/                 # Test suite
│   ├── integration/       # Integration tests (smoke tests, end-to-end)
│   ├── unit/              # Unit tests (removed during refactor)
│   └── tools/             # Test utilities
└── src/                   # Main source code
    ├── __init__.py
    ├── core/              # Core abstractions and types
    ├── data/              # Data fetching and models
    ├── execution/         # Order execution adapters
    ├── framework/         # Main run loop and replay
    ├── simulation/        # Optimization and backtesting
    └── strategies/        # Trading strategy implementations
```

## Directory Purposes

**src/core/:**
- Purpose: Shared types, configuration, and utilities used by all layers
- Contains:
  - `types.py` - Enums (ExecutionMode, Side, OrderType, OrderStatus) and dataclasses (OrderRequest, OrderResponse, Exposure)
  - `config.py` - Configuration loading from YAML (LeaderConfig, TraderConfig, ScalingConfig, MirrorStrategyConfig, BotConfig)
  - `portfolio.py` - Portfolio tracking with position and PnL management (Portfolio, PortfolioPosition)
  - `clock.py` - Time abstraction (SystemClock for live, SimulatedClock for deterministic replay)
- Key files: `types.py` (shared enum/dataclass definitions), `config.py` (loads config/config.yaml)

**src/data/:**
- Purpose: Fetch leader activity and market data from external APIs
- Contains:
  - `live_source.py` - Polymarket API client (positions, trades, market discovery)
  - `blockchain_detector.py` - Polygon blockchain event listener (detects leader transactions)
  - `ws_price.py` - WebSocket price feed subscriber (bid/ask quotes)
  - `models.py` - Data models (LeaderTrade, PriceSnapshot, MarketEvent, PolymarketPosition, PolymarketTrade)
- Key files: `models.py` (immutable data classes), `live_source.py` (API calls)

**src/execution/:**
- Purpose: Abstract order placement to support different execution modes
- Contains:
  - `base.py` - ExecutionAdapter abstract interface
  - `dry_run.py` - NullExecutionAdapter (logs trades without placing, DRY_RUN mode)
  - `live.py` - LiveExecutionAdapter (places real orders via py-clob-client, LIVE mode)
  - `hybrid.py` - Alternative adapter (not currently used)
- Key files: `base.py` (interface contract), `dry_run.py` (safe stub), `live.py` (API integration)

**src/framework/:**
- Purpose: Main application logic (run loop, recording, replay)
- Contains:
  - `runner.py` - UniversalRunner (polls blockchain, emits events to strategy, executes orders)
  - `recorder.py` - SessionRecorder (writes events + price snapshots to JSONL)
  - `replay.py` - SessionReplayer (reads JSONL, replays through any strategy)
- Key files: `runner.py` (main loop), `recorder.py` (persistence layer)

**src/simulation/:**
- Purpose: Offline backtesting and strategy optimization
- Contains:
  - `optimizer.py` - Grid search over strategy parameters
  - `full_optimizer.py` - Tests all strategies × all execution modes
  - `limit_order_sim.py` - Simulates limit order fill logic
  - `follow_metrics.py` - Calculates strategy quality metrics
- Key files: `optimizer.py` (parameter tuning), `full_optimizer.py` (comprehensive testing)

**src/strategies/:**
- Purpose: Trading strategy implementations
- Contains: Strategy implementations organized by type
  - `base.py` - Strategy abstract interface and StrategyConfig
  - `mirror/` - Mirror strategy (copies leader with scaling)
  - `momentum/` - Momentum strategy
  - `conservative/` - Conservative variant
  - `aggressive/` - Aggressive variant
  - `spread_aware/` - Spread-aware pricing
  - `velocity/` - Velocity-based sizing
  - `price_level/` - Price level targeting
  - `hybrid_conservative/` - Hybrid approach
- Key files: `base.py` (Strategy abstract class, register_strategy decorator), strategy subdirs (implementations)

**config/:**
- Purpose: Configuration files
- Contains: `config.example.yaml` (template with all settings)
- Usage: Copy to `config/config.yaml` and customize before running

**data/:**
- Purpose: Runtime data storage
- Subdirectories:
  - `sessions/` - Recorded sessions (JSONL files, one per `--record` run)
  - `state/` - Persisted state (seen_hashes.json for dedup, portfolio_state.json on shutdown)
  - `logs/` - Log files from runs
- Not committed to git (in .gitignore)

**tests/:**
- Purpose: Test suite
- Directories:
  - `integration/` - Integration tests (smoke tests for dry-run mode)
  - `unit/` - Unit tests (mostly removed during refactor)
  - `tools/` - Test utilities (trade verification scripts)
- Not committed by default (in .gitignore)

## Key File Locations

**Entry Points:**
- `main.py` - CLI entry point (parse args, load config, dispatch to execution path)

**Configuration:**
- `config/config.example.yaml` - Example configuration with all settings
- `.env.example` - Environment variable template (copy to .env for secrets)

**Core Logic:**
- `src/framework/runner.py` - Main event loop and orchestration
- `src/strategies/mirror/strategy.py` - Default mirror strategy implementation
- `src/execution/base.py` - Execution adapter interface

**Data Handling:**
- `src/data/live_source.py` - Polymarket API client
- `src/data/models.py` - Data model definitions
- `src/core/portfolio.py` - Position and PnL tracking

**Testing & Replay:**
- `src/framework/replay.py` - Replay recorded sessions
- `src/framework/recorder.py` - Record sessions to disk
- `src/simulation/optimizer.py` - Strategy optimization

## Naming Conventions

**Files:**
- Module names: snake_case (e.g., `live_source.py`, `blockchain_detector.py`)
- Strategy implementations: `strategy.py` in strategy subdirectory (e.g., `src/strategies/mirror/strategy.py`)
- Test files: `test_*.py` (e.g., `test_dry_run_smoke.py`)

**Directories:**
- Package names: lowercase (e.g., `src/data`, `src/strategies`)
- Strategy subdirectories: lowercase strategy name (e.g., `mirror/`, `aggressive/`, `conservative/`)
- Data directories: descriptive lowercase (e.g., `sessions/`, `state/`)

**Classes:**
- Abstract base classes: Strategy, ExecutionAdapter (defined in `base.py`)
- Concrete classes: PascalCase with descriptive names (MirrorStrategy, LiveExecutionAdapter, BlockchainDetector)
- Data classes: PascalCase (LeaderTrade, PriceSnapshot, OrderRequest, PortfolioPosition)

**Functions:**
- Public functions: snake_case (e.g., `get_strategy()`, `load_config()`)
- Private methods: leading underscore (e.g., `_init()`, `_cycle()`, `_make_event()`)

**Constants:**
- All caps with underscores (e.g., `MIN_MARKET_ORDER_DOLLARS`, `PRICE_EXTREME_HIGH`)
- Defined in module where used or in `base.py` for strategy constants

## Where to Add New Code

**New Strategy:**
- Create directory: `src/strategies/{strategy_name}/`
- Create files:
  - `src/strategies/{strategy_name}/__init__.py` (import and register strategy)
  - `src/strategies/{strategy_name}/strategy.py` (implement Strategy interface)
- Add to `src/strategies/__init__.py` imports
- Use `@register_strategy` decorator on class
- Reference: `src/strategies/mirror/strategy.py`

**New Execution Adapter:**
- Create file: `src/execution/{adapter_name}.py`
- Implement ExecutionAdapter interface from `src/execution/base.py`
- Add instantiation logic to `main.py` mode dispatch
- Reference: `src/execution/live.py`

**New Data Source:**
- Create file: `src/data/{source_name}.py`
- Implement interface expected by `UniversalRunner._init()` (see `live_source.py`)
- Update runner to initialize new source if needed
- Reference: `src/data/live_source.py`

**Utilities/Helpers:**
- Shared helpers: `src/core/` (if used by multiple layers)
- Strategy-specific: `src/strategies/{strategy_name}/` (keep with strategy)
- Data processing: `src/data/` (if used by multiple strategies)

**Tests:**
- Integration tests: `tests/integration/test_{feature}.py`
- Unit tests: `tests/unit/test_{module}.py`
- Test utilities: `tests/tools/{utility}.py`

## Special Directories

**data/sessions/:**
- Purpose: Store recorded trading sessions (JSONL files)
- Generated: Yes (by `SessionRecorder` when `--record` flag used)
- Committed: No (in .gitignore)
- Format: JSONL (one JSON object per line)
- Lifecycle: Created during `--mode dry-run --record`, replayed with `--replay-session`

**data/state/:**
- Purpose: Persist state between runs
- Generated: Yes (by `UniversalRunner._shutdown()`)
- Committed: No (in .gitignore)
- Files:
  - `seen_hashes.json` - Transaction hashes seen (prevents duplicate trades)
  - `portfolio_state.json` - Portfolio positions and stats
- Lifecycle: Loaded on startup, updated on shutdown

**.planning/codebase/:**
- Purpose: GSD architecture documentation
- Generated: Yes (by `/gsd:map-codebase`)
- Committed: Yes (in git)
- Files: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, STACK.md, INTEGRATIONS.md, CONCERNS.md
- Lifecycle: Updated by GSD when codebase changes significantly

**tests/:**
- Purpose: Test suite
- Generated: No (hand-written)
- Committed: No (in .gitignore)
- Note: Most tests were removed during refactor; only smoke test remains

---

*Structure analysis: 2026-01-30*
