# Architecture Patterns: Copy Trading Bot Simulation & Optimization

**Domain:** Copy trading bot simulation, backtesting, and strategy optimization
**Researched:** 2026-01-30
**Confidence:** MEDIUM (based on existing codebase analysis and established backtesting patterns)

## Executive Summary

Copy trading bot simulation systems require a clear separation between **replay infrastructure** (event sequencing, historical data) and **execution simulation** (order fills, slippage, market impact). The key architectural challenge is adding realism without rewriting the replay engine.

**Recommended approach:** Insert a **Market Simulator** layer between Strategy and Execution that intercepts trade decisions and models realistic fill behavior. This allows gradual enhancement of simulation realism while preserving existing replay logic.

## Current Architecture Analysis

### Existing Layered Structure

```
Data Layer (live_source, blockchain_detector)
    ↓ MarketEvent
Strategy Layer (base.Strategy → concrete strategies)
    ↓ TradeDecision
Execution Layer (ExecutionAdapter: live, dry_run, hybrid)
    ↓ Fills
Framework Layer (runner, recorder, replay)
    ↓
Simulation Layer (optimizer, limit_order_sim, metrics)
```

**Current replay flow:**
1. `SessionReplayer.load()` reads JSONL session file
2. `SessionReplayer.replay(strategy)` feeds events to strategy
3. Strategy produces TradeDecision
4. **Instant fill assumed** with fixed spread cost
5. Portfolio updated with full fill amount

**Problem:** No modeling of:
- Order book depth (partial fills)
- Slippage (price impact from order size)
- Fill timing (limit orders waiting for price)
- Market dynamics (spread changes during fill)

### Existing Limit Order Simulator (Not Integrated)

**Location:** `src/simulation/limit_order_sim.py`

**What it does:**
- Models limit orders with TTL (time-to-live)
- Checks if bid/ask crosses limit price
- Tracks filled/expired orders
- **Standalone:** Not connected to SessionReplayer

**Gap:** Needs integration point in replay flow.

## Recommended Component Architecture

### 1. Market Simulator Layer (NEW)

**Purpose:** Intercept trade decisions and model realistic execution.

**Component boundaries:**

```
Strategy → TradeDecision → [MARKET SIMULATOR] → ExecutionResult → Portfolio
                                    ↑
                           (Market state, order book)
```

**Responsibilities:**
- Receive TradeDecision from strategy
- Model execution realism (slippage, partial fills, timing)
- Return ExecutionResult with actual fill price/quantity
- Track pending orders (for limit orders)
- Update market state from price snapshots

**Interface:**

```python
class MarketSimulator(ABC):
    """Abstract interface for execution simulation."""

    def submit_order(self, decision: TradeDecision,
                     market_state: MarketState) -> ExecutionResult:
        """Submit order for execution. Returns immediate or pending result."""
        pass

    def update_market(self, prices: PriceSnapshot) -> List[ExecutionResult]:
        """Update market state, check pending orders. Returns fills."""
        pass

    def get_pending_orders(self) -> List[PendingOrder]:
        """Return orders awaiting fill."""
        pass
```

**Implementations:**

| Implementation | Realism Level | Use Case |
|----------------|---------------|----------|
| `InstantSimulator` | None (current behavior) | Fast optimization, baseline |
| `SpreadSimulator` | Basic (fixed spread cost) | Quick backtests |
| `LimitOrderSimulator` | Medium (order book crossing) | Realistic timing |
| `SlippageSimulator` | High (depth, impact) | Production-quality backtest |

### 2. Position Sizing Component (REFACTOR)

**Current state:** Sizing logic scattered across:
- `src/framework/runner.py` (lines 152-161): Scale calculation
- Strategy `_buy()` methods: Receive `scaled` amount, apply multipliers
- `src/core/config.py`: `ScalingConfig` (our_capital, leader_capital, k_factor)

**Problem:** Position sizing concerns mixed with execution logic.

**Recommended structure:**

```
┌─────────────────────────────────────────────────┐
│           Position Sizer (NEW)                  │
│  - Capital allocation                           │
│  - Risk limits                                  │
│  - Size calculation                             │
└─────────────────────────────────────────────────┘
         ↓ sized_amount
┌─────────────────────────────────────────────────┐
│              Strategy                           │
│  - Entry/exit signals                           │
│  - Multipliers (burst, momentum, etc)           │
└─────────────────────────────────────────────────┘
         ↓ TradeDecision(size=adjusted_amount)
┌─────────────────────────────────────────────────┐
│         Market Simulator                        │
│  - Slippage                                     │
│  - Partial fills                                │
└─────────────────────────────────────────────────┘
```

