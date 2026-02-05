# Technology Stack

**Analysis Date:** 2026-02-05

## Languages

**Primary:**
- Python 3.11+ - All bot logic, strategies, analysis, and testing

**Secondary:**
- YAML - Configuration files (`config/config.yaml`)
- JSON - State persistence, session recording, logging

## Runtime

**Environment:**
- CPython 3.11+
- Platform: Windows, macOS, Linux (cross-platform)

**Package Manager:**
- pip
- Lockfile: Not detected (uses `requirements.txt` with version pinning)

## Frameworks

**Core:**
- py-clob-client 0.34+ - Polymarket CLOB API integration for live trading (`src/execution/live.py`)
- httpx 0.27+ - HTTP client for REST APIs (`src/data/live_source.py`)

**Testing:**
- pytest 8.0+ - Unit and integration testing framework
- pytest-asyncio 0.23+ - Async test support

**Analysis & Reporting:**
- pandas 2.0+ - Data manipulation and analysis (`src/analysis/`, `src/comparison/`)
- matplotlib 3.8+ - Chart generation (`src/analysis/reports.py`)
- plotly 5.0+ - Interactive visualizations (`src/comparison/visualizer.py`)
- numpy 1.24+ - Numerical computations (`src/analysis/`)
- scipy 1.11+ - Scientific computing (`src/analysis/`)
- quantstats 0.0.81+ - Portfolio performance metrics (`src/analysis/reports.py`)
- empyrical-reloaded 0.5.11+ - Financial risk metrics

**Configuration:**
- PyYAML 6.0+ - YAML parsing (`src/core/config.py`)
- python-dotenv 1.0+ - Environment variable loading (`.env` file support)

**WebSockets:**
- websockets 12.0+ - Optional WebSocket support for price feeds (`src/data/ws_price.py`)

**Development Tools:**
- mypy 1.8+ - Static type checking
- ruff 0.2+ - Linting (fast Python linter)

## Key Dependencies

**Critical:**
- py-clob-client - Required for LIVE mode order placement; optional for DRY_RUN
- httpx - Required for all API data fetching from Polymarket
- pandas - Required for all analysis, comparison, and reporting modules
- PyYAML - Required for config file loading

**Infrastructure:**
- No external databases - Uses local JSON/JSONL for state and session persistence
- No cloud dependencies - Fully self-contained deployable application

## Configuration

**Environment:**
- `.env` file for secrets (not committed to git)
- `config.yaml` in `config/` directory for bot parameters
- Environment variable overrides for deployment flexibility:
  - `POLYMARKET_PRIVATE_KEY` - Private key for order signing (LIVE mode)
  - `POLYMARKET_FUNDER_ADDRESS` - Wallet address for trading (LIVE mode)
  - `POLYMARKET_SIGNATURE_TYPE` - Signature type: 0 (EOA), 1 (POLY_PROXY), 2 (GNOSIS_SAFE)
  - `DATA_DIR` - Override default data directory
  - `LOG_LEVEL` - Override logging level

**Build:**
- No build process - Pure Python, direct execution
- Entry point: `main.py`
- Module structure uses relative imports with project root on sys.path

## Platform Requirements

**Development:**
- Python 3.11+
- pip package manager
- Git (for version control)

**Production (LIVE mode):**
- Python 3.11+
- Polymarket account with wallet funded in USDC
- Private key and funder address for trading wallet
- Network access to Polymarket CLOB API (`https://clob.polymarket.com`)
- Network access to Polymarket data API (`https://data-api.polymarket.com`)

**Production (DRY_RUN mode):**
- Python 3.11+
- Network access to read-only Polymarket APIs
- No wallet/funding required

## API Endpoints (Polymarket)

**CLOB API:**
- Host: `https://clob.polymarket.com`
- Chain ID: 137 (Polygon)
- Used for order placement (LIVE) and market data

**Data API:**
- Host: `https://data-api.polymarket.com`
- Endpoints:
  - `/positions` - Fetch wallet positions (used in `src/data/live_source.py`)
  - `/trades` - Fetch recent trades (used in `src/data/live_source.py`)
  - `/markets` - Market metadata (optional)

**WebSocket:**
- URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Purpose: Real-time price feeds (optional optimization; polling is default)
- Used in: `src/data/ws_price.py`

---

*Stack analysis: 2026-02-05*
