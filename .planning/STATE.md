# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-03)

**Core value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital — every dollar matters at this budget.
**Current focus:** Phase 7.1 - Codebase Cleanup (milestone v1.1)

## Current Position

Phase: 7.1 of 11 (Codebase Cleanup) - IN PROGRESS
Plan: 3 of 7 in current phase
Status: Wave 2 execution - 3 plans complete
Last activity: 2026-02-05 — Completed 07.1-03-PLAN.md (extracted strategy mixins)

Progress: [███████░░░] 65% (30 of 46+ plans complete across milestones)

## Performance Metrics

**Velocity:**
- Total plans completed: 30 (v1.0 complete, v1.1 in progress)
- Average duration: 4.7min (all time)
- Total execution time: 2.38 hours (all time)

**By Phase:**

| Phase | Plans | Total | Avg/Plan | Status |
|-------|-------|-------|----------|--------|
| 01-test-coverage | 4 | 19min | 5min | Complete |
| 02-performance-analysis | 4 | 20min | 5min | Complete |
| 03-dynamic-sizing | 4 | 15min | 4min | Complete |
| 04-advanced-sizing | 5 | 24min | 5min | Complete |
| 05-validation | 5 | 23min | 5min | Complete |
| 06-statistical-validation | 3 | 11min | 4min | Complete |
| 07-comparison-infrastructure | 3 | 22min | 7min | Complete |
| 07.1-codebase-cleanup | 3 | 28min | 9min | In Progress (3/7) |

**Recent Trend:**
- Last 5 plans: 07.1-01 (8min), 07.1-06 (4min), 07.1-05 (5min), 07.1-03 (11min)
- Milestone v1.0: Complete (21 plans, 461 tests passing)
- Milestone v1.1: In progress (8 plans complete, Phase 7 complete, Phase 7.1 in progress)

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
- **Scattergl for WebGL performance (07-02):** Use WebGL-accelerated Plotly traces for large datasets
- **CDN for plotly.js (07-02):** Use include_plotlyjs='cdn' for ~3MB smaller HTML files
- **empyrical-reloaded for metrics (07-02):** Don't hand-roll Sharpe/Sortino/Calmar ratios
- **Pandas Styler for decision matrix (07-03):** df.style.map() for cell colors, apply() for row highlighting
- **QuantStats returns conversion (07-03):** equity.pct_change().dropna() for tear sheet generation
- **Keep profit_taker params (quick-005):** Current 35/20/12% optimal given 2 sessions - avoid overfitting
- **ET timezone for hourly analysis (quick-005):** Polymarket operates on US Eastern Time
- **Max 10 tokens per viz (quick-005):** Prevent overwhelming charts with too many subplots
- **Net Cash as primary metric (quick-006):** Show received - spent as main line, spent/received as context
- **Logical subplot order (quick-006):** Portfolio -> Cash -> Unrealized -> Realized for narrative flow
- **Hourly charts default (quick-006):** Generate per-market hourly charts by default (--no-hourly to skip)
- **Public function naming (07.1-01):** Changed _to_side to to_side for shared utils (public API)
- **utils.py for strategy utilities (07.1-01):** Place strategy-specific shared utilities in src/strategies/utils.py
- **Explicit mixin init (07.1-03):** Each mixin has __init__ that strategies must call explicitly (avoids MRO complexity)
- **Mixin composition (07.1-03):** Mixins before Strategy in inheritance list for proper method lookup
- **PnL calculator location (07.1-06):** src/analysis/pnl_calculator.py for shared PnL logic (analysis concern, not strategy)
- **Shared calculation pattern (07.1-06):** Extract duplicated calculations into modules with clean interfaces
- **Package structure for large modules (07.1-05):** Split 800+ line files into focused packages with loader/processor/orchestrator pattern
- **Property delegation (07.1-05):** Expose nested state via @property for backward compatibility

### Roadmap Evolution

- Phase 7.1 inserted after Phase 7: Codebase Cleanup (URGENT) — addresses 29 duplicated utility functions, inconsistent enums, 877 lines dead code, 4 untested strategies

### Pending Todos

None yet.

### Quick Tasks Completed

| # | Description | Date | Directory |
|---|-------------|------|-----------|
| 001 | Fix WebSocket reconnection | 2026-02-03 | [001-fix-ws-reconnect](./quick/001-fix-ws-reconnect/) |
| 003 | Refactor strategy cost to use real spread | 2026-02-04 | [003-refactor-strategy-config-real-spread](./quick/003-refactor-strategy-config-real-spread/) |
| 004 | Reorganize sessions into day folders | 2026-02-04 | [004-reorganize-sessions-day-folders](./quick/004-reorganize-sessions-day-folders/) |
| 005 | Profit taker docs, hourly split, visualization | 2026-02-04 | [005-profit-taker-hourly-viz-docs](./quick/005-profit-taker-hourly-viz-docs/) |
| 006 | Fix viz UP/DOWN P&L charts | 2026-02-04 | [006-fix-viz-up-down-pnl-charts](./quick/006-fix-viz-up-down-pnl-charts/) |

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

Last session: 2026-02-05
Stopped at: Completed 07.1-03-PLAN.md (extracted strategy mixins for shared behaviors)
Resume file: None
Next action: Continue Phase 7.1 execution - Plans 02, 04, 07 remaining (3 cleanup plans)
Testing script: `python -m src.simulation.full_optimizer data/sessions/` — runs all strategies against all sessions

**v1.0 Summary (COMPLETE):**
- 5 phases, 21 plans, 461 tests passing
- Validation pipeline operational
- Conservative strategy identified as only profitable in 12-session test

**v1.1 Context:**
- 6 phases planned (Phases 6-11)
- 26 requirements mapped to phases (100% coverage)
- Research: Validate significance → Compare → Analyze → Validate → Build → Monitor
- NEW DATA (2026-02-04): Second run lost money on ALL strategies — must investigate regime difference
- **Phase 7 COMPLETE:** Comparison infrastructure ready (97 tests, 10 module exports)
- **Phase 7.1 IN PROGRESS:** Codebase cleanup (3/7 plans complete - shared utils, PnL calculator, replay package refactored)

---
*State initialized: 2026-01-30*
*Last updated: 2026-02-05 after 07.1-05-PLAN.md completion*
*Milestone: v1.1 Beat Conservative*
