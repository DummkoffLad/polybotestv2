# Phase 7: Comparison Infrastructure - Context

**Gathered:** 2026-02-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Run all strategies side-by-side on identical session data with complete decision visibility. Produce equity curves, metrics tables, decision matrices, and QuantStats tear sheets. This is analysis infrastructure — strategy building and failure categorization are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Equity Curve Visualization
- Overlay drawdown as shaded zones on equity chart
- No trade markers on equity curve — keep it clean, trades shown in decision matrix

### Metrics Presentation
- Color coding for best/worst: green for best, red for worst in each metric column
- Statistical confidence shown in separate section, not inline with main metrics

### Decision Matrix Format
- Show position size + resulting PnL per cell (not just took/skipped)
- Highlight rows where strategies diverged — visual emphasis on disagreements
- No drill-down functionality — matrix is summary view only

### Report Structure
- Generate both interactive HTML (Plotly) and static export option
- User can choose format based on context (browser vs sharing)

### Claude's Discretion
- Equity curve layout (overlaid vs side-by-side panels)
- Time resolution for equity data points (per-trade vs fixed interval)
- Which metrics to include in summary table
- How to handle sample size differences between strategies
- Decision matrix organization (trade-centric vs strategy-centric rows)
- Report organization (consolidated vs per-strategy + summary)
- Output directory structure
- QuantStats generation policy (always vs on-demand)

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for trading strategy comparison tooling.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 07-comparison-infrastructure*
*Context gathered: 2026-02-04*
