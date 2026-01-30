# Phase 1: Test Coverage - Context

**Gathered:** 2026-01-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate existing strategies, risk caps, and portfolio math with automated tests. All 8 strategy implementations (mirror, momentum, conservative, aggressive, spread_aware, velocity, price_level, hybrid_conservative) must produce expected outputs. Risk caps must enforce correctly per-market, per-side, and globally. Portfolio cost basis and PnL must match verification. Test suite runs under 1 minute.

</domain>

<decisions>
## Implementation Decisions

### Correctness baseline
- Use two real 40-minute leader trade sessions from the data folder as test input data
- Expected outputs derived from code analysis (trace through strategy logic with known inputs, lock results as expected)
- User has NOT manually verified expected outputs — correctness is defined by what the code logic dictates for given inputs
- All 8 strategies are actively used and equally important — no priority ordering

### Test failure policy
- Bugs discovered by tests should be **documented, not fixed** in this phase
- Exception: Claude uses judgment for risk cap failures that could lose real money — those may warrant immediate fixes
- Code refactoring to improve testability is allowed and encouraged when needed
- Issue tracking approach is Claude's discretion

### Risk scenario coverage
- Budget is under $100 — caps will bind frequently, this is the primary operating regime
- Both over-concentration (too much in one market) and cascade losses (death by small cuts) are equally important to protect against
- Risk caps are percentage-based (proportional to current capital)
- Claude decides whether to test rapid consecutive trades based on whether the code has timing-dependent cap enforcement

### Claude's Discretion
- PnL precision tolerance (based on USDC decimal precision)
- Whether to hold one session back for Phase 5 out-of-sample validation vs use both now
- Whether to add synthetic edge-case inputs beyond the real session data
- Whether to test strategies in isolation only or also as composites
- Issue tracking format (markdown report, inline TODOs, etc.)
- Rapid/burst trade scenario coverage based on code analysis

</decisions>

<specifics>
## Specific Ideas

- Two recorded leader sessions (~40 min each) in the data folder serve as the primary test fixtures
- Under-$100 budget means cap enforcement is not just a safety net — it's the normal operating condition
- Tests should catch regressions — the suite needs to be reliable enough to trust going forward

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-test-coverage*
*Context gathered: 2026-01-30*
