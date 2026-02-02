# Roadmap: Polymarket Copy Trading Bot Optimization

## Overview

Transform the existing copy trading bot from a working prototype into a reliable small-budget profit machine. Starting with test coverage to validate existing behavior, we'll add performance analysis to identify profit leakage, optimize position sizing for $100 budget constraints, and validate robustness through systematic testing. The journey takes us from "it works" to "we trust the numbers and maximize every dollar."

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Test Coverage** - Validate existing strategy and risk implementations
- [x] **Phase 2: Performance Analysis** - Track attribution, drawdown, and profit leakage
- [x] **Phase 3: Dynamic Sizing** - Adapt position sizing to current capital
- [x] **Phase 4: Advanced Sizing** - Optimize capital efficiency and edge-based sizing
- [ ] **Phase 5: Validation** - Prove robustness through out-of-sample testing

## Phase Details

### Phase 1: Test Coverage
**Goal**: Existing strategies, risk caps, and portfolio math are validated with automated tests
**Depends on**: Nothing (first phase)
**Requirements**: TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. All 8 strategy implementations produce expected outputs for known inputs
  2. Risk caps enforce correctly (per-market, per-side, global) in all scenarios
  3. Portfolio cost basis and PnL calculations match manual verification
  4. Test suite runs in under 1 minute and catches regressions
**Plans**: 4 plans

Plans:
- [x] 01-01-PLAN.md -- Test infrastructure + portfolio math unit tests
- [x] 01-02-PLAN.md -- Strategy unit tests (all 8 strategies, parameterized + specific)
- [x] 01-03-PLAN.md -- Risk cap enforcement tests (per-market, per-side, global, boundary)
- [x] 01-04-PLAN.md -- Session replay integration tests + full suite validation

### Phase 2: Performance Analysis
**Goal**: Every trade is tracked from entry to outcome with full equity curve visibility
**Depends on**: Phase 1
**Requirements**: ANAL-01, ANAL-02, ANAL-03
**Success Criteria** (what must be TRUE):
  1. Each executed trade links to its final PnL (win/loss/open position)
  2. Equity curve shows capital over time with max drawdown from peak
  3. Replay results show where our sizing/timing differs from leader's actual profits
  4. Profit attribution identifies which trades contributed most to final PnL
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md -- Trade attribution + equity tracker (core analysis types + tests)
- [x] 02-02-PLAN.md -- Drawdown analyzer + slippage analyzer (risk metrics + execution quality + tests)
- [x] 02-03-PLAN.md -- Report generator + replay integration (wire everything together, console + charts)

### Phase 3: Dynamic Sizing
**Goal**: Position sizes adapt to current capital and trade quality rather than fixed rules
**Depends on**: Phase 2
**Requirements**: SIZE-01, SIZE-02
**Success Criteria** (what must be TRUE):
  1. Risk caps scale with current capital, not starting capital
  2. Capital compounds after wins and contracts after losses
  3. Low-confidence trades are skipped based on spread cost or other filters
  4. Selective following conserves capital for high-edge opportunities
**Plans**: 4 plans

Plans:
- [x] 03-01-PLAN.md -- DynamicSizer engine (percentage-based position sizing with TDD)
- [x] 03-02-PLAN.md -- CapitalManager floor system (two-tier soft/hard floor protection with TDD)
- [x] 03-03-PLAN.md -- TradeQualityScorer and SelectiveFollower (trade filtering with TDD)
- [x] 03-04-PLAN.md -- Wire dynamic sizing into MirrorStrategy (integration + regression tests)

### Phase 4: Advanced Sizing
**Goal**: Each dollar is allocated to maximize risk-adjusted returns
**Depends on**: Phase 3
**Requirements**: SIZE-03, SIZE-04
**Success Criteria** (what must be TRUE):
  1. Kelly criterion sizes positions proportional to estimated edge and bankroll
  2. Capital efficiency scoring ranks available trades by return per dollar deployed
  3. Position sizing adapts to trade confidence and market conditions
  4. Replay comparisons show improved PnL vs fixed sizing strategies
**Plans**: 5 plans

Plans:
- [x] 04-01-PLAN.md -- EdgeTracker + KellyCalculator (per-token edge tracking + Half Kelly sizing with TDD)
- [x] 04-02-PLAN.md -- ConvictionScorer + TradeRanker (leader conviction multiplier + trade prioritization with TDD)
- [x] 04-03-PLAN.md -- AdaptiveSizer (cold start fallback bridging Phase 3 and Kelly sizing with TDD)
- [x] 04-04-PLAN.md -- Wire Kelly into MirrorStrategy (integration + updated replay baselines)
- [x] 04-05-PLAN.md -- KellyValidator (statistical validation with paired t-test + bootstrap CI)

### Phase 5: Validation
**Goal**: Strategy performance is proven robust across different data and parameters
**Depends on**: Phase 4
**Requirements**: RBST-01, RBST-02, RBST-03
**Success Criteria** (what must be TRUE):
  1. Optimized strategies perform on test data not used during optimization
  2. Results remain stable when parameters are tweaked by 10-20%
  3. Replay simulations include realistic API latency delays
  4. Validation report shows confidence intervals and robustness metrics
**Plans**: TBD

Plans:
- [ ] TBD during planning

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Test Coverage | 4/4 | Complete | 2026-01-31 |
| 2. Performance Analysis | 3/3 | Complete | 2026-01-31 |
| 3. Dynamic Sizing | 4/4 | Complete | 2026-02-02 |
| 4. Advanced Sizing | 5/5 | Complete | 2026-02-02 |
| 5. Validation | 0/TBD | Not started | - |

---
*Roadmap created: 2026-01-30*
*Last updated: 2026-02-02 after Phase 4 execution complete*
