# Requirements: Polymarket Copy Trading Bot v1.2

**Defined:** 2026-02-10
**Core Value:** Reproduce the leader's profitable trades at a smaller scale using conviction-based filtering to maximize risk-adjusted returns — focus capital on trades where the leader has proven high confidence.

## v1.2 Requirements

### Codebase Cleanup

- [x] **CLEAN-01**: Portfolio positions keyed by composite (token_id, market_id, side) instead of token_id alone
- [x] **CLEAN-02**: Experiment scripts organized into experiments/archive/ and scripts/ directories
- [x] **CLEAN-03**: Shared trade logic module (src/core/trade_logic.py) used by both simulation and live execution paths
- [x] **CLEAN-04**: websockets library upgraded from 12.0 to 16.0

### Safety

- [ ] **SAFE-01**: Pre-flight checks validate credentials, connectivity, and wallet balance before live trading starts
- [ ] **SAFE-02**: Hard budget cap enforced at execution layer ($50/hr default, configurable via config)
- [ ] **SAFE-03**: Kill switch that immediately disarms live trading via CLI command
- [ ] **SAFE-04**: Credential hygiene — .gitignore verification for keys, env var validation, no hardcoded secrets

### Order Management

- [ ] **ORD-01**: Order lifecycle tracking (pending → filled/rejected) with state machine per order
- [ ] **ORD-02**: WebSocket user channel subscription for real-time order fill notifications
- [ ] **ORD-03**: Retry logic with exponential backoff for transient network failures (2-3 retries, skip API rejections)
- [ ] **ORD-04**: Structured JSON audit log per order (timestamp, token_id, side, amount, price, outcome, order_id)

### Monitoring & Deployment

- [ ] **MON-01**: Position reconciliation comparing local portfolio vs exchange positions every 5 minutes
- [ ] **MON-02**: Connection health monitoring with auto-disarm after 3 consecutive failures
- [ ] **MON-03**: Gradual live deployment protocol ($10 trial → $25 → $50 budget with dry-run validation first)
- [ ] **MON-04**: Daily loss limit with auto-shutdown when total daily loss exceeds configurable threshold (default $100)

## Future Requirements

### Performance Optimization (v1.3+)

- **PERF-01**: WebSocket price feeds replacing HTTP polling for lower latency
- **PERF-02**: Parallel order submission for burst scenarios
- **PERF-03**: Dynamic spread-based pricing (adjust based on orderbook depth)

### Observability (v1.3+)

- **OBS-01**: Real-time dashboard or web UI for monitoring
- **OBS-02**: External alerting (email/Telegram/Discord notifications)
- **OBS-03**: Historical performance reporting with live vs backtest comparison

## Out of Scope

| Feature | Reason |
|---------|--------|
| Limit order management (GTC/GTD) | FOK market orders sufficient for copy trading speed |
| Position averaging / re-entry | Adds complexity with no benefit at $50/hr budget |
| Order book depth modeling | Thin liquidity, $1-5 orders don't move the book |
| Smart order routing | Single venue (Polymarket CLOB) |
| Post-only orders for maker rebates | Copy trading requires speed, not fee optimization |
| ML-based strategy optimization | Insufficient data (~200 trades), will overfit |
| Multi-leader tracking | Single leader focus for v1.2 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CLEAN-01 | Phase 8 | Complete |
| CLEAN-02 | Phase 8 | Complete |
| CLEAN-03 | Phase 8 | Complete |
| CLEAN-04 | Phase 8 | Complete |
| SAFE-01 | Phase 9 | Pending |
| SAFE-02 | Phase 9 | Pending |
| SAFE-03 | Phase 9 | Pending |
| SAFE-04 | Phase 9 | Pending |
| ORD-01 | Phase 10 | Pending |
| ORD-02 | Phase 10 | Pending |
| ORD-03 | Phase 10 | Pending |
| ORD-04 | Phase 10 | Pending |
| MON-01 | Phase 11 | Pending |
| MON-02 | Phase 11 | Pending |
| MON-04 | Phase 11 | Pending |
| MON-03 | Phase 12 | Pending |

**Coverage:**
- v1.2 requirements: 16 total
- Mapped to phases: 16 ✓
- Unmapped: 0 ✓

---
*Requirements defined: 2026-02-10*
*Last updated: 2026-02-11 — CLEAN-01..04 complete (Phase 8)*
