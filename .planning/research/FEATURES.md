# Feature Landscape: Copy Trading Bot for Small Accounts

**Domain:** Copy trading bot for prediction markets (Polymarket)
**Researched:** 2026-01-30
**Confidence:** MEDIUM (based on codebase analysis + domain knowledge from training; web verification unavailable)

## Table Stakes

Features users expect from any copy trading simulation. Missing = results are not trustworthy.

### Simulation Realism

| Feature | Why Expected | Complexity | Status | Notes |
|---------|--------------|------------|--------|-------|
| **Bid/ask spread modeling** | Market orders pay ask (buy) or receive bid (sell). Ignoring spread = inflated PnL | Low | ✓ Exists | `PriceSnapshot` tracks bid/ask, strategies use correct execution price |
| **Slippage simulation** | Real orders move market. Especially critical for small-budget bots in thin markets | Medium | Partial | Config has `slippage_cost_pct` but unclear if applied consistently |
| **Order fill assumptions** | Can't assume instant fills at desired price. Limit orders may not fill | Medium | ✓ Exists | `limit_order_sim.py` models TTL and realistic fill conditions |
| **Position tracking with cost basis** | Must track avg entry price and P&L accurately | Low | ✓ Exists | `Portfolio` class tracks cost basis, realized/unrealized PnL |
| **Historical price accuracy** | Replay must use actual historical prices, not fabricated | High | ✓ Exists | `SessionReplayer` uses `price_snapshot` events, drops events without real prices |
| **Duplicate trade filtering** | Blockchain events can emit duplicates. Must dedupe or PnL is wrong | Medium | ✓ Exists | Content-based deduplication in `SessionReplayer._make_dedup_key()` |
| **Market resolution simulation** | Binary markets resolve to 0 or 1. Simulation must model winning/losing positions | Medium | ✓ Exists | `_calculate_resolved_pnl()` simulates resolution based on final prices |

### Risk Management

| Feature | Why Expected | Complexity | Status | Notes |
|---------|--------------|------------|--------|-------|
| **Per-market caps** | Prevent over-concentration in single market | Low | ✓ Exists | Config: `per_market_cap_pct` |
| **Per-side caps** | Prevent unbalanced exposure (too much UP or DOWN) | Low | ✓ Exists | Config: `per_side_pct` |
| **Global exposure cap** | Never deploy more than total capital allows | Low | ✓ Exists | Config: `global_exposure_pct`, tracked in `Portfolio.get_total_deployed()` |
| **Minimum order constraints** | Polymarket requires min $1 market / 5 shares limit | Low | ✓ Exists | `MIN_MARKET_ORDER_DOLLARS`, `MIN_LIMIT_ORDER_SHARES` in `base.py` |
| **Cash reserve** | Keep buffer for opportunity or to cover losses | Low | ✓ Exists | Config: `cash_reserve_pct` |
| **Position validation** | Never sell more shares than owned | Low | ✓ Exists | `Portfolio.apply_sell()` clamps to available shares |

### Strategy Testing

| Feature | Why Expected | Complexity | Status | Notes |
|---------|--------------|------------|--------|-------|
| **Session recording** | Capture leader trades + market conditions for replay | Medium | ✓ Exists | `SessionRecorder` writes JSONL with trades + price snapshots |
| **Deterministic replay** | Same session + same strategy = same result every time | Medium | ✓ Exists | `SessionReplayer` processes events chronologically |
| **Multiple strategy support** | Test different approaches on same data | Low | ✓ Exists | Strategy registration system, 7 strategies implemented |
| **Grid search optimization** | Sweep parameter ranges to find best config | Medium | ✓ Exists | `SimpleOptimizer` with configurable param grids |
| **PnL breakdown** | Separate realized vs unrealized, buy vs sell | Low | ✓ Exists | `ReplayResult` tracks both dimensions |

---

## Differentiators

Features that set high-quality copy trading bots apart. Not expected, but provide competitive advantage.

### Small-Budget Optimization

