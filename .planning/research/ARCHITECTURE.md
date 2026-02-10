# Architecture Patterns: Live WebSocket Trading Integration

**Domain:** Polymarket copy trading bot
**Researched:** 2026-02-09
**Confidence:** HIGH

## Executive Summary

The current architecture has **simulation (replay) and live (runner) executing trade logic separately**, leading to divergence. To add WebSocket order placement while preventing future divergence, we must:

1. **Extract shared trade decision logic** into reusable components
2. **Use dependency injection** to swap execution adapters (simulation vs live)
3. **Add order lifecycle management** for WebSocket fills/rejections/cancellations
4. **Optimize for latency** from leader detection → order placed

**Critical pattern:** Strategy should produce `TradeDecision` objects. Execution adapters should consume them. Runner/Replayer should orchestrate but never duplicate trade logic.

---

## Current State Analysis

### What Exists

| Component | Purpose | Location |
|-----------|---------|----------|
| **Strategy** | Trade decision logic (buy/sell/skip) | `src/strategies/profit_taker/strategy.py` |
| **Portfolio** | Position tracking, PnL calculation | `src/core/portfolio.py` |
| **UniversalRunner** | Live trading orchestrator | `src/framework/runner.py` |
| **SessionReplayer** | Simulation orchestrator | `src/framework/replay/replayer.py` |
| **SessionRecorder** | Records events to JSONL for replay | `src/framework/recorder.py` |
| **ExecutionAdapter** | Order placement interface | `src/execution/base.py` |
| **LiveExecutionAdapter** | py-clob-client wrapper (FOK orders only) | `src/execution/live.py` |
| **DryRunAdapter** | No-op for testing | `src/execution/dry_run.py` |

### Current Flow (Live)

```
BlockchainDetector.poll()
  → UniversalRunner._make_event()
    → Strategy.on_event() → TradeDecision
      → UniversalRunner._exec() → ExecutionAdapter.place_order()
        → Strategy.on_fill()
```

### Current Flow (Simulation)

```
SessionLoader.load()
  → SessionReplayer.run()
    → EventProcessor.process_event()
      → Strategy.on_event() → TradeDecision
        → Portfolio.apply_buy/sell() (no execution adapter!)
          → Strategy.on_fill()
```

### The Problem: Code Divergence

**Issue:** Simulation directly applies trades to portfolio. Live routes through execution adapter. This creates two code paths for "apply trade to strategy state."

**Evidence from codebase:**
- **Runner:** Calls `self.strategy.on_fill(event, decision)` only after `_exec()` succeeds
- **Replayer:** Calls `processor.process_event()` which directly manipulates portfolio

**Result:** Past bugs where simulation passed but live failed (or vice versa) because logic diverged.

---

## Target Architecture: Shared Execution Path

### Core Principle

**Single source of truth for trade execution:**
```
Strategy.on_event() → TradeDecision → ExecutionAdapter.execute() → Strategy.on_fill()
```

Both runner and replayer use **identical execution path**. Only difference: adapter implementation.

### Component Boundaries (New)

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| **Strategy** | Produces `TradeDecision` from `MarketEvent` | Portfolio (read-only), Config |
| **ExecutionEngine** | Coordinates decision → execution → fill | Strategy, ExecutionAdapter |
| **ExecutionAdapter** | Converts `TradeDecision` to platform orders | py-clob-client, WebSocket client |
| **OrderManager** | Tracks order lifecycle (pending/filled/rejected) | ExecutionAdapter, WebSocket |
| **SimulationAdapter** | Simulates fills using recorded prices | SessionLoader prices |
| **LiveAdapter** | Places real orders via WebSocket | Polymarket CLOB |
| **Runner/Replayer** | Orchestrates event flow, manages lifecycle | ExecutionEngine, Recorder |

