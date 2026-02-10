# Technology Stack - v1.2 Production Ready

**Researched:** 2026-02-10 (Updated)
**Focus:** WebSocket order management, order lifecycle, environment hardening, codebase modularity
**Overall Confidence:** HIGH

## Executive Summary

The existing stack (py-clob-client 0.34.5, websockets 12.0+, httpx 0.27+) is **production-ready** with minimal additions. Key finding: No new libraries needed for core functionality. Focus should be on **integration patterns and error handling** rather than adding dependencies.

**Three critical capabilities to add:**
1. **WebSocket user channel** - Real-time order status updates (use existing websockets library)
2. **Order state machine** - Robust order lifecycle tracking with Polymarket-specific error handling
3. **Environment validation** - Preflight checks before live execution

**What NOT to add:**
- websocket-client (inferior to existing websockets)
- CCXT (doesn't support Polymarket)
- Database (JSONL is sufficient)
- Message queue (in-process is fine)
- Separate logging libraries (built-in logging works)

**Upgrade recommendation:** websockets 12.0 → 16.0 for latest bug fixes

---

## Core Technologies (Already Validated)

### py-clob-client
- **Current Version:** 0.34.5 (released 2026-01-13)
- **Status:** Latest version, already installed
- **Purpose:** Official Polymarket CLOB API client
- **Capabilities:**
  - Order placement: Market orders (FOK), Limit orders (GTC, GTD, FAK)
  - Order creation and ECDSA signing
  - API credential management
  - Midpoint price queries
  - Token ID resolution
- **Integration:** `src/execution/live.py` already implements order placement
- **Limitations:** Synchronous only (no native WebSocket support)

**Rationale:** Official client library from Polymarket. Synchronous API is acceptable for our use case (~$50/hour budget, not HFT). HTTP POST for order placement has acceptable latency (100-500ms total detection-to-order).

**Sources:** [py-clob-client PyPI](https://pypi.org/project/py-clob-client/), [GitHub](https://github.com/Polymarket/py-clob-client)

### websockets
- **Current Version:** 12.0 (requirements.txt)
- **Latest Version:** 16.0 (released early 2026)
- **Status:** Upgrade recommended
- **Purpose:** WebSocket connections for real-time data (market prices AND order updates)
- **Capabilities:**
  - Asyncio-native (event-driven, non-blocking)
  - Built-in ping/pong heartbeat
  - Automatic reconnection with exponential backoff
  - Memory-optimized with C extensions
  - Production-stable, handles backpressure correctly
- **Current Usage:** `src/data/ws_price.py` for market price feeds
- **New Usage:** WebSocket user channel for order status updates

**Rationale:** Industry-standard asyncio WebSocket library. Already proven in your codebase. Superior to alternatives (websocket-client is synchronous/threaded, not asyncio-native).

**Upgrade recommendation:** 12.0 → 16.0 for performance improvements and bug fixes.

**Sources:** [websockets PyPI](https://pypi.org/project/websockets/), [websockets docs](https://websockets.readthedocs.io/), [asyncio WebSocket comparison](https://superfastpython.com/asyncio-websocket-clients/)

### httpx
- **Current Version:** 0.27+
- **Status:** Sufficient, no changes needed
- **Purpose:** HTTP/2 client for REST API calls
- **Current Usage:** API polling, market data queries
- **Notes:** py-clob-client likely uses httpx or requests internally

**Rationale:** Modern HTTP client with HTTP/2 support. Works well with synchronous py-clob-client.

---

## New Capabilities (No New Libraries)

### 1. WebSocket User Channel (Order Updates)

**Technology:** Extend existing websockets library
**Endpoint:** `wss://ws-subscriptions-clob.polymarket.com/ws/user`
**Authentication:** Required (API key, secret, passphrase from py-clob-client)

**Message Format (Initial Subscribe):**
```json
{
  "type": "user",
  "markets": ["<condition_id>"],
  "auth": {
    "apiKey": "...",
    "secret": "...",
    "passphrase": "..."
  }
}
```

**Message Types Received:**
- Order matched (filled)
- Order live (on book - not used for FOK)
- Order cancelled
- Order rejected/unmatched

**Implementation Pattern:**
```python
# Create new service: src/data/ws_order.py
# Mirror pattern from WebSocketPriceService in src/data/ws_price.py
# Key differences:
# 1. Subscribe to "user" channel instead of "market"
# 2. Send auth credentials on connect
# 3. Parse order status messages instead of price updates
# 4. Maintain order_id → status mapping
```

**Best Practices (from official docs):**
- Exponential backoff on reconnect (2s, 4s, 8s... max 60s)
- Send ping every 30-60 seconds to maintain connection
- Track sequence numbers to detect missed messages
- Resubscribe to all markets on reconnect (connection drop cancels all orders)
- Handle heartbeat pings from server

**Connection Health:**
- Track last_message_time - alert if >60s stale
- Monitor reconnection count
- Log connection state changes (CONNECTED, RECONNECTING, DISCONNECTED)

**Confidence:** MEDIUM (WebSocket endpoint documented, auth flow described but not fully detailed in search results)

**Sources:** [Polymarket WSS Overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview), [WSS Quickstart](https://docs.polymarket.com/quickstart/websocket/WSS-Quickstart)

### 2. Order Lifecycle State Machine

**Technology:** Pure Python (no library needed)
**Purpose:** Track order states with Polymarket-specific error handling

**Order States:**
```
PENDING → Order created locally, not yet submitted
SUBMITTED → HTTP POST sent to API
OPEN → Order on CLOB (GTC only, not used for FOK)
FILLED → Order matched completely
REJECTED → Order rejected by API
FAILED → System error (network, exception)
CANCELLED → Order cancelled (manual or auto-cancel on disconnect)
```

**Order Types (Polymarket):**
- **FOK (Fill-or-Kill):** Execute immediately in full or cancel - CURRENT APPROACH
- **FAK (Fill-and-Kill):** Execute immediately for available shares, cancel remainder
- **GTC (Good-Till-Canceled):** Rest on book until filled or manually cancelled
- **GTD (Good-Till-Date):** GTC with expiration timestamp

**Error Handling Matrix:**

| Error | API Response | Detection | Recovery |
|-------|--------------|-----------|----------|
| Invalid price (outside 0.01-0.99) | 400 "price ... min: 0.01 - max: 0.99" | Response parsing | Clamp to [0.01, 0.99], retry once |
| Price 0.999 rejection | 400 (known bug) | Response parsing | Clamp to 0.99 instead |
| Invalid API key | 401 "Invalid api key" | Response status | CRITICAL: Disarm adapter, alert, exit |
| Invalid signature | 400 "invalid signature" | Response parsing | CRITICAL: Check signature_type config |
| PostOnly would cross | 400 "postOnly order would cross" | Response parsing | Switch to FOK or adjust price |
| Insufficient balance | 400 "insufficient balance" | Response parsing | Log warning, skip trade, continue |
| Network timeout | Exception (httpx timeout) | Exception handling | Retry with backoff (max 3 attempts) |
| Connection lost | Exception | Exception handling | Reconnect, resubscribe |

**Minimum Order Sizes (Polymarket Constraints):**
- Market orders: Minimum $1 USD
- Limit orders: Minimum 5 shares

**Implication:** Validate order size before submission to avoid API rejections.

**Confidence:** HIGH (official docs + GitHub issues provide detailed error scenarios)

**Sources:** [Polymarket Order Creation](https://docs.polymarket.com/developers/CLOB/orders/create-order), [py-clob-client Issue #218 - Price Validation](https://github.com/Polymarket/py-clob-client/issues/218), [Issue #187 - Invalid API Key](https://github.com/Polymarket/py-clob-client/issues/187), [Issue #248 - Invalid Signature](https://github.com/Polymarket/py-clob-client/issues/248)

### 3. Environment Validation (Preflight Checks)

**Technology:** New Python module `src/execution/preflight.py`
**Purpose:** Validate environment before arming live execution

**Critical Checks:**
```python
def run_preflight_checks() -> bool:
    checks = [
        check_env_vars(),           # POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS
        check_key_format(),         # 64-char hex for private key, 0x-prefixed address
        check_clob_connectivity(),  # ClobClient.get_ok() returns True
        check_api_credentials(),    # create_or_derive_api_creds() succeeds
        check_ws_market_channel(),  # Market WebSocket connectable
        check_ws_user_channel(),    # User WebSocket auth succeeds
        check_test_order(),         # Dry-run order creation (no submission)
    ]
    return all(checks)
```

**Failure Handling:**
- CRITICAL failures (missing credentials, CLOB unreachable) → Refuse to arm
- WARNING failures (WebSocket unreachable) → Arm with degraded mode (REST API fallback)
- Log all check results with timestamps

**Environment Variables:**
```bash
# Required
POLYMARKET_PRIVATE_KEY=<64-char-hex>
POLYMARKET_FUNDER_ADDRESS=0x<40-char-hex>

# Optional
POLYMARKET_SIGNATURE_TYPE=2  # Default: 2 (EOA/MetaMask)
LOG_LEVEL=INFO               # DEBUG, INFO, WARNING, ERROR
```

**Validation:**
- Private key: 64-character hex string (with or without 0x prefix)
- Funder address: 42-character 0x-prefixed hex string (Ethereum address format)
- Never log private key (mask in logs: "POLYMARKET_PRIVATE_KEY=***masked***")

**Confidence:** HIGH (standard practice from trading bot literature)

**Sources:** [AI Python Trading Bot Guide](https://academy.exmon.pro/ai-python-trading-bot-build-your-first-binanceokx-bot-2026)

---

## Codebase Modularity Improvements

### Problem: 60+ Experiment Scripts in Root

**Current State:**
```
c:\Users\santi\OneDrive\Escritorio\polybotestv2\
  analyze_all_markets.py
  analyze_following_gap.py
  analyze_gap.py
  ... (60+ more scripts)
```

**Impact:**
- Hard to find active vs deprecated code
- No clear separation of experiments vs production scripts
- Cluttered root directory

**Solution: Directory Restructure**

```
experiments/              # NEW - All exploratory analysis
  archive/               # Old experiments (keep for reference)
    analyze_*.py
    debug_*.py
    experiment_*.py
  active/                # Current active experiments
    [move any scripts still being used]

scripts/                 # NEW - Production utilities
  backtest.py           # Run backtests
  compare_strategies.py # Strategy comparison
  validate.py           # Validation pipeline
  grid_search.py        # Grid search optimizer

src/                     # Existing - Core codebase
  [unchanged]

tools/                   # Existing - Developer tools
  [src/tools/ already exists]
```

**Migration Strategy:**
1. Create `experiments/archive/` and `scripts/` directories
2. Move all analyze_*, debug_*, experiment_*, grid_search_* to experiments/archive/
3. Identify actively-used scripts → move to scripts/ with clearer names
4. Add README.md in experiments/ explaining archive purpose
5. Update docs with script inventory

**Rationale:** Clean root directory improves navigability. Clear separation of exploratory (experiments) vs production (scripts) code.

**Confidence:** HIGH (standard Python project structure)

**Sources:** [Python Monorepo Structure](https://www.tweag.io/blog/2023-04-04-python-monorepo-1/), [Building a Monorepo with Python](https://earthly.dev/blog/python-monorepo/)

### Problem: Duplicated Trade Logic Across Execution Modes

**Current State:**
- Order sizing logic in `src/execution/dry_run.py`
- Order sizing logic in `src/execution/live.py`
- Fill simulation logic in `src/simulation/limit_order_sim.py`
- Slippage estimation logic scattered

**Impact:**
- Logic drift between simulation and live execution
- Hard to maintain consistency
- Low confidence that backtests match live behavior

**Solution: Shared Trade Logic Module**

Create `src/core/trade_logic.py`:

```python
# Shared functions used by ALL execution modes

def calculate_shares_from_dollars(
    amount_dollars: Decimal,
    price: Decimal
) -> Decimal:
    """Convert dollar amount to shares at given price."""
    # Single implementation used by dry_run, live, simulation

def adjust_price_from_mid(
    mid: Decimal,
    side: Side,
    offset: Decimal = Decimal("0.05")
) -> Decimal:
    """Calculate order price from midpoint."""
    # BUY: mid + offset, SELL: mid - offset
    # Clamp to [0.01, 0.99]

def validate_order_minimums(
    order_type: OrderType,
    amount_dollars: Optional[Decimal],
    shares: Optional[Decimal]
) -> bool:
    """Validate Polymarket minimum order sizes."""
    # Market: min $1, Limit: min 5 shares

def estimate_slippage(
    token_id: str,
    side: Side,
    shares: Decimal,
    bid: Decimal,
    ask: Decimal
) -> Decimal:
    """Estimate slippage based on spread and order size."""
```

**Consumers:**
- `src/execution/dry_run.py` - Use for simulated fills
- `src/execution/live.py` - Use for real order creation
- `src/simulation/limit_order_sim.py` - Use for replay fills
- `src/framework/replay/processor.py` - Use for replay logic

**Benefit:** Single source of truth ensures simulation matches live execution.

**Confidence:** HIGH (software engineering best practice)

---

## What NOT to Add

### websocket-client
**Why NOT:**
- websocket-client is synchronous/threaded, not asyncio-native
- websockets library is superior for asyncio event loops
- Adding both creates confusion
- No benefits over existing websockets

**Decision:** Use websockets for all WebSocket needs.

**Source:** [websocket-client vs websockets](https://superfastpython.com/asyncio-websocket-clients/)

### CCXT (CryptoCurrency eXchange Trading Library)
**Why NOT:**
- CCXT supports 100+ exchanges but NOT Polymarket
- py-clob-client is Polymarket's official client
- CCXT adds abstraction overhead with no benefit

**Decision:** py-clob-client for Polymarket API.

**Source:** [CCXT GitHub](https://github.com/ccxt/ccxt) (Polymarket not in supported list)

### structlog or loguru (Structured Logging)
**Why NOT:**
- Python's built-in logging module is sufficient for current scale
- Already in use throughout codebase
- structlog adds JSON formatting but not critical for single-bot deployment
- Can add later if production monitoring requires it

**Decision:** Continue using built-in logging. Consider structlog in future if needed.

**Note:** Previous research (2026-02-09) recommended structlog. Updated decision based on "no new libraries unless critical" principle.

### PostgreSQL, SQLite, MongoDB (Database)
**Why NOT:**
- JSONL session recording works well
- No complex queries needed
- Database adds deployment complexity
- JSONL is human-readable and version-controllable

**Decision:** Continue using JSONL for session recording.

### Redis (Caching/State)
**Why NOT:**
- Single-process bot (no distributed state)
- No caching layer needed (low request volume)
- WebSocket provides real-time prices (no cache staleness)

**Decision:** No caching layer needed.

### Celery, RQ (Task Queue)
**Why NOT:**
- Single-process event loop handles all tasks
- No background jobs needed
- Order placement is request-response (no async tasks)

**Decision:** In-process event pipeline sufficient.

### Prometheus, Grafana (Monitoring)
**Why NOT:**
- Overkill for single-bot deployment
- File logs + manual analysis sufficient
- Can add later if scaling to multiple bots

**Decision:** File logging + manual monitoring.

### Sentry (Error Tracking)
**Why NOT:**
- Not critical at this stage
- Logs capture all errors
- Manual monitoring sufficient for now

**Decision:** Defer error tracking service.

---

## Polymarket-Specific Constraints

### Price Range
- **Valid:** 0.01 to 0.99 (inclusive)
- **Invalid:** 0.00, 0.999, 1.00
- **Known bug:** API rejects 0.999 despite frontend allowing it

**Implication:** Clamp all prices to [0.01, 0.99]. Round to nearest 0.01 (tick size).

**Source:** [py-clob-client Issue #218](https://github.com/Polymarket/py-clob-client/issues/218)

### Minimum Order Sizes
- **Market orders:** $1 minimum
- **Limit orders:** 5 shares minimum

**Implication:** Validate before submission. Skip trades below minimums.

### Order Types
- **FOK:** Execute immediately in full or cancel (current approach - CORRECT)
- **FAK:** Execute immediately for available, cancel remainder
- **GTC:** Rest on book until filled/cancelled
- **GTD:** GTC with expiration

**Recommendation:** Continue using FOK for market orders (matches strategy intent).

### Connection Behavior
**Critical:** If WebSocket user channel disconnects, all open orders are cancelled automatically.

**Implication:** On reconnect, must resubscribe to all markets. Do NOT assume orders persist across disconnections.

**Source:** [Polymarket WSS Overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)

### API Rate Limits
- Not explicitly documented
- Assume conservative limits

**Implication:** Implement basic rate limiting if placing multiple orders rapidly. Current volume (~$50/hour) unlikely to hit limits.

---

## Integration with Existing Code

### WebSocketPriceService Extension
**File:** `src/data/ws_price.py`
**Current:** Market channel for token prices
**Pattern:** Thread + asyncio event loop, exponential backoff, heartbeat

**Create parallel:** `src/data/ws_order.py` for user channel
**Shared pattern:**
- Threading wrapper around asyncio
- Exponential backoff (2s, 4s, 8s... max 60s)
- Ping/pong heartbeat
- Automatic reconnection
- Resubscribe on reconnect

### LiveExecutionAdapter Enhancement
**File:** `src/execution/live.py`
**Current:** FOK market order placement with basic validation

**Additions:**
1. Order state machine (track lifecycle)
2. Error handling for known API errors
3. Retry logic with exponential backoff (max 3 attempts)
4. Integration with WebSocketOrderService for fill confirmations
5. Preflight checks in `arm()` method

**Pattern:**
```python
def arm(self) -> bool:
    if not run_preflight_checks():
        return False
    if not self._init_client():
        return False
    self._armed = True
    return True

def place_order(self, request: OrderRequest) -> OrderResponse:
    # State: PENDING
    order_state = OrderState.PENDING

    # Validate
    if not self._validate_order(request):
        return OrderResponse(status=OrderStatus.REJECTED, ...)

    # Create & sign
    signed_order = self._create_signed_order(request)
    order_state = OrderState.SUBMITTED

    # Post with retry
    response = self._post_with_retry(signed_order)

    # Update state based on response
    if response.success:
        order_state = OrderState.FILLED
    else:
        order_state = OrderState.REJECTED

    return OrderResponse(...)
```

### Runner Integration
**File:** `src/framework/runner.py`
**Current:** Orchestrates data source, strategy, execution adapter

**Additions:**
1. Start WebSocket services (price + order) before strategy loop
2. Monitor connection health
3. Graceful shutdown on KeyboardInterrupt/SIGTERM
4. Close WebSocket connections cleanly

---

## Stack Updates Recommended

| Component | Current | Recommended | Priority | Reason |
|-----------|---------|-------------|----------|--------|
| websockets | 12.0 | 16.0 | Medium | Performance improvements, bug fixes |
| py-clob-client | 0.34.5 | 0.34.5 | None | Already latest |
| httpx | 0.27+ | 0.27+ | None | Works fine |
| All others | Current | Current | None | No changes needed |

**Action:** Upgrade websockets from 12.0 to 16.0

```bash
pip install --upgrade websockets>=16.0
```

Update requirements.txt:
```diff
- websockets>=12.0
+ websockets>=16.0
```

---

## Latency Considerations

**Estimated Latency Breakdown:**

| Step | Estimated Time | Bottleneck |
|------|---------------|------------|
| Blockchain event → Detection | 50-200ms | Polygon block time + polling |
| Strategy decision | <1ms | Pure Python logic |
| Order creation + signing | 1-5ms | ECDSA signature |
| HTTP POST roundtrip | 50-300ms | Network to clob.polymarket.com |
| **Total** | **100-500ms** | Network + polling |

**Is this acceptable?**
- YES - Leader's trades take time to propagate
- YES - Polymarket has matching delays
- YES - Budget (~$50/hour) doesn't require HFT speed
- YES - Synchronous HTTP POST is fine

**When to optimize:**
- IF seeing consistent order rejections due to price movement
- IF profiling shows specific hot spots
- THEN reduce polling interval, optimize WebSocket handling

**Recommendation:** Ship with current approach. Optimize only if profiling shows clear wins.

**Source:** [Python in HFT: Low-Latency Techniques](https://www.pyquantnews.com/free-python-resources/python-in-high-frequency-trading-low-latency-techniques)

---

## New Code Structure

```
src/
  core/
    trade_logic.py       # NEW - Shared order logic
  data/
    ws_price.py          # Existing - Market WebSocket
    ws_order.py          # NEW - User WebSocket for order updates
  execution/
    base.py              # Existing
    dry_run.py           # Existing
    live.py              # ENHANCE - Add state machine, error handling
    preflight.py         # NEW - Environment validation
    order_state.py       # NEW - Order state machine
  framework/
    runner.py            # ENHANCE - WebSocket lifecycle management

experiments/             # NEW - Move all exploratory scripts
  archive/              # Old experiments
  active/               # Current experiments

scripts/                # NEW - Production utilities
  backtest.py
  compare_strategies.py
  validate.py
```

---

## Configuration Additions

**config/config.yaml additions:**

```yaml
execution:
  live:
    order_timeout_sec: 10
    max_retries: 3
    retry_backoff_sec: 2
    validate_spread: true

websocket:
  heartbeat_interval_sec: 30
  reconnect_backoff_max_sec: 60
  message_stale_threshold_sec: 60

logging:
  level: INFO
  output_file: logs/bot.log
  mask_secrets: true  # Never log private keys
```

**Environment variables:**

```bash
# Required
POLYMARKET_PRIVATE_KEY=<hex>
POLYMARKET_FUNDER_ADDRESS=0x<hex>

# Optional
POLYMARKET_SIGNATURE_TYPE=2
LOG_LEVEL=INFO
```

---

## Sources

### Official Documentation
- [Polymarket CLOB Introduction](https://docs.polymarket.com/developers/CLOB/introduction)
- [Polymarket WebSocket Overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [WSS Quickstart](https://docs.polymarket.com/quickstart/websocket/WSS-Quickstart)
- [Polymarket Order Creation](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- [py-clob-client PyPI](https://pypi.org/project/py-clob-client/)

### WebSocket Libraries
- [websockets PyPI](https://pypi.org/project/websockets/)
- [websockets Documentation](https://websockets.readthedocs.io/)
- [Asyncio WebSocket Clients Guide](https://superfastpython.com/asyncio-websocket-clients/)
- [Python WebSocket Clients 2026](https://oneuptime.com/blog/post/2026-02-03-python-websocket-clients/view)
- [websocket-client vs websockets Comparison](https://github.com/python-websockets/websockets) vs [websocket-client](https://github.com/websocket-client/websocket-client)

### Trading Bot Best Practices
- [AI Python Trading Bot Guide 2026](https://academy.exmon.pro/ai-python-trading-bot-build-your-first-binanceokx-bot-2026)
- [WebSocket Trading Bot with Python](https://konstantinmb.medium.com/how-to-utilize-websockets-in-creating-a-profitable-trading-bot-with-python-5cb840e6c753)
- [Real-Time Market Data with WebSockets](https://medium.com/@emily19980210/optimizing-real-time-market-data-feeds-a-python-websocket-approach-for-us-stocks-f141781752fb)
- [Crypto Trading Bot Development 2026](https://appinventiv.com/blog/crypto-trading-bot-development/)

### Error Handling
- [py-clob-client Issue #218 - Price Validation](https://github.com/Polymarket/py-clob-client/issues/218)
- [py-clob-client Issue #187 - Invalid API Key](https://github.com/Polymarket/py-clob-client/issues/187)
- [py-clob-client Issue #248 - Invalid Signature](https://github.com/Polymarket/py-clob-client/issues/248)

### Python Project Structure
- [Python Monorepo Example](https://www.tweag.io/blog/2023-04-04-python-monorepo-1/)
- [Building a Monorepo with Python](https://earthly.dev/blog/python-monorepo/)
- [Python Monorepo Tools](https://monorepo.tools/)

### Connection Health & Monitoring
- [WebSocket Monitoring Guide](https://www.dotcom-monitor.com/blog/websocket-monitoring/)
- [WebSocket Application Health](https://oneuptime.com/blog/post/2026-02-03-python-websocket-clients/view)

---

## Confidence Assessment

| Area | Confidence | Rationale |
|------|------------|-----------|
| py-clob-client capabilities | HIGH | Official client, version verified, existing integration working |
| WebSocket library choice | HIGH | websockets is industry standard, already working in codebase |
| Order lifecycle patterns | HIGH | Official docs + GitHub issues provide clear error cases |
| Polymarket constraints | HIGH | Minimum sizes, price ranges documented + validated via issues |
| Environment validation | MEDIUM | General best practices, not Polymarket-specific documentation |
| Codebase cleanup patterns | MEDIUM | General Python best practices, no Polymarket-specific patterns |
| WebSocket user channel auth | MEDIUM | Endpoint documented, auth flow described but not fully detailed |

---

## Roadmap Implications

Based on stack research, recommended phase structure:

**Phase 1: Environment Hardening (Low Risk)**
- Preflight checks module
- Credential validation
- Connection testing
- **Why first:** Fail-fast validation prevents runtime errors

**Phase 2: WebSocket User Channel (Medium Risk)**
- Create WebSocketOrderService
- Implement authentication
- Parse order status messages
- **Why second:** Non-critical (can fall back to REST API polling)

**Phase 3: Order State Machine (Medium Risk)**
- Order lifecycle tracking
- Error classification and handling
- Retry logic with backoff
- **Why third:** Builds on WebSocket for real-time updates

**Phase 4: Live Order Placement (HIGH Risk)**
- Enable live execution
- Integration testing with small amounts
- Monitoring and alerting
- **Why fourth:** Real money at stake, needs all previous phases working

**Phase 5: Codebase Cleanup (Low Risk, Parallel)**
- Directory restructure
- Shared trade logic module
- Documentation updates
- **Why parallel/deferred:** Refactoring, no behavior change, can run alongside other phases

**Critical dependencies:**
- Phases 1-4 must be sequential (each builds on previous)
- Phase 5 can be parallel or deferred

---

**Research complete.** Existing stack is production-ready. Focus on integration patterns, error handling, and validation rather than adding new libraries.