| Feature | Value Proposition | Complexity | Status | Notes |
|---------|-------------------|------------|--------|-------|
| **Dynamic position sizing** | Scale positions based on current capital, not static caps | Medium | Missing | Current caps are % of starting capital, don't adapt to growth/drawdown |
| **Fractional position entry** | With $100 budget, may need to enter position over multiple trades | High | Missing | Current mirror strategy tries to match leader immediately |
| **Capital efficiency scoring** | Prioritize trades with best risk-adjusted return per dollar | High | Missing | All leader trades treated equally regardless of edge |
| **Selective following** | Don't copy EVERY trade — filter for high-confidence setups | Medium | Partial | Some strategies skip trades, but no explicit confidence scoring |
| **Kelly criterion sizing** | Size positions proportional to edge and bankroll | High | Missing | Fixed k_factor doesn't account for win rate or edge size |

### Simulation Realism (Advanced)

| Feature | Value Proposition | Complexity | Status | Notes |
|---------|-------------------|------------|--------|-------|
| **Order book depth modeling** | Large orders move price more than small orders | High | Missing | Slippage is fixed %, doesn't scale with order size vs liquidity |
| **Time-to-fill simulation** | Markets move while limit orders wait. Model opportunity cost | Medium | ✓ Exists | `LimitOrderSimulator` with TTL and price-touch detection |
| **Cross-market correlation** | Positions in related markets affect risk profile | High | Missing | Each market treated independently |
| **Latency simulation** | Real bot has API delays, doesn't react instantly | Low | Missing | Replay assumes instant response to leader trades |
| **Fee modeling** | Polymarket charges maker/taker fees | Low | Missing | No fee deduction in PnL calculations |

### Performance Analysis

| Feature | Value Proposition | Complexity | Status | Notes |
|---------|-------------------|------------|--------|-------|
| **Follow quality metrics** | Measure how closely we track leader positions | Medium | ✓ Exists | `FollowMetricsTracker` with correlation, reaction time, same-sign % |
| **Profit attribution** | Which trades contributed most to PnL? Where did we deviate from leader? | Medium | Partial | Trade list exists but no per-trade PnL attribution |
| **Drawdown analysis** | Identify maximum capital loss from peak. Critical for risk assessment | Medium | Missing | No equity curve or drawdown tracking |
| **Trade clustering analysis** | Leader often bursts multiple trades quickly. Are we capturing all? | Medium | Partial | Follow metrics track this, but no explicit burst detection |
| **Market condition correlation** | Does strategy perform differently in trending vs ranging markets? | High | Missing | No market regime classification |

### Robustness

| Feature | Value Proposition | Complexity | Status | Notes |
|---------|-------------------|------------|--------|-------|
| **Out-of-sample testing** | Validate on data not used for optimization | Low | Missing | Optimizer runs on single session, no train/test split |
| **Walk-forward optimization** | Optimize on past period, test on next period, repeat | High | Missing | Grid search is static, not time-aware |
| **Monte Carlo simulation** | Randomize trade order to see if results are robust | Medium | Missing | Fixed replay order only |
| **Sensitivity analysis** | How much do results change with small parameter tweaks? | Medium | Partial | Grid search shows this implicitly, but no explicit sensitivity report |

---

## Anti-Features

Features to explicitly NOT build. Common mistakes in copy trading bots.

### Over-Optimization

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Curve-fitting to single session** | Finding parameters that work perfectly on one session but fail on new data | Use multiple sessions for validation. If a strategy works on Session A but fails on B, it's not robust |
| **Too many parameters** | 10+ tunable knobs = guaranteed overfitting | Keep strategies simple. Mirror strategy has ~8 params, that's already at the limit |
| **Micro-optimizing unrealistic precision** | Optimizing slippage to 1.237% when real slippage varies 0.5-3% | Use ranges, not point estimates. If optimal is 1.5% slippage, verify 1.0-2.0% all work |

### False Realism

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Fabricating missing data** | Making up bid/ask prices when not recorded | Drop events without real prices. Better to have fewer events than fake data |
| **Assuming perfect fills** | "I placed a limit order at X, so I got filled at X" | Simulate fill probability based on price movement and TTL (`LimitOrderSimulator` does this correctly) |
| **Ignoring market impact** | $5 order in $500 liquidity pool moves price differently than in $50k pool | Model slippage as function of order size. At minimum, use higher slippage % for large-relative-to-liquidity orders |

