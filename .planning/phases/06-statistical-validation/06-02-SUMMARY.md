---
phase: 06-statistical-validation
plan: 02
subsystem: statistics
tags: [hypothesis-testing, paired-t-test, scipy, statistical-significance]

requires:
  - "06-01: Sample size validation and confidence intervals"
provides:
  - "StrategyComparator class for paired t-test comparison"
  - "ComparisonResult dataclass with statistical analysis"
  - "Interpretation generation for comparison results"
affects:
  - "06-03: Will use StrategyComparator to compare conservative vs other strategies"

tech-stack:
  added:
    - scipy.stats.ttest_rel
  patterns:
    - "Paired t-test for same-session strategy comparison"
    - "Scipy-based statistical testing (no hand-rolled implementations)"

key-files:
  created:
    - src/statistics/hypothesis.py
    - tests/unit/test_statistics_hypothesis.py
  modified:
    - src/statistics/__init__.py

decisions:
  - id: STAT-scipy-direct
    what: "Use scipy.stats.ttest_rel directly instead of hand-rolling t-distribution"
    why: "Battle-tested accuracy, handles edge cases (NaN, inf), well-documented"
    when: "2026-02-04"
    alternatives:
      - "Hand-roll like KellyValidator does"
      - "Use statsmodels"
    impact: "More reliable results, less code to maintain"

metrics:
  duration: "4min"
  completed: "2026-02-04"
  tests: 12
  commits: 3
---

# Phase 6 Plan 02: Statistical Hypothesis Testing Summary

**One-liner:** Paired t-test strategy comparison using scipy.stats.ttest_rel with edge case handling and clear interpretations

## What Was Built

Implemented `StrategyComparator` for statistical significance testing between trading strategies:

**Core Components:**
- `ComparisonResult` dataclass: Captures means, t-statistic, p-value, significance, interpretation
- `StrategyComparator.compare_strategies()`: Paired t-test using scipy.stats.ttest_rel
- Interpretation generator: Human-readable results ("X significantly outperforms Y")

**Key Features:**
- Paired observations: Strategies tested on same sessions (proper statistical pairing)
- Edge case handling: Empty lists, single elements, mismatched lengths, NaN from identical data
- Two-sided test: Detects differences in either direction
- Configurable significance level (default 0.05)
- Direct scipy usage (no hand-rolled t-distribution approximations)

## Tests Created

Created 12 comprehensive tests:

1. **Edge cases:** Empty lists, single element, mismatched lengths (all raise ValueError)
2. **Statistical correctness:** Identical lists (NaN), clearly different lists (significant)
3. **Interpretation:** Winner identification, no-difference messaging, p-value formatting
4. **Scipy verification:** Results match scipy.stats.ttest_rel exactly
5. **Custom thresholds:** Significance level configurability
6. **Result structure:** All ComparisonResult fields populated correctly

All tests pass with expected scipy warnings for near-identical data.

## Technical Decisions

### Why scipy.stats.ttest_rel?

**Compared to KellyValidator approach:**
- KellyValidator hand-rolls t-distribution CDF using Wilson-Hilferty approximation
- StrategyComparator uses scipy directly for battle-tested accuracy
- Scipy handles edge cases (NaN, inf) consistently
- Less code to maintain, better documented

**Trade-offs:**
- Pro: Reliability, correctness, maintained by experts
- Pro: Handles precision issues (catastrophic cancellation) with warnings
- Con: External dependency (but scipy already used elsewhere)

### Paired vs Unpaired T-Test

Used paired t-test (`ttest_rel`) because:
- Strategies tested on SAME sessions (paired observations)
- Controls for session-specific effects (volatility regime, time of day)
- More statistical power than unpaired test
- Appropriate for before/after or matched-pairs comparisons

### Two-Sided Test

Default two-sided test detects differences in either direction:
- Appropriate when we don't know which strategy is better a priori
- p-value represents probability of observing this difference by chance
- Interpretation handles both "A wins" and "B wins" cases

## Implementation Highlights

**Clean validation:**
```python
if len(strategy_a_pnls) == 0 or len(strategy_b_pnls) == 0:
    raise ValueError("Cannot compare empty lists")
if len(strategy_a_pnls) != len(strategy_b_pnls):
    raise ValueError("Strategy PnL lists must have same length (paired observations)")
if len(strategy_a_pnls) == 1:
    raise ValueError("Cannot compute t-test with single observation (need at least 2)")
```

