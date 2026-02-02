---
phase: 04-advanced-sizing
plan: 04
subsystem: strategy-integration
status: complete
completed: 2026-02-02

requires:
  - 04-01 (EdgeTracker + KellyCalculator)
  - 04-02 (ConvictionScorer + TradeRanker)
  - 04-03 (AdaptiveSizer)
  - 03-04 (DynamicSizer integration)

provides:
  - MirrorStrategy with full Kelly integration
  - Per-token edge tracking from live trades
  - Conviction-weighted position sizing
  - Cold start handling with Phase 3 fallback
  - 8 Kelly integration tests

affects:
  - Phase 5 (Integration & Validation) - Kelly sizing now active in MirrorStrategy

tech-stack:
  added: []
  patterns:
    - Value capture before mutation (pos_before bug fix)
    - Realistic test data (95% win rate, not 100%)

key-files:
  created:
    - tests/unit/test_kelly_mirror.py: 8 Kelly integration tests
  modified:
    - src/core/__init__.py: Export Phase 4 modules
    - src/strategies/mirror/strategy.py: Kelly component integration
    - tests/integration/test_session_replay.py: Phase 4 comment

decisions:
  - "Entry price captured before portfolio mutation (pos_before bug fix)"
  - "95% win rate in tests (Kelly rejects 100% as unrealistic)"

metrics:
  tasks: 2
  commits: 2
  tests-added: 8
  tests-total: 369
  duration: 8min
  files-modified: 3
  files-created: 1
  loc-added: 399
---

# Phase 04 Plan 04: Kelly Integration Summary

**One-liner:** Wire EdgeTracker, KellyCalculator, ConvictionScorer, AdaptiveSizer, and TradeRanker into MirrorStrategy with cold start fallback and comprehensive integration tests

## What Was Built

### Task 1: Integrate Kelly Components into MirrorStrategy (78838d8)

**Core changes:**
- **core/__init__.py:** Added exports for EdgeTracker, TradeResult, KellyCalculator, ConvictionScorer, ConvictionSignals, TradeRanker, TradeOpportunity, AdaptiveSizer
- **MirrorStrategy.__init__():** Added Phase 4 component attributes (edge_tracker, kelly_calculator, conviction_scorer_kelly, adaptive_sizer, trade_ranker)
- **MirrorStrategy.initialize():**
  - KellyCalculator (0.5x Half Kelly)
  - EdgeTracker (50-trade lookback, 20 min for Kelly)
  - AdaptiveSizer (bridges DynamicSizer + Kelly)
  - ConvictionScorer (leader_avg_size=$100)
  - TradeRanker (15% correlation penalty, 1.5x rebalance gap)
- **MirrorStrategy._buy():**
  - Calculate conviction multiplier from leader trade size via ConvictionScorer
  - Use AdaptiveSizer.calculate_position_size() instead of DynamicSizer directly
  - Handle negative edge by skipping trade
  - All Phase 3 checks preserved (quality filter, floor, caps, min_shares)
- **MirrorStrategy.on_fill():**
  - Record trade results to EdgeTracker after sells
  - **Bug fix:** Capture entry_price BEFORE apply_sell() (pos_before reference gets modified)

**Flow:** Price validation → quality scoring → quality threshold → floor check → position limit → ADAPTIVE SIZING (Kelly/Phase3) → cost check → capacity/cap checks → order creation

**Result:** All 361 tests pass, Kelly sizing active but using Phase 3 fallback (no edge data yet)

### Task 2: Kelly Integration Tests + Edge Tracker Bug Fix (eb72bdb)

**tests/unit/test_kelly_mirror.py:** 8 integration tests
1. **test_cold_start_uses_phase3_sizing:** New strategy with no edge data uses Phase 3 DynamicSizer
2. **test_edge_tracker_updated_on_sell:** EdgeTracker records PnL after sell events
3. **test_conviction_multiplier_applied:** Larger leader trade → larger conviction multiplier → larger position size
4. **test_negative_edge_skips_trade:** 20 losing trades → negative edge → skip with reason "negative_edge"
5. **test_kelly_active_after_sufficient_trades:** 20+ trades (95% win rate) → Kelly sizing active
6. **test_different_tokens_independent_sizing:** Token A (Kelly), Token B (Phase 3 fallback)
7. **test_trade_ranker_initialized:** TradeRanker has correct config (15% penalty, 1.5x gap)
8. **test_scale_in_detection:** Second buy for same token detected as scale-in via leader_tracker

