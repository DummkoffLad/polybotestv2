---
phase: 04-advanced-sizing
plan: 03
subsystem: position-sizing
tags: [kelly-criterion, adaptive-sizing, cold-start, tdd, phase3-fallback]

requires:
  - 03-04: DynamicSizer with quality scoring
  - 04-01: EdgeTracker and KellyCalculator

provides:
  - AdaptiveSizer: Bridge between Phase 3 and Phase 4 sizing
  - Cold start handling: Fallback to DynamicSizer when data insufficient
  - Kelly sizing: Uses Kelly criterion when per-token edge data available
  - Conviction multiplier: Applied after Kelly sizing for final adjustment

affects:
  - 04-04: Strategy integration will use AdaptiveSizer
  - 04-05: Runner integration will handle cold start transitions

tech-stack:
  added: []
  patterns:
    - Fallback pattern for graceful degradation (Kelly → Phase 3)
    - Per-token edge-based sizing decision
    - TDD with real objects (no mocks)

decisions:
  - "Conviction multiplier applied AFTER Kelly sizing (not before fractional Kelly)"
  - "Phase 3 fallback ensures no trades skipped during cold start"
  - "Return tuple (size_or_None, reason_string) for transparency"
  - "Reason strings: 'phase3_fallback', 'kelly', 'negative_edge'"

key-files:
  created:
    - src/core/adaptive_sizer.py: "AdaptiveSizer implementation (173 lines)"
    - tests/unit/test_adaptive_sizer.py: "12 comprehensive tests (408 lines)"
  modified: []

metrics:
  duration: 3min
  tests-added: 12
  tests-total: 361
  test-pass-rate: 100%
  commits: 2
  completed: 2026-02-02
---

# Phase 4 Plan 03: Adaptive Sizer Summary

**One-liner:** Bridges Phase 3 DynamicSizer and Phase 4 Kelly sizing with automatic cold start fallback — no trades ever skipped due to lack of data.

## What Was Built

Built `AdaptiveSizer` that solves the cold start problem: Kelly criterion needs historical edge data that doesn't exist for new tokens. AdaptiveSizer automatically falls back to Phase 3 DynamicSizer until per-token edge data reaches minimum threshold (20 trades), then seamlessly transitions to Kelly-based sizing.

### Key Components

**AdaptiveSizer** (`src/core/adaptive_sizer.py`):
- Constructor takes `DynamicSizer`, `KellyCalculator`, `EdgeTracker`
- `calculate_position_size()` returns `(size_or_None, reason_string)`
- Checks `edge_tracker.get_edge_stats(token_id)` for per-token data
- If None (cold start) → uses `dynamic_sizer.calculate_position_size()`
- If stats available → uses `kelly_calculator.calculate_kelly_size()`
- Applies `conviction_multiplier` AFTER Kelly sizing (not before fractional Kelly)
- Returns `None` with reason `"negative_edge"` when Kelly computes negative edge
- All sizes quantized to `Decimal("0.01")`

