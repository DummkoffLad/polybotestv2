---
phase: 05-validation
plan: 02
subsystem: testing
tags: [sensitivity-analysis, parameter-sweep, robustness, validation]

# Dependency graph
requires:
  - phase: 04-advanced-sizing
    provides: Optimized parameters (Kelly, conviction weights, quality thresholds, risk floors)
  - phase: 03-dynamic-sizing
    provides: Quality scoring and risk management parameters
  - phase: 02-performance-analysis
    provides: SimpleOptimizer for running strategy replays with varied configs
provides:
  - SensitivitySweeper for systematic parameter robustness testing
  - One-at-a-time sweep (3N configs for N parameters)
  - Full grid sweep (3^k configs for k-parameter subset)
  - Fragility detection (>30% PnL swing threshold)
  - ParamSweepResult and SensitivityResult data structures
affects: [05-05-validation-pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - One-at-a-time parameter sensitivity analysis
    - Fragility detection via PnL swing threshold
    - Boolean parameter exclusion from numeric sweeps

key-files:
  created:
    - src/validation/sensitivity.py
    - tests/unit/test_sensitivity.py
  modified:
    - src/validation/__init__.py

key-decisions:
  - "15% sweep range by default (+-15% from optimized values)"
  - "30% PnL swing threshold for fragility (conservative safety margin)"
  - "One-at-a-time sweep as primary method (avoids combinatorial explosion)"
  - "Full grid sweep available for suspected parameter interactions"
  - "Boolean parameters excluded from sweeps (not numeric)"
  - "Zero baseline PnL treated as maximally fragile (any change is 100% swing)"

patterns-established:
  - "generate_variants produces [low, baseline, high] triplets"
  - "one_at_a_time_sweep yields (param_name, value, config) tuples"
  - "full_grid_sweep generates cartesian product for specified subset only"
  - "max_swing_pct excludes baseline value itself from swing calculation"

# Metrics
duration: 4min
completed: 2026-02-02
---

# Phase 05 Plan 02: Sensitivity Sweeper Summary

**Parameter robustness testing with one-at-a-time and grid sweeps, 30% fragility threshold, and automatic boolean parameter exclusion**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-02T22:30:55Z
- **Completed:** 2026-02-02T22:34:53Z
- **Tasks:** 1 (TDD task with 2 commits)
- **Files modified:** 3

## Accomplishments
- SensitivitySweeper generates parameter variants (+-15% from baseline)
- One-at-a-time sweep produces 3N configurations for N numeric parameters
- Full grid sweep available for k-parameter subsets (3^k configs)
- Fragility detection identifies parameters where ±15% causes >30% PnL swing
- Handles edge cases: zero baseline, negative PnL, boolean parameters

## Task Commits

TDD task with RED-GREEN cycle:

1. **Task 1 (RED): Add failing tests** - `1cd6118` (test)
2. **Task 1 (GREEN): Implement SensitivitySweeper** - `2137b21` (feat)

_No REFACTOR phase needed - implementation was clean on first pass_

## Files Created/Modified
- `src/validation/sensitivity.py` - SensitivitySweeper, ParamSweepResult, SensitivityResult classes
- `tests/unit/test_sensitivity.py` - 14 tests covering variant generation, sweeps, fragility detection
- `src/validation/__init__.py` - Export sensitivity classes

## Decisions Made

**15% sweep range:**
- Tests parameters at ±15% from optimized baseline
- Conservative enough to catch fragility without testing extreme ranges
- Aligns with phase 05 context guidance (10-20% range)

**30% PnL swing fragility threshold:**
- Parameter causing >30% PnL change from baseline is marked "fragile"
- Indicates instability that could cause unexpected live trading behavior
- Conservative safety margin for go/no-go decision

**One-at-a-time as primary sweep method:**
- Varying one parameter at a time keeps config count manageable (3N)
- Sufficient for identifying individual parameter sensitivity
- Full grid sweep available for suspected interactions (limited to small subsets)

**Boolean parameter exclusion:**
- Python treats `bool` as subclass of `int` (True == 1, False == 0)
- Explicit `isinstance(value, bool)` check before numeric check
- Prevents nonsensical ±15% variants of True/False values

**Zero baseline handling:**
- Zero PnL baseline makes percentage swing calculation undefined
- Treated as maximally fragile (any non-zero result is 100% swing)
- Ensures strategy with zero PnL is flagged for investigation

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**Test logic refinement (not a bug):**
- Initial test expected all sweep configs to differ from baseline
- Realized baseline value itself is one of the three variants (low, baseline, high)
- Updated test to allow diff_count == 0 for baseline variant, diff_count == 1 for others
- This is correct behavior: baseline variant SHOULD match baseline exactly

**Max swing calculation:**
- Initial test assumed max swing was 40% (high variant)
- Actual max swing was 50% (low variant: 100 → 50 is 50% swing)
- Corrected test expectations to match correct calculation
- Implementation was correct, test assumptions were wrong

## Next Phase Readiness

Ready for Plan 05-05 (Validation Pipeline) integration:
- SensitivitySweeper generates configs for testing
- Pipeline will run SimpleOptimizer.run_strategy() for each config
- ParamSweepResult and SensitivityResult provide structured results
- Fragility detection identifies risky parameters for go/no-go decision

**Integration point:**
```python
from src.validation.sensitivity import SensitivitySweeper
from src.simulation.optimizer import SimpleOptimizer

# Generate configs
sweeper = SensitivitySweeper(baseline_params, sweep_range=0.15)
configs = list(sweeper.one_at_a_time_sweep())

# Run optimizer on each config
optimizer = SimpleOptimizer(session_path)
results = [optimizer.run_strategy("mirror", cfg) for _, _, cfg in configs]

# Assess robustness
param_results = [...]  # Map results to ParamSweepResult
sensitivity = sweeper.assess_robustness(param_results)
print(f"Fragile params: {sensitivity.fragile_params}")
```

**Parameters to sweep in Phase 05:**
- kelly_fraction (0.5)
- quality_threshold (0.40)
- soft_floor (0.85)
- hard_floor_drawdown (0.30)
- per_market_cap_pct (30)
- cash_reserve_pct (10)
- conviction_position_weight (0.60)
- conviction_scale_in_weight (0.25)
- conviction_entry_speed_weight (0.15)
- dca_quality_threshold (0.75)
- correlation_penalty (0.15)
- rebalancing_edge_gap (1.5)
- rolling_window_size (50)
- min_trades_for_kelly (20)
- max_position_cap (0.20)

All 15 parameters from Phases 3 and 4 can be swept systematically.

---
*Phase: 05-validation*
*Completed: 2026-02-02*
