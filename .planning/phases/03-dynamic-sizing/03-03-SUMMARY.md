---
phase: 03-dynamic-sizing
plan: 03
subsystem: risk-management
tags: [quality-scoring, trade-filtering, position-limits, conviction-analysis, spread-analysis]

dependency-graph:
  requires:
    - "03-01: DynamicSizer for quality-adjusted sizing"
    - "03-02: CapitalManager for capital availability tracking"
  provides:
    - "TradeQualityScorer for 0.0-1.0 trade quality scoring"
    - "SelectiveFollower for position count gating"
  affects:
    - "03-04: Integration with Portfolio and strategies"
    - "Future: Quality-adjusted position sizing"

tech-stack:
  added: []
  patterns:
    - "Composite scoring with weighted components (60/40 spread/conviction)"
    - "Linear interpolation for spread cost normalization"
    - "Conviction capping at 2x leader average"
    - "Dual threshold system (0.70 initial, 0.75 DCA)"

file-tracking:
  created:
    - path: "src/core/trade_filter.py"
      lines: 217
      exports: ["TradeQualityScorer", "SelectiveFollower"]
    - path: "tests/unit/test_trade_filter.py"
      lines: 187
      test_count: 23
  modified: []

decisions:
  - id: "60-40-weighting"
    choice: "60% spread cost, 40% leader conviction weighting"
    rationale: "Research showed spread cost is dominant factor for sub-$100 accounts where every basis point matters"
    impact: "Prioritizes low-spread opportunities over large-conviction trades"
  - id: "linear-spread-interpolation"
    choice: "Linear interpolation between 50-300 bps thresholds"
    rationale: "Simple, predictable scoring that penalizes high spreads proportionally"
    impact: "Clear score degradation as spread increases"
  - id: "conviction-cap-2x"
    choice: "Cap conviction scoring at 2x leader average trade size"
    rationale: "Prevents oversized trades from dominating quality score"
    impact: "Limits influence of outlier large trades"
  - id: "dual-quality-thresholds"
    choice: "0.70 for initial trades, 0.75 for DCA follow"
    rationale: "Higher bar for DCA prevents averaging down into deteriorating quality"
    impact: "More selective on position averaging"
  - id: "fcfs-position-allocation"
    choice: "First-come-first-served position allocation with max_positions limit"
    rationale: "Sufficient for single-leader following, avoids complex prioritization"
    impact: "Simple, predictable position management"

metrics:
  duration: "2min"
  completed: "2026-02-02"
  commits: 2
  files_created: 2
  tests_added: 23
  test_pass_rate: "100%"

wave: 1
parallel_tasks: []
---

# Phase 3 Plan 03: Trade Quality Filtering Summary

Quality scoring conserves capital for high-edge opportunities using 60/40 spread/conviction weighting

## Objective Completed

Created TradeQualityScorer that rates trades from 0.0 (skip) to 1.0 (max size), and SelectiveFollower that decides which trades to follow when capital-constrained.

**Purpose delivered:** Trade filtering layer that conserves capital for high-edge opportunities. High-spread trades eat the edge, small-conviction trades signal low confidence. Quality scoring ensures capital is deployed only on favorable setups.

## Implementation Summary

### TradeQualityScorer

**Spread Scoring (60% weight):**
- Excellent spread (<= 50 bps): 1.00 score
- Poor spread (>= 300 bps): 0.00 score
- Linear interpolation between thresholds
- Rationale: Spread cost is dominant factor for sub-$100 accounts

