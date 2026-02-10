# Technology Stack - v1.2 Production Ready

**Researched:** 2026-02-09
**Focus:** WebSocket order placement, order lifecycle management, codebase modularity, latency optimization
**Overall Confidence:** HIGH

## Executive Summary

The existing stack is solid for simulation. For live trading, we need **three targeted additions**:

1. **Structured logging** (structlog) for production observability
2. **Profiling tools** (py-spy) for latency optimization
3. **WebSocket user channel** support (via py-clob-client WebSocket patterns)

**Key finding:** py-clob-client 0.34.5 is synchronous-only. Order placement via HTTP POST is acceptable for our ~$50/hour budget and Polymarket's matching delays. No asyncio rewrite needed.

**What NOT to add:**
- Async/await refactor (not needed, py-clob-client is sync)
- Database (JSON persistence sufficient for current scale)
- Message queue (single-process event loop handles our volume)
- Complex observability stack (structlog + file logs sufficient)

## Stack Additions for v1.2

### Logging: structlog

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **structlog** | 25.5.0 | Structured JSON logging | Production-ready, fully typed, supports both JSON output and pretty console. Context binding for trade IDs, order IDs, market IDs. |

**Installation:**
```bash
pip install structlog>=25.5
```

**Rationale:**
- Current: Standard library `logging` with string formatting
- Problem: Hard to query logs for specific orders, trades, markets in production
- Solution: structlog's context binding lets us attach `order_id`, `market_id`, `token_id` to logger and auto-include in all messages
- Alternatives considered:
  - **loguru** (0.7.3): Simpler API, but less structured output flexibility
  - **Standard logging with JSON formatter**: More code to maintain
  - **Verdict:** structlog is the industry standard for structured logging (in production since 2013)

**Integration points:**
- Replace `logging.getLogger(__name__)` with `structlog.get_logger()`
- Add context binding in `place_order()`: `log = log.bind(order_id=order_id, token_id=token_id)`
- Configure dual output: JSON to file (production), pretty to console (development)
- File: `src/core/logging.py` for centralized config

**Confidence:** HIGH (official docs, version verified via PyPI)

### Profiling: py-spy

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **py-spy** | 0.4.1 | Low-overhead sampling profiler | Zero code modification, safe for production, Rust-based (minimal overhead). Profiles live processes. |

**Installation:**
```bash
pip install py-spy>=0.4.1
```

**Rationale:**
- Current: No profiling infrastructure
- Problem: Need to identify latency bottlenecks (detection lag, order placement lag) without modifying code
- Solution: py-spy runs externally, attaches to PID, samples call stacks
- Alternatives considered:
  - **cProfile**: High overhead (tracing profiler), slows execution significantly
  - **line_profiler**: Requires code decoration (`@profile`), not suitable for production
  - **Verdict:** py-spy is the only profiler safe for production use with minimal overhead

**Usage:**
```bash
# Profile running bot (PID 1234) for 60 seconds
py-spy record -o profile.svg --pid 1234 --duration 60

# Top view (like htop for Python)
py-spy top --pid 1234
```

**Integration points:**
- No code changes required
- Use during testing sessions to measure:
  - Time from blockchain event to strategy decision
  - Time from order decision to HTTP POST completion
  - WebSocket message handling latency
- Document profiling workflow in `docs/PROFILING.md`

**Confidence:** HIGH (official GitHub docs, version verified via PyPI)

### WebSocket: No New Dependencies

**Current:** `websockets>=12.0` (already in requirements.txt)

**Usage expansion:**
- **Currently used:** `src/data/ws_price.py` for market price feeds
- **New usage:** WebSocket user channel for order status updates

**Polymarket WebSocket Channels:**
- **market channel**: Real-time price feeds (already implemented)
- **user channel**: Order status, trade fills (NEW for v1.2)

**Authentication:**
- Requires signed authentication message on connect
- py-clob-client provides API credentials via `create_or_derive_api_creds()`
- Use same credentials for WebSocket auth

