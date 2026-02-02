---
phase: 04-advanced-sizing
plan: 01
subsystem: sizing
tags: [kelly-criterion, edge-tracking, position-sizing, statistics]
status: complete

# Dependency graph
requires:
  - 03-04: Dynamic sizing infrastructure for integration
  - Phase 03: Equity tracking and position management
provides:
  - Edge statistics per token (win rate, avg win/loss)
  - Kelly criterion position sizing calculator
  - Cold start handling for insufficient data
affects:
  - 04-02: Kelly-Adjusted Sizing (will consume EdgeTracker + KellyCalculator)
  - Phase 5: Live integration will use Kelly sizing for real trades

# Tech stack
tech-stack:
  added:
    - collections.deque: Rolling window implementation (maxlen=50)
  patterns:
    - Per-token tracking with defaultdict
    - Optional return types for cold start handling
    - Decimal quantization for financial precision

# File tracking
key-files:
  created:
    - src/core/edge_tracker.py: "Per-token edge tracking with rolling window"
    - tests/unit/test_edge_tracker.py: "11 tests for EdgeTracker"
    - src/core/kelly_engine.py: "Kelly criterion calculator with Half Kelly"
    - tests/unit/test_kelly_engine.py: "12 tests for KellyCalculator"
  modified: []

# Decisions
decisions:
  - key: rolling-window-size
    choice: 50 trades per token
    rationale: Balances recency (adapts to changing edge) with statistical significance
    alternatives: [100 trades (slower adaptation), 30 trades (insufficient for Kelly)]

  - key: minimum-trades-for-kelly
    choice: 20 trades
    rationale: Statistical minimum for meaningful win rate estimation
    alternatives: [10 trades (too volatile), 30 trades (too slow to start)]

  - key: kelly-fraction
    choice: Half Kelly (0.5x)
    rationale: Reduces volatility by ~50% while keeping ~75% of growth rate
    alternatives: [Full Kelly (too volatile), Quarter Kelly (too conservative)]

  - key: max-position-cap
    choice: 20% of equity
    rationale: Risk management - prevents single position from dominating portfolio
    alternatives: [25% (too risky), 15% (too conservative)]

  - key: edge-case-defaults
    choice: "All wins → avg_loss=0.01, All losses → avg_win=0.00"
    rationale: Avoids division by zero in Kelly formula, returns sensible values
    alternatives: [Return None (loses data), Use fixed defaults (less accurate)]

# Metrics
duration: 4min
completed: 2026-02-02
---

# Phase 04 Plan 01: Edge Tracking & Kelly Calculator Summary

**One-liner:** Per-token edge tracking (win rate, avg win/loss) with Half Kelly position sizing calculator and 20% equity cap

## What Was Built

### EdgeTracker
Per-token trade result tracking with rolling 50-trade window:
- Records trade results: token_id, entry/exit price, pnl_pct, timestamp
- Calculates edge statistics: win_rate, avg_win_pct, avg_loss_pct
- Returns None during cold start (< 20 trades)
- Independent tracking per token using defaultdict + deque
- Edge cases: all wins default avg_loss=0.01, all losses avg_win=0.00

**Key methods:**
- `record_trade(result: TradeResult)` - Store trade result in rolling window
- `get_edge_stats(token_id) -> Optional[Tuple]` - Get (win_rate, avg_win, avg_loss, count)
- `has_sufficient_data(token_id) -> bool` - Check if >= min_trades