### Data Flow (Unified)

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Event Source (Blockchain or Recorded JSONL)                  │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Orchestrator (Runner or Replayer)                            │
│    - Loads event + all_prices context                           │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Strategy.on_event(event) → TradeDecision                     │
│    - Reads portfolio (positions, cash)                          │
│    - Checks filters (price, conviction, drawdown)               │
│    - Returns BUY/SELL/SKIP with dollars/shares/price            │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. ExecutionEngine.execute(decision, event)                     │
│    - Validates decision (Polymarket minimums, etc.)             │
│    - Delegates to ExecutionAdapter                              │
└─────────────────────┬───────────────────────────────────────────┘
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
┌───────────────────┐ ┌──────────────────────┐
│ SimulationAdapter │ │   LiveAdapter        │
│ - Apply at price  │ │ - Place WS order     │
│ - Instant fill    │ │ - Wait for fill/rej  │
└─────────┬─────────┘ └──────────┬───────────┘
          │                      │
          └──────────┬───────────┘
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. ExecutionResult (filled_shares, filled_price, status)        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. Strategy.on_fill(event, decision, result)                    │
│    - Portfolio.apply_buy/sell (same code for sim + live)        │
│    - Update cash, entries, conviction tracking                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key insight:** Steps 3-6 are identical for simulation and live. Only step 4's adapter implementation differs.

---

## New Components Needed

### 1. ExecutionEngine (NEW)

**Purpose:** Orchestrate decision → execution → fill. Prevent orchestrators from handling execution logic.

**Interface:**
```python
class ExecutionEngine:
    def __init__(self, strategy: Strategy, adapter: ExecutionAdapter):
        self.strategy = strategy
        self.adapter = adapter

    def process_event(self, event: MarketEvent, all_prices: Dict) -> ExecutionResult:
        """Process event through strategy → adapter → fill."""
        # 1. Get decision from strategy
        decision = self.strategy.on_event(event)

        # 2. Skip if no action
        if decision.action == DecisionAction.SKIP:
            return ExecutionResult.skipped(decision.skip_reason)

        # 3. Validate decision
        valid, error = decision.validate_order_constraints()
        if not valid:
            return ExecutionResult.rejected(error)

        # 4. Execute via adapter
        result = self.adapter.execute(decision, event, all_prices)

        # 5. Apply fill to strategy
        if result.success:
            self.strategy.on_fill(event, decision, result)

        return result
```

**Why:** Removes execution orchestration from Runner/Replayer. Both call `engine.process_event()` and get identical behavior.

### 2. OrderManager (NEW)

**Purpose:** Track order lifecycle for WebSocket fills/rejections/cancellations.

**Responsibilities:**
- Assign correlation IDs to orders
- Track pending orders (order_id → OrderState)
- Handle async fill notifications from WebSocket
- Timeout detection (order pending >30s → cancel)
- Retry logic for rejections (if retriable)

**Interface:**
```python
@dataclass
class OrderState:
    correlation_id: str
    order_id: str
    decision: TradeDecision
    event: MarketEvent
    status: OrderStatus  # PENDING/FILLED/REJECTED/CANCELLED/TIMEOUT
    created_at: float
    filled_at: Optional[float] = None
    filled_price: Optional[Decimal] = None
    filled_shares: Optional[Decimal] = None
    error: Optional[str] = None

class OrderManager:
    def __init__(self):
        self._pending: Dict[str, OrderState] = {}  # correlation_id → state
        self._order_id_map: Dict[str, str] = {}     # order_id → correlation_id

    def track_order(self, correlation_id: str, order_id: str,
                    decision: TradeDecision, event: MarketEvent) -> None:
        """Start tracking a pending order."""
        self._pending[correlation_id] = OrderState(
            correlation_id=correlation_id,
            order_id=order_id,
            decision=decision,
            event=event,
            status=OrderStatus.PENDING,
            created_at=time.time(),
        )
        self._order_id_map[order_id] = correlation_id

    def on_fill(self, order_id: str, filled_price: Decimal,
                filled_shares: Decimal) -> Optional[OrderState]:
        """Handle fill notification from WebSocket."""
        corr_id = self._order_id_map.get(order_id)
        if not corr_id:
            return None
        state = self._pending.get(corr_id)
        if state:
            state.status = OrderStatus.FILLED
            state.filled_at = time.time()
            state.filled_price = filled_price
            state.filled_shares = filled_shares
        return state

    def on_rejection(self, order_id: str, error: str) -> Optional[OrderState]:
        """Handle rejection notification."""
        corr_id = self._order_id_map.get(order_id)
        if not corr_id:
            return None
        state = self._pending.get(corr_id)
        if state:
            state.status = OrderStatus.REJECTED
            state.error = error
        return state

    def check_timeouts(self, timeout_sec: float = 30.0) -> List[OrderState]:
        """Return orders pending longer than timeout."""
        now = time.time()
        timeouts = []
        for state in self._pending.values():
            if state.status == OrderStatus.PENDING:
                if now - state.created_at > timeout_sec:
                    state.status = OrderStatus.TIMEOUT
                    timeouts.append(state)
        return timeouts

    def cleanup(self, correlation_id: str) -> None:
        """Remove completed order from tracking."""
        state = self._pending.pop(correlation_id, None)
        if state and state.order_id in self._order_id_map:
            del self._order_id_map[state.order_id]
```