**Implementation approach:**
- Extend `WebSocketPriceService` pattern to create `WebSocketOrderService`
- Subscribe to user channel with market condition IDs
- Handle incoming messages: order matched, live, filled, cancelled
- Thread-based event loop (matches existing `ws_price.py` pattern)

**Rationale for NOT switching to async:**
- py-clob-client 0.34.5 is synchronous only (verified Jan 13, 2026 release)
- Our order placement is request-response (POST order, get response)
- Threading + asyncio in separate thread (current pattern) works fine
- Full asyncio refactor = high effort, low benefit at current scale

**Confidence:** MEDIUM (WebSocket user channel documented, but auth flow not fully detailed in search results)

## Stack Unchanged (Validation)

### Core Dependencies (Keep As-Is)

| Library | Version | Rationale |
|---------|---------|-----------|
| py-clob-client | 0.34.5 | Latest version (Jan 13, 2026). Synchronous API is sufficient. Supports FOK, FAK, GTC, GTD order types. |
| httpx | 0.27+ | HTTP/2 support, modern async-capable client. Currently used synchronously (correct for py-clob-client). |
| websockets | 12.0+ | Powers existing price feed. Thread-based pattern works. No need for uvloop. |
| pandas | 2.0+ | Analysis and backtesting. Not in hot path. |
| PyYAML | 6.0+ | Config loading. Standard. |
| python-dotenv | 1.0+ | Secrets management. Standard. |

### Explicitly NOT Adding

| Technology | Why NOT |
|------------|---------|
| **uvloop** | Asyncio accelerator, but we're not using asyncio for order placement (py-clob-client is sync). |
| **asyncio refactor** | py-clob-client is synchronous. Refactoring to async = rewriting SDK wrapper for no latency gain. |
| **PostgreSQL/SQLite** | Current JSON persistence is fine. No complex queries needed. Session replay reads full files. |
| **Redis** | No need for shared state (single process) or caching (low volume). |
| **Celery/RQ** | No background jobs. Event loop handles everything. |
| **Prometheus/Grafana** | Overkill for single-bot deployment. structlog + file logs + manual analysis sufficient. |
| **Sentry** | Error tracking not critical at this stage. Logs + manual monitoring sufficient. |

## Order Placement Architecture

**Current (DRY_RUN):**
```
Strategy.on_leader_trade()
  └─> ExecutionAdapter.place_order() [dry-run simulation]
```

**New (LIVE):**
```
Strategy.on_leader_trade()
  └─> LiveExecutionAdapter.place_order()
       └─> ClobClient.post_order(signed_order, FOK)
            └─> HTTP POST to clob.polymarket.com
                 └─> Response: {success, orderID, errorMsg}
```

**Latency breakdown (estimated):**

| Step | Estimated Latency | Notes |
|------|-------------------|-------|
| Blockchain event to strategy decision | 50-200ms | Polygon block time ~2s, our polling interval |
| Strategy decision to order creation | <1ms | Pure Python logic |
| Order signing (ECDSA) | 1-5ms | Cryptographic signing |
| HTTP POST roundtrip | 50-300ms | Network to clob.polymarket.com |
| **Total detection-to-order** | **100-500ms** | Dominated by network + polling |

**Is this fast enough?**
- Leader's trades take time to propagate and match
- Polymarket has matching delays (marketable orders may be "delayed" status)
- Our budget (~$50/hour) means we're not competing on speed
- **Verdict:** Synchronous HTTP POST is acceptable. No need for aggressive optimization.

**When to optimize:**
- If we see consistent order rejections due to price movement
- If profiling shows hot spots (use py-spy to identify)
- Then: reduce polling interval, optimize WebSocket handling, consider price prediction

## Order Lifecycle Management

**Order states (Polymarket CLOB):**

