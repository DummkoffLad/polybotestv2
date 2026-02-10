# Architecture Research: v1.2 Production Ready

**Researched:** 2026-02-10
**Domain:** Live trading bot with real-time order execution
**Confidence:** HIGH (existing codebase analysis + verified industry patterns)

## Executive Summary

Your existing architecture is **well-structured for live trading** with one critical modification and several new components needed. The event-driven framework (Runner → Strategy → Adapter) is production-ready. The adapter pattern allows seamless switching between simulation and live execution. The primary architectural changes are:

1. **Fix Portfolio position keying** (token_id only → composite key with market_id + side)
2. **Add OrderLifecycleManager** for tracking order states and fills
3. **Add WebSocket order channel** alongside existing price WebSocket
4. **Separate experiment scripts** from production code into dedicated directory
5. **Enhance reconciliation** with periodic exchange position queries

**Key insight:** Your simulation replay uses the SAME strategy code as live execution. This is architecturally correct and must be preserved. The integration point is the ExecutionAdapter—simulation returns SIMULATED status, live returns FILLED/REJECTED.

## Current Architecture

### Data Flow (Validated from Code)

```
BlockchainDetector (Polygon)
    ↓ [detects leader trades]
LeaderTrade + PriceSnapshot
    ↓ [wrapped in MarketEvent]
Strategy.on_event()
    ↓ [returns TradeDecision]
UniversalRunner._exec()
    ↓ [builds OrderRequest]
ExecutionAdapter.place_order()
    ↓ [DRY_RUN: log only | LIVE: API call]
Strategy.on_fill()
    ↓ [updates portfolio]
Portfolio.apply_buy/sell()
```

**Strengths:**
- Clean separation of concerns (detection → decision → execution)
- Strategy logic identical in simulation and live
- Pluggable execution via adapter pattern
- Session recording for deterministic replay
- Hourly market transitions handled correctly

**Existing Components (Keep):**

| Component | Location | Purpose |
|-----------|----------|---------|
| UniversalRunner | src/framework/runner.py | Main event loop, orchestration |
| Strategy (base + implementations) | src/strategies/ | Decision-making logic |
| ExecutionAdapter (base + DryRun + Live) | src/execution/ | Order placement abstraction |
| Portfolio | src/core/portfolio.py | Position tracking, PnL calculation |
| SessionRecorder | src/framework/recorder.py | Event logging for replay |
| SessionReplayer | src/framework/replay/ | Deterministic replay |
| BlockchainDetector | src/data/blockchain_detector.py | Leader trade detection |
| WebSocketPriceService | src/data/ws_price.py | Real-time market prices |

## Target Architecture

### Enhanced Data Flow (with Live Trading)

```
BlockchainDetector (Polygon)          WebSocketOrderChannel (new)
    ↓ [leader trades]                      ↓ [our order fills]
    |                                      |
    ├─ Strategy.on_event() ────────────────┤
    |      ↓ [TradeDecision]               |
    |  OrderRequest                        |
    |      ↓                                |
    |  LiveAdapter.place_order()           |
    |      ↓ [POST to CLOB]                |
    |  OrderLifecycleManager (new) ────────┘
    |      ↓ [tracks pending → filled]
    |  Strategy.on_fill()
    |      ↓
    |  Portfolio.apply_buy/sell()
    |
    └─ PositionReconciler (enhanced)
           ↓ [periodic: compare local vs exchange]
           GET /positions endpoint
```

**Key changes:**
1. **OrderLifecycleManager** tracks order states (pending → filled/rejected)
2. **WebSocketOrderChannel** subscribes to user-specific order fill events
3. **PositionReconciler** enhanced with exchange position queries
4. **Portfolio** uses composite key (token_id, market_id, side) not just token_id

## Critical Bug: Portfolio Position Keying

### Current Problem

**File:** `src/core/portfolio.py` line 33-40

```python
class Portfolio:
    def __init__(self):
        self._positions: Dict[str, PortfolioPosition] = {}  # Keyed by token_id ONLY

    def get(self, token_id: str, market_id: str = "", side: Side = Side.UP) -> PortfolioPosition:
        if token_id not in self._positions:
            self._positions[token_id] = PortfolioPosition(token_id, market_id, side)
        return self._positions[token_id]  # ← WRONG: ignores market_id and side
```

**Impact:** If the leader trades BOTH sides of the same market (UP and DOWN), or trades the same token in different markets, positions collide. You'll track only one position when you actually have two.

### Solution

Change keying from `token_id` (string) to `(token_id, market_id, side)` (tuple).

