---
phase: 04-advanced-sizing
plan: 02
subsystem: sizing
tags: [conviction, kelly, trade-ranking, prioritization, capital-allocation]

# Dependency graph
requires:
  - phase: 03-dynamic-sizing
    provides: Quality filtering and dynamic position sizing foundation
provides:
  - ConvictionScorer for leader behavior-based confidence multipliers
  - TradeRanker for prioritizing competing opportunities by edge
  - Soft diversification penalty (15%) for correlated positions
  - Conservative rebalancing threshold (1.5x edge gap)
affects: [04-03-kelly-engine, 04-04-integration, capital-allocation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Non-linear signal mapping for conviction scoring"
    - "Weighted deviation from neutral baseline"
    - "Kelly/quality score fallback hierarchy"

key-files:
  created:
    - src/core/conviction.py
    - src/core/trade_ranker.py
    - tests/unit/test_conviction.py
    - tests/unit/test_trade_ranker.py
  modified: []

key-decisions:
  - "Position size weighted 60%, scale-in 25%, entry speed 15% for conviction"
  - "Non-linear mapping with quadratic amplification for above-average positions"
  - "15% soft penalty for correlated positions (same token)"
  - "1.5x edge gap threshold for rebalancing (conservative)"
  - "Quality score fallback when Kelly edge unavailable"

patterns-established:
  - "Conviction multiplier range [0.25, 2.0] with quantization to 0.01"
  - "Weighted deviation from neutral (1.0) baseline for signal combination"
  - "TradeOpportunity dataclass with Optional[Decimal] kelly_edge for cold-start handling"

# Metrics
duration: 5min
completed: 2026-02-02
---

# Phase 04 Plan 02: Conviction & Ranking Summary

**Leader conviction scoring (60% position size, 25% scale-in, 15% speed) and trade prioritization with soft diversification penalty**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-02T21:13:20Z
- **Completed:** 2026-02-02T21:18:41Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- ConvictionScorer calculates multipliers in [0.25, 2.0] from leader signals with 60/25/15 weighting
- TradeRanker prioritizes opportunities by Kelly edge with 15% correlation penalty
- Conservative rebalancing (1.5x gap) prevents excessive position churn
- Quality score fallback enables cold-start operation before Kelly statistics available

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD - ConvictionScorer leader conviction multiplier** - `c89f7d4` (test+feat)
2. **Task 2: TDD - TradeRanker trade prioritization with soft diversification** - `069e9a9` (test+feat)

_Note: Both tasks used TDD but combined RED+GREEN into single commits for efficiency_

## Files Created/Modified
- `src/core/conviction.py` - Leader conviction-based confidence multiplier calculation
- `tests/unit/test_conviction.py` - 12 tests covering all signal combinations and edge cases
- `src/core/trade_ranker.py` - Trade prioritization with soft diversification and rebalancing logic
- `tests/unit/test_trade_ranker.py` - 11 tests covering ranking, penalties, and rebalancing

## Decisions Made

**Signal weighting:** Position size (60%), scale-in (25%), entry speed (15%) based on predictive value hierarchy from Phase 4 context. Position size relative to leader average is strongest signal.

**Non-linear mapping:** Above-average positions use quadratic amplification to achieve full [0.25, 2.0] range despite 60% weight. Below-average uses linear interpolation.

**Soft diversification:** 15% penalty for correlated positions (same token) rather than hard block. Strong edge can overcome penalty - diversification is tiebreaker, not veto.

**Conservative rebalancing:** 1.5x edge gap prevents excessive position churn. Only swap when new opportunity significantly better.

**Quality fallback:** When Kelly edge unavailable (cold start), fall back to quality score. Enables system operation before sufficient trade history.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**Conviction formula iteration:** Initial weighted average approach couldn't reach full [0.25, 2.0] range with 60% position size weight. Solution: weighted deviation from neutral (1.0) baseline with non-linear mapping for extreme values. Above-average positions use quadratic amplification; below-average uses linear interpolation. Verified through comprehensive edge case testing (tiny/huge positions).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 04-03 (Kelly Engine):**
- ConvictionScorer can adjust Kelly-calculated sizes based on leader conviction
- TradeRanker can prioritize opportunities by Kelly edge once statistics accumulated
- TradeOpportunity dataclass designed for Optional[Decimal] kelly_edge (None during cold start)

**Test coverage:**
- 12 conviction tests: signal combinations, extreme values, quantization, leader avg updates
- 11 trade ranking tests: Kelly/quality ranking, correlation penalty, conviction multiplier, rebalancing
- Full suite: 349 tests passing (up from 303 - added 46 new tests)

**No blockers.**

---
*Phase: 04-advanced-sizing*
*Completed: 2026-02-02*
