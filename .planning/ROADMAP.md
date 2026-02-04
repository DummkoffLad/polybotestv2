# Roadmap: Polymarket Copy Trading Bot Optimization

## Milestones

- ✅ **v1.0 Validation** - Phases 1-5 (shipped 2026-02-02)
- 🚧 **v1.1 Beat Conservative** - Phases 6-11 (in progress)

## Phases

<details>
<summary>✅ v1.0 Validation (Phases 1-5) - SHIPPED 2026-02-02</summary>

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
**Plans**: 5 plans

Plans:
- [x] 05-01-PLAN.md -- DataSplitManager (in-sample vs out-of-sample session tracking with TDD)
- [x] 05-02-PLAN.md -- SensitivitySweeper (parameter robustness testing with TDD)
- [x] 05-03-PLAN.md -- LatencySimulator (detection + execution delay modeling with TDD)
- [x] 05-04-PLAN.md -- ValidationReportGenerator (go/no-go decision logic + report formatting with TDD)
- [x] 05-05-PLAN.md -- ValidationPipeline (wire components + integration tests)

</details>

## 🚧 v1.1 Beat Conservative (In Progress)

**Milestone Goal:** Build a strategy that outperforms conservative on fresh recorded sessions

Research insight: Conservative's win on 12 sessions may be luck (only 70-80% confidence with ~100-180 trades). Must validate statistical significance before analysis, understand causal mechanisms before building new strategy, and test on held-out data before deployment.

### Phase 6: Statistical Validation
**Goal**: Confirm conservative's edge is statistically significant, not random noise
**Depends on**: Phase 5
**Requirements**: STAT-01, STAT-02, STAT-03, STAT-04
**Success Criteria** (what must be TRUE):
  1. Confidence intervals show statistical bounds on PnL and win rate for each strategy
  2. T-test results show whether conservative's advantage is significant (p < 0.05) or likely noise
  3. Sample size warnings alert when trade count is insufficient for 95% confidence
  4. Decision to proceed with analysis or gather more sessions is data-driven
  5. Session regime comparison explains why Session 1 (overnight, conservative profitable) differs from Session 2 (daytime, all lost)
**Plans**: 3 plans

Plans:
- [ ] 06-01-PLAN.md -- Statistics foundation: ConfidenceIntervalCalculator (bootstrap CIs) + SampleSizeChecker (TDD)
- [ ] 06-02-PLAN.md -- Strategy comparison: StrategyComparator with paired t-test using scipy.stats.ttest_rel (TDD)
- [ ] 06-03-PLAN.md -- Regime analysis: RegimeAnalyzer for session classification + regime comparison with Welch's t-test (TDD)

### Phase 7: Comparison Infrastructure
**Goal**: Run all strategies side-by-side with complete decision visibility
**Depends on**: Phase 6
**Requirements**: COMP-01, COMP-02, COMP-03, COMP-04, COMP-05, COMP-06
**Success Criteria** (what must be TRUE):
  1. StrategyComparator runs multiple strategies on same session with identical market data
  2. Side-by-side equity curves show visual performance comparison across all strategies
  3. Metrics table compares Sharpe ratio, win rate, profit factor, max drawdown for each strategy
  4. Decision matrix reveals which trades each strategy took vs skipped
  5. QuantStats HTML tear sheets provide deep performance analysis per strategy
**Plans**: TBD

Plans:
- [ ] 07-01: TBD
- [ ] 07-02: TBD
- [ ] 07-03: TBD

### Phase 8: Failure Analysis
**Goal**: Understand WHY strategies differ by categorizing each divergent trade decision
**Depends on**: Phase 7
**Requirements**: FAIL-01, FAIL-02, FAIL-03, FAIL-04, FAIL-05, FAIL-06
**Success Criteria** (what must be TRUE):
  1. Every trade has a failure categorization (timing, sizing, filter, capital, correct_skip)
  2. Counterfactual PnL shows "what if strategy A followed strategy B on this trade"
  3. FailureModeAnalyzer identifies recurring patterns and ranks by impact
  4. Sizing impact analysis isolates whether sizing or selection drives performance gap
  5. Causal hypotheses are documented before building new strategy
  6. Peak profit analysis shows max unrealized profit per trade — identifies if we held winners that became losers
**Plans**: TBD

Plans:
- [ ] 08-01: TBD
- [ ] 08-02: TBD
- [ ] 08-03: TBD

### Phase 9: Visual Reporting
**Goal**: Make comparison insights accessible through interactive visual reports
**Depends on**: Phase 8
**Requirements**: VISU-01, VISU-02, VISU-03
**Success Criteria** (what must be TRUE):
  1. Decision timeline shows buy/sell/skip markers across strategies over session time
  2. PnL attribution waterfall visualizes cumulative divergence trade-by-trade
  3. Interactive Plotly charts allow drill-down to individual trade details
  4. Reports export to HTML for sharing and offline review
**Plans**: TBD

Plans:
- [ ] 09-01: TBD
- [ ] 09-02: TBD

### Phase 10: Validation Protocol
**Goal**: Rigorous out-of-sample testing prevents overfitting to 12-session analysis data
**Depends on**: Phase 8
**Requirements**: VALD-01, VALD-02, VALD-03, VALD-04
**Success Criteria** (what must be TRUE):
  1. Train/test split reserves 30% of sessions for validation (never used in analysis)
  2. Out-of-sample requirement enforces that new strategy must beat conservative on held-out data
  3. Execution cost modeling includes realistic spread and slippage estimates
  4. Walk-forward validation tests strategy on rolling time windows
**Plans**: TBD

Plans:
- [ ] 10-01: TBD
- [ ] 10-02: TBD

### Phase 11: Strategy Development & Monitoring
**Goal**: Build improved strategy from validated insights and monitor for degradation
**Depends on**: Phase 9 (reporting), Phase 10 (validation)
**Requirements**: STRT-01, STRT-02, MNTR-01, MNTR-02, MNTR-03
**Success Criteria** (what must be TRUE):
  1. New strategy implements causal mechanism from failure analysis (not copied parameters)
  2. Strategy beats conservative on out-of-sample validation sessions
  3. Rolling performance windows track 6-session and 12-session recent metrics
  4. Statistical alerts trigger on 2-sigma performance degradation
  5. Regime detection identifies when market conditions shift significantly
**Plans**: TBD

Plans:
- [ ] 11-01: TBD
- [ ] 11-02: TBD
- [ ] 11-03: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 6 → 7 → 8 → 9 → 10 → 11

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Test Coverage | v1.0 | 4/4 | Complete | 2026-01-31 |
| 2. Performance Analysis | v1.0 | 3/3 | Complete | 2026-01-31 |
| 3. Dynamic Sizing | v1.0 | 4/4 | Complete | 2026-02-02 |
| 4. Advanced Sizing | v1.0 | 5/5 | Complete | 2026-02-02 |
| 5. Validation | v1.0 | 5/5 | Complete | 2026-02-02 |
| 6. Statistical Validation | v1.1 | 0/3 | Planned | - |
| 7. Comparison Infrastructure | v1.1 | 0/TBD | Not started | - |
| 8. Failure Analysis | v1.1 | 0/TBD | Not started | - |
| 9. Visual Reporting | v1.1 | 0/TBD | Not started | - |
| 10. Validation Protocol | v1.1 | 0/TBD | Not started | - |
| 11. Strategy Development & Monitoring | v1.1 | 0/TBD | Not started | - |

---
*Roadmap created: 2026-01-30*
*Last updated: 2026-02-04 after Phase 6 planning*
