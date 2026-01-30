# Domain Pitfalls: Copy Trading Bot Optimization

**Domain:** Polymarket Copy Trading Bot (~$100 budget)
**Researched:** 2026-01-30
**Confidence:** MEDIUM (based on codebase analysis + training data on algorithmic trading)

## Executive Summary

Copy trading bots fail primarily through **simulation dishonesty** (making unrealistic assumptions), **scale mismatch** (copying whale trades at 1/100th scale), and **overfitting** (optimizing to noise instead of signal). For small-budget bots copying large traders, the standard pitfalls are amplified: minimum order sizes become binding constraints, spread costs dominate P&L, and what works at $80K capital breaks at $100.

**Critical insight from codebase analysis:** This bot has 7 strategies with NO data-driven comparison, simulation that may fabricate prices when missing, and risk caps designed for larger accounts. These are textbook setup conditions for the pitfalls below.

---

## Critical Pitfalls

Mistakes that cause rewrites, capital loss, or total strategy failure.

### Pitfall 1: Simulation Price Dishonesty

**What goes wrong:** Simulation fabricates prices when historical data is missing, leading to unrealistic fill assumptions and inflated backtest results.

**Why it happens:**
- Recording infrastructure doesn't capture full orderbook snapshots
- Replay assumes "if leader traded at X, we can too"
- Missing bid/ask spreads get filled with leader's execution price
- Limit order fill assumptions ignore queue position and liquidity

**Evidence in codebase:**
```python
# replay.py line 328-333: Drops events with no prices but doesn't validate spread realism
if not raw_bid or not raw_ask:
    self._dropped_no_prices += 1
    return None
```

**Consequences:**
- Backtest shows +15% return, live trading shows -8%
- Strategy "works" in simulation but fails when spreads are 2-5% in reality
- Limit orders expire unfilled but simulation assumed instant fills

**Warning signs:**
- Suspiciously high fill rates (>70% for limit orders is unrealistic)
- Backtest P&L significantly better than live/paper trading
- Strategy skips "cost_too_high" rarely in backtest, frequently in live
- Missing price data logs: `Dropped N events with no real bid/ask`

**Prevention:**
1. **Price validation protocol:**
   - Require BOTH bid and ask for every simulation event (already partially done)
   - Validate spread >= 0.5% minimum (hourly markets are volatile)
   - Log when spreads are suspiciously tight (<0.3%)
   - NEVER use leader's execution price as our execution price

2. **Limit order realism:**
   - Implement TTL (time-to-live) simulation as in `limit_order_sim.py`
   - Assume fill only if price moves THROUGH limit (not just touches)
   - Track fill rate % and flag if >60% (too optimistic)
   - Add slippage buffer: buy at ask+0.2%, sell at bid-0.2%

3. **Comparison discipline:**
   - Run SAME session through market orders vs limit orders
   - Fill rate should be 30-50% for aggressive limits, 60-80% for passive
   - If backtest fill rate >> live fill rate, simulation is lying

**Detection checklist:**
- [ ] Every trade has real bid/ask from orderbook snapshot
- [ ] Spread distribution matches exchange reality (0.5-5% typical)
- [ ] Limit order fill rate <70%
- [ ] Market order execution assumes worst price (ask for buy, bid for sell)
- [ ] Slippage modeled (minimum 0.5-1% on market orders)

**Phase mapping:** This MUST be validated in Phase 1 (Simulation Validation). Cannot optimize strategies until simulation is proven realistic.

---

### Pitfall 2: Scale Ratio Overfitting

**What goes wrong:** Optimizing the scaling factor `k_factor` and `leader_capital` estimate on historical data produces parameters that memorize past conditions but fail on new markets.

**Why it happens:**
- Optimizer searches grid of k_factor values [0.5, 0.7, 0.85, 1.0, 1.2]
- Best parameter on Session A is 1.2, Session B is 0.5, Session C is 0.85
- Team picks 1.2 because it had highest P&L on best session
- Live trading encounters different market conditions → 1.2 is too aggressive

**Evidence in codebase:**
```python
# optimizer.py line 41-44: Grid search over k_factor without validation split
"scaling.k_factor": [0.5, 0.7, 0.85, 1.0, 1.2],
# optimizer.py line 236: Sorted by score, which rewards highest PnL
all_flat.sort(key=lambda x: x.result.resolved_pnl if x.result.resolved_pnl else x.score, reverse=True)
```

