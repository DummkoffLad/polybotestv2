# Phase 4: Advanced Sizing — Context & Decisions

**Phase Goal**: Each dollar is allocated to maximize risk-adjusted returns
**Depends on**: Phase 3 (Dynamic Sizing)
**Requirements**: SIZE-03, SIZE-04

---

## 1. Kelly Criterion Sizing

### Decisions

- **Fraction**: Half Kelly (0.5x optimal). Industry-standard balance of growth vs drawdown protection.
- **Edge estimation**: Per-token performance — track each leader's win rate and win/loss ratio **per specific token/pair**, not just overall leader performance.
- **Lookback window**: Last 50 trades per leader-token pair. Medium window balancing stability with responsiveness.
- **Cold start behavior**: Fall back to Phase 3 dynamic sizing (no Kelly adjustment) until a leader-token pair has accumulated sufficient trade history for reliable edge estimation. No trades are skipped — the system just doesn't apply Kelly multipliers until it has data.

### Constraints

- Kelly fraction is fixed at 0.5 for v1 (not user-configurable yet)
- Edge = f(win_rate, avg_win/avg_loss) per leader-token pair
- Minimum sample size before Kelly activates: to be determined during implementation (likely 10-20 trades)

---

## 2. Trade Prioritization

### Decisions

- **Ranking criteria**: Highest Kelly edge wins. When multiple trades compete for limited capital, rank by estimated expected value (Kelly edge score).
- **Correlation handling**: Soft diversification preference. Correlated trades receive a slight deprioritization penalty, but are NOT blocked if their edge is strong enough. Diversification is a tiebreaker, not a veto.
- **Rebalancing**: Conservative — only exit an existing position to fund a new trade when the new opportunity has a **significantly** higher edge. No continuous portfolio rebalancing. Threshold for "significant" gap to be determined during implementation.
- **Queue behavior**: Deprioritized trades stay in a queue and can be entered later **if** capital frees up AND the entry price remains within acceptable spread of the leader's original entry. If price has moved too far, the opportunity is discarded.

### Constraints

- Prioritization only matters when capital is insufficient for all available trades
- Correlation is measured simply (same token = correlated, different tokens = uncorrelated)
- Queue timeout: trades expire when price moves beyond spread threshold, not on a time basis

---

## 3. Confidence Signals

### Decisions

- **Signal category**: Leader conviction signals only (for v1). Specifically:
  - Leader's position size relative to their portfolio (bigger = more conviction)
  - Whether the leader added to / scaled into the position
  - Speed of entry (deliberate vs impulsive)
- **Adjustment range**: 0.25x to 2x Kelly. Wide range — high-conviction trades get significantly more capital, low-conviction trades get minimal exposure.
- **Cutoff behavior**: Soft minimum floor with override. A minimum confidence threshold exists (below which trades are skipped), but leaders with exceptional track records can override the floor.
- **Signal combining**: Weighted blend of sub-signals within leader conviction. Weights determined by predictive value during implementation — position size relative to portfolio is likely the strongest signal.

### Constraints

- Market regime signals and token-specific signals are explicitly OUT of scope for Phase 4
- Confidence multiplier is applied AFTER Kelly sizing (Kelly * confidence_multiplier)
- Override logic uses existing leader performance data from the edge estimation system

---

## 4. Success Metrics & Validation

### Decisions

- **Primary metric**: Total PnL. The bottom line — did the system make more money than fixed sizing?
- **Secondary metrics** (all tracked):
  - Capital utilization % (how efficiently capital was deployed)
  - Profit factor (gross profit / gross loss)
  - Largest single trade loss (Kelly should prevent outsized losses)
- **Improvement threshold**: Statistically significant improvement. Not "it looks better" — run enough replays to prove the improvement is unlikely due to chance.
- **Replay count**: Use all available recorded sessions to maximize statistical power. The number needed depends on data availability.

### Constraints

- Comparison baseline is Phase 3 dynamic sizing with fixed quality filtering (no Kelly, no confidence)
- Statistical significance method: to be determined during research (likely paired t-test or bootstrap)
- Results must include confidence intervals, not just point estimates

---

## Deferred Ideas

*(Captured during discussion but out of scope for Phase 4)*

- Market regime signals for confidence adjustment (consider for Phase 5 or future milestone)
- Token-specific signals (volatility, liquidity, momentum) for confidence
- Full Kelly with configurable fraction parameter
- Continuous portfolio rebalancing (active position management)
- Time-based queue expiry for deprioritized trades

---

## Phase Boundary

Phase 4 delivers:
1. Kelly criterion engine with per-token edge estimation
2. Trade prioritization system with soft diversification
3. Leader conviction-based confidence multiplier
4. Replay validation proving statistically significant PnL improvement

Phase 4 does NOT touch:
- Market regime detection
- Token-specific analytics beyond what's needed for edge estimation
- Strategy selection or switching logic
- Live execution changes (Phase 4 is sizing logic + replay validation)

---
*Context created: 2026-02-02*
*Discussion areas: Kelly aggressiveness, Trade prioritization, Confidence signals, Success metrics*
