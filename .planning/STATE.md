# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital — every dollar matters at this budget.
**Current focus:** Phase 7 - Comparison Infrastructure (milestone v1.1)

## Current Position

Phase: 7 of 11 (Comparison Infrastructure)
Plan: 1 of 3 in current phase
Status: In progress
Last activity: 2026-02-04 — Completed 07-01-PLAN.md

Progress: [██████░░░░] 60% (25 of 42+ plans complete across milestones)

## Performance Metrics

**Velocity:**
- Total plans completed: 25 (v1.0 complete, v1.1 in progress)
- Average duration: 4.4min (all time)
- Total execution time: 1.73 hours (all time)

**By Phase:**

| Phase | Plans | Total | Avg/Plan | Status |
|-------|-------|-------|----------|--------|
| 01-test-coverage | 4 | 19min | 5min | Complete |
| 02-performance-analysis | 4 | 20min | 5min | Complete |
| 03-dynamic-sizing | 4 | 15min | 4min | Complete |
| 04-advanced-sizing | 5 | 24min | 5min | Complete |
| 05-validation | 5 | 23min | 5min | Complete |
| 06-statistical-validation | 3 | 11min | 4min | Complete |
| 07-comparison-infrastructure | 1 | 5min | 5min | In progress |

**Recent Trend:**
- Last 5 plans: 05-05 (7min), 06-01 (4min), 06-02 (4min), 06-03 (3min), 07-01 (5min)
- Milestone v1.0: Complete (21 plans, 461 tests passing)
- Milestone v1.1: In progress (4 plans complete, Phase 7 in progress)

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting v1.1 milestone:

- Conservative as baseline: Only profitable strategy in 12-session overnight test
- Pattern discovery deferred: v1.2 research track, not blocking strategy work
- Statistical validation first: Must confirm conservative's edge is real before analysis
- Scipy direct usage (06-02): Use scipy.stats.ttest_rel directly instead of hand-rolling t-distribution for reliability
- **scipy.stats.bootstrap (06-01):** Use industry-standard library instead of hand-rolled bootstrap
- **Fixed seed for reproducibility (06-01):** seed=42 for deterministic test results
- **Research-backed thresholds (06-01):** 30/100/200 trades for minimum/basic/high confidence
- **Overnight hours (06-03):** {22, 23, 0, 1, 2, 3, 4, 5} for regime classification (10PM-6AM)
- **Volatility threshold (06-03):** 5.0% avg absolute price change separates high/low vol regimes
- **Welch's t-test (06-03):** Use equal_var=False for independent regime comparison
- **Sequential replay for comparison (07-01):** Not parallel - ensures data consistency and state isolation
- **Fresh replayer per strategy (07-01):** Each strategy gets isolated SessionReplayer instance

### Pending Todos

None yet.

### Quick Tasks Completed

| # | Description | Date | Directory |
|---|-------------|------|-----------|
| 001 | Fix WebSocket reconnection | 2026-02-03 | [001-fix-ws-reconnect](./quick/001-fix-ws-reconnect/) |

### Blockers/Concerns

**Statistical Significance Risk (v1.1) — CONFIRMED:**
- Session 1: 12 hours overnight (low volatility) → conservative ONLY profitable
- Session 2: 14 hours (2026-02-04) → ALL strategies LOST including conservative
- Research warning validated: conservative's win was likely noise, not signal
- Key investigation: What changed? Time of day? Volatility regime? Market type?
- Phase 6 must now also explain why results flipped completely

**Small Sample Size (v1.1):**
- 12 sessions may not provide 95% confidence
- Must calculate actual trade count and confidence intervals
- Session 2 data adds ~14 more sessions — need combined analysis

**Overfitting Risk (v1.1):**
- With only 12 sessions, any patterns found are likely noise
- Phase 10 enforces 30% holdout for validation
- New strategy must beat conservative on out-of-sample data

**Exit Timing Investigation (v1.1):**
- Per-trade: Were positions profitable at some point before turning into losses?
- Could we have sold earlier than leader and captured profit?
- Phase 8 (FAIL-06) will track max unrealized profit per trade
- Key question: Is the problem entry selection or exit timing?

**Portfolio position keying bug (v1.0 documented):**
- Portfolio._positions keyed only by token_id, not (token_id, market_id, side)
- Same token_id in different markets incorrectly accumulates into single position
- Documented in test_multiple_markets_same_token_id
- Deferred to future fix

## Session Continuity

Last session: 2026-02-04
Stopped at: Completed 07-01-PLAN.md (StrategyComparator core)
Resume file: None
Next action: 07-02-PLAN.md (Equity curve visualization)
Testing script: `full_optimizer.py` — runs all strategies against recorded sessions

**v1.0 Summary (COMPLETE):**
- 5 phases, 21 plans, 461 tests passing
- Validation pipeline operational
- Conservative strategy identified as only profitable in 12-session test

**v1.1 Context:**
- 6 phases planned (Phases 6-11)
- 26 requirements mapped to phases (100% coverage)
- Research: Validate significance → Compare → Analyze → Validate → Build → Monitor
- NEW DATA (2026-02-04): Second run lost money on ALL strategies — must investigate regime difference

---
*State initialized: 2026-01-30*
*Last updated: 2026-02-04 after 07-01-PLAN.md completion*
*Milestone: v1.1 Beat Conservative*