**Critical bug fix:**
- **Problem:** `pos_before = portfolio.get()` returns reference to same PortfolioPosition object
- **Issue:** `portfolio.apply_sell()` modifies the object, setting avg_price=0
- **Result:** `if pos_before.avg_price > 0` check fails, EdgeTracker never records trades
- **Solution:** Capture `entry_price = pos_before.avg_price` and `had_position = entry_price > 0` BEFORE apply_sell()

**Test data realism:**
- Initial attempt: 20 trades with 100% win rate
- Kelly calculator rejects: `if win_rate >= Decimal("1"): return None` (unrealistic edge case)
- Solution: 19 wins + 1 small loss = 95% win rate (realistic, passes Kelly validation)

**Session replay baselines:** Unchanged (23/22/187 for mirror) - conviction multiplier adjusts sizes but doesn't cross min_shares thresholds

**Result:** 369 tests pass (8 new Kelly tests, full suite regression-free)

## Deviations from Plan

### Auto-fixed Issues (Deviation Rules 1-3)

**1. [Rule 1 - Bug] pos_before reference mutation bug**
- **Found during:** Task 2 test writing
- **Issue:** portfolio.get() returns mutable reference, apply_sell() zeroes avg_price before EdgeTracker check
- **Fix:** Capture entry_price and had_position values BEFORE apply_sell() mutation
- **Files modified:** src/strategies/mirror/strategy.py (on_fill method)
- **Commit:** eb72bdb

**2. [Rule 3 - Blocking] 100% win rate rejected by Kelly**
- **Found during:** Task 2 test debugging
- **Issue:** KellyCalculator validates `win_rate < 1.0`, rejects 100% as unrealistic
- **Fix:** Use 95% win rate in tests (19 wins + 1 small loss)
- **Files modified:** tests/unit/test_kelly_mirror.py
- **Commit:** eb72bdb

## Technical Decisions

### 1. Value Capture Before Mutation Pattern
**Context:** PortfolioPosition returned by portfolio.get() is mutable reference
**Decision:** Save critical values (entry_price, had_position) BEFORE mutations
**Rationale:** Prevents subtle bugs where checks use post-mutation state
**Impact:** EdgeTracker now correctly records all sell trades
**Alternative considered:** Deep copy pos_before (rejected: unnecessary overhead)

### 2. Realistic Test Data (95% Win Rate)
**Context:** Kelly calculator rejects 100% win rate as edge case
**Decision:** Use 19 wins + 1 small loss (95% win rate) in Kelly tests
**Rationale:** Matches real-world constraints, Kelly validation is correct design
**Impact:** Tests validate realistic edge estimation scenarios
**Alternative considered:** Remove 100% win rate check (rejected: defeats statistical validation purpose)

### 3. Conviction Multiplier Neutral Default (15 seconds)
**Context:** Entry speed not available in session replay
**Decision:** Default entry_speed_seconds=15.0 (neutral, no penalty or boost)
**Rationale:** Avoids penalizing/boosting replay trades due to missing data
**Impact:** Replay baseline unchanged, conviction primarily driven by position size
**Alternative considered:** Use average from session data (rejected: adds complexity for marginal benefit)

## Next Phase Readiness

### Ready for Phase 5

**What Phase 5 needs:**
- ✅ MirrorStrategy with full Kelly integration
- ✅ EdgeTracker recording trades from sells
- ✅ Per-token edge statistics accumulation
- ✅ AdaptiveSizer with cold start fallback
- ✅ Conviction-weighted sizing

**Known limitations:**
- EdgeTracker starts empty (no historical data)
- First 20 trades per token use Phase 3 fallback
- Cold start is expected and handled gracefully
- Entry speed always neutral in replay (15 seconds default)

