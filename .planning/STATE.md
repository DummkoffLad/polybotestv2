# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-01-30)

**Core value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital
**Current focus:** Phase 2 complete, ready for Phase 3: Dynamic Sizing

## Current Position

Phase: 3 of 5 (Dynamic Sizing)
Plan: 3 of 3 (Trade Quality Filtering)
Status: In progress
Last activity: 2026-02-02 — Completed 03-03-PLAN.md (Trade Quality Filtering)

Progress: [██████████████████░░] 90% (9/10 plans complete, 1 plan remaining in phase 3)

## Performance Metrics

**Velocity:**
- Total plans completed: 9
- Average duration: 4min
- Total execution time: 0.70 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-test-coverage | 4 | 19min | 5min |
| 02-performance-analysis | 4 | 20min | 5min |
| 03-dynamic-sizing | 3 | 6min | 2min |

**Recent Trend:**
- Last 5 plans: 02-02 (6min), 02-01 (4min), 02-03 (6min), 03-02 (2min), 03-03 (2min)
- Trend: Excellent velocity, TDD execution is very fast (2min)
- Note: Phase 3 plans using TDD are extremely efficient

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Focus on simulation before live — Need confidence in numbers before risking real money
- Single leader strategy — Identified whale is proven profitable
- Small budget optimization — Under $100 means sizing is the critical variable
- Helper functions over fixtures for flexibility (01-01) — make_trade(**overrides) pattern enables flexible test data creation
- Decimal-only arithmetic with quantize() (01-01) — Financial precision requires exact comparisons, never pytest.approx
- Document bugs, don't fix in test phase (01-01) — Portfolio position keying bug documented via test
- Local test helpers instead of conftest (01-02) — Enables Wave 1 parallel execution
- Parameterized tests + strategy-specific differentiators (01-02) — Validates interface compliance and unique behavior
- Self-contained test helpers with _buy_and_fill (01-03) — Build up positions before testing cap enforcement
- Test both standard and conservative cap configurations (01-03) — Mirror (30%/26%) vs Conservative (20%/18%) caps
- Focus on boundary conditions for cap tests (01-03) — Caps bind frequently in sub-$100 operation, boundary behavior critical
- Single session for integration baselines (01-04) — Reserve other 5 sessions for Phase 5 out-of-sample validation
- Exact regression baselines over approximations (01-04) — Lock down exact buy/sell/skip counts to catch unintended changes
- Lazy imports to break circular dependencies (01-04) — simulation/__init__.py uses __getattr__ pattern
- Lazy pandas import keeps EquityTracker lightweight (02-01) — Core functionality works without pandas, only to_dataframe() requires it
- Manual snapshot recording for replay integration (02-01) — Portfolio state comes from strategy in replay, not Portfolio object
- Trade sequencing allows multiple entries per token (02-01) — Same token can have multiple DCA/re-entry trades tracked separately
- Use pandas cummax() for drawdown calculation (02-02) — Vectorized operation is faster and more reliable than manual peak tracking
- Volume-weighted slippage aggregation (02-02) — Ensures larger trades have appropriate weight, matches dollar impact on PnL
- Three gap metrics measured equally (02-02) — Price, sizing, and selection gaps provide comprehensive view of profit leakage
- Separate execution and delay slippage (02-02) — Enables targeted optimization (reduce delay vs improve execution)
- track_analysis parameter optional (02-03) — Preserves existing replay behavior, analysis is opt-in
- Record equity snapshot after each event (02-03) — High-resolution equity curve for accurate drawdown calculation
- matplotlib Agg backend for charts (02-03) — Headless environments (CI, servers) can generate charts
- Soft floor quality threshold at 0.85 (03-02) — Exceptional entries only during 10% drawdown
- Position management allowed at soft floor (03-02) — Risk-reducing activity (sells, adjustments) still permitted
- Hard floor blocks all activity (03-02) — 30% drawdown triggers full trading halt
- Mode transitions logged at INFO level (03-02) — Clear audit trail for operational events
- 60/40 weighting for spread/conviction (03-03) — Spread cost is dominant factor for sub-$100 accounts
- Linear interpolation for spread scoring (03-03) — Simple, predictable scoring between 50-300 bps thresholds
- Conviction capping at 2x leader average (03-03) — Prevents outlier large trades from dominating quality score
- Dual quality thresholds (03-03) — 0.70 for initial trades, 0.75 for DCA (higher bar prevents averaging down)
- FCFS position allocation (03-03) — Sufficient for single-leader following, avoids complex prioritization

### Pending Todos

None yet.

### Blockers/Concerns

**Portfolio position keying bug (discovered in 01-01):**
- Portfolio._positions keyed only by token_id, not (token_id, market_id, side)
- Same token_id in different markets incorrectly accumulates into single position
- Documented in test_multiple_markets_same_token_id
- Should be tracked for future fix, but doesn't block testing

## Session Continuity

Last session: 2026-02-02 (phase 3 plan 03 execution complete)
Stopped at: Completed 03-03-PLAN.md — TradeQualityScorer and SelectiveFollower implemented, 23 tests passing
Resume file: None
Next action: Execute 03-04-PLAN.md (Integration) to wire sizing/capital/quality components together

---
*State initialized: 2026-01-30*
*Last updated: 2026-02-02*