| Status | Meaning | Bot Action |
|--------|---------|------------|
| **matched** | Order filled against resting order | Log success, update portfolio |
| **live** | Limit order resting on book | Not used (we only use FOK) |
| **delayed** | Marketable but matching delayed | Rare, log warning |
| **unmatched** | Failed to match (FOK rejected) | Log rejection, don't retry |

**Order types:**

| Type | Use Case | Current Usage |
|------|----------|---------------|
| **FOK** | Buy/sell immediately or cancel | PRIMARY (current implementation) |
| **FAK** | Partial fill, cancel remainder | Not needed (want all-or-nothing) |
| **GTC** | Limit order on book | Not needed (we mirror immediately) |
| **GTD** | GTC with expiration | Not needed |

**Error handling:**
- HTTP errors (network, auth): Log, alert, disarm
- Order rejections (insufficient balance, bad price): Log, alert, don't retry
- Partial fills: Can't happen with FOK
- Stale prices: Validate bid/ask before order creation

**Monitoring approach:**
- structlog logs with context (order_id, token_id, amount, price)
- JSON logs to file: `logs/orders_YYYY-MM-DD.jsonl`
- Query with `jq`: `cat logs/orders_*.jsonl | jq 'select(.status=="REJECTED")'`
- Alert on rejection rate >10% (manual check initially)

## Codebase Modularity Improvements

**Current issues:**
- Logging scattered across modules with inconsistent formats
- No centralized profiling documentation
- Order placement logic mixed with adapter logic

**Proposed structure:**

```
src/
  core/
    logging.py          # NEW: structlog configuration, context helpers
  execution/
    live.py             # REFACTOR: Extract order builder, add structured logging
    order_builder.py    # NEW: Separate order construction logic
    order_tracker.py    # NEW: Track order lifecycle via WebSocket user channel
  monitoring/
    profiler.py         # NEW: py-spy wrapper, profiling helpers
  data/
    ws_order.py         # NEW: WebSocket user channel for order updates
```

**Specific improvements:**

1. **Centralized logging config** (`src/core/logging.py`):
   - Configure structlog processors (JSON + pretty)
   - Helper: `get_logger_with_context(order_id, token_id, market_id)`
   - Environment-based config (JSON in production, pretty in dev)

2. **Order builder separation** (`src/execution/order_builder.py`):
   - Extract price calculation (mid ± offset)
   - Validate spread (bid <= ask, within bounds)
   - Size calculation (dollars to shares)
   - Sign order (py-clob-client integration)
   - Returns: `SignedOrder` ready for posting

3. **Order tracker** (`src/execution/order_tracker.py`):
   - Subscribe to WebSocket user channel
   - Map order_id to internal request
   - Emit events: OrderFilled, OrderRejected, OrderCancelled
   - Strategy can listen for fill confirmations

4. **Profiling documentation** (`docs/PROFILING.md`):
   - How to run py-spy against live bot
   - Interpreting flamegraphs
   - Common bottlenecks and fixes
   - Latency measurement methodology

## Installation

**Add to requirements.txt:**
```
# Structured logging (NEW)
structlog>=25.5

# Profiling (NEW)
py-spy>=0.4.1

# Existing dependencies (unchanged)
py-clob-client>=0.34
httpx>=0.27
websockets>=12.0
PyYAML>=6.0
python-dotenv>=1.0
pandas>=2.0
matplotlib>=3.8
numpy>=1.24
scipy>=1.11
plotly>=5.0
quantstats>=0.0.81
empyrical-reloaded>=0.5.11
pytest>=8.0
pytest-asyncio>=0.23
mypy>=1.8
ruff>=0.2
```

## Configuration

**New environment variables:**

```bash
# Logging
LOG_LEVEL=INFO              # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=json             # json, pretty
LOG_DIR=logs/               # Directory for log files

# Order placement
ORDER_TIMEOUT_SEC=10        # HTTP timeout for order placement
MAX_PRICE_OFFSET=0.05       # Maximum price offset from mid (5 cents)
VALIDATE_SPREAD=true        # Reject if bid > ask
```

**Config file additions** (`config/config.yaml`):