### Premature Complexity

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **ML-based position sizing** | Need 1000+ trades to train model, will overfit with less | Use simple rules (Kelly, fixed %, dynamic caps). ML needs more data than available |
| **Multi-timeframe analysis** | Polymarket hourly markets resolve in 1 hour. No "timeframes" | Focus on single-market, single-resolution mechanics |
| **Portfolio optimization** | Quadratic programming to find optimal position mix | At $100 budget, you can't diversify much anyway. Just copy leader selectively |

### Simulation Shortcuts

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Look-ahead bias** | Using future price data in strategy logic | Ensure `on_event()` only sees data available at trade time. `SessionReplayer` handles this correctly |
| **Survivorship bias** | Only testing on sessions where leader made money | Test on all available sessions, including losses. Leader isn't always right |
| **Ignoring fees** | Assuming zero-cost trading | Add maker/taker fee percentages to spread cost. Currently missing |

---

## Feature Dependencies

Critical ordering constraints for implementation.

```
Foundation Tier (Must exist first):
├─ Historical price accuracy
├─ Portfolio cost basis tracking
├─ Order constraint validation (min sizes)
└─ Session recording/replay

Risk Management Tier (Depends on foundation):
├─ Per-market / per-side / global caps
│  └─ Requires: Portfolio tracking
├─ Position validation (no over-selling)
│  └─ Requires: Cost basis tracking
└─ Cash reserve

Realism Tier (Depends on foundation + risk):
├─ Bid/ask spread modeling
│  └─ Requires: Historical prices
├─ Slippage simulation
│  └─ Requires: Spread modeling, order size
├─ Limit order fill simulation
│  └─ Requires: Historical prices, TTL config
└─ Market resolution simulation
   └─ Requires: Final prices, position tracking

Analysis Tier (Depends on all above):
├─ Follow quality metrics
│  └─ Requires: Replay, position tracking
├─ Profit attribution
│  └─ Requires: Trade history, PnL calculation
└─ Drawdown analysis
   └─ Requires: Equity curve over time

Optimization Tier (Depends on analysis):
├─ Grid search
│  └─ Requires: Replay, metrics
├─ Dynamic position sizing
│  └─ Requires: Current capital tracking, PnL
└─ Selective following
   └─ Requires: Follow metrics, profit attribution
```

---

## MVP Recommendation

For validating simulation realism and improving small-budget performance:

### Must Have (Already exists ✓)
1. Historical price accuracy with price snapshots
2. Bid/ask spread modeling
3. Portfolio tracking with cost basis
4. Order constraint validation
5. Session replay with deterministic results
6. Market resolution simulation
7. Follow quality metrics

### Critical Additions (Priority 1)
1. **Fee modeling** — Currently missing, inflates PnL
   - Add maker/taker fees (Polymarket: 0.5% maker rebate, 0.5% taker fee typically)
   - Deduct from buy cost, sell proceeds
   - Complexity: Low, impact: HIGH (materially affects small account profitability)

2. **Drawdown tracking** — Need to know max capital loss
   - Track equity curve over session
   - Report max drawdown from peak
   - Complexity: Low, impact: MEDIUM (critical for risk assessment)

3. **Per-trade PnL attribution** — Identify which trades made/lost money
   - Link each trade to final outcome (win/loss/open)
   - Show profit contribution of each trade
   - Complexity: Medium, impact: HIGH (identifies where strategy deviates profitably/unprofitably from leader)

### Priority 2 (Improve profitability)
1. **Dynamic position sizing** — Adapt to current capital
   - Caps should be % of current capital, not starting capital
   - Allows compounding wins, protects after losses
   - Complexity: Medium, impact: MEDIUM

2. **Selective following with confidence filtering** — Don't copy blindly
   - Develop heuristics for which leader trades to follow
   - E.g., skip trades with >5% spread cost
   - Complexity: Medium, impact: MEDIUM (conserves capital for high-edge trades)

