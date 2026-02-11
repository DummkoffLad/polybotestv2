---
phase: 08-foundation-bug-fixes
plan: 03
subsystem: core
tags: [portfolio, trade-logic, refactoring, duplication-elimination]

# Dependency graph
requires:
  - phase: 08-01
    provides: Composite-keyed Portfolio with _position_key() method
provides:
  - Shared trade logic module (src/core/trade_logic.py)
  - liquidate_positions_at_hour_boundary() function with dual modes
  - Single source of truth for hour boundary liquidation
affects: [08-04, live-runner, simulation, future-strategies]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Shared trade logic pattern: single source of truth for critical trading operations"
    - "Dual-mode liquidation: resolution prices vs actual bid prices"

key-files:
  created:
    - src/core/trade_logic.py
  modified:
    - src/core/__init__.py
    - src/framework/replay/replayer.py
    - src/strategies/profit_taker/strategy.py

key-decisions:
  - "Dual-mode liquidation via use_resolution_prices parameter"
  - "Composite key extraction via split('|')[0] for token_id lookup"
  - "Cash tracker pattern with hasattr check for optional cash tracking"

patterns-established:
  - "Shared logic modules in src/core for operations used by both simulation and live execution"
  - "LiquidationResult dataclass for structured return values"

# Metrics
duration: 5min
completed: 2026-02-11
---

# Phase 08 Plan 03: Shared Trade Logic Summary

**Extracted hour boundary liquidation logic into src/core/trade_logic.py with dual-mode support for both resolution prices and actual bid prices**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-11T15:44:41Z
- **Completed:** 2026-02-11T15:49:36Z
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- Created src/core/trade_logic.py with liquidate_positions_at_hour_boundary() function
- Eliminated 35+ lines of duplicated liquidation logic between replayer and strategy
- Both simulation and live execution now use identical liquidation logic
- Supports composite-keyed Portfolio from Plan 08-01
- Dual mode operation: resolution prices ($0.99/$0.01) vs actual bid prices

## Task Commits

Each task was committed atomically:

1. **Task 1: Create src/core/trade_logic.py with shared liquidation logic** - `e512b8d` (feat)
2. **Task 2: Wire shared trade logic into replayer and profit_taker strategy** - `55908ab` (refactor)

## Files Created/Modified

- `src/core/trade_logic.py` (new) - Shared liquidation logic with LiquidationResult dataclass
- `src/core/__init__.py` - Export liquidate_positions_at_hour_boundary and LiquidationResult
- `src/framework/replay/replayer.py` - _liquidate_all_positions now delegates to shared module (uses actual bid prices)
- `src/strategies/profit_taker/strategy.py` - _liquidate_hour_boundary now delegates to shared module (uses resolution prices)

## Decisions Made

**1. Dual-mode liquidation via use_resolution_prices parameter**
- Rationale: Replayer uses actual bid prices for mid-session liquidation, strategy uses resolution prices for hour boundary markets
- Implementation: Single function supports both modes cleanly

**2. Composite key extraction pattern**
- Rationale: Portfolio now uses composite keys ("token_id|market_id|side") from Plan 08-01
- Implementation: Extract token_id via `composite_key.split("|")[0]` for price lookups
- Pass full composite_key to portfolio.apply_sell() for proper position tracking

**3. Optional cash tracker with hasattr pattern**
- Rationale: Some strategies track cash, others don't
- Implementation: Accept optional cash_tracker parameter, increment cash only if has 'cash' attribute

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - refactoring was straightforward. All 28 portfolio tests pass. Pre-existing test failure (test_pipeline_latency_analysis) is unrelated to this refactoring (documented in STATE.md as pre-existing issue).

## Next Phase Readiness

- Shared trade logic module ready for use by live runner in Phase 12
- Both simulation (replay) and strategy code paths now use identical liquidation logic
- Eliminates risk of divergence between simulation and live execution
- Ready for Plan 08-04 (final bug fixes in phase)

**Blockers:** None

**Concerns:** None - refactoring successful with full test coverage

---
*Phase: 08-foundation-bug-fixes*
*Completed: 2026-02-11*
