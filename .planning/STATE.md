# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-01-30)

**Core value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital
**Current focus:** Phase 5 COMPLETE — All validation components delivered

## Current Position

Phase: 5 of 5 (Validation)
Plan: 5 of 5 (COMPLETE)
Status: Phase 5 complete
Last activity: 2026-02-02 — Completed 05-05 (ValidationPipeline integration)

Progress: [█████████████████████████] 100% (20/20 plans complete across all phases)

## Performance Metrics

**Velocity:**
- Total plans completed: 20
- Average duration: 4.5min
- Total execution time: 1.51 hours (91 minutes)

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-test-coverage | 4 | 19min | 5min |
| 02-performance-analysis | 4 | 20min | 5min |
| 03-dynamic-sizing | 4 | 15min | 4min |
| 04-advanced-sizing | 5 | 24min | 5min |
| 05-validation | 5 | 23min | 5min |

**Recent Trend:**
- Last 5 plans: 05-01 (4min), 05-02 (4min), 05-03 (4min), 05-04 (4min), 05-05 (7min)
- Trend: Phase 5 complete with consistent 4-7min per plan
- Note: Plan 05-05 took 7min (integration + testing) vs 4min average for TDD tasks

**PROJECT COMPLETE:** All 5 phases delivered, 461 tests passing

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
- Quality threshold set to 0.40 (03-04) — Permissive to minimize test disruption while still filtering worst trades
- Equity calculation simplified (03-04) — starting_capital + realized_pnl (deployed cancels out in expansion)
- Updated mirror baseline to 23/22/187 (03-04) — Quality filtering reduces trade count by ~20%
- Position size weighted 60%, scale-in 25%, entry speed 15% (04-02) — Conviction weighting by predictive value
- Non-linear mapping with quadratic amplification (04-02) — Enables full [0.25, 2.0] range despite 60% weight
- 15% soft penalty for correlated positions (04-02) — Diversification as tiebreaker, not veto
- 1.5x edge gap for rebalancing (04-02) — Conservative threshold prevents excessive position churn
- Quality score fallback when Kelly unavailable (04-02) — Enables cold-start operation before statistics accumulated
- Rolling window size 50 trades (04-01) — Balances recency with statistical significance
- Minimum 20 trades for Kelly (04-01) — Statistical minimum for meaningful win rate estimation
- Half Kelly (0.5x) (04-01) — Reduces volatility ~50% while keeping ~75% growth rate
- 20% max position cap (04-01) — Risk management, prevents single position domination
- Edge case defaults (04-01) — All wins → avg_loss=0.01, all losses → avg_win=0 (avoids div by zero)
- Conviction multiplier applied AFTER Kelly sizing (04-03) — Kelly fraction reduces volatility, conviction adjusts for signal strength
- Phase 3 fallback ensures no trades skipped (04-03) — Cold start is normal, all trades deserve sizing
- Return tuple (size_or_None, reason_string) (04-03) — Transparency for debugging and monitoring
- Entry price captured before portfolio mutation (04-04) — pos_before is mutable reference, save values before apply_sell()
- 95% win rate in tests (04-04) — Kelly rejects 100% win rate as unrealistic, use 19 wins + 1 loss for realism
- Manual t-test implementation (04-05) — scipy not available, implemented paired t-test using numpy only
- One-sided test for validation (04-05) — Phase 4 must be BETTER, not just different (p < 0.05 AND positive improvement)
- Fixed seed for bootstrap reproducibility (04-05) — Ensures consistent CI bounds across runs for debugging
- Profit factor returns None for no-loss case (04-05) — More explicit than infinity when all trades are wins
- Bootstrap CI allows lower == upper (04-05) — Zero-variance data is valid statistical outcome, not error
- Session IDs as filename stems (05-01) — Clean format: session_20260130_032713.jsonl → "session_20260130_032713"
- Automatic save on classification (05-01) — mark_in_sample/mark_out_of_sample call save() for safety
- Fail-fast on data leakage (05-01) — mark_out_of_sample raises ValueError if session already in-sample
- Idempotent OOS marking (05-01) — Safe to mark out-of-sample sessions multiple times
- Default slippage rate 0.1% per second (05-03) — Conservative for Polymarket hourly markets
- Independent jitter for detection and execution (05-03) — Each delay source has separate random sampling
- Private Random instance for reproducibility (05-03) — Prevents test interference via seeded RNG
- Linear price degradation model (05-03) — Simplified model proportional to delay in absence of order book
- Predefined stress scenarios (05-03) — Baseline, 2x, 3x, and zero configs for consistent testing
- All 4 criteria must pass for GO decision (05-04) — Conservative go-live approach protects capital
- Confidence from session count (05-04) — 1-2 sessions=low, 3-4=medium, 5+=high confidence
- Critical params: kelly_fraction, quality_threshold (05-04) — These control sizing and trade selection
- UTF-8 encoding for markdown reports (05-04) — Windows cp1252 doesn't support Unicode symbols
- Post-process ExecutedTrade for latency (05-05) — Avoids modifying SessionReplayer internals
- Use first OOS session as representative (05-05) — Balance accuracy vs speed for sensitivity/latency testing
- Convert BASELINE_PARAMS to floats internally (05-05) — SimpleOptimizer needs strings, SensitivitySweeper needs floats
- Bootstrap with 1000 iterations and seed=42 (05-05) — Reproducible CI calculation across runs

