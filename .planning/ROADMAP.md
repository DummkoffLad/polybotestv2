# Roadmap: Polymarket Copy Trading Bot

## Milestones

- ✅ **v1.0 MVP** - Phases 1-5 (shipped 2026-02-02)
- ✅ **v1.1 Beat Conservative** - Phases 6-7.1 (shipped 2026-02-09)
- 🚧 **v1.2 Production Ready** - Phases 8-12 (in progress)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-5) - SHIPPED 2026-02-02</summary>

### Phase 1: Test Coverage
**Goal**: Validate simulation accuracy against session recordings
**Plans**: 4 plans
**Status**: Complete

### Phase 2: Performance Analysis
**Goal**: Identify leader's edge and profit patterns
**Plans**: 4 plans
**Status**: Complete

### Phase 3: Dynamic Sizing
**Goal**: Capture more upside while managing risk
**Plans**: 4 plans
**Status**: Complete

### Phase 4: Advanced Sizing
**Goal**: Tune position sizing to maximize risk-adjusted returns
**Plans**: 5 plans
**Status**: Complete

### Phase 5: Validation
**Goal**: Prevent overfitting with statistical rigor
**Plans**: 5 plans
**Status**: Complete

</details>

<details>
<summary>✅ v1.1 Beat Conservative (Phases 6-7.1) - SHIPPED 2026-02-09</summary>

### Phase 6: Statistical Validation
**Goal**: Add statistical rigor to strategy comparison
**Plans**: 3 plans
**Status**: Complete

### Phase 7: Comparison Infrastructure
**Goal**: Side-by-side strategy evaluation with visual analysis
**Plans**: 3 plans
**Status**: Complete

### Phase 7.1: Codebase Cleanup
**Goal**: Consolidate duplicate code and improve maintainability
**Plans**: 7 plans
**Status**: Complete

</details>

## 🚧 v1.2 Production Ready (In Progress)

**Milestone Goal:** Clean up codebase for modularity and scalability, then enable live WebSocket trading with robust order management.

**Strategy Status:** Sharpe 0.345, PnL $324, WR 50%, MaxLoss -$26 (bgt50+noLo500+conviction)

### Phase 8: Foundation & Bug Fixes

**Goal:** Fix critical portfolio keying bug and organize codebase for live trading
**Depends on:** Phase 7.1
**Requirements:** CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-04

**Success Criteria** (what must be TRUE):
1. Portfolio positions are keyed by composite (token_id, market_id, side) and track accurately across multiple markets
2. Experiment scripts are organized into experiments/archive/ and scripts/ directories, root directory contains only production code
3. Trade logic module (src/core/trade_logic.py) exists and is used by both simulation and live execution paths with zero duplication
4. websockets library is upgraded to v16.0 and all WebSocket connections use the updated API

**Research flag:** skip-research — Code fixes and refactoring with well-known patterns

**Plans:** 4 plans

Plans:
- [ ] 08-01-PLAN.md — Fix portfolio composite keying bug (TDD, CLEAN-01)
- [ ] 08-02-PLAN.md — Organize root directory experiment scripts (CLEAN-02)
- [ ] 08-03-PLAN.md — Extract shared trade logic module (CLEAN-03)
- [ ] 08-04-PLAN.md — Upgrade websockets library to v16.0 (CLEAN-04)

---

### Phase 9: Safety Mechanisms

**Goal:** Prevent catastrophic losses with fail-fast validation and runtime protection
**Depends on:** Phase 8
**Requirements:** SAFE-01, SAFE-02, SAFE-03, SAFE-04

**Success Criteria** (what must be TRUE):
1. Bot performs pre-flight checks (credentials, connectivity, wallet balance) before allowing live trading to start
2. Hard budget cap ($50/hr default, configurable) is enforced at execution layer and rejects orders that would exceed limit
3. Kill switch CLI command exists and immediately disarms live trading when invoked
4. Credential hygiene is verified (.gitignore contains keys/env files, no hardcoded secrets in code, env var validation runs on startup)