```python
class Portfolio:
    def __init__(self):
        self._positions: Dict[Tuple[str, str, Side], PortfolioPosition] = {}

    def get(self, token_id: str, market_id: str, side: Side) -> PortfolioPosition:
        key = (token_id, market_id, side)
        if key not in self._positions:
            self._positions[key] = PortfolioPosition(token_id, market_id, side)
        return self._positions[key]
```

**Build order:** Fix this BEFORE going live. It's a data corruption bug that will cause incorrect position tracking in live trading.

## New Components

### 1. OrderLifecycleManager

**Purpose:** Track order states from submission to fill/rejection, handle partial fills, manage timeouts.

**Location:** `src/execution/order_lifecycle.py`

**Interfaces:**

```python
class OrderLifecycleManager:
    def submit_order(self, order_request: OrderRequest) -> str:
        """Submit order, return order_id, track as PENDING."""

    def mark_filled(self, order_id: str, filled_shares: Decimal, filled_price: Decimal) -> OrderFill:
        """Mark order as filled (from WebSocket or API response)."""

    def mark_rejected(self, order_id: str, reason: str) -> None:
        """Mark order as rejected."""

    def get_pending_orders(self) -> List[PendingOrder]:
        """Return all orders awaiting fill."""

    def cleanup_stale_orders(self, timeout_seconds: int = 60) -> None:
        """Cancel orders older than timeout."""
```

**Talks to:**
- LiveExecutionAdapter (submit orders)
- WebSocketOrderChannel (receive fill notifications)
- UniversalRunner (query pending orders for reconciliation)

**Why needed:** Currently, `LiveAdapter.place_order()` submits via API and immediately returns success/failure. For FOK (Fill-Or-Kill) orders this works. But for limit orders or if you switch to GTC (Good-Til-Cancelled), you need to track order lifecycle asynchronously. Even with FOK, WebSocket fill confirmations provide better reliability than trusting the POST response.

### 2. WebSocketOrderChannel

**Purpose:** Subscribe to user-specific order fill events from Polymarket CLOB WebSocket.

**Location:** `src/data/ws_orders.py`

**Interfaces:**

```python
class WebSocketOrderChannel:
    def __init__(self, user_address: str):
        """Initialize with user address for authenticated channel."""

    def start(self) -> None:
        """Connect to wss://ws-subscriptions-clob.polymarket.com and authenticate."""

    def subscribe_orders(self) -> None:
        """Subscribe to user order fills channel."""

    def on_fill(self, callback: Callable[[OrderFillEvent], None]) -> None:
        """Register callback for order fill events."""

    def stop(self) -> None:
        """Disconnect WebSocket."""
```

**Talks to:**
- OrderLifecycleManager (notify fills)
- UniversalRunner (lifecycle: start on init, stop on shutdown)

**Pattern:** Similar to existing `WebSocketPriceService` (src/data/ws_price.py) but subscribes to user channel instead of market channel.

**Why needed:** Per Polymarket CLOB API docs, WebSocket provides real-time order fill notifications. This is more reliable than polling GET /orders and has lower latency than waiting for blockchain confirmation.

### 3. PositionReconciler (Enhanced)

**Purpose:** Periodically compare local portfolio tracking with exchange positions, flag discrepancies.

**Location:** `src/core/reconciler.py` (new) or enhance existing `UniversalRunner._maybe_reconcile()`

