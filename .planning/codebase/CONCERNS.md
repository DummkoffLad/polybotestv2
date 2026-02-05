# Codebase Concerns

**Analysis Date:** 2026-02-05

## Duplicate Code - Critical Issue

### **Massive Duplication Across Strategies (10 identical implementations)**

**Files Affected:**
- `src/strategies/aggressive/strategy.py`
- `src/strategies/conservative/strategy.py`
- `src/strategies/hybrid_conservative/strategy.py`
- `src/strategies/price_level/strategy.py`
- `src/strategies/velocity/strategy.py`
- `src/strategies/spread_aware/strategy.py`
- `src/strategies/momentum/strategy.py`

**Issue:** Each strategy contains near-identical boilerplate:
```python
# Duplicated in EVERY strategy (10+ times):
def _to_side(side: TradeSide) -> Side:
    return Side.UP if side == TradeSide.UP else Side.DOWN

def _check_extreme_prices(self, event: MarketEvent) -> Optional[TradeDecision]:
    """Check for price extremes - auto-sell at 0.99, treat 0.01 as 0."""
    trade, prices = event.trade, event.prices
    pos = self.portfolio.get(trade.token_id, trade.market_id, _to_side(trade.side))

    if pos.shares > 0 and prices.bid and prices.bid >= PRICE_EXTREME_HIGH:
        logger.info(f"Auto-sell at extreme price {prices.bid}")
        shares_to_sell = pos.shares
        if shares_to_sell >= MIN_LIMIT_ORDER_SHARES:
            self.sells += 1
            return TradeDecision.sell(...)
    # ... more identical logic

def _skip(self, reason: str) -> TradeDecision:
    self.skips += 1
    self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
    return TradeDecision.skip(reason)

def _check_hourly_reset(self, event_time: datetime) -> None:
    current_hour = event_time.hour
    if self._current_hour is not None and current_hour != self._current_hour:
        self.hourly_budget_used = Decimal("0")
    self._current_hour = current_hour
```

**Impact:**
- 29 instances of `_to_side()` across 10 strategy files (should be 1 utility function)
- Same 150+ line initialization logic repeated in every strategy's `initialize()` method
- Same extreme price handling, skip tracking, and hourly budget reset copied verbatim
- Bug fixes must be applied in 10 places or some strategies stay broken
- Each strategy file is 200-400 lines when could be 50-100 lines with base class utilities

**Fix Approach:**
1. Move utility functions to `src/strategies/base.py`:
   - `_to_side()` helper → shared utility
   - `_skip()` → base class method
   - `_check_extreme_prices()` → base class or mixin
   - `_check_hourly_reset()` → base class method
2. Create base strategy mixin classes for common patterns (MirrorBase, CopyTraderBase)
3. Reduce each strategy file to only what's unique (sizing logic, decision rules)

**Test Coverage:** Most strategies have corresponding unit tests but duplication makes them harder to maintain consistently

---

### **Shared Methods Across Simple Follow and Profit Taker**

**Files:** `src/strategies/simple_follow/strategy.py`, `src/strategies/profit_taker/strategy.py`

**Duplicated Logic:**
- Leader position tracking (lines ~70 in both)
- Portfolio apply_buy/apply_sell calls with identical patterns
- PnL calculation methods nearly identical
- Skip reason tracking (same dict pattern)

**Impact:** Both strategies recompute leader position state independently, making it hard to keep them synchronized

---

## Inconsistent Patterns - Medium Priority

### **Multiple Formats for Same Concept**

**1. Configuration Management (3 different approaches):**
- `src/strategies/base.py`: `StrategyConfig.from_dict()` factory
- `src/strategies/simple_follow/strategy.py`: Direct params via `config.params.get()`
- `src/strategies/mirror/strategy.py`: Complex nested SizingConfig, CapitalManager, etc.

**2. Side Representation (2 formats)**
- `TradeSide` enum (UP/DOWN) from `src/data/models.py`
- `Side` enum (UP/DOWN) from `src/core/types.py`
- Every strategy must convert: `_to_side(trade.side)` converting TradeSide → Side

