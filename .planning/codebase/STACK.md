# Technology Stack

**Analysis Date:** 2026-01-30

## Languages

**Primary:**
- Python 3.11+ - Full codebase, bot logic, strategies, simulations

## Runtime

**Environment:**
- Python 3.13.5 (tested, 3.11+ required per requirements)

**Package Manager:**
- pip - Installs dependencies from requirements.txt
- Lockfile: requirements.txt (no lock file, uses version specifiers)

## Frameworks

**Core:**
- py-clob-client 0.34+ - Polymarket CLOB trading client for order placement
- httpx 0.27+ - HTTP/2 async client for data API calls

**Configuration:**
- PyYAML 6.0+ - YAML config file parsing (`config/config.yaml`)
- python-dotenv 1.0+ - Environment variable loading from `.env`

**Testing:**
- pytest 8.0+ - Test runner
- pytest-asyncio 0.23+ - Async test support

**Development/Quality:**
- mypy 1.8+ - Type checking
- ruff 0.2+ - Linting and code formatting

**Async/Networking:**
- websockets 12.0+ - WebSocket support (optional, for future enhancements)

## Key Dependencies

**Critical:**
- py-clob-client 0.34+ - Why it matters: Core SDK for placing orders on Polymarket via CLOB API
- httpx 0.27+ - Why it matters: All Polymarket API data reads (positions, trades, market metadata)

**Infrastructure:**
- PyYAML 6.0+ - Configuration management
- python-dotenv 1.0+ - Secrets management (private keys, addresses from `.env`)
- websockets 12.0+ - Future real-time price/trade streaming

**Testing & Development:**
- pytest 8.0+ - Test execution framework
- pytest-asyncio 0.23+ - Async/await test support (async polling/data sources)
- mypy 1.8+ - Type hints validation
- ruff 0.2+ - Code linting and formatting

## Configuration

**Environment:**
- Loaded via `dotenv.load_dotenv()` in `src/core/config.py:71`
- Critical env vars: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`
- Optional: `HTTP_PROXY`, `HTTPS_PROXY`, `LOG_LEVEL`, `DATA_DIR`

**Build:**
- No build step (pure Python)
- Runtime config: `config/config.yaml` (YAML format, see `config/config.example.yaml`)
- State persistence: `data/state/` (JSON files)

## Platform Requirements

**Development:**
- Python 3.11+
- Virtual environment recommended: `python -m venv venv`
- Windows, Linux, or macOS (path handling works cross-platform)

**Production:**
- Python 3.11+
- Polymarket proxy wallet with USDC balance (LIVE mode)
- Private key for wallet signing (stored in `.env`, never in config)
- Network access to:
  - `https://data-api.polymarket.com` (positions, trades, metadata)
  - `https://clob.polymarket.com` (order placement, market data)

## Port & Network

**No explicit ports required** - All API calls are outbound HTTPS to Polymarket APIs:
- `https://data-api.polymarket.com` - REST API for reading positions/trades
- `https://clob.polymarket.com` - CLOB API for order placement and market data

**Timeout & Rate Limiting:**
- Default httpx timeout: 10.0 seconds (configurable in `config.yaml`)
- Polymarket API rate limits: 5 requests/second (configurable)
- Built-in rate limiting: positions 0.1s, trades 0.05s minimum interval

## Data Persistence

**Local Storage:**
- SQLite: Not used (built-in sqlite3 mentioned in requirements but no ORM)
- JSON files: State, seen hashes, portfolio, session recordings
  - `data/state/seen_hashes.json` - Tracks processed transaction hashes
  - `data/state/portfolio_state.json` - Strategy state persistence
  - `data/sessions/` - Recorded session data for replay
  - `data/logs/` - JSONL format structured logs
  - `data/traces/` - Decision trace files (JSONL)

**File Format:**
- JSONL (JSON Lines) for logs and traces
- JSON for state files
- YAML for configuration

---

*Stack analysis: 2026-01-30*