**Research flag:** skip-research — Standard safety patterns for trading bots

**Plans:** TBD

Plans:
- [ ] 09-01: TBD
- [ ] 09-02: TBD

---

### Phase 10: Order Lifecycle & WebSocket

**Goal:** Track order state from submission to fill/rejection with real-time notifications
**Depends on:** Phase 9
**Requirements:** ORD-01, ORD-02, ORD-03, ORD-04

**Success Criteria** (what must be TRUE):
1. Order lifecycle state machine tracks each order from pending → filled/rejected with timestamps
2. WebSocket user channel is subscribed and receives real-time order fill notifications
3. Retry logic with exponential backoff handles transient network failures (2-3 retries) and skips permanent API rejections
4. Structured JSON audit log records every order attempt with timestamp, token_id, side, amount, price, outcome, order_id

**Research flag:** needs-research — WebSocket user channel auth flow not fully detailed in docs, needs testing during implementation

**Plans:** TBD

Plans:
- [ ] 10-01: TBD
- [ ] 10-02: TBD

---

### Phase 11: Position Reconciliation & Monitoring

**Goal:** Continuous reconciliation and health monitoring to catch execution drift early
**Depends on:** Phase 10
**Requirements:** MON-01, MON-02, MON-04

**Success Criteria** (what must be TRUE):
1. Position reconciliation runs every 5 minutes comparing local portfolio vs exchange positions with discrepancy alerts
2. Connection health monitoring detects failures and auto-disarms trading after 3 consecutive failures
3. Daily loss limit (default $100) is tracked and triggers auto-shutdown when exceeded

**Research flag:** skip-research — Standard reconciliation patterns, CLOB API for positions query well-documented

**Plans:** TBD

Plans:
- [ ] 11-01: TBD
- [ ] 11-02: TBD

---

### Phase 12: Live Deployment & Integration

**Goal:** Enable live WebSocket order placement with gradual capital deployment and comprehensive testing
**Depends on:** Phase 11
**Requirements:** MON-03

**Success Criteria** (what must be TRUE):
1. Gradual live deployment protocol is documented and executed ($10 trial → $25 → $50 budget with dry-run validation first)
2. Live WebSocket order placement is enabled and successfully places market orders on Polymarket
3. Integration tests validate hour boundary trades, reconciliation after fills, and slippage tracking
4. All pre-flight checks, budget caps, and monitoring systems are verified working with real capital

**Research flag:** needs-research — Live testing will uncover edge cases simulation can't (network errors, API rate limits, price staleness)

**Plans:** TBD

Plans:
- [ ] 12-01: TBD

---

## Progress

**Execution Order:**
Phases execute in numeric order: 8 → 9 → 10 → 11 → 12

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Test Coverage | v1.0 | 4/4 | Complete | 2026-02-02 |
| 2. Performance Analysis | v1.0 | 4/4 | Complete | 2026-02-02 |
| 3. Dynamic Sizing | v1.0 | 4/4 | Complete | 2026-02-02 |
| 4. Advanced Sizing | v1.0 | 5/5 | Complete | 2026-02-02 |
| 5. Validation | v1.0 | 5/5 | Complete | 2026-02-02 |
| 6. Statistical Validation | v1.1 | 3/3 | Complete | 2026-02-09 |
| 7. Comparison Infrastructure | v1.1 | 3/3 | Complete | 2026-02-09 |
| 7.1. Codebase Cleanup | v1.1 | 7/7 | Complete | 2026-02-09 |
| 8. Foundation & Bug Fixes | v1.2 | 0/4 | Planned | - |
| 9. Safety Mechanisms | v1.2 | 0/TBD | Not started | - |
| 10. Order Lifecycle & WebSocket | v1.2 | 0/TBD | Not started | - |
| 11. Position Reconciliation | v1.2 | 0/TBD | Not started | - |
| 12. Live Deployment | v1.2 | 0/TBD | Not started | - |

---
*Roadmap created: 2026-02-10*
*Last updated: 2026-02-10 for v1.2 milestone*
