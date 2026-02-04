---
phase: 07-comparison-infrastructure
plan: 01
subsystem: comparison
tags: [tdd, strategy-comparison, session-replay, dataclasses, plotly, quantstats, empyrical]

# Dependency graph
requires:
  - phase: 06-statistical-validation
    provides: Statistical validation infrastructure for confidence analysis
  - phase: framework
    provides: SessionReplayer for replaying sessions through strategies
provides:
  - StrategyComparator orchestrator for multi-strategy comparison
  - StrategyResult dataclass for per-strategy results
  - ComparisonResult dataclass for aggregated comparison output
  - New visualization/metrics dependencies (plotly, quantstats, empyrical-reloaded)
affects: [07-02, 07-03, phase-8, phase-9]

# Tech tracking
tech-stack:
  added: [plotly>=5.0, quantstats>=0.0.81, empyrical-reloaded>=0.5.11]
  patterns: [TDD RED-GREEN, dataclass result aggregation, sequential replay isolation]

key-files:
  created:
    - src/comparison/__init__.py
    - src/comparison/comparator.py
    - tests/unit/test_comparison_comparator.py
  modified:
    - requirements.txt

key-decisions:
  - "Sequential replay (not parallel) ensures data consistency and state isolation"
  - "Each strategy gets fresh SessionReplayer instance for isolation"
  - "track_analysis=True captures equity_df for downstream comparison"
  - "Empty DataFrame fallback when no equity data available"

patterns-established:
  - "StrategyComparator pattern: orchestrate multiple strategies on identical session data"
  - "StrategyResult/ComparisonResult dataclasses for structured comparison output"
  - "Test strategy fixtures (AlwaysBuyStrategy, AlwaysSkipStrategy, CountingStrategy)"

# Metrics
duration: 5min
completed: 2026-02-04
---

# Phase 7 Plan 01: StrategyComparator Core Summary

**StrategyComparator orchestrator with TDD tests running multiple strategies on same session with isolated state and result aggregation**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-04T23:23:48Z
- **Completed:** 2026-02-04T23:28:00Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Installed Phase 7 dependencies: plotly>=5.0, quantstats>=0.0.81, empyrical-reloaded>=0.5.11
- Created StrategyComparator orchestrator that runs multiple strategies sequentially on same session
- StrategyResult and ComparisonResult dataclasses for structured comparison output
- Comprehensive TDD test suite (23 tests) validating isolation, ordering, and result aggregation

## Task Commits

Each task was committed atomically:

1. **Task 1: Install dependencies and create module** - `da14a43` (chore)
2. **Task 2: TDD RED - Write failing tests** - `39872ad` (test)
3. **Task 3: TDD GREEN - Implement StrategyComparator** - `4ddf1dc` (feat)

## Files Created/Modified
- `requirements.txt` - Added plotly, quantstats, empyrical-reloaded dependencies
- `src/comparison/__init__.py` - Module exports for comparison infrastructure
- `src/comparison/comparator.py` - StrategyComparator, StrategyResult, ComparisonResult classes
- `tests/unit/test_comparison_comparator.py` - 23 TDD tests (667 lines)

## Decisions Made
- **Sequential replay:** Not parallel - ensures data consistency and each strategy sees identical events
- **Fresh replayer per strategy:** Each strategy gets isolated SessionReplayer instance to prevent state contamination
- **track_analysis=True:** Enables equity curve tracking for downstream comparison visualization
- **Empty DataFrame fallback:** When analysis not available, return empty DataFrame with expected columns

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None - TDD RED-GREEN cycle executed cleanly.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- StrategyComparator ready for use in 07-02 (equity curve visualization)
- ComparisonResult structure ready for metrics calculation in 07-03
- Dependencies installed for interactive visualization (plotly) and tear sheets (quantstats)

---
*Phase: 07-comparison-infrastructure*
*Completed: 2026-02-04*