**Conviction Scoring (40% weight):**
- Based on leader trade size relative to their average
- Capped at 2x average (larger trades don't increase score)
- Formula: min(size_ratio, 2.0) / 2.0
- Prevents outlier large trades from dominating

**Composite Scoring:**
- 60% spread + 40% conviction
- All arithmetic uses Decimal with quantize to 0.01
- Produces scores in [0.0, 1.0] range

**Thresholds:**
- Initial trade: quality >= 0.70
- DCA follow: both original and new >= 0.75 (higher bar prevents averaging down)

### SelectiveFollower

**Position Count Gating:**
- Limits concurrent positions to prevent over-diversification
- Returns (can_open, reason) tuple
- Default max_positions = 5
- FCFS allocation sufficient for single-leader following

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | TDD - TradeQualityScorer and SelectiveFollower | 2e112a1, a716f60 | src/core/trade_filter.py, tests/unit/test_trade_filter.py |

### Task 1: TDD - TradeQualityScorer and SelectiveFollower

**RED phase (2e112a1):** Wrote 23 failing tests covering:
- Spread scoring: 0/50/175/300/500 bps test cases
- Conviction scoring: 0x/0.5x/1x/2x/3x average size test cases
- Composite scoring: perfect/terrible/good combinations
- Threshold filtering: 0.70 initial, 0.75 DCA
- Position count gating: 0/4/5/6 positions vs max 5

**GREEN phase (a716f60):** Implemented:
- TradeQualityScorer with score_spread(), score_conviction(), score_trade()
- should_take_trade() and should_follow_dca() threshold checks
- SelectiveFollower with can_open_position() gating
- Logging: debug for score components, info for skip decisions
- All Decimal arithmetic with quantize to 0.01

**REFACTOR phase:** Not needed - implementation clean and simple

**Result:** All 23 tests pass, 100% pass rate

## Technical Decisions

### 60/40 Weighting (Spread/Conviction)

Research showed spread cost is the dominant factor for sub-$100 accounts where every basis point matters. Conviction provides secondary signal of leader confidence.

**Impact:** Prioritizes low-spread opportunities over large-conviction trades

### Linear Interpolation for Spread Scoring

Simple, predictable scoring between 50-300 bps thresholds. Clear degradation as spread increases.

**Alternative considered:** Exponential penalty for high spreads
**Rejected because:** Linear is easier to reason about and tune

### Conviction Capping at 2x Average

Prevents outlier large trades from dominating quality score. Normalizes conviction signal to [0, 1] range.

**Impact:** Limits influence of occasional whale-sized trades

### Dual Quality Thresholds

0.70 for initial trades, 0.75 for DCA. Higher bar for averaging prevents chasing deteriorating quality.

**Impact:** More selective on position averaging, protects against adverse DCA

### FCFS Position Allocation

First-come-first-served with max_positions limit. Sufficient for single-leader following.

**Alternative considered:** Quality-based prioritization (drop lowest quality when at limit)
**Rejected because:** Adds complexity for marginal benefit in single-leader scenario

## Testing Strategy

**TDD approach:** RED-GREEN-REFACTOR cycle
**Test count:** 23 tests, 100% pass rate
**Coverage areas:**
- Spread scoring edge cases (0/threshold/midpoint/over-threshold)
- Conviction scoring edge cases (0x/0.5x/1x/2x/3x average)
- Composite scoring combinations
- Threshold filtering
- Position count gating

**Test patterns:**
- Local helper functions (make_scorer, make_follower) for test data
- Exact Decimal comparisons (never pytest.approx)
- Clear test names describing behavior

## Files Created

### src/core/trade_filter.py (217 lines)

**Exports:**
- `TradeQualityScorer`: Scores trades 0.0-1.0 based on spread and conviction
- `SelectiveFollower`: Position count gating

**Key methods:**
- `score_spread(spread_bps)`: Linear interpolation 50-300 bps
- `score_conviction(leader_dollars)`: Relative to leader average, capped at 2x
- `score_trade(spread_bps, leader_dollars)`: 60/40 composite
- `should_take_trade(quality_score)`: >= 0.70 threshold
- `should_follow_dca(original, new)`: Both >= 0.75 threshold
- `can_open_position(count)`: Position count < max

**Dependencies:**
- decimal.Decimal (for precise arithmetic)
- logging (for debug/info logging)

### tests/unit/test_trade_filter.py (187 lines)

**Test classes:**
- `TestTradeQualityScorerSpread`: 5 tests
- `TestTradeQualityScorerConviction`: 5 tests
- `TestTradeQualityScorerComposite`: 3 tests
- `TestTradeQualityScorerThresholds`: 6 tests
- `TestSelectiveFollower`: 4 tests

**Total:** 23 tests, all passing

## Integration Points

### Upstream (Required by this plan)

**From 03-01 (DynamicSizer):**
- Quality scores will multiply base sizes (quality * base_size)
- Scores below threshold -> skip trade

**From 03-02 (CapitalManager):**
- Capital availability checked before quality scoring
- No point scoring if insufficient capital

### Downstream (Enables future work)

**To 03-04 (Integration):**
- Strategies will use TradeQualityScorer before sizing trades
- SelectiveFollower gates position opening before Portfolio updates
- Quality scores stored for DCA evaluation

**To future optimizations:**
- Quality thresholds (0.70, 0.75) can be tuned via simulation
- Spread/conviction weights (60/40) can be optimized
- Leader average size updated from historical data

## Verification Results

```bash
python -m pytest tests/unit/test_trade_filter.py -v
```

**Result:** 23 tests passed in 0.03s

**Verified:**
- Spread scoring: linear interpolation between 50-300 bps
- Conviction scoring: capped at 2x leader average
- Composite scoring: 60% spread + 40% conviction
- Threshold filtering: 0.70 initial, 0.75 DCA
- Position count gating: max_positions limit enforced
- All Decimal arithmetic with quantize to 0.01

## Success Criteria

- [x] TradeQualityScorer produces scores in [0.0, 1.0] range
- [x] Spread scoring: linear interpolation between 50-300 bps thresholds
- [x] Conviction scoring: capped at 2x leader average
- [x] Composite score: 60% spread + 40% conviction
- [x] should_take_trade gates at 0.70 threshold
- [x] should_follow_dca requires both original and new >= 0.75
- [x] SelectiveFollower gates at max_positions
- [x] All calculations use Decimal with quantize
- [x] Tests pass: python -m pytest tests/unit/test_trade_filter.py -v

## Deviations from Plan

None - plan executed exactly as written.

## Next Phase Readiness

**Plan 03-04 (Integration) is ready to proceed.**

**Inputs available:**
- TradeQualityScorer for trade quality evaluation
- SelectiveFollower for position count gating
- DynamicSizer for quality-adjusted sizing (03-01)
- CapitalManager for capital tracking (03-02)

**What's needed for integration:**
1. Wire TradeQualityScorer into strategy decision flow
2. Use SelectiveFollower before opening positions
3. Store quality scores for DCA evaluation
4. Test integrated flow with Portfolio

**No blockers.** All components ready for integration.

## Lessons Learned

**TDD discipline pays off:**
- 23 tests written before implementation
- All tests passed on first GREEN phase
- Zero refactoring needed

**Weighted scoring is flexible:**
- 60/40 split based on research insights
- Easy to tune via simulation
- Clear rationale for each component weight

**Dual thresholds prevent adverse behavior:**
- Higher DCA threshold (0.75 vs 0.70) prevents averaging down
- Simple rule, significant risk reduction

**FCFS sufficient for single-leader:**
- Avoided complexity of quality-based prioritization
- Can always add later if multi-leader scenario emerges

## Performance Notes

**Execution time:** 2 minutes
- RED phase: Write 23 tests (1 min)
- GREEN phase: Implement classes (1 min)
- REFACTOR phase: Not needed (0 min)

**Test execution:** 0.03s for 23 tests (extremely fast)

**Memory:** Negligible - pure calculation classes, no state

## Future Enhancements

**Quality threshold tuning:**
- Simulate different thresholds (0.65/0.70/0.75) for initial trades
- Optimize spread/conviction weights based on historical data

**Dynamic leader average:**
- Update leader_avg_size from rolling window of leader trades
- Adapts to changing leader trade patterns

**Spread data integration:**
- Connect to real-time order book for spread calculation
- Use bid/ask spread instead of fixed assumptions

**Quality-based prioritization (if multi-leader):**
- Drop lowest quality position when at max_positions
- Requires position quality tracking in Portfolio