```yaml
execution:
  live:
    order_timeout_sec: 10
    max_price_offset: 0.05
    validate_spread: true
    retry_on_network_error: false  # Don't retry failed orders

logging:
  level: INFO
  format: json  # json or pretty
  output:
    console: true
    file: true
    file_path: logs/bot.jsonl
  context:
    always_include:
      - timestamp
      - level
      - logger
      - event
```

## Latency Optimization Strategy

**Measurement first:**
1. Add latency tracking to existing code
2. Log timestamps: event_detected, decision_made, order_created, order_posted, response_received
3. Run on test sessions, collect data
4. Identify slowest steps

**Expected bottlenecks (hypothesis):**
- **Blockchain polling interval** (currently unknown, likely 100-500ms)
- **HTTP POST roundtrip** (network latency, 50-300ms)
- **Price feed staleness** (WebSocket updates may lag)

**Optimization roadmap (after measurement):**

| If bottleneck is... | Then... |
|---------------------|---------|
| Polling interval | Reduce interval, switch to event-driven (harder) |
| HTTP POST | Pre-warm connections (httpx connection pooling), consider WebSocket order placement if available |
| Price feed lag | Subscribe to more granular updates, validate with orderbook snapshot |
| Order signing | Cache client initialization, profile ECDSA signing |
| Python GIL | Profile with py-spy, move hot paths to C extension (extreme) |

**Realistic target latency:**
- Current (estimated): 100-500ms detection-to-order
- Optimized (achievable): 50-200ms
- Aggressive (requires major changes): 20-50ms
- HFT-level (<10ms): Not achievable in Python without C++ rewrite

**Trade-off:**
- Our edge is strategy quality, not speed
- Leader's trades are public on blockchain (unavoidable lag)
- Focus on correctness > microseconds
- Optimize only if profiling shows clear wins

## Sources

**py-clob-client:**
- [GitHub - Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- [py-clob-client on PyPI](https://pypi.org/project/py-clob-client/) (v0.34.5, Jan 13, 2026)
- [Place Single Order - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/orders/create-order)

**WebSocket:**
- [WSS Overview - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [The Polymarket API: Architecture, Endpoints, and Use Cases](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf)

**Structured Logging:**
- [structlog on PyPI](https://pypi.org/project/structlog/) (v25.5.0)
- [Guide to structured logging in Python](https://newrelic.com/blog/log/python-structured-logging)
- [Logging in Python: A Comparison of the Top 6 Libraries](https://betterstack.com/community/guides/logging/best-python-logging-libraries/)

**Profiling:**
- [py-spy on GitHub](https://github.com/benfred/py-spy) (v0.4.1)
- [Why Is My Code So Slow? A Guide to Py-Spy Python Profiling](https://towardsdatascience.com/why-is-my-code-so-slow-a-guide-to-py-spy-python-profiling/)
- [Top 7 Python Profiling Tools for Performance](https://daily.dev/blog/top-7-python-profiling-tools-for-performance)

**Asyncio & Latency:**
- [Python in High-Frequency Trading: Low-Latency Techniques](https://www.pyquantnews.com/free-python-resources/python-in-high-frequency-trading-low-latency-techniques)
- [Event Loop — Python 3.14.3 documentation](https://docs.python.org/3/library/asyncio-eventloop.html)
- [High-Performance Python: AsyncIO vs Multiprocessing vs ThreadPools (2026 Guide)](https://medium.com/@yogeshkrishnanseeniraj/high-performance-python-asyncio-vs-multiprocessing-vs-threadpools-2026-guide-ad49d40452fc)

**Decimal Precision:**
- [decimal — Decimal fixed-point and floating-point arithmetic](https://docs.python.org/3/library/decimal.html)
- [Precision Handling in Python (2026)](https://thelinuxcode.com/precision-handling-in-python-2026-representation-rounding-and-real-world-patterns/)

---

**Research complete.** Stack additions validated against official docs and current versions. Ready for roadmap creation.