**Reason Strings**:
- `"phase3_fallback"`: Used DynamicSizer (insufficient data)
- `"kelly"`: Used Kelly sizing (sufficient data)
- `"negative_edge"`: Kelly computed negative edge (don't trade this token)

### Test Coverage (12 tests)

**Cold Start (3 tests)**:
- Falls back to DynamicSizer when no edge data
- Never returns None during cold start
- Quality score forwarded to DynamicSizer

**Kelly Active (3 tests)**:
- Uses Kelly sizing with 20+ trades
- Applies conviction multiplier to Kelly size
- Conviction applied AFTER fractional Kelly (order matters)

**Negative Edge (2 tests)**:
- Returns None with "negative_edge" reason
- Negative edge means "skip this token"

**Transition (2 tests)**:
- Seamlessly transitions from fallback to Kelly as data accumulates
- Different tokens use different methods (per-token tracking)

**Edge Cases (2 tests)**:
- Zero equity returns zero size
- Final size quantized to cents

All tests use real objects (no mocks) following Phase 1 TDD patterns.

## Decisions Made

| Decision | Rationale | Implications |
|----------|-----------|--------------|
| **Conviction multiplier applied AFTER Kelly sizing** | Kelly fraction reduces volatility, conviction adjusts for signal strength — order matters | Strategy integration must not pre-multiply conviction |
| **Phase 3 fallback ensures no trades skipped** | Cold start is normal, not exceptional — all trades deserve sizing | DynamicSizer remains critical even in Phase 4 |
| **Return tuple (size_or_None, reason_string)** | Transparency for debugging and monitoring | Callers can log/track which sizing method was used |
| **Three distinct reason strings** | Clear distinction between fallback, Kelly, and negative edge | Enables metric tracking (% of trades using Kelly vs fallback) |

## Deviations from Plan

None - plan executed exactly as written. All 12 tests implemented and passing, no regressions.

## Technical Notes

**Kelly Formula**:
```
f* = (p*b - q) / b
where:
  p = win_rate
  q = 1 - win_rate
  b = avg_win / avg_loss
```

**Fractional Kelly (0.5x)**:
- Reduces volatility ~50% while keeping ~75% growth rate
- Applied before conviction multiplier

**Cold Start Threshold**:
- 20 trades minimum (from EdgeTracker config)
- Below threshold → Phase 3 fallback
- At/above threshold → Kelly sizing

**Negative Edge Detection**:
- Kelly returns None when expected value <= 0
- AdaptiveSizer propagates None with reason "negative_edge"
- Caller should skip trade (don't enter position)

## Integration Points

**Inputs Required**:
- `DynamicSizer` (Phase 3 component)
- `KellyCalculator` (04-01 component)
- `EdgeTracker` (04-01 component)
- Token ID, current equity, quality score, conviction multiplier

**Outputs Provided**:
- Position size (Decimal or None)
- Reason string (sizing method used)

**Next Phase Dependencies**:
- 04-04 will integrate AdaptiveSizer into strategy logic
- 04-05 will integrate into runner execution flow

## Key Files

**Created**:
- `src/core/adaptive_sizer.py` (173 lines)
- `tests/unit/test_adaptive_sizer.py` (408 lines)

## Test Results

```
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerColdStart::test_cold_start_uses_phase3_fallback PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerColdStart::test_cold_start_never_returns_none PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerColdStart::test_cold_start_passes_quality_score PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerKellyActive::test_kelly_active_uses_kelly_size PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerKellyActive::test_kelly_applies_conviction_multiplier PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerKellyActive::test_kelly_conviction_order_matters PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerNegativeEdge::test_negative_edge_returns_none PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerNegativeEdge::test_negative_edge_skips_trade PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerTransition::test_transition_from_fallback_to_kelly PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerTransition::test_different_tokens_different_methods PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerEdgeCases::test_zero_equity_returns_zero PASSED
tests/unit/test_adaptive_sizer.py::TestAdaptiveSizerEdgeCases::test_result_quantized_to_cents PASSED

============================= 361 passed in 2.42s ==============================
```

**Test Metrics**:
- Tests added: 12
- Total tests: 361 (up from 349)
- Pass rate: 100%
- No regressions

## Next Phase Readiness

**Ready for 04-04 (Strategy Integration)**:
- AdaptiveSizer fully tested and working
- Cold start handling verified
- Conviction multiplier integration tested
- Negative edge handling tested

**Ready for 04-05 (Runner Integration)**:
- Clear reason strings for logging/monitoring
- Graceful fallback ensures all trades get sized
- Per-token tracking enables fine-grained metrics

**No blockers identified.**

## Git History

**Commits**:
- `89377ca` - test(04-03): add failing test for AdaptiveSizer (RED phase)
- `fd3b1c5` - feat(04-03): implement AdaptiveSizer with Kelly + Phase 3 fallback (GREEN phase)

**Files Changed**:
- `tests/unit/test_adaptive_sizer.py` (created, 408 lines)
- `src/core/adaptive_sizer.py` (created, 173 lines)

## Lessons Learned

**TDD Velocity**:
- Two commits (RED → GREEN) in 3 minutes
- 12 tests covering all edge cases
- Zero refactoring needed (GREEN passed immediately)
- Real objects > mocks for integration confidence

**Cold Start Pattern**:
- Fallback pattern is critical for graceful degradation
- "No data" is normal, not exceptional
- Phase 3 remains valuable even in Phase 4

**Conviction Multiplier Order**:
- Order matters: (Kelly * fraction) * conviction ≠ (Kelly * conviction) * fraction
- Test verified correct order of operations
- Documentation clarifies for future integrators

**Transparency via Reason Strings**:
- Return values include explanation (not just size)
- Enables metric tracking (Kelly usage rate)
- Simplifies debugging ("why did this trade use fallback?")