**Consequences:**
- Parameters optimized on 3 sessions, all profitable, deployed live
- Next 5 sessions hit stop-loss because k_factor was tuned to lucky sessions
- "Conservative" becomes "aggressive" depending on which historical window you optimize on

**Root cause:** Confusing **in-sample performance** (optimization set) with **out-of-sample robustness** (real trading).

**Warning signs:**
- Optimal k_factor varies wildly between sessions (0.5 to 1.2 range)
- Best strategy on Session A is worst on Session B
- Parameters keep "needing adjustment" after each session
- Win rate is 60% on historical data, 40% live

**Prevention:**
1. **Train/validation/test split:**
   - Optimization set: 60% of sessions (find parameters)
   - Validation set: 20% of sessions (compare strategies)
   - Test set: 20% NEVER TOUCHED until final decision
   - Report test set performance, not optimization set

2. **Walk-forward validation:**
   - Optimize on Sessions 1-5, test on Session 6
   - Optimize on Sessions 2-6, test on Session 7
   - If performance degrades >20% out-of-sample, parameters are overfit

3. **Ensemble instead of picking winner:**
   - Run k_factor = [0.6, 0.7, 0.8, 0.85] in parallel
   - Average their signals or allocate capital across all
   - Reduces parameter sensitivity

4. **Constrain search space:**
   - Don't optimize leader_capital (estimate from blockchain, don't fit)
   - k_factor should be conservative (0.6-0.85 range, not 1.2)
   - Hourly budget should be % of capital (not independently tuned)

**Detection checklist:**
- [ ] Optimization uses <60% of available sessions
- [ ] Test set exists and is never used for parameter selection
- [ ] Best parameters on test set within 15% of validation set
- [ ] Parameter variance across sessions is low (k_factor std dev <0.15)
- [ ] Walk-forward results don't degrade >20%

**Phase mapping:** Phase 2 (Strategy Comparison) must use train/test split. Phase 3 (Parameter Optimization) must implement walk-forward validation.

---

### Pitfall 3: Minimum Order Size Death Spiral

**What goes wrong:** Small budget + position caps + minimum order size (5 shares for limits) creates situations where bot wants to trade $0.50 but can't, leading to portfolio drift and missed opportunities.

**Why it happens:**
- Leader trades $800 position, scaled down to $0.80 for us
- After caps and budget, we have $0.60 available
- Minimum order: 5 shares × $0.15 ask = $0.75 required
- Trade skipped as "min_shares"
- After 10 skips, our portfolio diverges from leader's
- When leader sells profitable position, we have nothing to sell

**Evidence in codebase:**
```python
# mirror/strategy.py line 145-152: Bumps to 5 shares if room, otherwise skips
if shares < MIN_LIMIT_ORDER_SHARES:
    if all(x >= min_dollars_needed for x in [available, mkt_room, side_room, global_room, budget_room]):
        shares = MIN_LIMIT_ORDER_SHARES
        dollars = shares * ask
    else:
        return self._skip("min_shares")
```

**Consequences:**
- 40% of leader trades are skipped due to "min_shares" or "min_order"
- Leader's portfolio: 15 positions, ours: 6 positions
- Leader exits winning trade for +$120, we never entered, miss profit
- Follow rate drops from 80% to 35%

**Math of the trap (at $100 budget):**
```
Per-market cap: 30% × $100 = $30
Per-side cap: 26% × $100 = $26
Minimum order: 5 shares × $0.20 avg = $1.00

If leader trades 20 markets, we can only enter ~6 before hitting caps
If price is $0.30+, minimum order is $1.50, exceeds per-side budget on small positions
```

**Warning signs:**
- Skip reason "min_shares" or "min_order" is >25% of skips
- Follow rate <50% (should be 70%+ for mirror strategy)
- Open positions: leader has 12-20, we have 4-8
- Total deployed << available capital (money sitting unused)

**Prevention:**
1. **Budget calibration for small accounts:**
   - At $100 budget, per_market_cap should be 40-50% (not 30%)
   - Per_side_cap should be 35-40% (not 26%)
   - These caps were designed for $500+ accounts, don't scale down linearly

