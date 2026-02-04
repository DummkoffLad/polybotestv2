---
phase: 07-comparison-infrastructure
plan: 02
subsystem: comparison
tags: [tdd, plotly, empyrical, visualization, metrics, equity-curves, drawdown]

# Dependency graph
requires:
  - phase: 07-01
    provides: StrategyComparator, StrategyResult, ComparisonResult classes
provides:
  - Interactive equity curve visualization with Plotly
  - Metrics calculation using empyrical-reloaded
  - Comparison table with conditional formatting
  - HTML export with CDN reference
affects: [07-03, phase-8, phase-9]

# Tech tracking
tech-stack:
  added: []
  patterns: [TDD RED-GREEN, Plotly subplots, empyrical metrics, pandas Styler]

key-files:
  created:
    - src/comparison/visualizer.py
    - src/comparison/metrics.py
    - tests/unit/test_comparison_visualizer.py
    - tests/unit/test_comparison_metrics.py
  modified:
    - src/comparison/__init__.py

key-decisions:
  - "Scattergl for WebGL performance on large datasets"
  - "Downsample datasets >5000 points for visualization performance"
  - "CDN reference for plotly.js (~3MB smaller files)"
  - "empyrical-reloaded for all ratio calculations (don't hand-roll)"
  - "Profit factor estimated from win/loss counts when per-trade data unavailable"

patterns-established:
  - "Two-panel visualization: equity curves (70%) + drawdown zones (30%)"
  - "Conditional formatting: green=#90EE90 (best), red=#FFB6C1 (worst)"
  - "Max drawdown is inverse (less negative is better)"

# Metrics
duration: 6min
completed: 2026-02-04
---

# Phase 7 Plan 02: Equity Visualization and Metrics Summary

**Plotly equity curve visualization with drawdown overlay and empyrical-reloaded metrics comparison table with conditional green/red formatting**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-04T23:30:21Z
- **Completed:** 2026-02-04T23:36:52Z
- **Tasks:** 3
- **Files created:** 4
- **Files modified:** 1

## Accomplishments

- Created interactive Plotly equity curve visualization with two subplots
- Top panel overlays all strategy equity curves using WebGL (Scattergl)
- Bottom panel shows drawdown as shaded zones (fill='tozeroy')
- No trade markers on equity curve (per CONTEXT.md)
- HTML export uses CDN reference for ~3MB smaller files
- Metrics calculation using empyrical-reloaded for Sharpe, Sortino, Calmar ratios
- Comparison table with pandas Styler conditional formatting
- Green highlighting for best values, red for worst per metric column
- Max drawdown treated as inverse metric (less negative is better)
- 48 comprehensive TDD tests (17 visualizer + 31 metrics)

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD RED - Equity visualization tests** - `9379d53` (test)
2. **Task 2: TDD GREEN - Equity visualization** - `f3eff93` (feat)
3. **Task 3: TDD RED+GREEN - Metrics calculation** - `5de4420` + `71ada70` (test/feat)

## Files Created/Modified

- `src/comparison/visualizer.py` - Plotly equity curve generation (172 lines)
- `src/comparison/metrics.py` - Metrics calculation with empyrical (193 lines)
- `src/comparison/__init__.py` - Updated exports
- `tests/unit/test_comparison_visualizer.py` - 17 tests (376 lines)
- `tests/unit/test_comparison_metrics.py` - 31 tests (568 lines)

## API Reference

### Visualization

```python
from src.comparison import create_equity_comparison, save_equity_html

# Create equity chart with drawdown overlay
fig = create_equity_comparison({
    "conservative": equity_df_1,
    "aggressive": equity_df_2
})

# Save to HTML with CDN reference
save_equity_html(fig, Path("output/equity_comparison.html"))
```

### Metrics

```python
from src.comparison import calculate_strategy_metrics, create_metrics_table

# Calculate metrics for single strategy
metrics = calculate_strategy_metrics(strategy_result)
# Returns: total_return_pct, sharpe_ratio, sortino_ratio, calmar_ratio,
#          max_drawdown_pct, win_rate_pct, profit_factor, trade_count

# Create comparison table with formatting
styler = create_metrics_table(comparison_result)
html = styler.to_html()  # Green/red conditional formatting
```

## Decisions Made

- **Scattergl for performance:** Uses WebGL-accelerated traces for large datasets
- **Downsample >5000 points:** Prevents browser lag with high-resolution data
- **CDN for plotly.js:** Requires internet but saves ~3MB per file
- **empyrical-reloaded:** Don't hand-roll financial metrics (per RESEARCH.md)
- **Profit factor estimation:** Uses win/loss counts when per-trade PnL unavailable

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- ReplayResult signature mismatch in test fixtures (required strategy_name field)
- Fixed by adding strategy_name to all ReplayResult instantiations in tests

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Visualization infrastructure ready for 07-03 (decision matrix)
- Metrics calculation ready for comparison reports
- Can generate HTML reports for strategy comparison

---
*Phase: 07-comparison-infrastructure*
*Completed: 2026-02-04*
