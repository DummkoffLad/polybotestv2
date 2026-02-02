# Phase 5: Validation - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Prove the optimized strategy is robust through out-of-sample testing, parameter sensitivity analysis, latency simulation, and a validation report. This phase does NOT add new trading capabilities — it validates the existing Phase 3 + Phase 4 stack and produces a go/no-go decision for live trading.

</domain>

<decisions>
## Implementation Decisions

### Data splitting
- Existing session data is mostly low quality — only 1-2 of 6 sessions are usable
- User will gather new session data before running validation
- Design validation to accept new session data as it becomes available
- New data should be gathered in observation/dry-run mode (Claude's discretion on approach)
- The 1 session used for Phase 1 baselines is in-sample; all new sessions are out-of-sample

### Parameter sensitivity
- Sweep ALL tunable parameters across Phases 3 and 4 (Kelly sizing, quality thresholds, floor/risk params, conviction weights, etc.)
- Test within 10-20% range of current values
- Comprehensive analysis — user wants confidence that nothing is fragile

### Latency simulation
- Model the full latency pipeline: detection delay + execution delay
- Current live latency is under 5 seconds (relatively fast)
- Model realistic conditions and stress scenarios

### Validation reporting
- Dual output: console summary for quick runs + saved markdown report for each validation session
- The report must answer a clear go/no-go question: is the strategy ready for live trading?
- Minimum bar for "go": positive PnL on out-of-sample data
- Charts and visualization at Claude's discretion where they add clarity

### Claude's Discretion
- Observation vs dry-run mode for new data gathering
- Parameter sweep resolution (extremes only vs fine grid)
- One-at-a-time vs combination parameter sweeps (based on likely interactions)
- Latency stress test scenarios and severity levels
- Price impact modeling approach (actual tick data vs estimated slippage)
- Which charts to include vs tables-only sections
- How to present the go/no-go recommendation (thresholds, scoring, etc.)

</decisions>

<specifics>
## Specific Ideas

- User's primary concern is knowing whether to trust the bot with real money
- The go/no-go framing means the report should be decisive, not ambiguous
- Fresh data collection is a prerequisite — validation is only meaningful on unseen data
- Sub-5s latency in production means latency impact may be small but should still be quantified

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-validation*
*Context gathered: 2026-02-02*
