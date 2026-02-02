---
phase: 03-dynamic-sizing
plan: 02
subsystem: capital-protection
tags: [decimal, drawdown, risk-management, high-water-mark, tdd]

# Dependency graph
requires:
  - phase: 01-test-coverage
    provides: TDD patterns and Decimal arithmetic conventions
provides:
  - Two-tier floor system (soft floor at 10% DD, hard floor at 30% DD)
  - TradingMode enum and CapitalManager class
  - Entry gating based on drawdown severity and trade quality
  - Position management controls
  - High water mark tracking
affects: [03-03-selective-following, 03-04-integration, risk-management]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Two-tier floor system (progressive trading restrictions)
    - Quality-based entry gating at soft floor
    - HWM-based drawdown tracking

key-files:
  created:
    - src/core/capital_manager.py
    - tests/unit/test_capital_manager.py
  modified: []

key-decisions:
  - "Soft floor at 90% of HWM (10% drawdown) allows only exceptional trades (quality >= 0.85)"
  - "Hard floor at 70% of HWM (30% drawdown) halts all trading activity"
  - "Position management allowed at soft floor but blocked at hard floor"
  - "HWM only increases, never decreases (locked peak equity)"

patterns-established:
  - "Progressive restriction: NORMAL -> SOFT_FLOOR -> HARD_FLOOR based on drawdown"
  - "Mode transition logging for operational visibility"
  - "Separate gating for entry (quality-dependent) vs position management (mode-dependent)"

# Metrics
duration: 2min
completed: 2026-02-01
---

# Phase 03 Plan 02: Capital Floor System Summary

**Two-tier capital floor with soft protection at 10% DD (exceptional trades only) and hard stop at 30% DD**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-02T04:37:16Z
- **Completed:** 2026-02-02T04:39:43Z
- **Tasks:** 1 (TDD task with RED-GREEN cycle)
- **Files modified:** 2

## Accomplishments
- TradingMode enum with three states: NORMAL, SOFT_FLOOR, HARD_FLOOR
- CapitalManager tracks high water mark and enforces drawdown-based restrictions
- Soft floor (90% of HWM) gates new entries to exceptional quality only (>= 0.85)
- Hard floor (70% of HWM) halts all trading activity (entries and position management)
- Full recovery path: returns to NORMAL when equity rises above soft floor threshold
- Comprehensive test coverage: 22 tests covering all modes, transitions, edge cases

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD - CapitalManager with two-tier floor system**
   - RED phase: `4b0f522` (test)
   - GREEN phase: `aa06ead` (feat)

## Files Created/Modified

- `src/core/capital_manager.py` - TradingMode enum and CapitalManager class (189 lines)
- `tests/unit/test_capital_manager.py` - Comprehensive TDD tests (300 lines, 22 tests)

## Decisions Made

**1. Soft floor quality threshold at 0.85**
- Rationale: User locked this as requirement - exceptional entries only in drawdown
- Effect: Filters out marginal opportunities when capital is stressed

**2. Position management allowed at soft floor**
- Rationale: Managing existing positions (sells, adjustments) is risk-reducing activity
- Effect: Can exit bad trades or take profits even during soft floor

**3. Hard floor blocks all activity**
- Rationale: 30% drawdown indicates severe distress - full stop needed
- Effect: Forces pause to reassess strategy before further capital erosion

**4. Mode transitions logged at INFO level**
- Rationale: Critical operational events that affect all trading decisions
- Effect: Clear audit trail for post-mortem analysis

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - TDD cycle proceeded smoothly. All 22 tests passed on first GREEN implementation.

## Next Phase Readiness

**Ready for Phase 03 Plan 03:** Trade quality scoring and selective following integration.

CapitalManager provides the capital protection layer. Next plan will integrate:
- TradeQualityScorer to generate quality scores (0.0 to 1.0)
- SelectiveFollower to combine quality scores with capital manager gating

**No blockers.** CapitalManager is fully functional and tested.

---
*Phase: 03-dynamic-sizing*
*Completed: 2026-02-01*