### Defer to Post-MVP
- Order book depth modeling (need more market data)
- Walk-forward optimization (need multiple sessions)
- Monte Carlo simulation (nice-to-have, not critical)
- Cross-market correlation (small budget = few positions anyway)
- ML-based sizing (insufficient data)

---

## Small-Budget Specific Considerations

At ~$100 capital, certain features become MORE important than for large accounts:

### Amplified Importance

1. **Transaction costs** — Fees are 0.5-1% of trade value. At $5/trade, that's $0.05. Over 100 trades in a session, $5 in fees = 5% of capital. Massive impact.

2. **Minimum order constraints** — $1 min market order = 1% of capital. Can't precisely scale positions. Need to round intelligently.

3. **Selective following** — Can't afford to copy every trade. Budget forces prioritization, which could be an advantage (skip low-edge trades).

4. **Capital efficiency** — With $100, might max out at 3-4 open positions. Each position must pull weight. No room for diversification drag.

### Reduced Importance

1. **Drawdown analysis** — With $100, absolute drawdown is small ($20 loss = 20%). Still need to track, but less scary than $20k loss on $100k account.

2. **Cross-market correlation** — Small budget = few positions = less correlation risk.

3. **Order book depth** — At $1-5 trade sizes, unlikely to move market in any material way.

---

## Confidence Assessment

| Feature Category | Confidence | Rationale |
|-----------------|------------|-----------|
| **Table stakes identification** | HIGH | Based on codebase analysis showing these features implemented |
| **Small-budget features** | MEDIUM | Domain knowledge from training (Jan 2025 cutoff), no live web verification |
| **Simulation realism features** | MEDIUM | Based on backtesting framework best practices from training, verified against codebase |
| **Anti-features** | MEDIUM | Common copy trading pitfalls from training knowledge |
| **Priority ordering** | HIGH | Based on PROJECT.md stating "sizing is suspected main profit leakage" |

---

## Sources

**Codebase Analysis (HIGH confidence):**
- `src/core/portfolio.py` — Portfolio tracking implementation
- `src/simulation/limit_order_sim.py` — Limit order fill modeling
- `src/framework/replay.py` — Session replay with price snapshots
- `src/simulation/follow_metrics.py` — Follow quality tracking
- `src/strategies/base.py` — Order constraints, strategy interface
- `.planning/PROJECT.md` — Project context and constraints

**Domain Knowledge (MEDIUM confidence):**
- Copy trading systems design patterns (training knowledge, Jan 2025 cutoff)
- Backtesting framework realism requirements (training knowledge)
- Prediction market mechanics (training knowledge)
- Small account position sizing challenges (training knowledge)

**Verification Status:**
- Web search unavailable — could not verify current (2026) best practices
- No Context7 access — could not verify library-specific features
- Recommendations based on codebase + training knowledge, marked MEDIUM confidence where unverified

---

## Gaps to Address

### Uncertain/Missing Information

1. **Polymarket fee structure** — Assumed 0.5% maker/taker based on training knowledge. Should verify with official API docs.
   - Impact: HIGH — incorrect fees = wrong PnL
   - Resolution: Check Polymarket docs or py-clob-client implementation

2. **Typical order book depth** — Don't know if $5 orders are "large" relative to typical liquidity
   - Impact: MEDIUM — affects slippage modeling
   - Resolution: Collect order book snapshots during session recording

3. **Leader's actual capital** — Config assumes $800, PROJECT.md mentions "estimated"
   - Impact: MEDIUM — affects scaling calculation
   - Resolution: Analyze leader's total positions across markets to estimate better

4. **Real-world fill rates** — `LimitOrderSimulator` uses 8s TTL, but is that realistic?
   - Impact: MEDIUM — affects limit vs market order choice
   - Resolution: Compare replay results with/without limit orders, validate against known outcomes

### Validation Needed

- Test that PnL calculation matches known outcomes (if any live trades exist for comparison)
- Verify duplicate filtering catches all duplicates (log analysis)
- Confirm market resolution logic matches Polymarket's actual resolution prices
- Validate follow metrics against manual inspection of leader vs our trades

---

*Research completed: 2026-01-30*
*Confidence: MEDIUM (codebase analysis HIGH, external verification unavailable)*