**Current state (runner.py line 339-365):**
- Fetches positions every 5 minutes
- Logs warnings on mismatch
- Does NOT auto-correct (intentional—don't silently overwrite local state)

**Enhancements needed:**
1. Use new composite portfolio key (token_id, market_id, side)
2. Store reconciliation history for debugging
3. Alert on persistent discrepancies (3+ consecutive mismatches)
4. Optionally pause trading if discrepancy exceeds threshold

**Build order:** Enhance after portfolio keying fix.

### 4. Experiment Script Organization

**Problem:** 75 experiment scripts in root directory (analyze_*.py, experiment_*.py, debug_*.py, grid_search_*.py). This is research code, not production code.

**Solution:** Separate directories by purpose.

```
scripts/
├── experiments/       # Strategy experiments (experiment_*.py, grid_search_*.py)
├── analysis/          # Post-session analysis (analyze_*.py, deep_analysis.py)
├── debug/             # One-off debug scripts (debug_*.py, sanity_check.py)
└── data_collection/   # Data fetching (fetch_*.py, test_api_*.py)

src/                   # Production code only
├── strategies/
├── execution/
├── framework/
└── ...
```

**Migration:**
- Move scripts to appropriate directories
- Update any imports (if scripts import from src/, paths stay the same)
- Add scripts/README.md explaining each directory's purpose
- Update .gitignore to prevent future root-level script sprawl

**Build order:** Early (Phase 1-2) to clean workspace before live integration work.

## Modified Components

### UniversalRunner (src/framework/runner.py)

**Current:** Polls blockchain, feeds events to strategy, calls adapter.place_order(), updates portfolio.

**Changes:**

1. **Integrate OrderLifecycleManager:**
   - Initialize in `_init()`
   - Submit orders through manager instead of directly to adapter
   - Register fill callback to update portfolio

2. **Integrate WebSocketOrderChannel:**
   - Start in `_init()` alongside WebSocketPriceService
   - Stop in `_shutdown()` and `_hourly_cleanup()`
   - Connect fill events to `OrderLifecycleManager.mark_filled()`

3. **Enhanced reconciliation:**
   - Call `PositionReconciler.reconcile()` every 5 minutes
   - Store reconciliation results for audit trail
   - Optionally halt trading on critical discrepancies

**No changes to core loop:** Event detection → strategy decision → execution flow stays identical.

### LiveExecutionAdapter (src/execution/live.py)

**Current:** Submits FOK orders via POST /order, returns FILLED/REJECTED immediately.

**Changes:**

1. **Return order_id:** Currently returns `OrderResponse` with order_id, but that ID isn't tracked anywhere. Pass it to `OrderLifecycleManager`.

2. **Support limit orders:** Add support for GTC (Good-Til-Cancelled) orders in addition to FOK. Requires order lifecycle tracking.

3. **Handle partial fills:** If switching from FOK to GTC, handle partial fills (WebSocket may report multiple fill events for one order).

**Build order:** After OrderLifecycleManager exists.

### Strategy.on_fill() (base class pattern)

**Current:** Called after successful order execution, updates internal strategy state.

**Enhancement:** Provide `OrderFill` object with actual fill price/shares (not just requested amounts). Strategies can track slippage, adjust future sizing based on realized execution quality.

```python
@dataclass
class OrderFill:
    order_id: str
    token_id: str
    market_id: str
    side: Side
    requested_shares: Decimal
    filled_shares: Decimal
    requested_price: Decimal
    filled_price: Decimal
    timestamp: datetime
```

**Build order:** After WebSocketOrderChannel provides fill details.

## Integration Points

### Detection → Strategy (Unchanged)

**Current:** BlockchainDetector emits LeaderTrade → wrapped in MarketEvent → Strategy.on_event()

**Keep as-is.** This works correctly.

### Strategy → Execution (Enhanced)

**Current:**
```
Strategy.on_event() → TradeDecision
Runner._exec() → OrderRequest
Adapter.place_order() → OrderResponse
Strategy.on_fill()
```

**Target:**
```
Strategy.on_event() → TradeDecision
Runner._exec() → OrderRequest
OrderLifecycleManager.submit_order() → order_id
    ↓ [async]
WebSocketOrderChannel receives fill
OrderLifecycleManager.mark_filled() → OrderFill
Strategy.on_fill(OrderFill)
Portfolio.apply_buy/sell()
```

**Migration path:** Initially keep synchronous flow (FOK orders), add async path for GTC orders later.

### Simulation → Live (Critical: No Duplication)

**Problem statement:** How to share trade logic between replay simulation and live execution?

**Answer:** You already solved this correctly. The logic is in the **Strategy**, not in the runner or adapter.

**Current pattern (KEEP THIS):**

| Component | Simulation | Live |
|-----------|------------|------|
| Strategy | Same code | Same code |
| Runner | SessionReplayer | UniversalRunner |
| Adapter | NullExecutionAdapter | LiveExecutionAdapter |
| Portfolio | Same code | Same code |

**What to avoid:**
- ❌ Duplicating decision logic in UniversalRunner
- ❌ Putting trade logic in ExecutionAdapter
- ❌ Different code paths for simulation vs live

**What to do:**
- ✅ Keep all trade logic in Strategy
- ✅ Adapters only handle I/O (API calls vs logging)
- ✅ Runner orchestrates but doesn't decide

**Testing protocol:**
1. Record live session with SessionRecorder
2. Replay recorded session with profit_taker strategy
3. Compare trade-by-trade: same decisions at same events
4. If divergence: fix Strategy, not runner/adapter

## Data Flow Changes

### Before (Simulation)

```
SessionLoader reads JSONL
    ↓
For each MarketEvent:
    Strategy.on_event() → TradeDecision
    NullAdapter.place_order() → SIMULATED
    Strategy.on_fill()
    Portfolio.apply_buy/sell()
```

### After (Live with Order Lifecycle)

```
BlockchainDetector polls Polygon
    ↓
For each leader trade:
    Build MarketEvent (leader trade + price snapshot)
    Strategy.on_event() → TradeDecision
    OrderLifecycleManager.submit_order()
        ↓ [LiveAdapter.place_order() → POST to CLOB]
    [ASYNC WAIT]
    WebSocketOrderChannel receives fill notification
    OrderLifecycleManager.mark_filled()
    Strategy.on_fill(OrderFill)
    Portfolio.apply_buy/sell(composite_key)
```

**Key difference:** Simulation is synchronous (immediate fill). Live is asynchronous (wait for WebSocket fill). But Strategy code is identical—it just receives `on_fill()` at different times.

## Build Order (Suggested Sequencing)

### Phase 1: Foundation (Low Risk)
1. **Organize experiment scripts** into scripts/ directory
2. **Fix portfolio composite keying** (token_id, market_id, side)
3. **Add unit tests** for new portfolio keying

**Why first:** Zero impact on existing functionality, reduces workspace clutter.

### Phase 2: Order Lifecycle (Core Feature)
4. **Build OrderLifecycleManager** (submit, track, mark_filled)
5. **Integrate with LiveAdapter** (return order_id)
6. **Add integration test** (mock WebSocket fill)

**Why second:** Foundation for async order tracking, testable in isolation.

### Phase 3: WebSocket Orders (Real-time Integration)
7. **Build WebSocketOrderChannel** (similar to WebSocketPriceService)
8. **Connect to OrderLifecycleManager** (on_fill callback)
9. **Test with live API** in dry-run mode (log fills, don't trade)

**Why third:** Requires live API, but can be tested without real orders.

### Phase 4: Runner Integration (Live Trading Path)
10. **Integrate OrderLifecycleManager into UniversalRunner**
11. **Start/stop WebSocketOrderChannel** in runner lifecycle
12. **Enhance on_fill()** to use OrderFill object with actual prices

**Why fourth:** Touches critical path, requires all previous components.

### Phase 5: Reconciliation (Safety)
13. **Enhance PositionReconciler** with composite key support
14. **Add reconciliation audit trail** (log to data/reconciliation/)
15. **Add trading halt** on critical discrepancies

**Why last:** Safety feature, depends on portfolio keying and order lifecycle.

## Architectural Patterns to Follow

### 1. Single Responsibility

**Good:**
- Strategy decides WHAT to trade
- Adapter decides HOW to execute
- Portfolio decides WHAT we own

**Bad:**
- Strategy calling API directly
- Adapter making trade decisions
- Runner duplicating strategy logic

### 2. Dependency Injection

**Good:**
```python
runner = UniversalRunner(config, strategy, execution_adapter, recorder)
```

**Bad:**
```python
class UniversalRunner:
    def __init__(self):
        self.strategy = ProfitTakerStrategy()  # ← hardcoded
```

### 3. Async-Aware but Sync-Default

**Pattern:** Keep synchronous flow for simple cases (FOK orders), add async path for complex cases (GTC orders, partial fills).

**Implementation:**
- OrderLifecycleManager tracks all orders
- For FOK: submit → immediate fill → callback in same loop iteration
- For GTC: submit → fill arrives later → callback in future iteration

### 4. Event Sourcing (Already Implemented Correctly)

**Keep this:** SessionRecorder writes all events to JSONL. SessionReplayer reads JSONL and deterministically replays. This is textbook event sourcing and it's production-ready.

**Don't break this:** Any new components must integrate with session recording (e.g., log order submissions to JSONL).

## Anti-Patterns to Avoid

### 1. Mixing Simulation and Live Logic

**Bad:**
```python
if self.mode == "LIVE":
    # Live-specific logic
else:
    # Simulation logic
```

**Good:**
```python
# Same logic everywhere, adapter handles mode differences
adapter.place_order(request)  # Adapter knows if it's live or not
```

### 2. State Duplication

**Bad:**
- Portfolio tracks positions
- Strategy tracks positions separately
- Runner tracks positions separately

**Good:**
- Portfolio is single source of truth
- Strategy reads from portfolio
- Runner reads from portfolio

### 3. Blocking WebSocket in Main Loop

**Bad:**
```python
def _cycle():
    # ...
    fill_event = websocket.wait_for_fill()  # ← BLOCKS
```

**Good:**
```python
def _cycle():
    # ...
    websocket.poll_non_blocking()  # Returns immediately
    # Process fills in callback
```

### 4. Silent Reconciliation Corrections

**Bad:**
```python
if exchange_position != local_position:
    local_position = exchange_position  # ← Silently overwrites
```

**Good:**
```python
if exchange_position != local_position:
    logger.warning(f"MISMATCH: local={local_position} exchange={exchange_position}")
    # Manual investigation required
```

**Rationale:** If local tracking diverges from exchange, that's a bug. Silently correcting hides the bug. Flag it, investigate root cause, fix the bug.

## Scalability Considerations

### Current Scale
- 1 leader address
- ~10-20 trades per hour
- $50 budget per hour
- 1 strategy running

### At 10x Scale (Future)
- 5-10 leader addresses
- ~100-200 trades per hour
- $500 budget per hour
- Multiple strategies in parallel

**Bottlenecks to watch:**

1. **Blockchain polling:** Currently polls every 2 seconds. At 10x volume, switch to WebSocket block subscriptions.

2. **Position reconciliation:** Currently fetches all positions every 5 minutes. At 100+ positions, use differential updates.

3. **Session recording:** Currently writes to single JSONL file. At 200 trades/hour, implement log rotation.

**Good news:** Your architecture is already modular enough to handle these. No fundamental redesign needed, just component upgrades.

## Error Handling Philosophy

### Current Approach (Keep This)

**Strategy errors:** Strategy returns `TradeDecision.skip()` with reason. No exception, no crash.

**Execution errors:** Order rejected → log error, increment stats, DON'T update portfolio. Portfolio stays consistent.

**Detection errors:** Blockchain poll fails → log warning, continue to next iteration. Don't crash runner.

### Add for Live Trading

**Order timeout:** If order pending for >60 seconds, cancel and retry or skip.

**WebSocket disconnect:** Auto-reconnect with exponential backoff (already implemented in WebSocketPriceService, reuse pattern).

**Position mismatch:** If reconciliation fails 3+ times, halt trading and alert (don't silently continue with bad state).

## Testing Strategy

### Unit Tests
- Portfolio with composite keys (multiple sides, multiple markets)
- OrderLifecycleManager state transitions (pending → filled/rejected)
- WebSocketOrderChannel message parsing

### Integration Tests
- UniversalRunner with OrderLifecycleManager (mocked WebSocket)
- SessionReplayer produces same results as before (regression test)
- Portfolio reconciliation with mocked exchange data

### Live Testing Protocol
1. **Dry-run mode:** Connect to live API, WebSockets, detect trades, but DON'T execute (NullAdapter)
2. **Paper trading mode:** Execute with small capital ($10 budget) for 24 hours
3. **Gradual ramp:** Start at $25/hour, increase to $50/hour after 1 week of stable operation
4. **Session recording:** Record ALL live sessions for replay validation

## Monitoring and Observability

### Existing (Keep)
- Trade log CSV (continuous and per-hour)
- JSONL session recording
- Console output with trade summary

### Add for Live
- **Order fill latency:** Time from order submission to fill confirmation
- **Reconciliation discrepancies:** Count and magnitude of position mismatches
- **WebSocket health:** Connection uptime, reconnection count
- **Capital utilization:** Actual vs budgeted capital usage per hour

### Alerts (New)
- Order rejected 3+ times in a row
- Position mismatch >$10 for >15 minutes
- WebSocket disconnected for >60 seconds
- Actual capital >110% of hourly budget

## Sources

**Industry Best Practices:**
- [Step-by-Step Crypto Trading Bot Development Guide (2026)](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [Trading System Architecture 2026 | Microservices to Agentic Mesh](https://www.tuvoc.com/blog/trading-system-architecture-microservices-agentic-mesh/)
- [From Silos to Sequencers: 24/7 Trading Architectures](https://weareadaptive.com/trading-resources/from-silos-to-sequencers-24-7-trading-architectures/)
- [Automated Trading on Polymarket: Bots & Execution Strategies](https://www.quantvps.com/blog/automated-trading-polymarket)

**Polymarket CLOB API:**
- [WSS Overview - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [The Polymarket API: Architecture and Use Cases](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf)

**Python Trading Bot Organization:**
- [Jesse - Open-source Python Trading Bot](https://jesse.trade/)
- [Quant Trading Systems: Architecture & Infrastructure](https://mbrenndoerfer.com/writing/quant-trading-system-architecture-infrastructure)

**Confidence Assessment:**
- HIGH confidence on existing architecture analysis (verified from codebase)
- HIGH confidence on portfolio keying bug (code inspection)
- MEDIUM confidence on WebSocket order patterns (official docs + industry patterns)
- MEDIUM confidence on build order sequencing (depends on team priorities)