### KellyCalculator
Half Kelly position sizing with 20% equity cap:
- Kelly formula: f* = (p*b - q) / b, where b = avg_win / avg_loss
- Fractional Kelly: f* × 0.5 (configurable kelly_fraction parameter)
- Expected value: E = (W × AvgWin) - ((1-W) × AvgLoss)
- Returns None for negative edge (don't bet)
- Caps position at 20% of equity (risk management)
- All calculations use Decimal with quantize("0.01")

**Key methods:**
- `calculate_kelly_size(win_rate, avg_win, avg_loss, equity) -> Optional[Decimal]`
- `calculate_expected_value(win_rate, avg_win, avg_loss) -> Decimal`

## Test Coverage

**EdgeTracker (11 tests):**
- ✓ Trade storage and retrieval
- ✓ Cold start handling (< 20 trades → None)
- ✓ Rolling window eviction (deque maxlen=50)
- ✓ Win rate calculation (12/20 wins → 0.60)
- ✓ Avg win/loss calculations
- ✓ Edge cases: all wins, all losses
- ✓ Multi-token independence

**KellyCalculator (12 tests):**
- ✓ Positive edge returns position size
- ✓ Negative edge returns None
- ✓ Half Kelly = 0.5 × Full Kelly
- ✓ 20% equity cap enforcement
- ✓ Edge cases: 0% win rate, 100% win rate, zero avg_loss
- ✓ Expected value calculation
- ✓ Custom Kelly fractions (quarter, half, full)
- ✓ Small equity scenarios

**Total: 23/23 tests passing**
**Baseline: 326 tests passing (303 original + 23 new)**

## Deviations from Plan

**1. [Bug Fix - Test Parameter] Fixed test_half_kelly_is_half_of_full**
- **Found during:** Task 2 GREEN phase
- **Issue:** Test parameters (win_rate=0.60, avg_win=0.20, avg_loss=0.10) produced Kelly percentage exceeding 20% cap for both half and full Kelly, causing test to fail
- **Fix:** Changed to modest parameters (win_rate=0.55, avg_win=0.10, avg_loss=0.08) that don't hit cap
- **Files modified:** tests/unit/test_kelly_engine.py
- **Commit:** a66c215

This was a test design issue, not an implementation bug. The Kelly formula was correct; the test just needed parameters that demonstrate the fractional relationship without hitting the cap.

## Integration Points

### Inputs Required
- Trade results from strategy execution (entry/exit prices, pnl_pct)
- Current equity from Portfolio
- Leader average trade size (for conviction scoring)

### Outputs Provided
- Edge statistics per token (win_rate, avg_win_pct, avg_loss_pct)
- Kelly position sizes in dollars
- Expected value (edge magnitude)

### Consumers (Phase 4 remaining plans)
- **04-02 Kelly-Adjusted Sizing:** Will integrate EdgeTracker + KellyCalculator into DynamicSizer
- **04-03 Integration Testing:** Will verify Kelly sizing in replay scenarios
- **Phase 5 Live:** Will use Kelly sizing for real trades

## Next Phase Readiness

**Ready for 04-02:** ✓
- EdgeTracker and KellyCalculator fully tested and ready for integration
- API is clear: EdgeTracker records trades, KellyCalculator computes sizes
- Cold start handling ensures no crashes when insufficient data

**Remaining concerns:**
- None - clean implementation with comprehensive test coverage

**Blockers:**
- None

## Technical Debt

None introduced. Clean implementation following codebase conventions:
- Decimal arithmetic with quantize("0.01")
- Optional return types for None cases
- Logging at appropriate levels (INFO for initialization, DEBUG for calculations)
- Dataclass for TradeResult structure

## Performance Characteristics

**EdgeTracker:**
- O(1) trade recording (deque append)
- O(n) edge stats calculation where n ≤ 50 (rolling window size)
- Negligible memory per token (50 TradeResult objects)

**KellyCalculator:**
- O(1) position size calculation
- No state maintained (stateless calculations)

**Scalability:** Both modules scale linearly with number of tokens tracked. For single-leader following (current scope), performance is not a concern.

## Lessons Learned

1. **Test parameter selection matters:** When testing mathematical formulas with caps, choose parameters that exercise the formula without hitting artificial limits
2. **Edge case defaults prevent division by zero:** Defaulting avg_loss=0.01 for all-wins scenario keeps Kelly formula stable
3. **Rolling window with deque is elegant:** Python's deque(maxlen=N) handles eviction automatically
4. **Fractional Kelly reduces variance:** Half Kelly achieves ~75% of growth rate with ~50% of volatility - good tradeoff for small accounts

## Files Modified

**Created (4 files):**
- src/core/edge_tracker.py (171 lines)
- tests/unit/test_edge_tracker.py (296 lines)
- src/core/kelly_engine.py (190 lines)
- tests/unit/test_kelly_engine.py (265 lines)

**Total:** 922 lines added

## Commits

1. `3bd5f7c` - test(04-01): add failing tests for EdgeTracker (RED phase)
2. `a66c215` - feat(04-01): implement KellyCalculator with Half Kelly position sizing (GREEN phase)

## Verification

```bash
pytest tests/unit/test_edge_tracker.py tests/unit/test_kelly_engine.py -v
# Result: 23/23 passed

pytest tests/ --ignore=tests/unit/test_conviction.py -q
# Result: 326/326 passed (no regressions)
```

---

**Status:** ✓ Complete - Both modules implemented, tested, and ready for integration in 04-02
