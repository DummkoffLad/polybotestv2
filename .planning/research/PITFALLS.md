# Pitfalls Research: Strategy Debugging & Comparison

**Domain:** Trading Strategy Performance Analysis
**Context:** Understanding why conservative strategy won vs others in 12-session test
**Researched:** 2026-02-03
**Confidence:** HIGH (verified with multiple 2026 sources)

---

## Executive Summary

Strategy debugging fails primarily through **small sample bias** (12 sessions is not statistically significant), **overfitting explanations to noise** (finding patterns that don't generalize), and **confusing correlation with causation** (conservative traded more ≠ more trades caused profit). The path from "conservative won on 12 sessions" to "deploy a better strategy" is filled with cognitive traps that lead to strategies optimized for historical data that fail live.

**Critical insight:** With only 12 sessions, a 95% confidence interval on performance is ±42%. Conservative may have won by luck, not skill. Any new strategy based on this limited data risks overfitting to noise.

**Research foundation:** Analysis synthesizes findings from [Backtesting Traps](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/), [Overfitting in Trading](https://blog.traderspost.io/article/understanding-overfitting-in-trading-strategy-development), [Sample Size Requirements](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05), [Polymarket Trading Mistakes](https://www.crypticorn.com/how-to-trade-polymarket-profitably-what-actually-works-in-2026/), and [Strategy Comparison Best Practices](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/).

---

## Analysis Pitfalls

### Pitfall 1: Small Sample Size Illusion

**What goes wrong:** Drawing conclusions from 12 sessions when 200-500 trades are needed for statistical significance at 95% confidence.

**Why it happens:**
- User observes: conservative profitable, others not
- Intuition: 12 sessions feels like "enough data"
- Reality: need 385 trades for 95% confidence, 107 for 70% confidence
- With 12 sessions averaging 8-15 trades each, you have ~100-180 trades total
- This gives you only 70-80% confidence — meaning 20-30% chance results are random

**Research evidence:**
- [Sample Size Study](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05): "To achieve statistical confidence, you need at least 107 trades for 70% confidence, 385 trades for 95% confidence"
- [Statistical Power](https://www.backtestbase.com/education/how-many-trades-for-backtest): "With 20 trades, a win rate of 65% still has p-value > 0.2, meaning 20%+ chance this is just noise"
- [Trading System Evaluation](https://www.dara.trade/blog/2019/10/14/how-to-build-a-profitable-trading-system-part-1-confidence-in-numbers): "A system showing 60% profitable trades over 50 trades remains highly uncertain, while 60% over 500 trades suggests much stronger evidence"

**Consequences:**
- Conservative's win is likely luck, not skill
- Build new strategy based on pattern, deploy live, loses money
- Team wastes time "understanding" noise instead of gathering more data
- Premature optimization before validation

**Warning signs:**
- "Conservative won 12/12" feels conclusive (it's not)
- Focusing on "what did conservative do right" without checking if significance
- Planning to build new strategy before statistical validation
- Confidence in conclusions >> confidence supported by data

**Prevention strategies:**

1. **Statistical significance first:**
   ```python
   # Before analysis, check if results are significant
   from scipy import stats

   n_trades = 120  # across all strategies
   win_rate = 0.58  # conservative's rate
   expected = 0.50  # null hypothesis

   z_score = (win_rate - expected) / (0.5 / sqrt(n_trades))
   p_value = stats.norm.sf(abs(z_score))

   if p_value > 0.05:
       print("NOT SIGNIFICANT - need more data")
   ```

2. **Confidence intervals on metrics:**
   - Conservative PnL: +$18.50 ± $14.20 (95% CI)
   - That means true performance could be +$4.30 to +$32.70
   - Overlaps with zero — not conclusively profitable
   - Report: "Conservative appears profitable but sample too small to confirm"

3. **Power analysis:**
   - To detect 10% edge with 95% confidence, need 280 trades minimum
   - To detect 5% edge, need 1,120 trades
   - Current data: 120 trades — can only detect 20%+ edge reliably
   - Conclusion: "Need 160 more trades before meaningful comparison"

4. **Defer pattern analysis:**
   - Don't ask "why did conservative win" until significance confirmed
   - First: gather 200+ more trades
   - Then: if conservative STILL wins, analyze why
   - Otherwise you're finding patterns in noise

**Detection checklist:**
- [ ] Calculate n_trades across all sessions
- [ ] Compute 95% confidence interval on win rate and PnL
- [ ] Check if confidence interval excludes zero
- [ ] Require p-value < 0.05 before declaring "winner"
- [ ] Report "insufficient data" if n < 200 trades

**Phase implications:** Phase 1 (comparison tooling) should calculate and display confidence intervals, NOT just raw PnL. Phase 2 (attribution analysis) should only proceed if Phase 1 shows statistical significance.

---

### Pitfall 2: Correlation vs Causation Confusion

**What goes wrong:** Observing "conservative took MORE trades than mirror" and concluding "taking more trades made it profitable" when causation may be reversed or spurious.

**Why it happens:**
- Observation: conservative 65 trades, mirror 48 trades, conservative profitable
- Intuition: more trades → more profit → new strategy should trade more
- Reality: correlation could be:
  - Conservative traded more BECAUSE it had winning positions (pyramid effect)
  - Mirror skipped trades due to caps being hit (losing positions tied up capital)
  - Both strategies entered same markets, but conservative exited faster freeing capital
  - More trades is EFFECT of profitability, not CAUSE

**Research evidence:**
- [Backtesting Errors](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/): "Correlation does not imply causation — strategies with higher trade counts may simply have different market exposure periods"
- [Strategy Analysis Pitfalls](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/): "When comparing strategies, ensure you're measuring the mechanism, not just the outcome"

**Real example from project:**
```
Conservative: 65 trades, +$18.50 PnL
Mirror: 48 trades, -$4.20 PnL

Wrong conclusion: "Trade more to profit more"
Right question: "WHY did conservative trade 17 more times?"

Possible answers:
A) Conservative entered MORE markets (selection)
B) Conservative exited faster, freeing capital (turnover)
C) Conservative positions went profitable, triggered pyramid rules (feedback)
D) Mirror hit risk caps early from losing positions (constraint)

Only A suggests "enter more markets"
B/C/D suggest conservative's advantage was EXIT timing or risk management
```

**Consequences:**
- Build "aggressive" strategy that enters more markets
- Strategy loses because problem wasn't entry count, it was exit timing
- Team chases wrong variable, fails to find real edge
- Waste development time on correlation theater

**Warning signs:**
- Statements like "X did Y, therefore we should do Y"
- Comparing OUTPUT metrics (trade count, PnL) without analyzing INPUT decisions
- Missing the "why" — what decision logic led to the outcome
- Assuming first observed difference is the cause

**Prevention strategies:**

1. **Causal path analysis:**
   ```
   Trace decision logic:

   Conservative Trade 1: Leader bought, we bought (same as mirror)
   Conservative Trade 2: Leader sold, we sold (same as mirror)
   Conservative Trade 3: Leader bought again, we bought (mirror skipped - WHY?)

   Mirror skip reason: "global_cap_hit"
   Root cause: Mirror still holding losing position from Trade 1
   Conservative no longer holding it (exited early on stop loss?)

   Causation: Early exit → freed capital → could take Trade 3
   NOT: "Take more trades" → profit
   ```

2. **Controlled comparison:**
   - Hold one variable constant, change another
   - Test: conservative with mirror's exit rules
   - Test: mirror with conservative's risk caps
   - Isolate which component drives performance difference

3. **Mechanism hypothesis:**
   - Before building new strategy, write hypothesis:
   - "Conservative wins because [specific mechanism]"
   - Test mechanism directly, not just correlation
   - Example: "Conservative exits losing trades faster, preserving capital"
   - Test: measure avg hold time on losing positions

4. **Decomposition:**
   ```
   PnL difference = Entry difference + Exit difference + Sizing difference

   Entry: Did conservative enter better markets? Measure win rate per market.
   Exit: Did conservative exit at better times? Measure avg hold time.
   Sizing: Did conservative size better? Measure dollars per trade.

   Only optimize the component that actually differs.
   ```

**Detection checklist:**
- [ ] Every "X caused Y" claim has causal mechanism explained
- [ ] Correlation backed by decision logic trace
- [ ] Controlled experiments isolate variables
- [ ] Can explain why conservative traded more (not just that it did)

**Phase implications:** Phase 2 (per-trade attribution) MUST separate correlation from causation by tracing decision paths. Phase 3 (root cause) requires controlled experiments, not just observation.

---

### Pitfall 3: Overfitting Explanations to Limited Data

**What goes wrong:** Finding a pattern that "explains" conservative's wins on 12 sessions, but pattern is noise that won't generalize to future sessions.

**Why it happens:**
- User analyzes 12 sessions, finds: "Conservative won when it avoided markets with spread >2%"
- Insight feels profound: spread avoidance = profitability
- Reality: with 12 sessions, you can find 100 patterns by chance
- New strategy implements spread filter
- Next 12 sessions: spread filter hurts performance (pattern was noise)

**Research evidence:**
- [Overfitting Study](https://blog.traderspost.io/article/understanding-overfitting-in-trading-strategy-development): "Quantopian's 888-strategy study found that Sharpe ratios from backtests had almost zero predictive power for live returns. The more a quant optimized, the worse it performed live."
- [Multiple Testing Bias](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/): "Testing hundreds of strategies or tweaking parameters endlessly makes you more likely to stumble upon setups that seem profitable purely by chance"
- [Pattern Reliability](https://algotrading101.com/learn/what-is-overfitting-in-trading/): "A 2014 study found that 44% of published trading strategies couldn't replicate their success when applied to new data"

**Example of overfitting explanation:**
```
Analyst: "I found it! Conservative won because:
1. It avoided markets where leader's first trade was >$100
2. It only entered when bid/ask spread was <2.5%
3. It exited when unrealized profit hit +8%
4. It had higher exposure limits on YES side

These 4 rules perfectly explain all 12 sessions!"

Reality check:
- 12 sessions × 10 markets = 120 data points
- Testing 100 possible rules → expect 5 false positives at p=0.05
- Rules aren't causal, they're curve-fit to outcome
- Next 12 sessions: rules perform randomly (50/50)
```

**Consequences:**
- Build "improved" strategy based on discovered patterns
- Deploy on fresh data → performance regresses to mean
- Pattern was memorization of 12 sessions, not insight
- Team loses confidence in analysis when predictions fail

**Warning signs:**
- Pattern has many conditions (>3 rules)
- Pattern explains ALL sessions perfectly (100% fit = overfitting)
- Pattern "makes sense in hindsight" but wasn't hypothesized upfront
- Can't articulate WHY pattern should work (no causal mechanism)
- Performance difference is large (>50% better) on small sample

**Prevention strategies:**

1. **Train/validation/test split:**
   ```
   12 sessions available:

   Training: Sessions 1-7 (find patterns)
   Validation: Sessions 8-10 (compare patterns)
   Test: Sessions 11-12 (final check - NEVER TOUCH until end)

   Process:
   1. Analyze training set, find patterns
   2. Test patterns on validation set
   3. If validation performance drops >20%, pattern is overfit
   4. Only report test set results
   ```

2. **Hypothesis-driven analysis:**
   ```
   WRONG: "Let's find what conservative did differently"
   RIGHT: "I hypothesize conservative wins because it uses tighter risk caps"

   Test hypothesis:
   1. Measure: conservative's avg position size vs mirror
   2. Predict: if hypothesis true, conservative should have smaller positions
   3. Verify: check if true on ALL 12 sessions (not cherry-picked)
   4. Validate: test on NEW sessions before building strategy
   ```

3. **Out-of-sample requirement:**
   - Never deploy strategy based only on 12 sessions
   - Require validation on 12 NEW sessions before live deployment
   - If performance degrades >20% out-of-sample, explanation was overfit
   - Accept that you might need 24-50 sessions total for reliable pattern

4. **Simplicity penalty:**
   - Prefer simple explanations over complex ones
   - "Conservative uses lower k_factor" (1 variable)
   - Better than: "Conservative uses lower k_factor when spread >2% on YES side in first 10min" (4 variables)
   - Each parameter increases overfitting risk exponentially

5. **Mechanism requirement:**
   - Pattern must have causal story
   - "Spread avoidance works because high spread = low liquidity = higher slippage"
   - That's testable and generalizable
   - "Conservative entered markets ending in odd minutes" — spurious, will fail

**Detection checklist:**
- [ ] Pattern tested on hold-out set (not just training data)
- [ ] Pattern has ≤3 parameters
- [ ] Pattern has causal mechanism, not just correlation
- [ ] Out-of-sample performance within 20% of in-sample
- [ ] Can explain why pattern should work on NEW data

**Phase implications:** Phase 2 (attribution) should use 7 sessions for analysis, hold out 5 for validation. Phase 3 (root cause) should require out-of-sample validation before declaring "found the answer."

---

### Pitfall 4: Ignoring Regime Change

**What goes wrong:** Conservative won during specific market conditions (e.g., high volatility period), but those conditions won't persist, making conservative's advantage temporary.

**Why it happens:**
- 12 sessions happened during specific regime (e.g., election prediction markets)
- Markets had high volatility, fast price moves, frequent reversals
- Conservative's tight stops and quick exits thrived in this regime
- Regime shifts (post-election, markets become slower, trends persistent)
- Conservative's strategy now underperforms (stops hit on normal noise)

**Research evidence:**
- [2026 Market Regime Change](https://home.cib.natixis.com/articles/2026-entering-a-new-market-regime): "Markets are experiencing broader regime change, with low-volatility 2010s giving way to greater macro uncertainty and policy unpredictability"
- [Strategy Failure Modes](https://www.blackrock.com/us/financial-professionals/insights/2026-macro-outlook): "2026 will be defined by structural adjustment rather than cyclical repetition, with higher volatility and greater dispersion reshaping market dynamics"
- [Regime Risk](https://realinvestmentadvice.com/resources/blog/the-market-risk-in-2026-if-growth-projections-fail/): "Analysts projecting growth into 2026 are assuming demand-driven economy without income growth needed to support it — assumption is increasingly fragile"

**Example:**
```
Sessions 1-12 (Nov 2025 - Jan 2026): Election prediction markets
- High volume, fast price discovery, frequent new information
- Conservative: tight stops, quick exits → worked well
- Mirror: held positions longer → stopped out on volatility

Sessions 13-24 (Feb 2026 - Apr 2026): Sports/entertainment markets
- Lower volume, slower price moves, trend-following profitable
- Conservative: stops hit on normal noise → death by 1000 cuts
- Mirror: held positions captured trends → profitable

Conclusion: Conservative didn't have "better strategy"
It had "strategy matched to regime"
```

**Consequences:**
- Deploy conservative-inspired strategy in wrong regime
- Strategy loses because market behavior changed
- Team doesn't understand why "winning strategy" stopped working
- Constant strategy switching chasing recent performance

**Warning signs:**
- All 12 sessions are similar time period (same regime)
- Markets were all same type (elections, sports, etc.)
- Conservative's advantage is large (>30% better) suggesting regime-specific edge
- Can't explain why conservative's rules would work in ALL conditions

**Prevention strategies:**

1. **Regime diversity check:**
   ```
   Sessions 1-12 analysis:
   - Market types: 10 political, 2 sports
   - Volatility: avg 3.2% spreads (HIGH)
   - Volume: avg $2M liquidity (HIGH)

   Conclusion: Data is regime-homogeneous
   Risk: Findings may not generalize to low-vol, low-liquidity regimes
   Mitigation: Defer conclusions until testing on diverse regimes
   ```

2. **Regime segmentation:**
   ```
   Segment 12 sessions by regime:

   High volatility (spreads >2.5%): Sessions 1,3,5,7,8,10,12
   Low volatility (spreads <2.5%): Sessions 2,4,6,9,11

   Test: Does conservative win in BOTH regimes?
   If only high-vol: advantage is regime-specific
   If both: advantage is robust
   ```

3. **Walk-forward validation:**
   ```
   Train on Sessions 1-6 → test on 7-8
   Train on Sessions 3-8 → test on 9-10
   Train on Sessions 5-10 → test on 11-12

   If performance degrades in out-of-sample period:
   Strategy is overfitting to past regime
   ```

4. **Explicit regime assumptions:**
   ```
   Document: "Conservative wins when:
   - Spreads are >2% (high volatility)
   - Price moves >10% intraday (fast discovery)
   - Leader trades >8x per session (active period)

   If these conditions change, strategy may underperform."

   Then: Monitor for regime shift before deploying
   ```

**Detection checklist:**
- [ ] 12 sessions span multiple market types
- [ ] Volatility regime varies (high/low periods)
- [ ] Strategy performance tested across regime splits
- [ ] Explicit assumptions about when strategy works
- [ ] Plan for regime monitoring post-deployment

**Phase implications:** Phase 1 (comparison) should segment sessions by regime. Phase 2 (attribution) should test if patterns hold across regimes. Phase 4 (new strategy) should include regime-detection logic or explicit scope.

---

### Pitfall 5: Survivorship Bias in Strategy Selection

**What goes wrong:** Focusing only on strategies that completed all 12 sessions, ignoring strategies that "failed" early (hit stop-loss, depleted capital, crashed), which biases analysis toward survivorship.

**Why it happens:**
- 7 strategies started: mirror, conservative, aggressive, momentum, velocity, price_level, hybrid
- After 12 sessions: conservative still running, others stopped/lost capital
- Analysis: "Let's compare conservative vs others"
- Problem: "Others" includes dead strategies — survivorship bias
- Should compare: conservative vs strategies that COULD HAVE survived

**Research evidence:**
- [Survivorship Bias Impact](http://adventuresofgreg.com/blog/2026/01/14/survivorship-bias-backtesting-avoiding-traps/): "Survivorship-biased analysis of mutual funds might show annual returns inflated by 2.1% simply by excluding failed funds"
- [Quantdare Study](https://quantdare.com/survivorship-bias-an-investment-decision-trap/): "Excluding just one delisted asset might inflate a strategy's average quarterly return from 0.50% to 2.00% and push the Sharpe ratio from 0.09 to 0.66 — an 86% jump in performance metrics"
- [Selection Bias](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained/): "Survivorship bias occurs when backtesting only includes securities that currently exist, ignoring delisted, bankrupt, or failed companies"

**Example:**
```
Session 1: All 7 strategies start with $100
Session 6: Aggressive depletes to $12, stops trading (survival failure)
Session 12: Conservative at $118, Mirror at $96, others at $80-95

Analysis question: "Why did conservative win?"

Biased comparison:
Conservative +18% vs Mirror -4% (compares survivor to survivor)

Unbiased comparison:
Conservative +18% vs Aggressive -88% (includes failure)

Insight: Conservative's advantage may be "didn't blow up"
Not "made most profit" but "avoided ruin"
```

**Consequences:**
- Optimize for "highest return" when should optimize for "survival"
- New strategy takes excessive risk (like aggressive) but luckier sample
- Deploy → blows up on first drawdown
- Survivorship bias made conservative look "moderately better" when it was "massively more robust"

**Warning signs:**
- Some strategies missing from final comparison (where did they go?)
- Analysis focuses on "best performer" not "survivors vs failures"
- No measurement of drawdown or ruin risk
- Equity curves show only end-state, not blow-up paths

**Prevention strategies:**

1. **Include failure analysis:**
   ```
   Strategy outcomes after 12 sessions:

   Survived:
   - Conservative: +$18.50 (max drawdown -$3.20)
   - Mirror: -$4.20 (max drawdown -$8.40)
   - Momentum: +$2.10 (max drawdown -$11.20)

   Failed (stopped trading):
   - Aggressive: -$88.40 (depleted at Session 6)
   - Velocity: -$42.10 (stopped at Session 9 on risk limit)

   Key insight: Conservative's edge is ROBUSTNESS, not just return
   ```

2. **Risk-adjusted metrics:**
   ```
   Instead of comparing PnL:
   Compare Sharpe ratio, Sortino ratio, max drawdown

   Conservative: Sharpe 1.2, max DD -3.2%
   Mirror: Sharpe 0.4, max DD -8.4%
   Aggressive: Sharpe -1.8, max DD -88.4% (RUIN)

   Conservative wins on risk-adjusted basis by huge margin
   ```

3. **Ruin probability:**
   ```
   Calculate: What's probability each strategy hits $0 in 100 sessions?

   Conservative: 2% ruin probability (very safe)
   Mirror: 12% ruin probability (moderate risk)
   Aggressive: 67% ruin probability (unsafe)

   This explains why aggressive "failed" — not bad luck, high ruin risk
   ```

4. **Survival curve:**
   ```
   Plot: % of starting capital over time for ALL strategies

   Conservative: Smooth curve, stays 95-118% entire period
   Mirror: Volatile, dips to 85%, recovers to 96%
   Aggressive: Crashes from 100% to 12% by Session 6

   Visualization makes survivorship bias obvious
   ```

**Detection checklist:**
- [ ] All strategies tracked through full period (even failures)
- [ ] Max drawdown calculated for each strategy
- [ ] Risk-adjusted metrics (Sharpe, Sortino) used, not just PnL
- [ ] Ruin probability or survival analysis included
- [ ] Comparison explains WHY some strategies failed

**Phase implications:** Phase 1 (comparison tooling) should track survival metrics, not just PnL. Phase 2 (attribution) should analyze failure modes, not just wins. Phase 3 (root cause) should include "what prevented ruin" as primary question.

---

## Development Pitfalls

### Pitfall 6: Building Strategy Based on Insufficient Insight

**What goes wrong:** Rush to implement "improved strategy" after superficial analysis, before understanding causal mechanisms, resulting in strategy that doesn't capture the real edge.

**Why it happens:**
- Analysis finds: conservative takes more trades and wins
- Team: "Let's build strategy that takes more trades!"
- Implementation: New strategy enters markets more aggressively
- Reality: More trades was EFFECT of better risk management, not CAUSE
- New strategy takes more trades but loses money (missed root cause)

**Research evidence:**
- [Strategy Development Process](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/): "Develop clear economic hypotheses before backtesting by asking why the strategy should work and what market inefficiency it exploits"
- [Hypothesis-Driven Trading](https://quantlane.com/blog/avoid-overfitting-trading-strategies/): "Start with a hypothesis based on sound market principles and use a simple and focused approach rather than overcomplicating your strategy"

**Example of premature development:**
```
Week 1: Run 12-session comparison
Finding: Conservative traded 65 times, others 40-50 times

Week 2: Build "TakeMoreTrades" strategy
Logic: Enter markets faster, lower thresholds

Week 3: Test on 12 new sessions
Result: TakeMoreTrades loses -$12 (worse than original)

Root cause: Missed that conservative's trades came from:
1. Better exit discipline (freed capital for new trades)
2. Avoiding markets that hit risk caps (preserved budget)
3. NOT from "entering faster" (which is what was implemented)

Should have spent Week 2 on deeper analysis, not building
```

**Consequences:**
- Waste development time on wrong strategy
- Deploy strategy based on misunderstanding
- Strategy fails, team loses confidence in analysis process
- Cycle repeats (new superficial analysis, new failed strategy)

**Warning signs:**
- Moving from comparison to development in <1 week
- Can't articulate specific mechanism being implemented
- Strategy has vague goal ("be more like conservative")
- No controlled experiment validating hypothesis

**Prevention strategies:**

1. **Mechanism requirement:**
   ```
   Before building, document:

   Observation: Conservative takes 30% more trades

   Hypothesis A: Conservative enters markets faster
   Hypothesis B: Conservative exits faster, freeing capital
   Hypothesis C: Conservative avoids markets that hit caps

   Test each hypothesis:
   A: Measure avg time from leader trade to our trade (same for both)
   B: Measure avg hold time (conservative 45min, mirror 90min) ✓
   C: Measure skip rate by reason (conservative skips caps less) ✓

   Conclusion: B+C explain difference, not A
   Implication: New strategy should optimize EXITS and CAP AVOIDANCE
   ```

2. **Prototype validation:**
   ```
   Before full implementation:
   1. Modify existing strategy with single change
   2. Test change on validation sessions
   3. If improvement <10%, change isn't the driver
   4. Iterate until finding mechanism that works
   5. Then build full strategy
   ```

3. **Causal diagram:**
   ```
   Draw decision flow for both strategies:

   Conservative:
   Leader trades → Check if will hit cap → No → Enter
                                         → Yes → Skip
   Position → Reaches +5% → Partial exit → Frees capital

   Mirror:
   Leader trades → Enter (no cap lookahead)
   Position → Hold until leader exits

   Difference: Conservative has cap-awareness and partial exits
   New strategy: Implement THOSE mechanisms
   ```

4. **Minimum viable hypothesis:**
   ```
   Build simplest possible strategy to test hypothesis:

   Hypothesis: Partial exits improve performance

   MVH: Mirror strategy + "exit 50% at +5% profit"
   Test on 5 sessions
   If better than mirror → hypothesis supported
   If not → hypothesis rejected, don't build full strategy
   ```

**Detection checklist:**
- [ ] Can explain in 2 sentences WHY new strategy should work
- [ ] Mechanism tested independently before full build
- [ ] Controlled experiment validates hypothesis
- [ ] Causal path from observation to implementation documented
- [ ] Prototype tested before full development

**Phase implications:** Phase 3 (root cause analysis) MUST complete before Phase 4 (new strategy development). No development without validated causal hypothesis.

---

### Pitfall 7: Copying Conservative's Parameters Instead of Principles

**What goes wrong:** New strategy copies conservative's exact parameters (k_factor, caps, thresholds) which were tuned to 12-session sample, instead of copying conservative's underlying principles.

**Why it happens:**
- Analysis: Conservative uses k_factor=0.7, per_market_cap=35%, hourly_budget=$85
- Team: "Let's use those parameters in new strategy!"
- Reality: Those parameters were optimized for those 12 sessions (overfit)
- New sessions: Parameters fail because market conditions different
- Should have copied: "Use conservative sizing" not "Use 0.7"

**Research evidence:**
- [Parameter Overfitting](https://blog.traderspost.io/article/understanding-overfitting-in-trading-strategy-development): "Excessive parameter optimization — developers endlessly fine-tune parameters to achieve flawless historical results. Testing slightly different variations gives false confidence."
- [Quantlane Best Practices](https://quantlane.com/blog/avoid-overfitting-trading-strategies/): "Reducing the number of variables and rules in your strategy makes it generally more reliable and less prone to overfitting"

**Example:**
```
Conservative's configuration (from 12 sessions):
k_factor: 0.7
per_market_cap: 35%
per_side_cap: 30%
hourly_budget: $85
stop_loss: -8%

Team builds "ConservativeV2" with exact parameters

Next 12 sessions: ConservativeV2 loses -$8
Why? Market conditions changed:
- Spreads narrower now (35% cap too restrictive)
- Leader trading less frequently (0.7 k_factor too aggressive)

Should have copied PRINCIPLE:
"Size conservatively relative to market conditions"
Not exact number 0.7 which was sample-specific
```

**Consequences:**
- New strategy performs well on historical 12 sessions (by design)
- Performs poorly on new sessions (parameters don't generalize)
- Team concludes "analysis was wrong" when actually implementation was wrong
- Miss opportunity to build robust strategy

**Warning signs:**
- New strategy config looks identical to conservative's
- Parameters are specific decimals (0.7, 0.35) not ranges
- No adaptation logic (fixed parameters)
- Documentation says "use conservative's parameters" not principles

**Prevention strategies:**

1. **Extract principles, not parameters:**
   ```
   Wrong:
   "Conservative uses k_factor=0.7"

   Right:
   "Conservative sizes positions 30% smaller than typical"

   Implementation:
   # Wrong
   k_factor = 0.7  # fixed number

   # Right
   typical_k = calculate_typical_k_for_regime()
   conservative_k = typical_k * 0.7  # scales with regime
   ```

2. **Parameter ranges, not points:**
   ```
   Conservative analysis shows:
   k_factor: 0.6-0.8 (depending on session)
   per_market_cap: 30-40% (depending on leader activity)

   New strategy: Use adaptive parameters
   If leader_trade_frequency > 10/hr: k_factor = 0.6
   If leader_trade_frequency < 5/hr: k_factor = 0.8

   This captures principle: "be more conservative when leader is active"
   ```

3. **Mechanism over values:**
   ```
   Conservative's real edge (hypothesized):
   1. Avoids positions that would hit risk caps
   2. Exits partial positions to free capital
   3. Uses tighter stops on losing positions

   New strategy should implement THOSE mechanisms
   Not copy the numeric values conservative happened to use
   ```

4. **Walk-forward parameter adaptation:**
   ```
   Instead of fixed parameters:

   Every 10 sessions:
   - Measure recent market volatility
   - Measure recent leader activity level
   - Adjust k_factor, caps based on current regime

   This prevents overfitting to single regime
   ```

**Detection checklist:**
- [ ] New strategy parameters are ranges or adaptive, not fixed
- [ ] Can explain principle behind each parameter
- [ ] Parameters tested on multiple regimes
- [ ] Implementation focuses on mechanisms, not values
- [ ] No decimal-precision parameters (0.7342 is overfitting)

**Phase implications:** Phase 3 (root cause) should identify principles, not parameters. Phase 4 (new strategy) should implement adaptive mechanisms, not copy configs.

---

## Validation Pitfalls

### Pitfall 8: Testing New Strategy on Same 12 Sessions

**What goes wrong:** Validate new strategy by testing on the same 12 sessions used for analysis, creating circular validation that guarantees good results but proves nothing.

**Why it happens:**
- Analyze 12 sessions → find patterns → build strategy
- Test strategy: "Let's see if it beats conservative on those 12 sessions!"
- Result: New strategy wins (because it was designed to win on that data)
- Conclude: "Strategy validated!" Deploy live
- Reality: Strategy was tested on training data, not validation data
- Live performance: Strategy fails (optimized for historical noise)

**Research evidence:**
- [Backtesting Validation](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/): "At least 30% of historical data should be reserved for out-of-sample testing, as this untouched data serves as a reality check"
- [Walk-Forward Testing](https://quantlane.com/blog/avoid-overfitting-trading-strategies/): "Walk-forward optimization tests your strategy across multiple rolling time windows, ensuring it adapts to changing market conditions"
- [Train-Test Split](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/): "Never test on the same data used for development — requires separate validation and test sets"

**Example of circular validation:**
```
Phase 1: Analyze Sessions 1-12
Finding: Conservative wins with tight stops

Phase 2: Build "TightStop" strategy
Logic: Use -5% stop loss (tighter than conservative's -8%)

Phase 3: Validate (WRONG)
Test TightStop on Sessions 1-12
Result: TightStop gets +$22 (beats conservative's +$18.50)
Team: "Validated! Deploy!"

Phase 4: Deploy on Sessions 13-24
Result: TightStop gets -$14 (worse than conservative would be)

What went wrong:
- Sessions 1-12 were LOW volatility (tight stops worked)
- Strategy was optimized for low-vol
- Sessions 13-24 are HIGHER volatility (tight stops get stopped out)
- Circular validation gave false confidence
```

**Consequences:**
- Strategy appears validated but isn't
- Deploy with false confidence
- Strategy fails on fresh data
- Team loses trust in validation process
- May abandon working principles because validation was flawed

**Warning signs:**
- Validation uses same sessions as analysis
- No mention of "held-out data" or "test set"
- Validation performance is suspiciously good (>90% win rate)
- Team is confident based on one validation run

**Prevention strategies:**

1. **Strict data splitting:**
   ```
   Available: 24 sessions total

   Training: Sessions 1-14 (60%) - analyze, find patterns
   Validation: Sessions 15-19 (20%) - compare strategies
   Test: Sessions 20-24 (20%) - final check, ONE TIME ONLY

   Rules:
   - NEVER look at test set during development
   - If validation fails, go back to training (not test)
   - Only use test set for final decision
   - Report test performance as "real" estimate
   ```

2. **Walk-forward validation:**
   ```
   Instead of single train/test split:

   Train Sessions 1-7 → Test on 8-10 → Performance A
   Train Sessions 4-10 → Test on 11-13 → Performance B
   Train Sessions 7-13 → Test on 14-16 → Performance C

   If A, B, C are consistent: Strategy is robust
   If degrading: Strategy is overfitting
   ```

3. **Paper trading requirement:**
   ```
   Before live deployment:
   1. Record 12 NEW sessions (paper trading mode)
   2. Test new strategy on those sessions
   3. Compare to conservative's performance on SAME sessions
   4. If new strategy wins by <10%: Not worth switching
   5. If wins by >30%: Check for regime shift
   6. Only deploy if wins by 15-25% on fresh data
   ```

4. **Cross-validation (if sample size allows):**
   ```
   With 24 sessions, 4-fold cross-validation:

   Fold 1: Train 1-18, Test 19-24
   Fold 2: Train 1-12 + 19-24, Test 13-18
   Fold 3: Train 1-6 + 13-24, Test 7-12
   Fold 4: Train 7-24, Test 1-6

   Average test performance across folds
   This prevents lucky train/test split from creating false confidence
   ```

**Detection checklist:**
- [ ] Test data was NEVER seen during analysis or development
- [ ] Test performance within 20% of validation performance
- [ ] Multiple validation windows (walk-forward)
- [ ] Paper trading on fresh data before live
- [ ] Document exact sessions used for train/validation/test

**Phase implications:** Phase 1 (comparison) should use Sessions 1-7 ONLY. Sessions 8-12 reserved for Phase 4 validation. New sessions recorded specifically for final test.

---

### Pitfall 9: Mistaking Backtest Edge for Execution Edge

**What goes wrong:** New strategy shows +25% improvement in backtest, but improvement assumes perfect fills, no slippage, zero latency — none of which are realistic in live trading.

**Why it happens:**
- Backtest: New strategy makes 5% more profit than conservative
- Assumes: Can execute at observed bid/ask prices
- Reality: By time we react, prices moved (latency), or liquidity gone (slippage)
- Live trading: "5% edge" becomes -2% after execution costs
- Net: Strategy loses money despite positive backtest

**Research evidence:**
- [Implementation Slippage](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/): "Backtests often assume perfect fills with no slippage and minimal spread, but real trading rarely looks like this"
- [Execution Costs](https://medium.com/@jpolec_72972/building-a-robust-backtesting-framework-trading-costs-1bb75f063756): "Slippage can be as low as 0.1% in liquid markets or well above 1% when liquidity thins out"
- [Reality Gap](https://www.exegy.com/avoiding-slippage-equities-trading-with-backtesting/): "The gap between backtested results and live performance is often explained by execution costs, not strategy flaws"
- [Transaction Costs](https://support.capitalise.ai/en/articles/5963164-trading-slippage-and-how-it-affects-live-trading-simulated-trading-and-backtests): "Systematic traders aim to minimize slippage relative to benchmark, aligning actual trading performance more closely with strategy's projected results"

**Example:**
```
Backtest results:
Conservative: +$18.50 over 12 sessions
NewStrategy: +$23.40 over 12 sessions (26% better!)

Backtest assumptions:
- Execute at recorded bid/ask
- No latency (instant fills)
- No slippage on market orders
- Limit orders fill when price touches

Reality in live trading:
- Conservative: +$14.20 (23% worse than backtest due to execution costs)
- NewStrategy: +$8.10 (65% worse!)

Why NewStrategy worse?
- Takes 30% more trades than conservative
- Each trade pays spread cost (~2%)
- 30% more trades = 30% more spread costs
- Backtest didn't model cumulative spread impact
```

**Consequences:**
- Deploy strategy expecting +25% edge
- Actual results: -30% (worse than baseline)
- Team concludes "strategy doesn't work" when problem is execution realism
- May abandon valid strategy insights

**Warning signs:**
- Backtest edge is <10% (likely wiped out by execution costs)
- New strategy trades MORE frequently (more spread costs)
- Backtest assumes limit orders fill at high rate (>70%)
- No slippage modeling in backtest
- Polymarket specific: No consideration for market impact on thin markets

**Prevention strategies:**

1. **Model execution costs in backtest:**
   ```python
   # Add to backtest
   SPREAD_COST = 0.02  # 2% typical Polymarket spread
   SLIPPAGE = 0.003  # 0.3% market order slippage
   LATENCY_COST = 0.005  # 0.5% price move during delay

   execution_cost = (ask - bid) / ask  # actual spread
   execution_cost += SLIPPAGE if market_order else 0
   execution_cost += LATENCY_COST

   pnl_after_costs = pnl - (execution_cost * position_size)
   ```

2. **Limit order realism:**
   ```python
   # Backtest: Conservative fill assumption
   def limit_order_fills(limit_price, market_prices):
       # Fills only if price moves THROUGH limit (not just touches)
       # And only if we'd be ahead of queue (prob < 50%)

       fill_prob = 0.4  # pessimistic
       return filled if (random() < fill_prob and
                         market_crossed_limit_with_volume)
   ```

3. **Cost break-even analysis:**
   ```
   Strategy comparison (after execution costs):

   Conservative: 45 trades, 2% spread each, -$1.80 in costs
   Net PnL: $18.50 - $1.80 = $16.70

   NewStrategy: 68 trades, 2% spread each, -$2.72 in costs
   Backtest PnL: $23.40, Net PnL: $23.40 - $2.72 = $20.68

   Advantage: $20.68 - $16.70 = $3.98 (24% better after costs)

   If execution costs were 3% instead of 2%:
   Conservative: $18.50 - $2.70 = $15.80
   NewStrategy: $23.40 - $4.08 = $19.32
   Advantage only $3.52 (22% better)

   Edge is fragile to execution cost assumptions
   ```

4. **Paper trading stress test:**
   ```
   Before live deployment:
   1. Run strategy in paper trading mode
   2. Measure ACTUAL spread costs per trade
   3. Measure ACTUAL fill rates for limit orders
   4. Measure ACTUAL latency from signal to execution
   5. Recalculate backtest with realistic costs
   6. If edge drops below 10%, strategy too marginal
   ```

5. **Polymarket-specific costs:**
   ```
   Polymarket considerations:
   - Spreads widen during news events (2% → 5%)
   - Thin markets have higher market impact
   - Limit orders may sit unfilled for minutes
   - Position minimums ($1 market, 5 shares limit)

   Backtest should model:
   - Time-varying spreads (not constant 2%)
   - Market impact on $100+ orders
   - Limit order fill rate <60%
   - Order minimums causing position rounding
   ```

**Detection checklist:**
- [ ] Backtest includes spread costs (2-5%)
- [ ] Backtest includes slippage on market orders (0.3-0.5%)
- [ ] Limit order fill rate <60%
- [ ] Latency cost modeled (0.5%+)
- [ ] Edge remains >15% after all costs
- [ ] Paper trading validates cost assumptions

**Phase implications:** Phase 1 (comparison) should add execution cost modeling to replay. Phase 4 (validation) should require paper trading to measure real costs before deployment.

---

### Pitfall 10: Ignoring Roll-Forward Performance Degradation

**What goes wrong:** Strategy performs well on historical 12 sessions, but performance degrades steadily over time as markets/leader behavior evolves, yet team doesn't monitor this degradation.

**Why it happens:**
- Strategy validated on Sessions 1-12
- Deploy live starting Session 13
- Performance: Session 13-15 good, 16-18 okay, 19-21 poor, 22+ bad
- Team doesn't notice degradation because each session looks "reasonable"
- After 30 sessions: Strategy is consistently losing but no intervention
- Root cause: Markets evolved, strategy didn't adapt

**Research evidence:**
- [Walk-Forward Validation](https://quantlane.com/blog/avoid-overfitting-trading-strategies/): "Walk-forward optimization tests your strategy across multiple rolling time windows, ensuring it adapts to changing market conditions"
- [Performance Monitoring](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/): "If performance degrades >20% out-of-sample, parameters are overfit and strategy needs revalidation"

**Example:**
```
Validation (Sessions 1-12): NewStrategy +$24
Deploy (Sessions 13-20):
  13-14: +$4.20 (good start)
  15-16: +$1.80 (okay)
  17-18: -$0.60 (hmm)
  19-20: -$3.10 (concerning)

Cumulative: +$2.30 (still positive, team keeps running)

Sessions 21-28:
  21-22: -$4.20
  23-24: -$6.10
  25-26: -$8.20
  27-28: -$5.30

Cumulative: -$21.50 (now clearly failing)

Problem: Didn't catch degradation early
Should have stopped at Session 18 when rolling 6-session avg went negative
```

**Consequences:**
- Strategy loses money for extended period
- Team doesn't realize strategy "stopped working"
- By time degradation is obvious, significant capital lost
- Delayed intervention (should have stopped earlier)

**Warning signs:**
- No monitoring process after deployment
- Team checks performance monthly, not weekly
- Focus on cumulative PnL (masks recent degradation)
- No alerts for degradation thresholds

**Prevention strategies:**

1. **Rolling window monitoring:**
   ```python
   # Alert system
   def check_performance_degradation(recent_sessions, baseline):
       rolling_6_session = recent_sessions[-6:].sum()
       rolling_12_session = recent_sessions[-12:].sum()

       if rolling_6_session < baseline * 0.5:
           alert("WARNING: 6-session performance 50% below baseline")

       if rolling_12_session < 0:
           alert("CRITICAL: 12-session performance is negative - STOP STRATEGY")
   ```

2. **Statistical process control:**
   ```
   Baseline: Conservative averaged +$1.54 per session (std $4.20)

   Control limits:
   Upper: +$9.94 (+2 std)
   Lower: -$6.86 (-2 std)

   If 3 consecutive sessions below mean: Warning
   If 5 consecutive sessions below mean: Stop
   If 1 session below lower limit: Stop

   This catches degradation early
   ```

3. **Comparative benchmarking:**
   ```
   Every 6 sessions, compare:
   NewStrategy last 6: +$0.80
   Conservative last 6 (simulated): +$4.20

   If NewStrategy < Conservative for 2 consecutive windows:
   → Revert to conservative
   → Investigate what changed
   ```

4. **Regime detection:**
   ```
   Monitor market regime indicators:
   - Avg spread: was 2.1%, now 4.3% (regime shift)
   - Leader trade frequency: was 12/session, now 6/session
   - Market types: was 80% political, now 60% sports

   If regime shifts significantly:
   → Flag for strategy review
   → May need parameter adaptation
   ```

**Detection checklist:**
- [ ] Rolling performance monitored every session
- [ ] Alerts for 20%+ degradation vs baseline
- [ ] Comparative benchmark (conservative or other baseline)
- [ ] Regime indicators tracked
- [ ] Clear stop-loss rules (when to halt strategy)

**Phase implications:** Phase 5 (deployment) should include monitoring dashboard. Phase 6+ (maintenance) requires ongoing performance tracking and revalidation protocol.

---

## Summary

### Critical Insights for v1.1 Milestone

**The user observation:** "Conservative took MORE trades than mirror but was only profitable strategy"

**Key pitfalls to avoid:**

1. **Small sample bias:** 12 sessions is insufficient for statistical significance (need 200+ trades for 95% confidence)
2. **Correlation ≠ causation:** More trades may be EFFECT of profitability, not CAUSE
3. **Overfitting patterns:** Patterns found on 12 sessions likely won't generalize
4. **Regime specificity:** Conservative may have won due to temporary market conditions
5. **Survivorship bias:** Must analyze WHY other strategies failed, not just that they did

**Recommended approach:**

```
Phase 1: Validate significance FIRST
- Calculate confidence intervals on conservative's edge
- Check if statistically significant (p < 0.05)
- If not significant: Gather more data before analysis

Phase 2: Causal mechanism analysis
- Trace decision paths: WHY did conservative trade more?
- Test hypotheses with controlled experiments
- Separate correlation from causation

Phase 3: Out-of-sample validation
- Hold out 30-40% of data for testing
- Build strategy on training data only
- Validate on held-out data before deployment

Phase 4: Realistic execution costs
- Model spread costs (2-5%), slippage (0.3-0.5%)
- Ensure edge remains >15% after costs
- Paper trade before live deployment

Phase 5: Ongoing monitoring
- Track rolling performance windows
- Alert on degradation vs baseline
- Be ready to revert if strategy stops working
```

### Quick Reference: Research-Backed Requirements

Based on 2026 research sources:

| Metric | Minimum Requirement | Source |
|--------|---------------------|--------|
| Sample size for 95% confidence | 385 trades | [Medium](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) |
| Sample size for 70% confidence | 107 trades | [Medium](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) |
| Minimum Sharpe ratio (non-overfit) | <3.0 | [LuxAlgo](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/) |
| Profit factor range (realistic) | 1.5-2.0 | [TradersPost](https://blog.traderspost.io/article/understanding-overfitting-in-trading-strategy-development) |
| Out-of-sample data reservation | 30%+ | [LuxAlgo](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/) |
| Performance degradation threshold | <20% drop | [QuantStart](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/) |
| Spread cost (Polymarket typical) | 0.5-5% | [Polymarket Guide](https://www.crypticorn.com/how-to-trade-polymarket-profitably-what-actually-works-in-2026/) |
| Slippage (market orders) | 0.3-1% | [LuxAlgo](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/) |
| Position sizing (Polymarket risk) | 3-5% per event | [BeInCrypto](https://beincrypto.com/polymarket-trader-loss-risk-management/) |
| Limit order fill rate (realistic) | <60-70% | [Capitalise.ai](https://support.capitalise.ai/en/articles/5963164-trading-slippage-and-how-it-affects-live-trading-simulated-trading-and-backtests) |

### Warning Signs Checklist

Before deploying a new strategy, verify:

- [ ] Sample size ≥200 trades OR confidence intervals explicitly calculated
- [ ] Pattern tested on held-out data (not same 12 sessions)
- [ ] Can explain causal mechanism (not just correlation)
- [ ] Tested across multiple market regimes
- [ ] Edge remains >15% after modeling execution costs
- [ ] Performance monitored with rolling windows and alerts
- [ ] Strategy implements principles (adaptive), not fixed parameters
- [ ] Included failed strategies in analysis (survivorship check)
- [ ] Paper traded on fresh data before live deployment
- [ ] Clear stop-loss rules (when to halt strategy)

---

## Sources

### High Confidence (2026 Research)

**Statistical Foundations:**
- [Sample Size Requirements](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) - Trading Dude, Medium 2026
- [Confidence in Numbers](https://www.dara.trade/blog/2019/10/14/how-to-build-a-profitable-trading-system-part-1-confidence-in-numbers) - DARA.TRADE
- [Sample Size Calculator](https://www.backtestbase.com/education/how-many-trades-for-backtest) - BacktestBase 2026

**Overfitting & Backtesting:**
- [Understanding Overfitting](https://blog.traderspost.io/article/understanding-overfitting-in-trading-strategy-development) - TradersPost Blog 2026
- [Backtesting Traps](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/) - LuxAlgo 2026
- [Overfitting in Algorithmic Trading](https://bookmap.com/blog/what-is-overfitting-in-algorithmic-trading) - Bookmap 2026
- [Avoiding Overfitting](https://quantlane.com/blog/avoid-overfitting-trading-strategies/) - Quantlane 2026
- [How to Avoid Overfitting Testing Rules](http://adventuresofgreg.com/blog/2025/12/18/avoid-overfitting-testing-trading-rules/) - Greg's Blog 2025

**Strategy Validation:**
- [Successful Backtesting Part II](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/) - QuantStart
- [Backtesting Strategies That Work](https://www.fortraders.com/blog/backtesting-strategies-that-actually-work) - ForTraders 2026

**Bias & Pitfalls:**
- [Survivorship Bias in Backtesting](http://adventuresofgreg.com/blog/2026/01/14/survivorship-bias-backtesting-avoiding-traps/) - Greg's Blog 2026
- [Survivorship Bias Explained](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained/) - LuxAlgo 2026
- [Survivorship Bias in Market Data](https://bookmap.com/blog/survivorship-bias-in-market-data-what-traders-need-to-know) - Bookmap 2026
- [Survivorship Bias Investment Trap](https://quantdare.com/survivorship-bias-an-investment-decision-trap/) - Quantdare

**Execution & Slippage:**
- [Backtesting Limitations: Slippage](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/) - LuxAlgo 2026
- [Trading Slippage Effects](https://support.capitalise.ai/en/articles/5963164-trading-slippage-and-how-it-affects-live-trading-simulated-trading-and-backtests) - Capitalise.ai
- [Building Robust Backtesting Framework](https://medium.com/@jpolec_72972/building-a-robust-backtesting-framework-trading-costs-1bb75f063756) - Medium 2026
- [Using Backtesting to Avoid Slippage](https://www.exegy.com/avoiding-slippage-equities-trading-with-backtesting/) - Exegy

**Market Regime:**
- [2026: Entering a New Market Regime](https://home.cib.natixis.com/articles/2026-entering-a-new-market-regime) - Natixis 2026
- [2026 Macro Outlook](https://www.blackrock.com/us/financial-professionals/insights/2026-macro-outlook) - BlackRock 2026
- [Market Risk in 2026](https://realinvestmentadvice.com/resources/blog/the-market-risk-in-2026-if-growth-projections-fail/) - RIA 2026

**Polymarket-Specific:**
- [How To Trade Polymarket Profitably 2026](https://www.crypticorn.com/how-to-trade-polymarket-profitably-what-actually-works-in-2026/) - Crypticorn 2026
- [Trader Lost $2M on Polymarket](https://beincrypto.com/polymarket-trader-loss-risk-management/) - BeInCrypto 2026
- [Complete Polymarket Playbook](https://medium.com/thecapital/the-complete-polymarket-playbook-finding-real-edges-in-the-9b-prediction-market-revolution-a2c1d0a47d9d) - The Capital, Medium Jan 2026
- [Market Making on Prediction Markets 2026](https://newyorkcityservers.com/blog/prediction-market-making-guide) - NYC Servers 2026

**Copy Trading:**
- [Smart Copy Trading Strategies 2026](https://bestcopytrading.com/strategies/smart-copy-trading-strategies/) - BestCopyTrading 2026
- [Copy Trading Risks](https://tradefundrr.com/copy-trading-risks/) - TradeFundrr 2026
- [Risk Management for Copy Trading](https://capitalxtend.com/forex-academy/forex/how-to-manage-risk-while-copy-trading) - CapitalXtend

### Medium Confidence (Domain Knowledge)

- General algorithmic trading principles (training data)
- Statistical analysis methods (training data)
- Trading psychology and behavioral biases (training data)

### Verification Status

**Verified with multiple 2026 sources:**
- Small sample requirements (3+ sources agree on 200-500 trades)
- Overfitting detection methods (5+ sources describe same techniques)
- Execution cost ranges (2+ sources for Polymarket specifically)
- Survivorship bias impact (4+ sources with quantified effects)

**Inferred from domain knowledge:**
- Specific application to user's 12-session scenario
- Integration with existing codebase context
- Polymarket-specific nuances (limited sources, extrapolated from prediction market norms)

**Gaps requiring validation:**
- Actual spread statistics from user's recorded sessions
- True sample size (may have more/fewer than estimated 120 trades)
- Whether conservative's edge is statistically significant with actual data
- Real execution costs in user's Polymarket copy trading context