**Why:** WebSocket fills arrive asynchronously. Need centralized tracking to correlate fill messages with original decisions.

### 3. WebSocketOrderClient (NEW)

**Purpose:** Maintain persistent WebSocket connection for order placement and lifecycle notifications.

**Responsibilities:**
- Connect to `wss://ws-subscriptions-clob.polymarket.com/ws/orders`
- Subscribe to order updates for our wallet address
- Place orders via WebSocket (lower latency than REST)
- Notify OrderManager of fills/rejections/cancellations
- Handle reconnection if connection drops

**Interface:**
```python
class WebSocketOrderClient:
    def __init__(self, wallet_address: str, on_fill: Callable,
                 on_rejection: Callable):
        self.wallet_address = wallet_address
        self.on_fill = on_fill  # Callback: (order_id, price, shares) → None
        self.on_rejection = on_rejection  # Callback: (order_id, error) → None
        self._ws = None
        self._running = False

    async def connect(self) -> bool:
        """Connect to WebSocket and subscribe to order updates."""
        # Connect to wss://ws-subscriptions-clob.polymarket.com/ws/orders
        # Subscribe to {"type": "subscribe", "channel": "orders", "address": wallet_address}
        pass

    async def place_order(self, order_id: str, token_id: str,
                         side: str, size: float, price: float) -> bool:
        """Place order via WebSocket (faster than REST POST)."""
        # Send {"type": "order", "order_id": order_id, "token_id": token_id, ...}
        pass

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        pass

    def _handle_message(self, msg: dict) -> None:
        """Handle incoming WebSocket message."""
        if msg.get("type") == "fill":
            self.on_fill(msg["order_id"], msg["price"], msg["shares"])
        elif msg.get("type") == "rejection":
            self.on_rejection(msg["order_id"], msg["error"])
```

**Why:** WebSocket order placement is 50-200ms faster than REST POST. For copy trading, latency = slippage.

### 4. LiveExecutionAdapter (MODIFIED)

**Current:** Uses REST POST (`client.post_order()`) with FOK orders only.

**Modified:** Delegates to `WebSocketOrderClient` for order placement, uses `OrderManager` for lifecycle tracking.

**Changes:**
```python
class LiveExecutionAdapter(ExecutionAdapter):
    def __init__(self, clob_client: ClobClient, ws_client: WebSocketOrderClient,
                 order_manager: OrderManager):
        self.clob_client = clob_client  # Still needed for midpoint, balances
        self.ws_client = ws_client
        self.order_manager = order_manager

    async def execute(self, decision: TradeDecision, event: MarketEvent,
                     all_prices: Dict) -> ExecutionResult:
        """Execute decision via WebSocket order placement."""
        # 1. Build order (same as current)
        token_id = self._get_token_id(event.trade.market_id, event.trade.side)
        mid = self.clob_client.get_midpoint(token_id)["mid"]
        price = mid + 0.05 if decision.action == "BUY" else mid - 0.05
        size = decision.dollars if decision.action == "BUY" else decision.shares

        # 2. Generate correlation ID
        correlation_id = str(uuid.uuid4())[:8]
        order_id = self._generate_order_id()  # UUID

        # 3. Place via WebSocket (async, faster than REST)
        success = await self.ws_client.place_order(
            order_id, token_id, decision.action, size, price
        )

        if not success:
            return ExecutionResult.rejected("WebSocket send failed")

        # 4. Track order in OrderManager
        self.order_manager.track_order(correlation_id, order_id, decision, event)

        # 5. Wait for fill/rejection (with timeout)
        # For FOK orders: fills come back in <1 second
        # For GTD orders: may be pending, return PENDING status
        await asyncio.sleep(0.1)  # Brief wait for FOK fill

        state = self.order_manager._pending.get(correlation_id)
        if state.status == OrderStatus.FILLED:
            return ExecutionResult.filled(state.filled_price, state.filled_shares)
        elif state.status == OrderStatus.REJECTED:
            return ExecutionResult.rejected(state.error)
        else:
            return ExecutionResult.pending(order_id)
```