**Direct scipy usage:**
```python
t_statistic, p_value = ttest_rel(a_floats, b_floats)
is_significant = bool(p_value < self.significance_level)
```

**Clear interpretations:**
- "Conservative significantly outperforms Aggressive (p = 0.0123 < 0.05)"
- "No significant difference between Strategy A and Strategy B (p = 0.3456 >= 0.05)"

## Edge Cases Handled

1. **Empty lists:** Raise ValueError immediately
2. **Single observation:** Cannot compute variance, raise ValueError
3. **Identical data:** Scipy returns NaN, properly handled as not significant
4. **Uniform differences:** Scipy returns -inf/0.0, tests adjusted to use varied data
5. **Mismatched lengths:** Raise ValueError (paired test requirement)

## Integration

**Exports added to src/statistics/__init__.py:**
```python
from .hypothesis import StrategyComparator, ComparisonResult

__all__ = [
    "ConfidenceIntervalCalculator",
    "SampleSizeChecker",
    "SampleSizeWarning",
    "StrategyComparator",  # NEW
    "ComparisonResult",     # NEW
]
```

**Usage example:**
```python
from src.statistics import StrategyComparator

comparator = StrategyComparator(significance_level=0.05)
result = comparator.compare_strategies(
    strategy_a_pnls=[Decimal("10.5"), Decimal("12.3"), ...],
    strategy_b_pnls=[Decimal("15.2"), Decimal("14.8"), ...],
    strategy_a_name="Conservative",
    strategy_b_name="Aggressive"
)

print(result.interpretation)
# "Aggressive significantly outperforms Conservative (p = 0.0023 < 0.05)"
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed missing module imports in __init__.py**
- **Found during:** Task 1, test collection phase
- **Issue:** __init__.py imported confidence.py and sample_size.py before they existed
- **Fix:** Made imports conditional with try/except to unblock test execution
- **Files modified:** src/statistics/__init__.py
- **Commit:** 7403924 (bundled with test commit)

**2. [Rule 1 - Bug] Fixed test data for statistical power**
- **Found during:** GREEN phase, 5 tests failing
- **Issue:** Tests used only 3 data points, insufficient for statistical significance even with large differences
- **Fix:** Increased to 6+ data points in significance tests for proper statistical power
- **Files modified:** tests/unit/test_statistics_hypothesis.py
- **Commit:** e3aee78 (bundled with implementation)

**3. [Rule 1 - Bug] Fixed uniform difference test data**
- **Found during:** GREEN phase, scipy precision warning
- **Issue:** Test used [1,2,3,4,5] vs [2,3,4,5,6] (all differences = 1), causing scipy catastrophic cancellation
- **Fix:** Changed to varied real-world-like data with natural variation
- **Files modified:** tests/unit/test_statistics_hypothesis.py
- **Commit:** e3aee78 (bundled with implementation)

All deviations were Rule 1-3 (auto-fix) - no architectural decisions needed.

## Next Phase Readiness

**Ready for 06-03 (Conservative vs Others Comparison):**
- ✅ StrategyComparator exported and tested
- ✅ Handles real-world edge cases (NaN, small samples)
- ✅ Clear interpretation messages for reporting
- ✅ Paired t-test appropriate for same-session comparison

**Remaining statistical work:**
- 06-03: Compare conservative to other strategies using this comparator
- 06-04: Multi-strategy comparison (potentially ANOVA or multiple comparisons)

**Known limitations:**
- Two-sided test only (no one-sided option currently)
- No correction for multiple comparisons (Bonferroni, etc.) - deferred to 06-04
- No effect size calculation (Cohen's d) - could be added if needed

## Performance Notes

- **Duration:** 4 minutes (TDD cycle: RED 1min, GREEN 2min, REFACTOR 0min, Task 2: 1min)
- **Tests:** 12 tests, all passing
- **Commits:** 3 (RED, GREEN, exports update)
- **Lines of code:** hypothesis.py (148 lines), tests (183 lines)

## Coaching Notes

**What went well:**
- TDD cycle caught issues early (test data, edge cases)
- Scipy direct usage avoided reimplementation bugs
- Clear separation of validation, calculation, interpretation

**What was learned:**
- Statistical tests need sufficient data points for power (n >= 6 for moderate effects)
- Scipy handles edge cases with warnings (NaN, inf) - better to embrace than avoid
- Paired t-test is correct choice for same-session strategy comparison

**Future improvements:**
- Consider adding effect size (Cohen's d) to ComparisonResult
- Add one-sided test option if we have directional hypothesis
- Document minimum sample size recommendations