**3. Order Type Definitions (2 duplicate enums)**
- `OrderType` in `src/strategies/base.py` (MARKET, LIMIT)
- `OrderType` in `src/core/types.py` (MARKET, LIMIT)
- Both are identical, causing import confusion

**4. Skip Tracking (2 approaches)**
```python
# Approach 1: Simple dict
self.skip_reasons: Dict[str, int] = {}
self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

# Approach 2: Counter-style (seen in some test code)
# Different accounting, hard to aggregate
```

**Impact:**
- New developers must understand which enum/class to use
- Converting between Side and TradeSide adds error-prone boilerplate
- Inconsistent skip tracking makes aggregate statistics unreliable
- Merging configs requires understanding 3 different patterns

**Fix Approach:**
1. Consolidate: Keep ONE OrderType in `src/core/types.py`, import elsewhere
2. Consolidate: Keep ONE Side representation, remove TradeSide conversion step
3. Standardize config: Create ConfigBuilder pattern for all strategies
4. Unify skip tracking: Create SkipReasonTracker utility class

---

## Code Organization Issues - Medium Priority

### **Fragmented Analysis/Reporting Modules**

**Files:** `src/analysis/`, `src/comparison/`, multiple visualization tools

**Issue:** Same metrics calculated in multiple places:
- `src/analysis/reports.py` - calculates PnL, Sharpe, drawdown
- `src/comparison/metrics.py` - calculates Sharpe, returns, correlation
- `src/simulation/follow_metrics.py` - calculates follow quality metrics
- Test files duplicate calculations again

**Example - PnL calculation:**
```python
# src/strategies/simple_follow/strategy.py:433 (calculate_pnl method)
unrealized = sum(...)

# src/strategies/profit_taker/strategy.py:350 (same logic, different syntax)
unrealized = sum(...)

# src/framework/replay.py (third implementation)
# Fourth implementation in test files
```

**Impact:**
- Bug in PnL calc discovered in one place but not fixed everywhere
- Difficult to audit which calculation is "canonical"
- Performance analysis modules contradict each other

---

### **Abandoned/Dead Code**

**Files:**
- `src/simulation/optimizer_old.py` (877 lines) - marked as old but not deleted
- Multiple old parameter files in `.planning/quick/` directories

**Impact:** Creates confusion about which optimizer is active, increases maintenance burden

**Fix:** Delete optimizer_old.py if deprecated, or document why it's kept

---

## Performance & Fragility Issues

### **Large Complex Files**

**Oversized Files (should be <300 lines):**
- `src/framework/replay.py` (821 lines) - handles parsing, event generation, execution, analysis
- `src/simulation/follow_metrics.py` (467 lines) - single massive class
- `tests/unit/test_strategies.py` (663 lines) - monolithic test file

**Impact:**
- Harder to understand single responsibility
- More merge conflicts
- Harder to test subcomponents in isolation

**Fix Approach:** Break into smaller, focused modules:
- `replay.py` → split into event_parser.py, execution_engine.py, result_builder.py
- `follow_metrics.py` → split into quality_scorer.py, signal_detector.py

---

### **Missing Error Handling**

**Pattern Observed:** Minimal validation of extreme cases:

```python
# src/strategies/base.py:calculate_actual_spread_pct (lines 20-31)
# Returns 0 silently if prices invalid, could mask real bugs
if not prices.bid or not prices.ask or prices.bid <= 0 or prices.ask <= 0:
    return Decimal("0")  # Falls through silently
```

**Issue:** Silently returning 0 for invalid spreads means bad price data doesn't trigger warnings

**Impact:** Strategy might execute at terrible spreads without logging

---

## Hardcoded Magic Numbers

**Issue:** Strategy parameters hardcoded in multiple files:

```python
# src/strategies/profit_taker/strategy.py (lines 36-46)
PROFIT_TARGET_LOW = Decimal("35")
PROFIT_TARGET_MID = Decimal("20")
PROFIT_TARGET_HIGH = Decimal("12")
IGNORE_LEADER_MINISELLS_PCT = Decimal("10")

# src/strategies/simple_follow/strategy.py (lines 36-41)
DEFAULT_MIN_BET = Decimal("1")
DEFAULT_MAX_BET = Decimal("7")
DEFAULT_PER_MARKET_CAP = Decimal("20")
```