**Why:** WebSocket placement + async lifecycle tracking enables GTD/GTC orders (not just FOK) and reduces latency.

### 5. SimulationExecutionAdapter (NEW)

**Purpose:** Simulate order execution using recorded prices. Replaces current direct portfolio manipulation in replayer.

**Interface:**
```python
class SimulationExecutionAdapter(ExecutionAdapter):
    def __init__(self, session_loader: SessionLoader):
        self.loader = session_loader  # Access to recorded prices

    def execute(self, decision: TradeDecision, event: MarketEvent,
                all_prices: Dict) -> ExecutionResult:
        """Simulate fill at recorded price."""
        # 1. Get price from all_prices (from SessionLoader)
        price_snap = all_prices.get(event.trade.token_id)
        if not price_snap:
            return ExecutionResult.rejected("No price data")

        # 2. Determine fill price (ask for buy, bid for sell)
        if decision.action == DecisionAction.BUY:
            fill_price = price_snap.ask
        else:
            fill_price = price_snap.bid

        if not fill_price or fill_price <= 0:
            return ExecutionResult.rejected("Invalid price")

        # 3. Apply slippage model (same as current strategy logic)
        # ... (reuse SLIPPAGE_PER_SHARE logic from strategy)

        # 4. Instant fill (simulation assumption)
        return ExecutionResult.filled(fill_price, decision.shares)
```

**Why:** Encapsulates simulation-specific logic (instant fills, slippage model). Keeps replayer clean.

---

## Integration Points

### 1. Runner → ExecutionEngine

**Current:**
```python
# runner.py line 277-306
decision = self.strategy.on_event(event)
if decision.action in (BUY, SELL):
    success = self._exec(event, decision)
    if success:
        self.strategy.on_fill(event, decision)
```

**New:**
```python
# runner.py line 277+
result = self.execution_engine.process_event(event, all_prices)
if result.success:
    # on_fill already called by ExecutionEngine
    self.stats["buys" if result.action == "BUY" else "sells"] += 1
```

**Change impact:** Remove `_exec()` method entirely. ExecutionEngine handles it.

### 2. Replayer → ExecutionEngine

**Current:**
```python
# replayer.py line 105
processor.process_event(i, event, all_prices_at_time)
# EventProcessor directly manipulates portfolio
```

**New:**
```python
# replayer.py line 105
result = self.execution_engine.process_event(event, all_prices_at_time)
# Same code path as live runner!
```

**Change impact:** Remove EventProcessor's direct portfolio manipulation. Use ExecutionEngine.

### 3. Strategy → No Changes

**Critical:** Strategy interface remains unchanged. Strategies return `TradeDecision`, receive `on_fill()` callbacks. Zero migration needed.

### 4. Recorder → Capture Order Lifecycle

**Current:** Records leader trades + our decisions.

**New:** Also record order lifecycle events (placed/filled/rejected) for replay.

**Changes:**
```python
# recorder.py add new event types
def record_order_placed(self, order_id: str, decision: TradeDecision):
    self._write_all({
        "type": "order_placed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_id": order_id,
        "action": decision.action.value,
        "dollars": str(decision.dollars),
        "shares": str(decision.shares),
        "price": str(decision.price),
    })

def record_order_filled(self, order_id: str, filled_price: Decimal,
                       filled_shares: Decimal):
    # ... similar
```

**Why:** Enables replay to simulate realistic order lifecycle (e.g., partial fills, rejections).

---

## Latency Optimization Strategy

**Goal:** Minimize time from leader trade detection → our order placed.

**Current bottlenecks:**
1. Blockchain polling (2-second blocks on Polygon)
2. WebSocket price fetch (10-50ms)
3. Strategy decision logic (1-5ms)
4. REST POST to CLOB (50-200ms)

**Optimizations:**

### 1. WebSocket Order Placement (HIGH IMPACT)

**Current:** REST POST to `https://clob.polymarket.com/order`
**Latency:** 50-200ms (HTTP round-trip)

**Optimized:** WebSocket send
**Latency:** 10-30ms (persistent connection, no TCP handshake)

**Implementation:** Use `WebSocketOrderClient` instead of `client.post_order()`.