2. **Minimum order accumulation:**
   - Don't trade every leader event, batch them
   - Wait until scaled position >= $1.50 (safe above minimum)
   - Accept lag in following for sake of actually entering positions

3. **Market order escape hatch:**
   - If limit order would skip on min_shares, try market order
   - Market minimum is $1, lower than limit's 5 shares (~$1.25-2.00)
   - Accept higher spread cost to avoid complete skip

4. **Position consolidation:**
   - Don't mirror all markets, select top 10 by leader exposure
   - Concentrate capital in fewer positions to avoid minimum size trap
   - Better to follow 10 markets well than 20 markets poorly

**Detection checklist:**
- [ ] Skip breakdown shows min_shares + min_order <20%
- [ ] Follow rate >60% on trades we could afford
- [ ] Average trade size >$1.50 (safely above minimums)
- [ ] Open positions ≥50% of leader's position count

**Phase mapping:** Phase 1 (Simulation Validation) should measure minimum order impact. Phase 2 (Strategy Comparison) should test market vs limit order strategies. Phase 3 should optimize caps for small budgets.

---

### Pitfall 4: Strategy Overfitting to Historical Leader Behavior

**What goes wrong:** Optimizing strategy parameters to maximize P&L on past sessions produces a strategy that memorizes the leader's past patterns, not general profitable trading.

**Why it happens:**
- Leader was aggressive in Sessions 1-3 (fast entries, high leverage)
- Team optimizes on these sessions, finds "aggressive" strategy wins
- Deploys aggressive strategy
- Leader changes behavior in Sessions 4-6 (slower, selective entries)
- Strategy losses because it's tuned to old leader behavior, not current

**Evidence in codebase:**
```python
# 7 different strategies with different assumptions about leader behavior
# mirror, conservative, aggressive, momentum, velocity, price_level, hybrid
# No evidence of robustness testing or regime detection
```

**Consequences:**
- Strategy works perfectly on historical sessions (80% win rate)
- Live trading: leader's behavior shifts, strategy fails (35% win rate)
- Team keeps switching strategies chasing recent performance
- Never achieve consistent profitability

**Root cause:** Leader is a human trader whose behavior changes with market conditions, time pressure, and conviction. Optimizing to past behavior assumes stationarity (constant behavior), which is false.

**Warning signs:**
- Strategy performance correlates with leader's recent trading style
- When leader is aggressive, momentum strategy wins; when conservative, conservative wins
- No strategy is consistently best across all sessions
- Need to "pick the right strategy" for each session (impossible in live trading)

**Prevention:**
1. **Regime-robust strategies:**
   - Don't tune to leader behavior, tune to market microstructure
   - Strategy should work whether leader is aggressive or conservative
   - Focus on "when CAN we trade" not "when DOES leader trade"

2. **Leader behavior detection (advanced):**
   - Calculate leader's recent aggression score (trade frequency, size)
   - If leader behavior shifts >30%, flag for review
   - Consider ensemble of conservative + aggressive weighted by regime

3. **Strategy selection criteria:**
   - Test across MULTIPLE leaders if possible (generalization)
   - Prefer strategies with low variance across sessions
   - Don't pick winner of single session, pick consistent performer

4. **Simplicity over complexity:**
   - Mirror with good caps is often better than "smart" strategies
   - Velocity tracking, momentum, price levels add parameters = overfitting risk
   - If strategy has >5 tunable parameters, you're curve-fitting

**Detection checklist:**
- [ ] Strategy performance variance across sessions <30%
- [ ] Win rate stable across different leader behavior regimes
- [ ] Strategy doesn't require "picking the right one" for each session
- [ ] Out-of-sample performance within 20% of in-sample

**Phase mapping:** Phase 2 (Strategy Comparison) should test across different leader behavior periods. Phase 4 (Robustness) should validate strategies don't memorize leader patterns.

---

## Moderate Pitfalls

Mistakes that cause technical debt, reduced performance, or require rework.

### Pitfall 5: Ignoring Spread Cost in Position Sizing

**What goes wrong:** Position sizing based on notional dollars without accounting for spread cost, leading to actual risk exposure being 2-4% higher than intended.

