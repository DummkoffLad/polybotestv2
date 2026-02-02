# Requirements: Polymarket Copy Trading Bot

**Defined:** 2026-01-30
**Core Value:** Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital

## v1 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Testing

- [x] **TEST-01**: Unit test suite covering all 8 strategy implementations with known input/output pairs
- [x] **TEST-02**: Unit tests for risk cap enforcement (per-market, per-side, global exposure)
- [x] **TEST-03**: Unit tests for portfolio math (cost basis calculation, realized/unrealized PnL)

### Analysis & Attribution

- [x] **ANAL-01**: Per-trade PnL attribution linking each trade to its final outcome (win/loss/open)
- [x] **ANAL-02**: Drawdown tracking with equity curve over session and max drawdown from peak
- [x] **ANAL-03**: Profit leakage analysis comparing our sizing/timing vs leader's actual results

### Small-Budget Sizing

- [x] **SIZE-01**: Dynamic position sizing — caps as % of current capital, adapts to growth/drawdown
- [x] **SIZE-02**: Selective following — filter for high-confidence trades, skip low-edge setups
- [x] **SIZE-03**: Kelly criterion position sizing proportional to estimated edge and bankroll
- [x] **SIZE-04**: Capital efficiency scoring to prioritize best risk-adjusted return per dollar

### Robustness

- [ ] **RBST-01**: Out-of-sample testing with train/test split for optimizer validation
- [ ] **RBST-02**: Sensitivity analysis showing how results change with small parameter tweaks
- [ ] **RBST-03**: Latency simulation modeling real API delays in replay

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Simulation Enhancements

- **SIM-01**: Slippage consistency — ensure slippage is applied uniformly across all replay paths
- **SIM-02**: Order book depth modeling — scale slippage with order size vs liquidity
- **SIM-03**: Cross-market correlation tracking for position risk

### Advanced Optimization

- **OPT-01**: Walk-forward optimization — optimize on past period, test on next, repeat
- **OPT-02**: Monte Carlo simulation — randomize trade order to test robustness

### Production Readiness

- **PROD-01**: End-to-end PnL verification against known live results
- **PROD-02**: Live execution testing with Polymarket testnet/sandbox
- **PROD-03**: Circuit breaker for stuck orders or API failures

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Fee modeling | Polymarket 1h crypto markets do not charge fees — confirmed by user |
| Live trading deployment | Focus is simulation quality first |
| New data sources or leader discovery | Leader already identified and proven |
| UI or dashboard | CLI output sufficient |
| ML-based position sizing | Insufficient data (need 1000+ trades), will overfit |
| Multi-leader tracking | Single leader focus |
| Multi-timeframe analysis | 1h markets resolve in 1h, no timeframes |
| Portfolio optimization (quadratic) | $100 budget can't diversify meaningfully |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| TEST-01 | Phase 1 | Complete |
| TEST-02 | Phase 1 | Complete |
| TEST-03 | Phase 1 | Complete |
| ANAL-01 | Phase 2 | Complete |
| ANAL-02 | Phase 2 | Complete |
| ANAL-03 | Phase 2 | Complete |
| SIZE-01 | Phase 3 | Complete |
| SIZE-02 | Phase 3 | Complete |
| SIZE-03 | Phase 4 | Complete |
| SIZE-04 | Phase 4 | Complete |
| RBST-01 | Phase 5 | Pending |
| RBST-02 | Phase 5 | Pending |
| RBST-03 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0

---
*Requirements defined: 2026-01-30*
*Last updated: 2026-02-02 after Phase 4 completion*