**Expected improvement:** 40-170ms reduction per order.

### 2. Pre-computed Strategy State (MEDIUM IMPACT)

**Current:** Strategy recomputes drawdown, budget used, conviction on every event.

**Optimized:** Cache computations, only invalidate on state change.

**Example:**
```python
# Current: recomputed every event
def _get_hourly_drawdown(self, all_prices):
    drawdown = self._hourly_realized_loss
    for token_id, pos in self.portfolio.get_positions().items():
        # ... loops through all positions
    return drawdown

# Optimized: cache until position changes
@cached_property
def _hourly_drawdown(self):
    # ... same logic, but cached
    pass

def on_fill(self, event, decision):
    # Invalidate cache when position changes
    del self._hourly_drawdown
```

**Expected improvement:** 1-3ms reduction per event (more if many open positions).

### 3. Async Pipeline (MEDIUM IMPACT)

**Current:** Sequential processing:
```
blockchain.poll() → make_event() → strategy.on_event() → execute()
```

**Optimized:** Pipeline with async:
```python
async def _cycle(self):
    # Fetch blockchain + prices in parallel
    trades_task = asyncio.create_task(self.blockchain.poll())
    prices_task = asyncio.create_task(self.price_service.fetch_all())

    trades = await trades_task
    prices = await prices_task

    # Process trades with fresh prices
    for bt in trades:
        event = self._make_event(bt, prices)
        await self.execution_engine.process_event_async(event)
```

**Expected improvement:** 10-20ms reduction (parallel price fetch while waiting for blockchain).

### 4. VPS Placement (HIGH IMPACT)

**Current:** Run on local machine or random VPS.

**Optimized:** VPS in same AWS region as Polymarket CLOB (us-east-1).

**Expected improvement:** 20-100ms reduction in network latency.

### 5. Eliminate Duplicate API Calls (LOW IMPACT)

**Current:** `LiveExecutionAdapter` calls `client.get_midpoint()` for every order.

**Optimized:** Use WebSocket price feed midpoint (already subscribed).

**Expected improvement:** 5-10ms per order.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Duplicating Logic in Adapter

**What goes wrong:** Putting position tracking, cash management, or risk checks inside execution adapter.

**Why bad:** Breaks separation of concerns. Simulation and live diverge because logic lives in adapter, not strategy.

**Instead:** Adapter ONLY converts `TradeDecision` to platform orders. All logic in strategy.

### Anti-Pattern 2: Synchronous WebSocket Handling

**What goes wrong:** Blocking main thread waiting for WebSocket responses.

**Why bad:** If WebSocket is slow, entire bot freezes. Miss subsequent leader trades.

**Instead:** Async WebSocket client + OrderManager tracks pending orders. Main loop continues processing events.

### Anti-Pattern 3: No Order Timeout Handling

**What goes wrong:** Order gets stuck in PENDING state forever. Strategy thinks it has open position but doesn't.

**Why bad:** Portfolio state diverges from reality. Risk management fails.

**Instead:** OrderManager checks timeouts (30s), auto-cancels, reports to strategy.

### Anti-Pattern 4: Mixing Simulation and Live Code

**What goes wrong:** `if mode == "LIVE": ... else: ...` conditionals in shared components.

**Why bad:** Creates divergent code paths. Defeats purpose of shared execution engine.

**Instead:** Use dependency injection. Swap adapters, not logic.

---

## Build Order Recommendation

### Phase 1: Extract Shared Execution (CLEANUP)

**Goal:** Make simulation and live use identical code path.

**Tasks:**
1. Create `ExecutionEngine` class
2. Create `SimulationExecutionAdapter` (encapsulates current replay logic)
3. Refactor `SessionReplayer` to use `ExecutionEngine` + `SimulationExecutionAdapter`
4. Refactor `UniversalRunner` to use `ExecutionEngine` + `LiveExecutionAdapter`
5. Remove `_exec()` from runner, direct portfolio manipulation from replayer
6. Test: Run replay on historical sessions, verify PnL matches previous implementation

**Why first:** Establishes shared foundation. Prevents adding live WebSocket on top of divergent code.

**Validation:** Replay produces identical results before/after refactor.

### Phase 2: Add Order Lifecycle Tracking (FOUNDATION)

**Goal:** Track order states before adding WebSocket complexity.

