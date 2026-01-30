# Research Summary: Copy Trading Bot Features

**Research Type:** Features dimension for Polymarket copy trading bot
**Date:** 2026-01-30
**Confidence:** MEDIUM (codebase HIGH, external verification unavailable)

---

## Executive Summary

Researched feature landscape for copy trading bot simulation and small-budget ($100) optimization. Analysis based on codebase inspection and domain knowledge (web search unavailable).

**Key Finding:** The bot has strong table-stakes features (simulation realism, risk management, strategy testing) but is missing critical profitability features for small accounts: fee modeling, per-trade PnL attribution, and dynamic position sizing.

**Confidence Constraint:** Web search and Context7 unavailable. Relied on codebase analysis (HIGH confidence) + training knowledge about copy trading/backtesting (MEDIUM confidence, Jan 2025 cutoff).

---

## Key Findings

### Table Stakes (All Present ✓)

The bot has excellent foundations:
- **Simulation realism**: Bid/ask spreads, limit order fill simulation, historical prices, duplicate filtering, market resolution
- **Risk management**: Per-market/side/global caps, minimum order constraints, position validation
- **Strategy testing**: Session recording/replay, multi-strategy support, grid search, PnL breakdown

### Critical Missing Features (High Impact)

2. **Per-trade PnL attribution** (Complexity: MEDIUM, Impact: HIGH)
   - Need to identify which trades contributed profit/loss
   - Critical for understanding where strategy deviates from leader
   - Exists partially (trade list) but not linked to outcomes

3. **Drawdown tracking** (Complexity: LOW, Impact: MEDIUM)
   - Must know maximum capital loss from peak
   - Critical for risk assessment before live trading
   - Currently missing equity curve

### Small-Budget Differentiators (Competitive Advantage)

1. **Dynamic position sizing** — Scale with current capital, not starting capital
2. **Selective following** — Filter for high-confidence trades, conserve capital
3. **Capital efficiency scoring** — Prioritize best risk-adjusted trades

### Anti-Features (Do NOT Build)

- Over-optimization to single session (use multiple sessions)
- Fabricating missing price data (drop events instead)
- ML-based sizing (insufficient data, will overfit)
- Look-ahead bias (already avoided correctly)

---

## Implications for Requirements

### Must Fix Before Validation

1. **Add fee modeling** — PnL numbers are currently wrong without fees
   - Deduct 0.5-1% from all trades
   - Update `Portfolio` buy/sell methods
   - Affects all strategy comparisons

2. **Build drawdown tracker** — Need max loss metric
   - Track equity curve through session
   - Report max % drawdown
   - Critical for risk assessment

3. **Link trades to outcomes** — Per-trade attribution
   - Extend `ExecutedTrade` with final PnL
   - Compare our trades vs leader trades with outcomes
   - Identify profitable deviations

### High-Priority Improvements

1. **Dynamic caps** — Current caps are % of starting capital
   - Should be % of current capital
   - Allows compounding wins, protects after losses

2. **Selective following** — Skip low-edge trades
   - Filter trades with high spread cost
   - Prioritize trades when capital is constrained

### Defer

- Order book depth modeling (need more data)
- Walk-forward optimization (need multiple sessions)
- Monte Carlo simulation (nice-to-have)
- ML approaches (insufficient data)

---

## Small-Budget Context

At ~$100 capital, certain features have AMPLIFIED importance:

1. **Transaction costs** — Fees = 0.5-1% per trade. Over 100 trades = 5% of capital
2. **Minimum constraints** — $1 min order = 1% of capital. Limits precision
3. **Selective following** — Can't copy everything. Forces prioritization (could be advantage)
4. **Capital efficiency** — Max 3-4 open positions. Each must count

Conversely, some features have REDUCED importance:
- Order book depth (small orders don't move market)
- Cross-market correlation (few positions)

---

## Feature Dependencies

```
Foundation (Exists):
├─ Historical prices ✓
├─ Portfolio tracking ✓
├─ Session replay ✓
└─ Order constraints ✓

Critical Additions:
├─ Fee modeling (needed for accurate PnL)
├─ Drawdown tracking (needed for risk assessment)
└─ Per-trade attribution (needed to find profit leakage)

Optimization (After validation):
├─ Dynamic sizing
├─ Selective following
└─ Capital efficiency scoring
```

---

## Confidence Assessment

| Area | Confidence | Source |
|------|------------|--------|
| Existing features | HIGH | Direct codebase analysis |
| Table stakes | HIGH | Verified implementation exists |
| Missing features impact | MEDIUM | Domain knowledge (training), not web-verified |
| Small-budget priorities | MEDIUM | Training knowledge, matches PROJECT.md context |
| Fee percentages | LOW | Assumed 0.5-1% (training), need to verify Polymarket docs |
| Anti-features | MEDIUM | Common pitfalls from training knowledge |

---

## Gaps to Address

### Verification Needed

1. **Polymarket fee structure** — Confirm actual maker/taker fees from official docs
2. **Order book depth** — Unknown if $5 orders are "large" for typical markets
3. **Leader capital estimate** — Currently assumes $800, marked as "estimated"
4. **Limit order fill rates** — 8s TTL in simulator, but is that realistic?

### Data Collection Opportunities

- Capture order book snapshots during session recording (for slippage modeling)
- Analyze leader's total positions to estimate capital better
- Compare replay with/without limit orders to validate fill assumptions

---

## Next Steps for Orchestrator

1. **Immediate**: Add fee modeling (HIGH impact, LOW complexity)
2. **Immediate**: Add drawdown tracking (risk assessment critical)
3. **Next**: Build per-trade attribution (find profit leakage)
4. **Then**: Consider dynamic sizing and selective following

All features documented in `.planning/research/FEATURES.md` with:
- Table stakes vs differentiators vs anti-features
- Complexity and status for each
- Dependencies and implementation order
- Small-budget specific considerations

---

## Files Created

| File | Purpose |
|------|---------|
| `.planning/research/FEATURES.md` | Complete feature landscape with categorization, dependencies, MVP recommendations |
| `.planning/research/RESEARCH_SUMMARY.md` | This summary for orchestrator |

---

*Research completed: 2026-01-30*
*Ready for requirements definition*
