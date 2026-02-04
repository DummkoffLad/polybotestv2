# Requirements: Polymarket Copy Trading Bot

**Defined:** 2026-02-03
**Core Value:** Build a strategy that beats conservative on fresh recorded sessions

## v1.1 Requirements

Requirements for milestone v1.1: Beat Conservative. Each maps to roadmap phases.

### Statistical Validation

- [ ] **STAT-01**: Confidence interval calculator reports PnL and win rate bounds for each strategy
- [ ] **STAT-02**: Statistical significance test (t-test) compares strategies and reports p-values
- [ ] **STAT-03**: Sample size adequacy check warns if insufficient trades for 95% confidence

### Comparison Infrastructure

- [ ] **COMP-01**: Side-by-side equity curves visualize all strategies on same chart
- [ ] **COMP-02**: Metrics comparison table shows Sharpe, win rate, profit factor, max drawdown per strategy
- [ ] **COMP-03**: Trade-by-trade listing is sortable and filterable by outcome, strategy, market
- [ ] **COMP-04**: StrategyComparator runs all strategies on same session and aggregates results
- [ ] **COMP-05**: QuantStats HTML tear sheets are generated for each strategy
- [ ] **COMP-06**: Decision matrix shows event × strategy with buy/sell/skip decisions

### Failure Analysis

- [ ] **FAIL-01**: Enhanced per-trade attribution includes failure categorization field
- [ ] **FAIL-02**: Failure taxonomy categorizes trades as timing, sizing, filter, capital, or correct_skip
- [ ] **FAIL-03**: Counterfactual PnL calculation shows "what if we followed" for skipped trades
- [ ] **FAIL-04**: FailureModeAnalyzer detects patterns and suggests fixes
- [ ] **FAIL-05**: Sizing impact analysis compares "what if A used B's sizing" counterfactuals

### Visual Reporting

- [ ] **VISU-01**: Decision timeline chart shows strategies × time with buy/sell/skip markers
- [ ] **VISU-02**: PnL attribution waterfall shows cumulative diff by trade
- [ ] **VISU-03**: Interactive Plotly charts support drill-down to individual trades

### Validation Protocol

- [ ] **VALD-01**: Train/test session split methodology reserves 30% sessions for validation
- [ ] **VALD-02**: Out-of-sample testing requirement enforces validation before deployment
- [ ] **VALD-03**: Execution cost modeling includes spread and slippage estimates
- [ ] **VALD-04**: Walk-forward validation tests strategy on rolling time windows

### New Strategy

- [ ] **STRT-01**: New strategy implementation based on validated causal insights from analysis
- [ ] **STRT-02**: Strategy design is mechanism-focused (principles, not copied conservative configs)

### Monitoring

- [ ] **MNTR-01**: Rolling performance windows track 6-session and 12-session metrics
- [ ] **MNTR-02**: Statistical alerts trigger on 2σ performance degradation
- [ ] **MNTR-03**: Regime detection identifies spread, volume, or market type shifts

## v1.0 Requirements (Previous Milestone - Complete)

### Testing (Complete)

- [x] **TEST-01**: Unit test suite covering all 8 strategy implementations with known input/output pairs
- [x] **TEST-02**: Unit tests for risk cap enforcement (per-market, per-side, global exposure)
- [x] **TEST-03**: Unit tests for portfolio math (cost basis calculation, realized/unrealized PnL)

### Analysis & Attribution (Complete)

- [x] **ANAL-01**: Per-trade PnL attribution linking each trade to its final outcome (win/loss/open)
- [x] **ANAL-02**: Drawdown tracking with equity curve over session and max drawdown from peak
- [x] **ANAL-03**: Profit leakage analysis comparing our sizing/timing vs leader's actual results

### Small-Budget Sizing (Complete)

- [x] **SIZE-01**: Dynamic position sizing — caps as % of current capital, adapts to growth/drawdown
- [x] **SIZE-02**: Selective following — filter for high-confidence trades, skip low-edge setups
- [x] **SIZE-03**: Kelly criterion position sizing proportional to estimated edge and bankroll
- [x] **SIZE-04**: Capital efficiency scoring to prioritize best risk-adjusted return per dollar

### Robustness (Complete)

- [x] **RBST-01**: Out-of-sample testing with train/test split for optimizer validation
- [x] **RBST-02**: Sensitivity analysis showing how results change with small parameter tweaks
- [x] **RBST-03**: Latency simulation modeling real API delays in replay

## v1.2 Requirements (Future)

Deferred to future milestone. Tracked but not in current roadmap.

### Pattern Discovery

- **PTRN-01**: Market context recording captures price levels, timing, volatility during sessions
- **PTRN-02**: Pattern search tool finds correlations between market state and profitable trades
- **PTRN-03**: Pattern validation tests discovered patterns on out-of-sample data
- **PTRN-04**: Custom algorithm development based on validated patterns

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| ML-based strategy optimization | Insufficient data (~200 trades), will overfit |
| Real-time dashboard | CLI output sufficient, adds complexity |
| Multi-leader tracking | Single leader focus for this milestone |
| Live trading deployment | Focus is building/validating strategy first |
| Order book depth modeling | Polymarket 1h markets have thin books, won't improve |
| Monte Carlo confidence intervals | Complex, lower ROI vs bootstrap CIs already in validation |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STAT-01 | Phase 6 | Pending |
| STAT-02 | Phase 6 | Pending |
| STAT-03 | Phase 6 | Pending |
| COMP-01 | Phase 7 | Pending |
| COMP-02 | Phase 7 | Pending |
| COMP-03 | Phase 7 | Pending |
| COMP-04 | Phase 7 | Pending |
| COMP-05 | Phase 7 | Pending |
| COMP-06 | Phase 7 | Pending |
| FAIL-01 | Phase 8 | Pending |
| FAIL-02 | Phase 8 | Pending |
| FAIL-03 | Phase 8 | Pending |
| FAIL-04 | Phase 8 | Pending |
| FAIL-05 | Phase 8 | Pending |
| VISU-01 | Phase 9 | Pending |
| VISU-02 | Phase 9 | Pending |
| VISU-03 | Phase 9 | Pending |
| VALD-01 | Phase 10 | Pending |
| VALD-02 | Phase 10 | Pending |
| VALD-03 | Phase 10 | Pending |
| VALD-04 | Phase 10 | Pending |
| STRT-01 | Phase 11 | Pending |
| STRT-02 | Phase 11 | Pending |
| MNTR-01 | Phase 11 | Pending |
| MNTR-02 | Phase 11 | Pending |
| MNTR-03 | Phase 11 | Pending |

**Coverage:**
- v1.1 requirements: 26 total
- Mapped to phases: 26 (100% coverage)
- Unmapped: 0

**Phase Distribution:**
- Phase 6 (Statistical Validation): 3 requirements
- Phase 7 (Comparison Infrastructure): 6 requirements
- Phase 8 (Failure Analysis): 5 requirements
- Phase 9 (Visual Reporting): 3 requirements
- Phase 10 (Validation Protocol): 4 requirements
- Phase 11 (Strategy + Monitoring): 5 requirements

---
*Requirements defined: 2026-02-03*
*Last updated: 2026-02-03 after v1.1 roadmap creation*