**Tasks:**
1. Create `OrderManager` class
2. Modify `LiveExecutionAdapter` to use `OrderManager` for correlation IDs
3. Add timeout detection (30s) + logging
4. Add order lifecycle recording to `SessionRecorder`
5. Test: Dry run with REST orders, verify order tracking works

**Why second:** Order tracking is needed for WebSocket fills. Test with simpler REST first.

**Validation:** Dry run logs show order_placed → order_filled with correlation IDs.

### Phase 3: WebSocket Order Placement (LIVE TRADING)

**Goal:** Replace REST POST with WebSocket for lower latency.

**Tasks:**
1. Create `WebSocketOrderClient` class
2. Connect to `wss://ws-subscriptions-clob.polymarket.com/ws/orders`
3. Implement `place_order()`, `cancel_order()` via WebSocket
4. Wire callbacks to `OrderManager.on_fill()` / `on_rejection()`
5. Modify `LiveExecutionAdapter` to use `WebSocketOrderClient`
6. Test: Paper trade with WebSocket, verify fills arrive async
7. Add reconnection logic (WebSocket drops → reconnect + resubscribe)

**Why third:** Latency optimization only matters after foundation is solid.

**Validation:** WebSocket orders execute 40-170ms faster than REST (measure with correlation ID timestamps).

### Phase 4: Async Pipeline Optimization (PERFORMANCE)

**Goal:** Reduce total latency through parallelization.

