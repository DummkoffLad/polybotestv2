---
phase: 02-performance-analysis
plan: 01
subsystem: analysis
tags: [decimal, attribution, equity-tracking, pnl, pandas, dataclass]

# Dependency graph
requires:
  - phase: 01-test-coverage
    provides: "Portfolio with calculate_pnl(), data models (MarketEvent, TradeAction), strategy base classes"
provides:
  - "AttributedTrade dataclass linking trades to PnL outcomes"
  - "TradeAttributor for lifecycle management (entry, exit, unrealized updates)"
  - "EquitySnapshot dataclass for timestamped capital state"
  - "EquityTracker producing pandas DataFrames for equity curves"
affects: [02-03-per-trade-metrics, 02-04-performance-reports, Phase-3-session-analysis]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy pandas import pattern - core functionality works without pandas"
    - "Manual snapshot recording for replay integration"
    - "Decimal-only financial calculations with .quantize()"

key-files:
  created:
    - src/analysis/attribution.py
    - src/analysis/equity_tracker.py
    - tests/unit/test_attribution.py
    - tests/unit/test_equity_tracker.py
  modified:
    - src/analysis/__init__.py

key-decisions:
  - "Lazy pandas import keeps EquityTracker lightweight for runtime use"
  - "Manual snapshot method supports replay integration where portfolio state comes from strategy"
  - "Trade sequencing allows multiple entries on same token_id"

patterns-established:
  - "to_dict() serialization with Decimal → str for JSONL persistence"
  - "Mark-to-market unrealized PnL updates separate from realized tracking"
  - "Volume-weighted summary statistics (avg win/loss based on actual trade sizes)"

# Metrics
duration: 4min
completed: 2026-02-01
---

# Phase 02 Plan 01: Trade Attribution & Equity Tracking Summary

**Per-trade PnL attribution with Decimal precision and timestamped equity snapshots for equity curve construction**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-01T00:57:19Z
- **Completed:** 2026-02-01T01:01:12Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- AttributedTrade tracks each trade from entry to exit with realized/unrealized PnL
- TradeAttributor manages multiple trades per token with comprehensive summary statistics
- EquityTracker records timestamped snapshots and produces pandas DataFrame for analysis
- 19 unit tests with exact Decimal assertions covering all scenarios

## Task Commits

Each task was committed atomically:

1. **Task 1: Create analysis package with AttributedTrade and TradeAttributor** - `00020fd` (feat)
2. **Task 2: Create EquityTracker with timestamped PnL snapshots** - `5189dbf` (feat)
3. **Update analysis package exports** - `6876879` (chore)

## Files Created/Modified

- `src/analysis/attribution.py` - AttributedTrade dataclass and TradeAttributor class for per-trade PnL tracking
- `src/analysis/equity_tracker.py` - EquitySnapshot dataclass and EquityTracker for equity curve construction
- `src/analysis/__init__.py` - Updated lazy imports to export new classes
- `tests/unit/test_attribution.py` - 9 tests covering entry/exit/PnL calculation/summary stats
- `tests/unit/test_equity_tracker.py` - 10 tests covering snapshots/DataFrame conversion/returns

## Decisions Made

**Lazy pandas import in EquityTracker**
- Rationale: Core equity tracking doesn't require pandas. Only to_dataframe() needs it. This keeps the module lightweight for runtime use and avoids import overhead.
- Implementation: Pandas imported inside to_dataframe() method only

**Manual snapshot recording method**
- Rationale: Replay integration computes portfolio state in strategy context, not via Portfolio object. Manual recording allows passing pre-computed values.
- Implementation: Both record_snapshot(portfolio) and record_manual_snapshot(values) supported

**Trade sequencing per token_id**
- Rationale: Same token can have multiple entries (DCA, re-entries after exits). Sequencing enables unique trade_id while grouping by token.
- Implementation: Trade ID format "{token_id}_{sequence}", stored as token_id -> list of trades

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for next plans:**
- Plan 02-02 (Drawdown & Slippage) already complete, ran in parallel (Wave 1)
- Plan 02-03 (Per-Trade Metrics) can build on AttributedTrade
- Plan 02-04 (Performance Reports) can use EquityTracker.to_dataframe()

**Foundation complete:**
- Trade attribution satisfies success criterion 1 (each trade links to final PnL)
- Equity tracking enables success criterion 2 (equity curve with drawdown calculation)
- All downstream analysis modules can build on these primitives

**No blockers or concerns**

---
*Phase: 02-performance-analysis*
*Completed: 2026-02-01*