**vs.** Config-based approach in `src/strategies/mirror/strategy.py`

**Impact:** Inconsistent whether parameters are tunable via config or hardcoded
- Some strategies can't be reconfigured without code edits
- Testing different parameters requires modifying source files

---

## Test Coverage Gaps

**Files:** `tests/unit/test_simple_follow.py`, `tests/unit/test_strategies.py`

**Gap 1: Inconsistent Test Data**
- Some tests use realistic market data (equity curves with 1000+ points)
- Others use minimal synthetic data (5-10 candles)
- Makes test results non-comparable

**Gap 2: Strategy-Specific Tests Missing**
- `src/strategies/price_level/`, `src/strategies/velocity/` have NO unit tests
- Only `simple_follow` and `profit_taker` are well-tested
- Other 8 strategies untested

**Gap 3: Integration Test Gaps**
- No tests for strategy coordination (multiple strategies running together)
- No tests for extreme market conditions (flash crash, liquidity gap)
- No tests for edge cases in partial fill aggregation (timeout, missing price data)

---

## Known Issues & TODOs

**Issue 1: Unfinished Integration (HIGH)**
- File: `src/simulation/limit_order_sim.py` (line 358)
- Comment: `# TODO: Integrate with SessionReplayer using limit order execution mode`
- Status: Limit order simulator exists but not integrated with main replay loop
- Impact: Can't test limit order strategies end-to-end

**Issue 2: Log Format Parsing**
- Files: `src/framework/replay.py` (line 116), `src/tools/split_session_hourly.py` (line 48)
- Comment: `# Handle prefixed log format: [MODE=XXX] {...}`
- Issue: Two independent implementations of same log parsing logic (brittle to format changes)

---

## Scaling Concerns

### **Dictionary-Based State Accumulation**

Pattern seen in multiple strategies:
```python
# src/strategies/simple_follow/strategy.py
self.leader_tracker: Dict[str, Dict] = {}  # Unbounded growth
self._tx_aggregator: Dict[str, WindowedTrade] = {}  # Manual cleanup needed
```

**Issue:**
- No automatic cleanup of old entries
- Only cleaned manually in `_clean_old_aggregations()` with hardcoded timeout
- If window timeout is misconfigured, memory grows indefinitely
- No metrics to monitor dict sizes

**Risk:** Long-running bots could run out of memory

**Fix:** Use LRU cache or explicit TTL for cached data

---

### **Dataclass Proliferation**

Multiple redundant dataclass definitions scattered across codebase:
- `TradeDecision` (base.py)
- `WindowedTrade` (simple_follow/strategy.py)
- `ExecutedTrade` (framework/replay.py)
- `OptimizationResult` (simulation/optimizer_old.py)

All store similar trade information. Should unify to single canonical format.

---

## Technical Debt Roadmap

**Priority 1 (Do First - blocks scaling):**
1. Extract `_to_side()` and utility helpers to `src/strategies/utils.py` (saves ~200 lines of duplication)
2. Create `StrategyBase` mixin for `_skip()`, `_check_extreme_prices()`, hourly reset (saves ~150 lines duplication)
3. Consolidate OrderType and Side enums in `src/core/types.py` only
4. Delete `src/simulation/optimizer_old.py` or document its purpose

**Priority 2 (Do Next - improves maintainability):**
5. Extract PnL calculation to single module: `src/analysis/pnl_calculator.py`
6. Split `src/framework/replay.py` into 3 modules (event_parser, execution, results)
7. Move hardcoded parameters to config files or StrategyConfig
8. Create unit tests for `price_level`, `velocity`, `momentum`, `spread_aware` strategies (currently zero tests)

**Priority 3 (Do Later - code quality):**
9. Implement LRU cache for strategy state dictionaries
10. Create shared log format parser in `src/data/log_parser.py`
11. Consolidate dataclass definitions in `src/core/models.py`

---

*Concerns audit: 2026-02-05*