**Tasks:**
1. Convert `UniversalRunner._cycle()` to async
2. Parallelize blockchain poll + price fetch
3. Make `ExecutionEngine.process_event()` async
4. Add async order placement (don't block on fill for GTD orders)
5. Test: Measure end-to-end latency (leader trade → our order placed)

**Why fourth:** Async refactor is risky. Do after WebSocket works synchronously.

**Validation:** Latency reduces by 10-20ms (measure with timestamps).

### Phase 5: Advanced Order Types (OPTIONAL)

**Goal:** Support GTD/GTC orders (not just FOK).

**Tasks:**
1. Add order type parameter to `TradeDecision`
2. Modify `OrderManager` to handle long-lived pending orders
3. Add order status monitoring (check fills after event loop)
4. Add cancellation logic (cancel unfilled orders at hour boundary)

**Why fifth:** FOK orders work for most copy trading. GTD adds complexity (unfilled orders at hour end).

**Validation:** GTD orders fill correctly or cancel gracefully.

---

## Dependency Injection Pattern

**Goal:** Swap execution adapters without changing orchestrator or strategy code.

**Implementation:**

```python
# config.py
class ExecutionConfig:
    mode: ExecutionMode  # LIVE or SIMULATION
    # ... other settings

# factory.py
class ExecutionFactory:
    @staticmethod
    def create_adapter(config: ExecutionConfig, **deps) -> ExecutionAdapter:
        if config.mode == ExecutionMode.SIMULATION:
            return SimulationExecutionAdapter(
                session_loader=deps["session_loader"]
            )
        elif config.mode == ExecutionMode.LIVE:
            return LiveExecutionAdapter(
                clob_client=deps["clob_client"],
                ws_client=deps["ws_client"],
                order_manager=deps["order_manager"],
            )
        else:
            raise ValueError(f"Unknown mode: {config.mode}")

# runner.py
config = ExecutionConfig(mode=ExecutionMode.LIVE)
adapter = ExecutionFactory.create_adapter(config, **live_deps)
engine = ExecutionEngine(strategy, adapter)
# Now runner uses engine.process_event() — same as replayer!

# replayer.py
config = ExecutionConfig(mode=ExecutionMode.SIMULATION)
adapter = ExecutionFactory.create_adapter(config, session_loader=loader)
engine = ExecutionEngine(strategy, adapter)
# Same ExecutionEngine, different adapter
```

**Why:** Zero `if mode == LIVE` conditionals. Adapters are swapped via factory. Code paths stay identical.

---

## Migration Path (Existing Strategies)

**Critical:** 7+ existing strategies in `src/strategies/`. Changes must not break them.

### Compatibility Layer

**Strategy interface remains unchanged:**
- `on_event(event: MarketEvent) → TradeDecision` (no change)
- `on_fill(event, decision)` (add optional third parameter `result`)
- `calculate_pnl(final_prices)` (no change)

**Modified signature:**
```python
# Old (still supported)
def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
    # ... apply trade

# New (optional)
def on_fill(self, event: MarketEvent, decision: TradeDecision,
            result: Optional[ExecutionResult] = None) -> None:
    # ... apply trade
    # Access result.filled_price, result.filled_shares if needed
```

**Why:** Strategies don't need to change. `ExecutionEngine` calls `on_fill()` with 2 or 3 args (introspect signature).

### Gradual Migration

**Phase 1:** Refactor runner/replayer to use `ExecutionEngine`. Strategies unchanged.

**Phase 2:** Add WebSocket order placement. Strategies unchanged.

**Phase 3 (optional):** Strategies can opt-in to `ExecutionResult` for advanced features (e.g., partial fills).

**Result:** Zero breaking changes. All existing strategies work with new architecture.

---

## Monitoring & Observability

**Critical for live trading:** Must know when things go wrong.

### Metrics to Track

**Latency:**
- Leader trade detected → strategy decision made
- Decision made → order placed (WebSocket send)
- Order placed → fill received
- End-to-end: leader trade → our fill

**Order Success Rate:**
- Orders placed / orders attempted
- Orders filled / orders placed
- Orders rejected / orders placed (by reason: insufficient balance, invalid price, etc.)
- Orders timed out / orders placed

**Execution Quality:**
- Slippage: |our fill price - leader fill price| / leader fill price
- Miss rate: leader trades we skipped / total leader trades
- Fill delay: our fill timestamp - leader fill timestamp

### Logging

**Order lifecycle logging:**
```python
# When order placed
logger.info(f"ORDER_PLACED: corr={correlation_id} token={token_id[:16]} "
            f"action={action} size={size} price={price}")

# When fill received
logger.info(f"ORDER_FILLED: corr={correlation_id} order_id={order_id} "
            f"filled_price={filled_price} filled_shares={filled_shares} "
            f"latency_ms={(time.time() - state.created_at) * 1000:.1f}")

# When rejection received
logger.warning(f"ORDER_REJECTED: corr={correlation_id} order_id={order_id} "
               f"error={error}")

# When timeout detected
logger.error(f"ORDER_TIMEOUT: corr={correlation_id} order_id={order_id} "
             f"pending_sec={time.time() - state.created_at:.1f}")
```

**Why:** Correlation IDs link logs across components. Latency tracking shows optimization impact.

### Alerting

**Critical alerts:**
- Order timeout rate >5% (WebSocket connection issue)
- Order rejection rate >20% (balance issue, price validation bug)
- Execution latency >500ms (network issue, need VPS move)
- WebSocket disconnect (reconnection failing)

**Where to send:** Log to file + send to monitoring service (e.g., Sentry, CloudWatch).

---

## Scalability Considerations

### At 100 Leader Trades/Hour

**Current architecture:** Adequate. Single-threaded execution handles 1-2 trades/minute easily.

**Bottleneck:** None. Python event loop processes trades in <10ms each.

### At 1000 Leader Trades/Hour

**Challenge:** 1 trade every 3-4 seconds. If execution takes >3s, events queue up.

**Solution:**
- Async execution pipeline (process next event while waiting for fill)
- WebSocket placement (reduces per-order latency)
- Pre-computed strategy state (reduces decision time)

**Bottleneck:** OrderManager tracking 100+ pending orders → O(n) timeout checks.

**Mitigation:** Use heap for timeout detection (O(log n) instead of O(n)).

### At 10K Leader Trades/Hour

**Challenge:** 2-3 trades/second. Need true parallelism.

**Solution:**
- Multi-threaded event processing (worker pool)
- Separate WebSocket connection per worker
- Redis for shared OrderManager state (cross-process tracking)

**Bottleneck:** Polymarket CLOB rate limits (likely hit at this volume).

**Mitigation:** Batch orders, use smart order routing.

**Realistic assessment:** 10K trades/hour unlikely for copy trading (leader would need to trade every 0.36 seconds). 1000/hour is realistic ceiling.

---

## Risk Management

### Order Placement Failures

**Scenario:** WebSocket send fails (connection dropped, server error).

**Mitigation:**
1. Retry once with exponential backoff (wait 100ms, retry)
2. If retry fails, fall back to REST POST (slower but more reliable)
3. Log failure, alert if fallback rate >10%

**Code:**
```python
async def execute(self, decision, event, all_prices):
    try:
        success = await self.ws_client.place_order(...)
        if not success:
            # Fallback to REST
            logger.warning("WebSocket order failed, falling back to REST")
            return self._place_order_rest(decision, event)
    except Exception as e:
        logger.error(f"WebSocket exception: {e}, falling back to REST")
        return self._place_order_rest(decision, event)
```

### Stale Price Data

**Scenario:** WebSocket price feed lags, we use stale bid/ask for decision.

**Mitigation:**
1. Timestamp price snapshots
2. Reject decisions if price >2 seconds old
3. Fetch fresh price from REST as fallback

**Code:**
```python
def on_event(self, event):
    price_age = time.time() - event.prices.timestamp
    if price_age > 2.0:
        logger.warning(f"Stale price data: {price_age:.1f}s old")
        return TradeDecision.skip("stale_price")
```

### Portfolio Divergence

**Scenario:** Live portfolio diverges from strategy's internal tracking (missed fill notification, etc.).

**Mitigation:**
1. Reconciliation: fetch actual positions from CLOB API every 5 minutes
2. Compare with strategy portfolio
3. Log discrepancies, alert if >10 shares difference
4. Auto-correct: update strategy portfolio to match actual (dangerous, log heavily)

**Code:** Already exists in `runner.py` line 339-365 (`_maybe_reconcile()`).

### WebSocket Reconnection

**Scenario:** WebSocket connection drops (network issue, server restart).

**Mitigation:**
1. Detect disconnect in `_handle_message()` (connection closed)
2. Reconnect with exponential backoff (1s, 2s, 4s, ...)
3. Resubscribe to order updates
4. Mark all pending orders as UNKNOWN (may have filled during disconnect)
5. Reconcile portfolio after reconnection

**Code:**
```python
async def _reconnect(self):
    for attempt in range(5):
        wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
        logger.info(f"Reconnecting WebSocket (attempt {attempt+1}/5)")
        await asyncio.sleep(wait)
        if await self.connect():
            logger.info("WebSocket reconnected")
            return True
    logger.error("WebSocket reconnection failed after 5 attempts")
    return False
```

---

## Sources

**Architecture Patterns:**
- [Python Dependency Injection: A Guide for Cleaner Code Design | DataCamp](https://www.datacamp.com/tutorial/python-dependency-injection)
- [PyNest Dependency Injection](https://pythonnest.github.io/PyNest/dependency_injection/)
- [GitHub - ets-labs/python-dependency-injector](https://github.com/ets-labs/python-dependency-injector)

**WebSocket Trading Optimization:**
- [How To Utilize WebSockets In Creating A Profitable Trading Bot - With Python | Medium](https://konstantinmb.medium.com/how-to-utilize-websockets-in-creating-a-profitable-trading-bot-with-python-5cb840e6c753)
- [Optimizing Real-Time Market Data Feeds: A Python WebSocket Approach | Medium](https://medium.com/@emily19980210/optimizing-real-time-market-data-feeds-a-python-websocket-approach-for-us-stocks-f141781752fb)
- [How Latency Impacts Polymarket Bot Performance | QuantVPS](https://www.quantvps.com/blog/how-latency-impacts-polymarket-trading-performance)

**Order Lifecycle Management:**
- [Trading API — ProjectX Python SDK 3.3.4 documentation](https://project-x-py.readthedocs.io/en/stable/api/trading.html)
- [Websocket Streaming | Alpaca Markets](https://docs.alpaca.markets/docs/websocket-streaming)
- [API Changelog - Kalshi](https://docs.kalshi.com/changelog)

**Polymarket CLOB Integration:**
- [GitHub - Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- [Quickstart - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/quickstart)
- [How to Use Polymarket API: Complete Developer Guide (2026) | Hypereal](https://hypereal.tech/a/polymarket-api)

**Trading Bot Best Practices:**
- [AI Python Trading Bot: Build Your First Binance/OKX Bot (2026) | Exmon](https://academy.exmon.pro/ai-python-trading-bot-build-your-first-binanceokx-bot-2026)
- [Step-by-Step Crypto Trading Bot Development Guide (2026) | Appinventiv](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [GitHub - jesse-ai/jesse: Advanced crypto trading bot](https://github.com/jesse-ai/jesse)
- [GitHub - freqtrade/freqtrade: Free, open source crypto trading bot](https://github.com/freqtrade/freqtrade)
