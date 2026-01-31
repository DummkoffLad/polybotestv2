# Phase 2: Performance Analysis - Context

**Gathered:** 2026-01-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Track every trade from entry to outcome with full equity curve visibility. Measure profit leakage by comparing our execution against the leader's actual trades. Produce actionable reports showing where money is made, lost, and left on the table. No optimization or sizing changes — that's Phase 3+.

</domain>

<decisions>
## Implementation Decisions

### Trade lifecycle tracking
- Each order tracked individually (not round-trip pairs) — Polymarket positions often resolve rather than being explicitly sold
- Open positions valued mark-to-market using current prices
- Market resolution auto-detected from Polymarket API — positions automatically close with final P&L
- Tracking works across all execution modes: simulation replay, dry run, and live — one unified system
- Trade data persisted to disk — survives restarts, enables cross-session analysis
- Leader data sourced from blockchain (partial fills, fast) + Polymarket API (portfolio data, no open orders)

### Equity curve mechanics
- Both per-trade event snapshots and periodic interval snapshots — full accuracy with calendar-time view
- Open positions repriced on every trade event AND at regular intervals — comprehensive mark-to-market
- Storage-conscious design — data is valuable for future analysis but budget for disk space

### Leader comparison method
- Three gap metrics measured equally:
  - **Price gap (slippage):** Our fill price vs leader's fill price — direct execution gap
  - **Sizing gap:** Our position sizes vs leader's proportional exposure
  - **Selection gap:** Which leader trades we skipped and their eventual P&L outcome
- Two slippage baselines tracked:
  - Leader's fill price vs our fill price (execution gap)
  - Market price at signal detection vs our fill price (delay cost)
- Separates delay cost from execution cost for targeted optimization

### Reporting & output
- Console summary after each run for quick checks
- Generated report files with visual charts for deeper analysis (equity curve plots, drawdown visualization, trade scatter)
- Charts require matplotlib or similar visualization library

### Claude's Discretion
- Capture fields per trade (timestamp, market, side, price, size + whatever context the success criteria need for attribution)
- Strategy tagging on trades (whether each trade records which strategy generated it)
- Storage format for persisted trade data (JSON, SQLite, CSV — whatever best serves analysis needs)
- Drawdown metrics (max drawdown from peak + recovery duration vs simpler metrics)
- Console summary density (scoreboard vs detailed breakdown)
- Whether to include a machine-readable output for Phase 3 consumption alongside human reports
- Full-follow benchmark simulation for measuring selection gap cost

</decisions>

<specifics>
## Specific Ideas

- "Use this copytrading architecture and data to make our own trader" — user sees this data as foundation for future independent trading (deferred, but influences data richness decisions)
- Leader data comes from blockchain partial fills (fast) + Polymarket API portfolio data — researcher should investigate both sources to map available fields
- Bot has three execution modes (simulation replay, dry run, live) — tracking must be mode-agnostic

</specifics>

<deferred>
## Deferred Ideas

- Building an independent trader using the copytrading architecture and accumulated performance data — future project/milestone, not Phase 2
- This influences Phase 2's data capture decisions: richer data now enables more options later

</deferred>

---

*Phase: 02-performance-analysis*
*Context gathered: 2026-01-31*