**Phase 5 validation approach:**
- Run session replays to accumulate edge data
- Verify EdgeTracker populates correctly
- Confirm transition from Phase 3 → Kelly sizing at 20-trade threshold
- Compare Kelly-sized positions vs Phase 3 fallback (expect larger sizes for positive edge tokens)

### Blockers/Concerns

**None.** All Phase 4 components integrated and tested.

**Portfolio position keying bug (existing, documented in Phase 1):**
- Portfolio._positions keyed only by token_id, not (token_id, market_id, side)
- Does not block Phase 4/5 operation (mirror strategy uses single market/side)
- Tracked for future multi-market support

## Testing

### Test Coverage

**New tests (8):**
- Cold start Phase 3 fallback
- EdgeTracker update after sells
- Conviction multiplier sizing impact
- Negative edge rejection
- Kelly activation threshold (20 trades)
- Per-token independent sizing
- TradeRanker initialization
- Scale-in detection

**Integration tests:**
- Session replay baselines unchanged (23/22/187)
- All 369 tests pass (361 existing + 8 new)
- No regressions in Phase 1-3 functionality

### Key Test Insights

**Edge tracker update test revealed mutation bug:**
- Initial test failure led to discovery of pos_before reference mutation
- Bug would have been silent in production (EdgeTracker never populated)
- Fix ensures accurate per-token edge statistics

**Kelly validation caught unrealistic test data:**
- 100% win rate test data rejected by Kelly
- Led to more realistic test scenarios (95% win rate)
- Validates Kelly's statistical integrity checks

## Performance

**Duration:** 8 minutes (2 tasks)
- Task 1 (Integration): ~3 minutes
- Task 2 (Tests + Bug Fix): ~5 minutes

**Test execution:** 2.59 seconds (369 tests)

**Comparison to estimates:**
- Estimated: 10-15 minutes
- Actual: 8 minutes
- Delta: -2 to -7 minutes (faster than expected, TDD workflow efficient)

## Lessons Learned

### 1. Mutable References in Financial Code
**Observation:** portfolio.get() returns mutable reference, not immutable snapshot
**Learning:** Always capture critical values BEFORE mutations in financial calculations
**Application:** Review other uses of portfolio.get() for similar mutation timing issues
**Impact:** Prevents silent failures in edge tracking and PnL estimation

### 2. Statistical Validation Edge Cases
**Observation:** Kelly calculator correctly rejects 100% win rate as unrealistic
**Learning:** Test data should reflect realistic scenarios, not mathematical edge cases
**Application:** Use 90-95% win rates in tests, add small losses for realism
**Impact:** Tests validate real-world Kelly behavior, not corner cases

### 3. Integration Testing Catches Reference Bugs
**Observation:** Unit tests for EdgeTracker passed, but integration test revealed mutation bug
**Learning:** Integration tests are critical for catching cross-component timing issues
**Application:** Always test components in realistic execution flow, not just in isolation
**Impact:** Discovered and fixed silent EdgeTracker bug before production

### 4. TDD Workflow Speed
**Observation:** Task 2 completed in 5 minutes despite bug discovery and fix
**Learning:** TDD's fast feedback loop enables rapid bug detection and resolution
**Application:** Continue TDD for future integration work
**Impact:** Faster debugging, higher confidence in fixes

## Files Changed

**Created (1):**
- tests/unit/test_kelly_mirror.py (8 integration tests, 399 lines)

**Modified (3):**
- src/core/__init__.py (+5 lines: Phase 4 exports)
- src/strategies/mirror/strategy.py (+60 lines: Kelly integration, -3 lines: bug fix)
- tests/integration/test_session_replay.py (+1 line: Phase 4 comment)

**Total:** +399 LOC added, -3 LOC removed

## Commits

1. **78838d8** - feat(04-04): integrate Kelly components into MirrorStrategy
2. **eb72bdb** - test(04-04): add Kelly integration tests and fix edge tracker update

---

*Phase 4 Complete! All adaptive sizing components integrated and tested. Ready for Phase 5 validation.*