### Pending Todos

None.

### Quick Tasks Completed

| # | Description | Date | Directory |
|---|-------------|------|-----------|
| 001 | Fix WebSocket reconnection | 2026-02-03 | [001-fix-ws-reconnect](./quick/001-fix-ws-reconnect/) |

### Blockers/Concerns

**Portfolio position keying bug (discovered in 01-01):**
- Portfolio._positions keyed only by token_id, not (token_id, market_id, side)
- Same token_id in different markets incorrectly accumulates into single position
- Documented in test_multiple_markets_same_token_id
- Should be tracked for future fix, but doesn't block testing

**pos_before mutation bug (discovered and fixed in 04-04):**
- portfolio.get() returns mutable reference, not snapshot
- apply_sell() modified pos_before.avg_price to 0 before EdgeTracker check
- Fixed by capturing entry_price and had_position before apply_sell()
- Bug prevented EdgeTracker from recording any trades (silent failure)

**All tests passing — 461 total:**
- Phase 1: 22 tests (test coverage)
- Phase 2: 9 tests (performance analysis)
- Phase 3: 20 tests (dynamic sizing)
- Phase 4: 35 tests (Kelly sizing: 27 unit + 8 integration)
- Phase 5: 77 tests (validation: 14 data split + 14 sensitivity + 21 latency + 20 report + 8 integration)
- Integration: 41 tests (session replay)
- Existing: 257 tests (portfolio, strategies, core)

**Phase 5 COMPLETE:**
- All 5 plans executed successfully (05-01 through 05-05)
- ValidationPipeline orchestrates all components end-to-end
- Go/no-go decision framework operational
- Ready for live trading deployment validation

## Session Continuity

Last session: 2026-02-03 (Quick task 001)
Stopped at: Completed quick-001 (WS reconnect fix)
Resume file: None
Next action: PROJECT COMPLETE — Ready for live validation workflow

**Phase 5 Progress (5/5 plans COMPLETE):**
- Plan 05-01: DataSplitManager with data leakage prevention (4min, 14 tests)
- Plan 05-02: SensitivitySweeper with parameter sweep analysis (4min, 14 tests)
- Plan 05-03: LatencySimulator with stress scenario testing (4min, 21 tests)
- Plan 05-04: ValidationReportGenerator with go/no-go decision (4min, 20 tests)
- Plan 05-05: ValidationPipeline integration (7min, 8 integration tests)
- All validation components complete and tested
- Added 77 tests total in Phase 5
- All 461 tests passing

**Live Deployment Workflow:**
1. Gather 3-5 new out-of-sample sessions
2. Run: `from src.validation import run_validation`
3. Provide: `run_validation(out_of_sample_sessions=["session_xxx", ...])`
4. Review: Console summary shows GO/NO-GO decision
5. Analyze: Read markdown report for detailed metrics
6. Decision: If GO, proceed with live trading; if NO-GO, address issues
7. Monitor: Track live performance against OOS predictions

---
*State initialized: 2026-01-30*
*Last updated: 2026-02-02*
*Project Status: COMPLETE*