**Why it happens:**
- Strategy calculates: "We can deploy $5 on this trade"
- Executes: Buy 25 shares at $0.20 ask = $5.00
- Forgets: Leader bought at $0.195 (mid-price), we paid $0.20 (ask)
- Actual cost basis: $5.00, but fair value is $4.875 (25 × $0.195)
- Immediate unrealized loss: -$0.125 (-2.5%)

**Evidence in codebase:**
```python
# mirror/strategy.py line 142: Calculates shares from dollars without spread adjustment
shares = (dollars / ask).quantize(Decimal("0.01"))
# No reduction for embedded spread cost
```

**Consequences:**
- Every trade starts 1-3% underwater due to spread
- Portfolio "total deployed" overstates true exposure by 2-4%
- Caps trigger early (think we're at 30% exposure, actually 29%)
- Realized P&L includes spread cost, unrealized P&L doesn't until sell

**Prevention:**
1. **Spread-adjusted position sizing:**
   - Target notional = $5.00
   - Spread cost ~2%, reduce size to $4.90 gross
   - Buy $4.90 / ask, not $5.00 / ask
   - This ensures deployed capital matches risk capital

2. **Cost basis correction:**
   - Record cost basis at mid-price, not execution price
   - Track spread cost separately as transaction cost
   - P&L calculation more accurate

3. **Spread budget:**
   - Allocate 10% of capital to transaction costs
   - Don't count it in deployable capital
   - Prevents compounding of spread costs eating into caps

**Detection:**
- [ ] Average trade starts <1% underwater (not 2%+)
- [ ] Total deployed matches sum of position mid-prices ±5%
- [ ] Spread costs are tracked separately from P&L

**Phase mapping:** Phase 1 (Simulation Validation) should measure actual spread cost per trade. Phase 3 (Parameter Optimization) should incorporate spread-adjusted sizing.

---

### Pitfall 6: Hourly Budget Reset Timing Mismatch

**What goes wrong:** Hourly budget resets at clock hour boundary (3:00:00 PM), but leader trades most actively at 3:00-3:05 PM and 3:55-4:00 PM (market open/close), creating artificial budget exhaustion.

**Why it happens:**
- Budget reset: every hour at :00
- Leader behavior: burst trades at market start (3:00 PM) and end (3:55 PM)
- Bot deploys $90 during 3:00-3:10 PM burst
- Sits idle 3:10-4:00 PM (budget exhausted)
- Misses 3:55 PM trades even though it's "new session"

**Evidence in codebase:**
```python
# mirror/strategy.py line 50-56: Resets at hour boundary regardless of market timing
def _check_hourly_reset(self, event_time: datetime) -> None:
    current_hour = event_time.hour
    if self._current_hour is not None and current_hour != self._current_hour:
        self.hourly_budget_used = Decimal("0")
```

**Consequences:**
- Miss 30-40% of leader trades due to budget exhausted
- Budget unused in quiet periods, exhausted in active periods
- Effective follow rate varies wildly (90% some hours, 20% others)

**Prevention:**
1. **Rolling budget window:**
   - Budget is $90 per 60-minute rolling window
   - Track spending timestamps, expire old ones
   - Always have budget for recent activity

2. **Session-aligned budget:**
   - Polymarket hourly markets run 3:00-4:00, 4:00-5:00, etc.
   - Reset budget at market boundaries, not clock hours
   - Aligns budget with actual trading opportunities

3. **Burst budget reserve:**
   - Reserve 20% of hourly budget for last 10 minutes
   - Prevents early exhaustion missing end-of-session trades

**Detection:**
- [ ] Budget exhaustion happens uniformly, not clustered at session end
- [ ] Follow rate >60% in both early and late session periods
- [ ] Budget utilization >70% (if <50%, budget too conservative)

**Phase mapping:** Phase 1 should analyze budget exhaustion timing. Phase 3 should test rolling vs session-aligned budgets.

---

### Pitfall 7: Unrealistic Market Resolution Simulation

**What goes wrong:** Simulation assumes positions resolve at extreme prices (0.99 for wins, 0.00 for losses) to calculate "resolved P&L", but this overstates actual profit by 10-20%.

**Why it happens:**
- Markets rarely resolve at exactly 0.99 (more like 0.90-0.95 for clear wins)
- Simulation uses bid >= 0.5 as "win" threshold, but 0.51 is barely winning (not 0.99)
- Exit slippage not modeled (selling at 0.90 bid when "worth" 0.95)

**Evidence in codebase:**
```python
# replay.py line 543-551: Assumes 0.99 resolution for wins
if our_side_wins:
    resolution_value = pos.shares * Decimal("0.99")  # Assumes perfect 0.99 exit
```

**Consequences:**
- Backtest shows resolved P&L of +$18.00
- Actual profit when markets resolve: +$14.00
- 22% overstated profitability
- Decisions made on inflated expected value

**Prevention:**
1. **Conservative resolution prices:**
   - Clear win (bid >0.80): resolve at 0.92 (not 0.99)
   - Moderate win (bid 0.60-0.80): resolve at 0.85
   - Marginal win (bid 0.50-0.60): resolve at 0.70
   - This models realistic exit prices

2. **Exit slippage:**
   - Subtract 3-5% from final bid for exit slippage
   - Accounts for spread and urgency to close before resolution

3. **Comparison with actual outcomes:**
   - Track actual resolution prices from past sessions
   - Calibrate simulation to match reality

**Detection:**
- [ ] Resolved P&L within 10% of actual outcomes
- [ ] Simulation uses realistic exit prices (<0.95 for wins)
- [ ] Accounts for exit slippage

**Phase mapping:** Phase 1 (Simulation Validation) should calibrate resolution assumptions against actual data.

---

## Minor Pitfalls

Mistakes that cause annoyance but are easily fixable.

### Pitfall 8: Leader Capital Estimate Instability

**What goes wrong:** Estimating leader's total capital from recent trades produces volatile estimates that change 20-40% session to session, destabilizing position sizing.

**Why it happens:**
- Session A: leader trades $80, $60, $40 → estimate $900 capital
- Session B: leader trades $120, $200, $90 → estimate $1400 capital
- Session C: leader trades $20, $15, $30 → estimate $600 capital
- Scale ratio fluctuates wildly, position sizing becomes erratic

**Prevention:**
- Use blockchain analysis to estimate leader's wallet balance (one-time)
- Don't re-estimate every session from trade sizes
- If estimate changes >30%, flag for manual review

**Detection:**
- [ ] Leader capital estimate stable ±15% across sessions
- [ ] Estimate based on wallet analysis, not recent trades

---

### Pitfall 9: Duplicate Event Handling Gaps

**What goes wrong:** Same trade emits multiple events (maker + taker OrderFilled), both get recorded, inflating trade counts and double-counting exposure.

**Why it happens:**
- Blockchain logs emit OrderFilled for both maker and taker
- Same economic trade appears twice with different log_index
- Deduplication by content works, but edge cases remain

**Evidence in codebase:**
```python
# replay.py line 191-210: Content-based dedup exists but may miss edge cases
dedup_key = f"{tx_hash}_{trade.token_id}_{trade.action.value}_{trade.dollars}"
```

**Prevention:**
- Validate dedup coverage: check for trades with identical timestamp + token + dollars
- Log duplicate detection stats per session
- If duplicate rate >5%, investigate data collection

**Detection:**
- [ ] Duplicate rate <2% of events
- [ ] No trades with identical timestamp + token + dollars ± $0.01

---

### Pitfall 10: Loss Protection Overreach

**What goes wrong:** "Block loss sells if leader profit" protection prevents profitable exits when our entry price was worse than leader's.

**Why it happens:**
- Leader buys at $0.50, we buy at $0.52 (spread + delay)
- Price rises to $0.60, leader sells at $0.60 (profit)
- We have unrealized profit ($0.60 - $0.52 = +$0.08)
- Loss protection blocks sell because $0.60 bid < $0.62 ask (we'd "lose" vs perfect mid)
- Price crashes to $0.40, we hold a losing position

**Evidence in codebase:**
```python
# mirror/strategy.py line 173-177: Blocks sell if leader profits but we'd lose
if pos.avg_price > 0 and bid < pos.avg_price:
    if lt and lt.get("avg_price", 0) > 0 and trade.price >= lt["avg_price"]:
        return self._skip("leader_profit_our_loss")
```

**Prevention:**
- Only block if loss is >2% (small losses from spread are acceptable)
- Or remove loss protection entirely (trust leader's timing)
- Log blocked sells to measure cost of protection

**Detection:**
- [ ] "leader_profit_our_loss" skips <5% of sells
- [ ] Blocked sells didn't result in better outcomes

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|----------------|------------|
| Simulation Validation | Price dishonesty (Pitfall 1) | Implement spread validation, limit order TTL simulation, compare fill rates to exchange reality |
| Strategy Comparison | Overfitting to leader behavior (Pitfall 4) | Test across multiple session regimes, prefer low-variance strategies |
| Parameter Optimization | Scale ratio overfitting (Pitfall 2) | Use train/validation/test split, walk-forward validation, constrain search space |
| Small Budget Sizing | Minimum order death spiral (Pitfall 3) | Recalibrate caps for $100 budget, consider market order escape hatch |
| Live Trading | Hourly budget timing mismatch (Pitfall 6) | Align budget reset with market sessions, not clock hours |

---

## Research Quality Assessment

**Confidence levels:**
- **HIGH:** Pitfalls 1, 3 (direct evidence in codebase)
- **MEDIUM:** Pitfalls 2, 4, 5, 6, 7 (inference from code patterns + domain knowledge)
- **LOW:** Pitfalls 8, 9, 10 (edge cases, may not be severe)

**Validation approach:**
All pitfalls should be tested empirically during Phase 1 (Simulation Validation):
1. Run optimizer on historical sessions
2. Measure: fill rates, skip reasons, follow rate, P&L variance
3. Compare simulation assumptions to live/paper trading logs
4. If any metric deviates >20%, pitfall is confirmed

**Gaps:**
- No access to live trading logs to confirm spread costs
- Leader capital estimate method unclear (may be more sophisticated than assumed)
- Don't know if team has already addressed some pitfalls

---

## Actionable Checklist for Roadmap

**Phase 1: Simulation Validation**
- [ ] Validate every event has real bid/ask (no price fabrication)
- [ ] Measure spread distribution, confirm >0.5% average
- [ ] Implement limit order TTL simulation
- [ ] Compare backtest fill rate to realistic benchmarks (target <70%)
- [ ] Test resolution P&L assumptions against actual outcomes

**Phase 2: Strategy Comparison**
- [ ] Use train/validation/test split (60/20/20)
- [ ] Measure strategy variance across sessions
- [ ] Test across different leader behavior regimes
- [ ] Prefer strategies with consistent performance over "best on one session"

**Phase 3: Parameter Optimization**
- [ ] Walk-forward validation required
- [ ] Optimize on <60% of data
- [ ] Report test set results (not optimization set)
- [ ] Constrain k_factor to [0.6-0.85] for small budgets
- [ ] Recalibrate caps for $100 budget (40-50% per-market, 35-40% per-side)

**Phase 4: Robustness Testing**
- [ ] Measure minimum order skip rate (target <20%)
- [ ] Calculate follow rate (target >60%)
- [ ] Test budget timing alignment with market sessions
- [ ] Validate spread cost modeling

---

## Sources

**Codebase analysis (HIGH confidence):**
- `src/simulation/optimizer.py` - Grid search implementation, scoring methodology
- `src/simulation/limit_order_sim.py` - Limit order fill assumptions
- `src/framework/replay.py` - Price validation, event deduplication, resolution simulation
- `src/strategies/mirror/strategy.py` - Position sizing, minimum order handling, loss protection
- `src/strategies/conservative/strategy.py` - Risk cap implementation
- `src/strategies/base.py` - Order constraints (5 shares limit, $1 market)

**Domain knowledge (MEDIUM confidence):**
- Algorithmic trading pitfalls from training data (backtesting overfitting, simulation realism)
- Market microstructure (spread costs, slippage, minimum order constraints)
- Copy trading specific challenges (scale mismatch, leader behavior shifts)

**Unverified assumptions (LOW confidence):**
- Typical Polymarket spread ranges (0.5-5%) - based on prediction market norms, not verified with Polymarket API
- Realistic limit order fill rates (30-70%) - based on general trading, not Polymarket specifically
- Leader behavior volatility - inferred from multi-strategy existence, not measured

**Recommended validation sources:**
- Polymarket API documentation for actual spread statistics
- Live trading logs to confirm fill rates and spread costs
- Session recordings with full orderbook snapshots (not just leader trades)
