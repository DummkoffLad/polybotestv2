# Phase 3: Dynamic Sizing - Context

**Gathered:** 2026-01-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Position sizes adapt to current capital and trade quality rather than fixed rules. Risk caps scale dynamically, capital compounds after wins and contracts after losses, and low-confidence trades are filtered out to conserve capital for high-edge opportunities.

This phase covers sizing logic, capital management, trade filtering, and selective following. It does NOT cover Kelly criterion, capital efficiency scoring, or advanced edge estimation (Phase 4), nor out-of-sample validation (Phase 5).

</domain>

<decisions>
## Implementation Decisions

### Capital scaling rules
- Two-tier floor system: **soft floor** and **hard floor**
- **Soft floor behavior:** Bot reduces activity but can still manage existing positions AND enter new trades if quality is exceptional (high-confidence only)
- **Hard floor behavior:** Full trading stop, no new entries, no adjustments
- Soft/hard floor values: Claude's discretion (percentage vs fixed, exact thresholds)
- Scaling approach (proportional vs stepped vs percentage-based): Claude's discretion

### Compounding & contracting
- Compounding strategy after wins: Claude's discretion (immediate vs gradual)
- Contraction strategy after losses: Claude's discretion (proportional vs aggressive pullback)
- Cooldown after consecutive losses: Claude's discretion (pause vs just reduce size)
- High-water mark tracking: Claude's discretion (conservative below peak vs ignore peak)

### Trade filtering criteria
- Primary filter selection (spread, leader size, liquidity): Claude's discretion based on existing trade data analysis
- Minimum edge threshold: Claude's discretion (strict vs adaptive)
- Time-to-resolution filtering: Claude's discretion
- Skip logging level: Claude's discretion

### Selective following policy
- Trade prioritization when capital-constrained: Claude's discretion (FCFS vs score-and-rank)
- Maximum position limit strategy: Claude's discretion (hard count vs capital-based minimum)
- Active rebalancing (exit weak for strong): Claude's discretion
- DCA following policy (follow leader's adds vs only first entries): Claude's discretion

### Claude's Discretion
The user has given broad discretion on nearly all implementation details for this phase. The key constraint is:
- **Two-tier floor system is locked** — soft floor (reduced activity + exceptional entries) and hard floor (full stop) must be implemented
- Everything else: Claude should research what works best for sub-$100 copy trading accounts and make data-driven decisions during planning

</decisions>

<specifics>
## Specific Ideas

- Soft floor should allow managing existing positions AND rare high-confidence entries (not just one or the other)
- The user trusts the leader's judgment generally but wants protection against grinding to zero
- Sub-$100 account constraints are the primary design driver — every decision should optimize for small capital

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-dynamic-sizing*
*Context gathered: 2026-01-31*
