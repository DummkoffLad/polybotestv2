# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital — every dollar matters at this budget.
**Current focus:** Phase 6 - Statistical Validation (milestone v1.1)

## Current Position

Phase: 6 of 11 (Statistical Validation)
Plan: 2 of TBD in current phase
Status: In progress
Last activity: 2026-02-04 — Completed 06-02-PLAN.md

Progress: [█████░░░░░] 54% (23 of 42+ plans complete across milestones)

## Performance Metrics

**Velocity:**
- Total plans completed: 23 (v1.0 complete, v1.1 in progress)
- Average duration: 4.5min (all time)
- Total execution time: 1.60 hours (all time)

**By Phase:**

| Phase | Plans | Total | Avg/Plan | Status |
|-------|-------|-------|----------|--------|
| 01-test-coverage | 4 | 19min | 5min | Complete |
| 02-performance-analysis | 4 | 20min | 5min | Complete |
| 03-dynamic-sizing | 4 | 15min | 4min | Complete |
| 04-advanced-sizing | 5 | 24min | 5min | Complete |
| 05-validation | 5 | 23min | 5min | Complete |
| 06-statistical-validation | 2 | 8min | 4min | In progress |

**Recent Trend:**
- Last 5 plans: 05-03 (4min), 05-04 (4min), 05-05 (7min), 06-01 (4min), 06-02 (4min)
- Milestone v1.0: Complete (21 plans, 461 tests passing)
- Milestone v1.1: In progress (2 plans complete, Phase 6)

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
Stopped at: Completed 06-02-PLAN.md (Statistical hypothesis testing)
Resume file: None
Next action: Continue with Phase 6 plans (06-03: Conservative vs others comparison)
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
*Last updated: 2026-02-04 after 06-01-PLAN.md completion*
*Milestone: v1.1 Beat Conservative*