**Component: PositionSizer**

**Responsibilities:**
- Calculate base position size from leader's trade
- Apply capital scaling (our_capital / leader_capital * k_factor)
- Enforce risk limits (hourly budget, cash reserve)
- Portfolio-aware sizing (consider existing positions)

**Does NOT:**
- Make entry/exit decisions (Strategy's job)
- Apply strategy-specific multipliers (Strategy's job)
- Model execution realism (MarketSimulator's job)

**Interface:**

```python
class PositionSizer:
    """Calculates position sizes based on capital and risk limits."""

    def size_for_trade(self, leader_amount: Decimal,
                       portfolio: Portfolio,
                       config: ScalingConfig) -> Decimal:
        """Calculate our position size for leader's trade."""
        base_scale = config.our_capital / config.leader_capital * config.k_factor
        scaled_amount = leader_amount * base_scale

        # Apply risk limits
        return self._apply_constraints(scaled_amount, portfolio, config)

    def _apply_constraints(self, amount: Decimal,
                          portfolio: Portfolio,
                          config: ScalingConfig) -> Decimal:
        """Enforce hourly budget, cash reserve, position limits."""
        # Budget checks
        # Reserve checks
        # Max position size
        pass
```

**Location:** `src/core/position_sizer.py` (new file in core layer)

**Why core layer:** Capital management is a fundamental concern, not strategy-specific.

### 3. Slippage Models (NEW)

**Purpose:** Model price impact and market depth.

**Models to implement:**

#### Fixed Spread Model (Simple)
```python
class FixedSpreadModel:
    """Buy at ask, sell at bid. No additional slippage."""

    def get_fill_price(self, side: OrderSide,
                       bid: Decimal, ask: Decimal,
                       size: Decimal) -> Decimal:
        return ask if side == OrderSide.BUY else bid
```

#### Square Root Impact Model (Standard)
```python
class SqrtImpactModel:
    """Slippage = spread + sqrt(size/liquidity) * volatility."""

    def get_fill_price(self, side: OrderSide,
                       bid: Decimal, ask: Decimal,
                       size: Decimal,
                       avg_volume: Decimal) -> Decimal:
        mid = (bid + ask) / 2
        spread = ask - bid

        # Market impact: larger orders move price more
        impact_factor = (size / avg_volume).sqrt() * spread

        if side == OrderSide.BUY:
            return mid + spread/2 + impact_factor
        else:
            return mid - spread/2 - impact_factor
```

#### Order Book Depth Model (Realistic)
```python
class DepthModel:
    """Walk order book levels to fill order."""

    def __init__(self, levels: List[Tuple[Decimal, Decimal]]):
        """levels: [(price, size), ...] for bid or ask side."""
        self.levels = levels

    def get_fill_price(self, side: OrderSide,
                       size: Decimal) -> FillResult:
        """Fill against order book levels. May return partial."""
        filled = Decimal("0")
        total_cost = Decimal("0")

        for price, available in self.levels:
            if filled >= size:
                break

            fill_qty = min(size - filled, available)
            total_cost += fill_qty * price
            filled += fill_qty

        if filled == 0:
            return FillResult(filled=0, avg_price=None, status="UNFILLED")

        avg_price = total_cost / filled
        status = "FULL" if filled == size else "PARTIAL"

        return FillResult(filled=filled, avg_price=avg_price, status=status)
```

**Slippage model selection:**

| Market Condition | Recommended Model |
|------------------|-------------------|
| Deep liquid markets (major events) | Fixed spread |
| Normal markets | Square root impact |
| Thin markets (niche events) | Order book depth |
| Unknown liquidity | Conservative (2x spread penalty) |

### 4. Integration Point: Replay with Simulation

**Challenge:** Add simulation realism without rewriting SessionReplayer.

**Solution:** Inject MarketSimulator into replay flow.

**Modified replay architecture:**

```python
class SessionReplayer:
    def __init__(self, session_path: Path,
                 market_simulator: Optional[MarketSimulator] = None):
        self.session_path = session_path
        self.simulator = market_simulator or InstantSimulator()

    def replay(self, strategy: Strategy) -> ReplayResult:
        """Replay session through strategy with simulation."""
        for event in self.events:
            if event["type"] == "price_snapshot":
                # Update simulator with market state
                fills = self.simulator.update_market(event["prices"])

                # Process any fills from pending orders
                for fill in fills:
                    self._apply_fill(fill)

            elif event["type"] == "market_event":
                # Strategy produces decision
                decision = strategy.on_event(event)

                if decision.action != Action.HOLD:
                    # Submit to simulator instead of instant fill
                    result = self.simulator.submit_order(
                        decision,
                        self._get_market_state(event)
                    )

                    if result.status == "FILLED":
                        self._apply_fill(result)
                    elif result.status == "PENDING":
                        # Order awaiting fill (limit order)
                        pass
```

**Backward compatibility:** `InstantSimulator` preserves current behavior.

**Gradual enhancement path:**
1. Start: `InstantSimulator` (no change)
2. Add: `SpreadSimulator` (fixed spread cost)
3. Add: `LimitOrderSimulator` (integrate existing limit_order_sim.py)
4. Add: `SlippageSimulator` (market impact modeling)

## Data Flow Diagrams

### Current Flow (Instant Fills)

```
SessionReplayer
    ↓ load JSONL
[Event 1: leader trade] → Strategy → TradeDecision(BUY, 100 shares)
    ↓ instant fill
Portfolio.apply_buy(100 shares @ fixed price)
    ↓
[Event 2: price update] → Strategy → ...
```

**Problem:** No realism in fill modeling.

### Proposed Flow (With Simulation)

```
SessionReplayer
    ↓ load JSONL
[Event 1: leader trade] → Strategy → TradeDecision(BUY, 100 shares)
    ↓
MarketSimulator.submit_order(decision, market_state)
    ↓ check liquidity, slippage
ExecutionResult(FILLED, 95 shares @ adjusted price)  [partial fill!]
    ↓
Portfolio.apply_buy(95 shares @ slippage-adjusted price)
    ↓
[Event 2: price update] → MarketSimulator.update_market()
    ↓ check pending orders
ExecutionResult(FILLED, 5 shares @ new price)  [rest of order fills]
    ↓
Portfolio.apply_buy(5 shares @ new price)
```

**Benefit:** Models partial fills, slippage, timing.

## Build Order & Dependencies

### Phase 1: Extract Position Sizing (Foundation)

**Goal:** Separate sizing concerns from strategy logic.

**Tasks:**
1. Create `src/core/position_sizer.py`
2. Move capital scaling logic from `runner.py` to `PositionSizer`
3. Update strategies to use `PositionSizer.size_for_trade()`
4. Add tests for risk constraints

**Deliverable:** Position sizing in dedicated component.

**Why first:** Sizing logic needed by all simulators. Clean this before adding complexity.

**Dependencies:** None (refactor only)

### Phase 2: Create Market Simulator Abstraction (Architecture)

**Goal:** Define interfaces and instant simulator.

**Tasks:**
1. Create `src/simulation/market_simulator.py` with abstract interface
2. Implement `InstantSimulator` (preserves current behavior)
3. Update `SessionReplayer` to accept optional `MarketSimulator`
4. Add integration tests (instant simulator = current behavior)

**Deliverable:** Simulation abstraction in place, no behavior change.

**Why second:** Establishes extension point without breaking existing code.

**Dependencies:** None (adds abstraction, defaults to current behavior)

### Phase 3: Add Spread & Slippage Models (Realism)

**Goal:** Model basic market costs.

**Tasks:**
1. Implement `SpreadSimulator` (fixed bid-ask spread cost)
2. Implement `SqrtImpactModel` for slippage
3. Implement `SlippageSimulator` combining spread + impact
4. Add configuration for slippage parameters
5. Compare results: instant vs spread vs slippage

**Deliverable:** Basic market cost modeling.

**Why third:** Lowest-hanging fruit for realism improvement.

**Dependencies:** Phase 2 (needs simulator abstraction)

### Phase 4: Integrate Limit Order Simulator (Timing)

**Goal:** Model order fill timing with limit orders.

**Tasks:**
1. Refactor `src/simulation/limit_order_sim.py` to implement `MarketSimulator`
2. Handle pending orders in replay flow
3. Add TTL (time-to-live) and expiration logic
4. Compare instant vs limit order results

**Deliverable:** Limit order simulation in replay.

**Why fourth:** Requires more complex state management (pending orders).

**Dependencies:** Phase 2 (needs simulator abstraction), Phase 3 (should model spread + timing)

### Phase 5: Add Order Book Depth Modeling (Advanced)

**Goal:** Model partial fills from order book.

**Tasks:**
1. Extend `PriceSnapshot` to include order book depth (optional)
2. Implement `DepthSimulator` that walks order book levels
3. Handle partial fills in portfolio logic
4. Add depth data collection in recorder (if available from API)

**Deliverable:** Realistic partial fill modeling.

**Why last:** Requires order book data, which may not be available in historical sessions.

**Dependencies:** Phase 4 (partial fills require limit order handling)

### Phase 6: Parallelize Optimizer (Performance)

**Goal:** Speed up grid search.

**Tasks:**
1. Profile current optimizer performance
2. Implement parallel replay with `ProcessPoolExecutor`
3. Add progress reporting
4. Benchmark: sequential vs parallel

**Deliverable:** Faster optimization.

**Why last:** Optimization speed doesn't affect simulation quality. Do after realism is correct.

**Dependencies:** Phases 1-4 (optimize after simulation is realistic)

## Dependency Graph

```
Phase 1 (Position Sizing)
    ↓ sizing logic extracted
Phase 2 (Simulator Abstraction)
    ↓ extension point created
    ├─→ Phase 3 (Spread/Slippage)
    │       ↓ basic costs modeled
    └─→ Phase 4 (Limit Orders)
            ↓ timing modeled
        Phase 5 (Order Book Depth)
            ↓ partial fills modeled
        Phase 6 (Parallel Optimizer)
```

**Critical path:** 1 → 2 → 4 (position sizing, abstraction, limit orders)

**Parallel work:** Phase 3 (slippage) can happen alongside Phase 4 (limit orders)

## Validation Strategy

### How to Verify Simulation Realism

**1. Sanity Checks (Instant → Realistic):**
- Realistic simulation should have **lower** returns (costs added)
- Partial fills should reduce position sizes
- Limit orders should have **delayed** fills (timestamps after decision)

**2. Comparison Matrix:**

| Metric | Instant Fill | + Spread | + Slippage | + Limit Orders | + Depth |
|--------|--------------|----------|------------|----------------|---------|
| Total trades | 100 | 100 | 100 | 95 (5 expired) | 95 |
| Avg fill price | $0.50 | $0.505 | $0.512 | $0.509 | $0.515 |
| Total cost | $5000 | $5050 | $5120 | $5090 | $5150 |
| Partial fills | 0 | 0 | 0 | 0 | 8 |
| Avg fill delay | 0s | 0s | 0s | 1.2s | 1.5s |

**Expected trend:** Each layer of realism adds cost and reduces fills.

**3. A/B Testing:**
- Run same session with instant vs realistic simulator
- Compare: final portfolio value, trade count, fill rates
- Document: which strategies are most affected by realism

**4. Historical Validation:**
- If live trading data exists: compare backtest vs actual results
- Simulation should be **pessimistic** (worse than live)
- Gap indicates missing costs (fees, etc)

## Anti-Patterns to Avoid

### Anti-Pattern 1: Embedding Simulation in Strategy

**Bad:**
```python
class ConservativeStrategy(Strategy):
    def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        # Calculate slippage inside strategy
        slippage = self._estimate_slippage(scaled)
        adjusted = scaled * (1 - slippage)
        return TradeDecision(action=Action.BUY, size=adjusted)
```

**Why bad:**
- Strategy logic mixed with execution modeling
- Cannot swap slippage models
- Testing requires mocking market conditions

**Instead:** Keep strategies pure (signal generation), delegate to MarketSimulator.

### Anti-Pattern 2: Tight Coupling to Session Format

**Bad:**
```python
class MarketSimulator:
    def submit_order(self, decision: TradeDecision):
        # Read directly from session file
        prices = self._load_from_jsonl(self.session_path)
```

**Why bad:**
- Simulator cannot work with live data
- Session format changes break simulator
- Cannot test without session file

**Instead:** Pass market state as parameters, decouple from data source.

### Anti-Pattern 3: Stateful Replay

**Bad:**
```python
class SessionReplayer:
    def replay(self, strategy: Strategy):
        # Simulator stores state globally
        global pending_orders
        pending_orders = []
```

**Why bad:**
- Cannot run multiple replays in parallel
- State leaks between runs
- Testing requires global cleanup

**Instead:** Simulator encapsulates state, reset between replays.

### Anti-Pattern 4: Premature Optimization

**Bad:**
```python
# Implement full order book matching engine before testing spread model
class OrderBookSimulator:
    def __init__(self):
        self.order_book = RedBlackTree()  # Complex data structure
        self.matching_engine = PriceTimePriority()
```

**Why bad:**
- High complexity, low immediate value
- Unknown if order book data is available
- Delays testing of basic improvements

**Instead:** Start simple (fixed spread), measure impact, then add complexity if needed.

## Key Abstractions

### ExecutionResult

```python
@dataclass
class ExecutionResult:
    """Result of order submission to market simulator."""
    status: str  # "FILLED", "PARTIAL", "PENDING", "REJECTED"
    filled_qty: Decimal
    avg_fill_price: Optional[Decimal]
    fill_time: Optional[datetime]
    remaining_qty: Decimal = Decimal("0")
    order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

### MarketState

```python
@dataclass
class MarketState:
    """Current market conditions for simulation."""
    token_id: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    spread_pct: Decimal
    last_price: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    order_book_depth: Optional[List[OrderBookLevel]] = None
```

### PendingOrder

```python
@dataclass
class PendingOrder:
    """Order awaiting fill."""
    order_id: str
    token_id: str
    side: OrderSide
    limit_price: Decimal
    quantity: Decimal
    filled: Decimal
    submitted_at: datetime
    expires_at: Optional[datetime]
    metadata: Dict[str, Any]
```

## Configuration Schema

```yaml
simulation:
  # Execution model selection
  mode: "instant" | "spread" | "slippage" | "limit_order" | "depth"

  # Spread modeling
  fixed_spread_bps: 50  # 0.5% spread if not available from snapshot

  # Slippage modeling (sqrt impact)
  slippage:
    enabled: true
    impact_factor: 0.1  # Multiplier for sqrt(size/volume)
    min_slippage_bps: 10  # Minimum 0.1%
    max_slippage_bps: 500  # Cap at 5%

  # Limit order settings
  limit_orders:
    default_ttl_seconds: 8.0  # Order expiration
    use_limit_orders: true
    offset_bps: 10  # Place limit order 0.1% better than current price

  # Order book depth
  order_book:
    use_depth: false  # Requires depth data in session
    min_liquidity_threshold: 100  # Reject if insufficient depth

  # Fill timing
  fill_delay_ms: 0  # Simulated network latency
```

## Open Questions & Future Research

### 1. Fee Modeling
**Question:** Should gas fees and Polymarket fees be in simulator or separate component?

**Tradeoff:**
- **In simulator:** Complete cost model, single source of truth
- **Separate:** Easier to test, swap fee structures

**Recommendation:** Separate fee calculator called by simulator. Fees are policy (configurable), slippage is market dynamics (simulated).

### 2. Multi-Asset Correlation
**Question:** Should simulator model cross-market impact (buying in one market affects another)?

**Current:** Each market simulated independently.

**Enhancement:** If buying token A moves token B (e.g., correlated markets), simulator could propagate impact.

**Complexity:** High. Defer until single-market simulation is validated.

### 3. Live Execution Adapter Reuse
**Question:** Can same MarketSimulator interface work for live trading?

**Possibility:** `LiveMarketSimulator` wraps actual API calls, returns pending orders that poll for fills.

**Benefit:** Unified interface for backtest and live.

**Risk:** Live trading has different failure modes (network errors, rate limits). May need separate abstraction.

**Recommendation:** Keep separate for now. Revisit after simulation is stable.

### 4. Simulation State Persistence
**Question:** Should simulator checkpoint state for resume?

**Use case:** Long optimization runs that crash.

**Complexity:** Moderate (serialize pending orders, market state).

**Priority:** Low. Only valuable for >1hr optimization runs.

## Sources & Confidence

**Source hierarchy:**

| Claim | Source | Confidence |
|-------|--------|------------|
| Position sizing is scattered | Codebase grep (runner.py, strategies) | HIGH |
| Limit order sim exists but not integrated | `src/simulation/limit_order_sim.py` line 358 TODO | HIGH |
| Square root impact model is standard | Established quant finance pattern (Almgren-Chriss) | MEDIUM |
| Order book depth modeling is complex | Codebase analysis, no depth data in sessions | HIGH |
| Parallel optimization will help | ProcessPoolExecutor imported but unused | HIGH |

**Gaps:**
- No Polymarket-specific documentation for their order book API
- Unknown if order book depth data is available historically
- Slippage parameters may need empirical tuning

**Validation needed:**
- Phase-specific research before implementing order book depth (check API capabilities)
- Empirical testing of slippage models against live trading results (if data exists)

---

*Architecture research completed: 2026-01-30*
*Confidence: MEDIUM (codebase-driven, standard backtesting patterns)*
*Ready for roadmap creation: YES*
