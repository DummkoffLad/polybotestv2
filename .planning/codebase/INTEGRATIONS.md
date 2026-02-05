# External Integrations

**Analysis Date:** 2026-02-05

## APIs & External Services

**Polymarket CLOB (Copy-Trading):**
- Service: Polymarket Decentralized CLOB (Central Limit Order Book)
- What it's used for: Live order placement, market data fetching, position tracking
  - SDK/Client: `py-clob-client` 0.34+
  - Implementation: `src/execution/live.py` (LiveExecutionAdapter)
  - Host: `https://clob.polymarket.com`
  - Chain: Polygon (chain_id=137)
  - Auth: ECDSA signature (private key signing)
  - Signature types supported: EOA, POLY_PROXY, GNOSIS_SAFE

**Polymarket Data API:**
- Service: REST API for historical and position data
- What it's used for: Fetching leader positions, trade history, market metadata
  - SDK/Client: `httpx` (HTTP client)
  - Implementation: `src/data/live_source.py` (LiveDataSource)
  - Host: `https://data-api.polymarket.com`
  - Endpoints:
    - `GET /positions?user={address}&limit=500` - Fetch positions
    - `GET /trades?user={address}&limit=100` - Fetch trades
  - Rate limiting: Built-in with `_rate_limit()` method to prevent throttling

**Polymarket WebSocket Feed (Price Subscriptions):**
- Service: Real-time market price updates
- What it's used for: Live bid/ask prices for trading (optional optimization)
  - SDK/Client: `websockets` 12.0+
  - Implementation: `src/data/ws_price.py` (WebSocketPriceService)
  - URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - Purpose: Real-time price subscription (default is polling API)
  - Stale threshold: 30 seconds

## Data Storage

**Databases:**
- None - No external database
- Local: JSONL files in `data/` directory
- Format: JSON Lines (newline-delimited JSON)

**File Storage:**
- Local filesystem only
- Directories:
  - `data/sessions/` - Recorded trading sessions (JSONL)
  - `data/state/` - Bot state persistence (JSON)
  - `data/reports/` - Generated reports and charts
  - `data/collected/` - Collected market data during operation
  - `config/` - Configuration files (YAML)
  - `logs/` - Bot execution logs (JSONL)

**State Persistence:**
- `data/state/seen_hashes.json` - Transaction hash deduplication (24-hour cache)
- `data/state/portfolio_state.json` - Portfolio state on shutdown/recovery
- Implemented in: `src/framework/runner.py` (UniversalRunner._load_seen_hashes, _save_state)

**Caching:**
- None - No external caching service
- In-memory market info cache in LiveDataSource._market_info

## Authentication & Identity

**Auth Provider:**
- Polymarket Web3 wallets (self-custodial)
- Types supported:
  - EOA (Externally Owned Account) - Direct wallet
  - POLY_PROXY - Magic link email login
  - GNOSIS_SAFE - Browser wallets (MetaMask, etc.) - most common

**Implementation:**
- Private key signing: ECDSA signature in `src/execution/live.py`
- Environment variable: `POLYMARKET_PRIVATE_KEY` (from .env file)
- Funder address: `POLYMARKET_FUNDER_ADDRESS` (wallet holding USDC)
- Signature type: `POLYMARKET_SIGNATURE_TYPE` (0, 1, or 2)
- No OAuth/centralized auth - Direct blockchain signing

## Monitoring & Observability

**Error Tracking:**
- None - No external error tracking service (Sentry, etc.)
- All errors logged locally to `data/logs/bot.jsonl`

**Logs:**
- Local JSONL logging (JSON lines format for machine readability)
- Paths:
  - `data/logs/bot.jsonl` - Main bot execution log
  - `data/traces/decisions.jsonl` - Decision trace for strategy analysis
- Logging implementation: Python `logging` module with custom handlers
- Controlled in config: `logging.level`, `logging.output`, `logging.file_path`
- Log rotation: Configurable with `log_max_mb` and `log_backup_count`
- Trace rotation: Configurable with `trace_max_mb` and `trace_backup_count`

**Metrics & Analysis:**
- QuantStats: Portfolio performance analytics (`src/analysis/reports.py`)
- Empyrical: Risk metrics (Sharpe, Sortino, max drawdown)
- Custom analysis: `src/analysis/` modules for slippage, attribution, drawdown

## CI/CD & Deployment

**Hosting:**
- None configured - Self-hosted execution
- Target: Local machine or VPS with Python 3.11+
- Deployment: Manual or via script (no CI/CD pipeline detected)

**CI Pipeline:**
- None detected
- Local testing: `pytest` (run manually or via hook)
- Test discovery: `tests/unit/` directory

## Environment Configuration

**Required env vars (LIVE mode):**
- `POLYMARKET_PRIVATE_KEY` - Private key for signing orders (SECRET)
- `POLYMARKET_FUNDER_ADDRESS` - Wallet address for trading capital
- `POLYMARKET_SIGNATURE_TYPE` - Signature type (0, 1, or 2) - default: 2

**Optional env vars:**
- `DATA_DIR` - Override default data directory (default: `./data`)
- `LOG_LEVEL` - Override config log level (DEBUG, INFO, WARNING, ERROR)
- `HTTP_PROXY` / `HTTPS_PROXY` - HTTP proxy configuration
- `POLYMARKET_PRIVATE_KEY_ENV` - Alternative env var name for private key (config: `trader.private_key_env`)

**Secrets location:**
- `.env` file (NOT committed to git, added to `.gitignore`)
- Template: `.env.example` in root directory
- All secrets must be in `.env` file - NEVER in config.yaml or committed files

## Webhooks & Callbacks

**Incoming:**
- None - No webhooks consumed

**Outgoing:**
- None - No webhooks sent
- Application operates in polling mode for data fetching

## Rate Limiting & API Quotas

**Polymarket APIs:**
- Data API: Rate limiting with `_rate_limit()` method in `LiveDataSource`
  - Default implementation: Time-based throttling (requests/second)
  - Configured in `src/data/live_source.py` with hardcoded intervals
- CLOB API: Implicitly rate-limited by order placement frequency
- WebSocket: Subscription-based (no additional rate limiting)

**Circuit Breakers (Config-based):**
- Max consecutive API errors: 5 (pause if exceeded)
- Max pending orders: 3
- Max orders per minute: 10
- Max new exposure per minute: $10
- Max unknown fill duration: 30 seconds
- Configured in `config.yaml` under `circuit_breakers:`

## Market Data Sources

**Leader Discovery:**
- Polymarket API fetches leader positions and trades
- Market discovery via position snapshots
- Automatic token_id to market mapping in `src/data/live_source.py`

**Price Data:**
- REST API: Polymarket Data API (polling)
- WebSocket: Polymarket WebSocket feed (optional real-time)
- Default: Polling-first design; WebSocket is optimization
- Implementation: `src/data/live_source.py` and `src/data/ws_price.py`

## Session Recording & Replay

**Session Recording:**
- Format: JSONL (JSON Lines)
- Contents: Market events (trades, price snapshots, decisions)
- Storage: `data/sessions/{date}/{time}.jsonl`
- Purpose: Post-trade analysis, strategy backtesting, optimization
- Implementation: `src/framework/recorder.py` (SessionRecorder)

**Replay Engine:**
- Reads recorded sessions and replays strategy decisions
- Simulates executions without real orders
- Used for: Backtesting, parameter optimization, performance comparison
- Implementation: `src/framework/replay.py` (SessionReplayer)

---

*Integration audit: 2026-02-05*
