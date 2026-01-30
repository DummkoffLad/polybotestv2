# External Integrations

**Analysis Date:** 2026-01-30

## APIs & External Services

**Polymarket Trading:**
- Polymarket CLOB API - Order placement, cancellation, execution
  - SDK/Client: `py-clob-client>=0.34`
  - Implementation: `src/execution/live.py:LiveExecutionAdapter`
  - Host: `https://clob.polymarket.com`
  - Chain ID: 137 (Polygon)
  - Auth: Wallet private key signing (configurable signature type)

**Polymarket Data:**
- Polymarket Data API - Read positions, trades, market metadata
  - SDK/Client: `httpx>=0.27` (raw HTTP)
  - Implementation: `src/data/live_source.py:LiveDataSource`
  - Base URL: `https://data-api.polymarket.com`
  - Endpoints:
    - `GET /positions?user={address}&limit=500` - Fetch wallet positions
    - `GET /trades?user={address}&limit={limit}` - Fetch trade history
  - Rate limiting: 0.1s per positions call, 0.05s per trades call

## Data Storage

**Databases:**
- Not applicable - No external database used
- Local persistence only (JSON/JSONL files)

**File Storage:**
- Local filesystem only
  - Session recordings: `data/sessions/*.jsonl`
  - State files: `data/state/*.json`
  - Logs: `data/logs/*.jsonl`
  - Traces: `data/traces/*.jsonl`

**Caching:**
- In-memory caching of discovered markets (`src/data/live_source.py:_discovered_markets`)
- Persisted seen transaction hashes: `data/state/seen_hashes.json`
- No external cache services (Redis, Memcached)

## Authentication & Identity

**Auth Provider:**
- Self-managed wallet signing (no third-party auth)

**Implementation:**
- Wallet private key signing via `py-clob-client`
- Three signature types supported:
  - `0` = EOA (standard externally owned account, requires POL for gas)
  - `1` = POLY_PROXY (Magic Link email wallet)
  - `2` = GNOSIS_SAFE (most common, browser wallet like MetaMask)
- Signature type set in config: `trader.signature_type` (default 2)

**Secret Management:**
- Private key stored in `.env` file (never in `config.yaml`)
- Loaded via `python-dotenv` before startup
- Environment variable: `POLYMARKET_PRIVATE_KEY` (configurable as `trader.private_key_env`)
- Funder address: `POLYMARKET_FUNDER_ADDRESS` environment variable or config

## Monitoring & Observability

**Error Tracking:**
- Not integrated with external service
- Errors logged to local JSONL files
- Structured logging in `src/logging/` (if present)

**Logs:**
- Local file-based logging (JSONL format)
- Log path: `data/logs/bot.jsonl` (configurable)
- Log level: DEBUG, INFO, WARNING, ERROR (configurable)
- Log rotation: Max 50MB per file, 5 backups (configurable)
- Types of logs:
  - Bot operations (trades, errors, state changes)
  - Decision traces (per-decision JSON records)
  - API interactions (httpx requests/responses via logging)

**Structured Logging:**
- Format: JSON Lines (one JSON object per line)
- Includes timestamp, level, message, metadata
- Decision traces logged separately to `data/traces/decisions.jsonl`

## CI/CD & Deployment

**Hosting:**
- Self-hosted (no cloud platform lock-in)
- Runs locally or on user's server
- Supports Windows, Linux, macOS

**CI Pipeline:**
- Not configured (pytest available but no GitHub Actions/GitLab CI)
- Manual testing: `pytest tests/ -v`

**Deployment:**
- Direct execution: `python main.py --mode live`
- No containerization (Docker/Kubernetes) currently
- No deployment automation

## Environment Configuration

**Required env vars:**
- `POLYMARKET_PRIVATE_KEY` - Wallet signing key (LIVE only)
- `POLYMARKET_FUNDER_ADDRESS` - Wallet address for order placement (LIVE only)
- `POLYMARKET_SIGNATURE_TYPE` - Signature type: 0, 1, or 2 (default 2)

**Optional env vars:**
- `HTTP_PROXY`, `HTTPS_PROXY` - Proxy configuration
- `LOG_LEVEL` - Override config file log level
- `DATA_DIR` - Override default `data/` directory

**Secrets location:**
- `.env` file (gitignored, never committed)
- Template: `.env.example` (shows required vars)
- Loaded once at startup via `python-dotenv`

## Webhooks & Callbacks

**Incoming:**
- None - Bot reads data via polling

**Outgoing:**
- None currently implemented
- Bot operates on poll-based state reconciliation, not webhooks
- Future enhancement: WebSocket subscriptions for real-time market data

## Order Execution Flow

**Place Order (LIVE only):**
1. Strategy calls `execution.place_order(OrderRequest)`
2. `LiveExecutionAdapter.place_order()` validates request
3. Fetches current midpoint: `GET https://clob.polymarket.com/midpoint?token_id=...`
4. Calculates order price (market order with 5¢ adjustment)
5. Creates order via `py-clob-client.ClobClient.create_order(OrderArgs)`
6. Signs order with private key (signature type configurable)
7. POSTs signed order: `POST https://clob.polymarket.com/order` (FOK - Fill or Kill)
8. Returns `OrderResponse` with status (FILLED, REJECTED, PENDING)

**Order Types:**
- Market orders only (BUY at ask, SELL at bid)
- No limit orders implemented
- Order execution type: FOK (Fill or Kill - all or nothing)

## Data Discovery & Market Detection

**Market Discovery (Live Mode):**
- Reads leader positions from Polymarket API
- Extracts market metadata (condition_id, outcome, title)
- Filters to "hourly up/down" crypto markets only
- Implementation: `src/data/live_source.py:is_hourly_updown_market()`

**Position & Trade Fetching:**
- `fetch_positions(address)` - Get all open positions for an address
- `fetch_trades(address, limit)` - Get recent trade history
- Rate-limited to prevent API throttling

---

*Integration audit: 2026-01-30*
