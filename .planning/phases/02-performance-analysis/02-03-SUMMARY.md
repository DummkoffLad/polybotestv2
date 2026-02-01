---
phase: 02-performance-analysis
plan: 03
subsystem: analysis
tags: [integration, replay, reporting, charts, matplotlib, end-to-end]

# Dependency graph
requires:
  - phase: 02-01
    provides: "TradeAttributor, EquityTracker for trade and equity tracking"
  - phase: 02-02
    provides: "DrawdownAnalyzer, SlippageAnalyzer for metrics calculation"
  - phase: 01-test-coverage
    provides: "SessionReplayer, strategy base classes, Portfolio"
provides:
  - "Full performance analysis integrated into SessionReplayer.run(track_analysis=True)"
  - "ReportGenerator for console summaries, equity/drawdown charts, trade scatter, JSON reports"
  - "run_session_replay_with_analysis() convenience function for complete analysis workflow"
  - "End-to-end analysis pipeline: replay → attribution → equity → drawdown → slippage → reports"
affects: [phase-03-optimization, phase-05-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Optional analysis via track_analysis flag preserves existing replay behavior"
    - "Equity snapshots recorded after each event for high-resolution equity curve"
    - "Volume-weighted slippage aggregation matches dollar impact on PnL"
    - "matplotlib Agg backend for headless chart generation"

key-files:
  created:
    - src/analysis/reports.py
    - tests/unit/test_reports.py
    - tests/integration/test_replay_analysis.py
  modified:
    - src/framework/replay.py
    - src/analysis/__init__.py

key-decisions:
  - "track_analysis parameter optional (default False) to preserve existing replay behavior"
  - "Record equity snapshot after each event (not just trades) for complete equity curve"
  - "Extract starting_capital and leader_capital from config for accurate gap calculations"
  - "Charts use matplotlib Agg backend to avoid GUI issues in headless environments"

patterns-established:
  - "Analysis pipeline integration via optional flag pattern"
  - "Convenience function wraps full analysis workflow (replay + reports + charts)"
  - "Integration tests use existing session files to prove end-to-end functionality"

# Metrics
duration: 6min
completed: 2026-02-01
---

# Phase 02 Plan 03: Replay Integration & Report Generation Summary

**Complete performance analysis pipeline: SessionReplayer integrates attribution, equity tracking, drawdown, slippage with formatted reports and charts**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-01T02:18:10Z
- **Completed:** 2026-02-01T02:24:06Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- ReportGenerator produces formatted console output, equity/drawdown charts, trade scatter, and machine-readable JSON reports
- SessionReplayer.run(track_analysis=True) produces complete analysis results
- Full integration: records entries/exits, measures slippage (execution + delay), tracks sizing gaps, records selection gaps (skipped trades)
- Equity snapshots recorded after each event for high-resolution curve
- End-to-end convenience function: run_session_replay_with_analysis() runs full pipeline
- 6 unit tests for ReportGenerator verify console output, chart generation, JSON serialization
- 8 integration tests prove end-to-end functionality with real session files
- All 232 tests pass (no regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ReportGenerator with console summary and chart output** - `e3ab1fa` (feat)
2. **Task 2: Integrate analysis pipeline into SessionReplayer** - `f6bf84d` (feat)

## Files Created/Modified

- `src/analysis/reports.py` - ReportGenerator class for console summaries, charts (equity/drawdown, trade scatter), and JSON reports
- `tests/unit/test_reports.py` - 6 unit tests for report generation
- `src/framework/replay.py` - Modified to integrate analysis pipeline via track_analysis parameter
- `tests/integration/test_replay_analysis.py` - 8 integration tests proving end-to-end analysis works
- `src/analysis/__init__.py` - Added ReportGenerator to lazy imports

## Decisions Made

**track_analysis parameter optional (default False)**
- Rationale: Existing replay behavior must not change. Analysis is additive, not required.
- Implementation: When track_analysis=False, result.analysis is None and existing tests pass unchanged
- Benefit: Zero impact on existing code, opt-in for analysis

**Record equity snapshot after each event**
- Rationale: High-resolution equity curve needed for accurate drawdown calculation. Recording only on trades misses unrealized PnL changes.
- Implementation: After each event (trade or skip), call equity_tracker.record_manual_snapshot() with current portfolio state
- Benefit: Precise equity curve reflects all market movements

**Extract capital values from config**
- Rationale: Replay doesn't have direct access to strategy.starting_capital, must read from merged config
- Implementation: `starting_capital = Decimal(str(config.get('scaling', {}).get('our_capital', '100')))`, same for leader_capital
- Benefit: Accurate equity tracking and sizing gap calculations

**matplotlib Agg backend**
- Rationale: Headless environments (CI, servers) don't have GUI display. Default backend fails.
- Implementation: `matplotlib.use('Agg')` at top of reports.py
- Benefit: Charts generate in all environments

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Success criteria satisfied:**
1. SessionReplayer.run(track_analysis=True) produces analysis dict with trade_summary, drawdown, slippage, sizing, selection ✓
2. Console summary prints all metrics in readable format ✓
3. Equity curve + drawdown chart saved as PNG ✓
4. Trade scatter chart saved as PNG ✓
5. JSON report saved with machine-readable metrics for Phase 3 ✓
6. Existing replay behavior unchanged when track_analysis=False ✓
7. All existing tests pass (no regressions) ✓
8. Integration test proves end-to-end pipeline works ✓

**Phase 2 complete:**
- All 4 success criteria from Phase 2 objective met:
  1. Each trade links to its final outcome (win/loss/open) ✓ (02-01)
  2. Equity curve with max drawdown calculation ✓ (02-02)
  3. Trade attribution shows where our sizing/timing differed from leader ✓ (02-02)
  4. Machine-readable output for Phase 3 consumption ✓ (02-03)

**Ready for Phase 3: Strategy Optimization**
- JSON reports provide baseline metrics for optimization comparison
- Slippage breakdown (execution vs delay) guides optimization targets
- Sizing gaps identify over/undersizing patterns
- Selection gaps show cost of skipped trades

**Foundation for Phase 5: Out-of-Sample Validation**
- Full analysis pipeline ready to run on reserved test sessions
- Charts provide visual validation of performance
- Regression testing ensures deterministic behavior

---
*Phase: 02-performance-analysis*
*Completed: 2026-02-01*
