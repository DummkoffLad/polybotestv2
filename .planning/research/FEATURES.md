# Feature Landscape: Strategy Debugging & Comparison Tools

**Domain:** Trading strategy analysis and comparison
**Researched:** 2026-02-03
**Confidence:** HIGH (verified with 2026 industry sources)

## Executive Summary

Professional trading strategy debugging tools focus on three core capabilities:
1. **Trade-level forensics** - Understanding WHY individual trades won/lost
2. **Attribution analysis** - Decomposing performance into selection vs sizing vs timing
3. **Comparative visualization** - Side-by-side equity curves and metrics

Based on the user's specific problem (conservative strategy profitable, others not), the feature set must answer:
- Which individual trades caused losses?
- What's different about conservative's trade selection/sizing?
- Where did other strategies fail (sizing? timing? selection?)?

## Table Stakes

Features users expect. Missing = product feels incomplete.

| Feature | Why Expected | Complexity | Implementation Notes |
|---------|--------------|------------|---------------------|
| **Side-by-side equity curves** | Visual comparison is foundation of strategy analysis | Low | Overlay multiple strategies on same chart, align by time or trade number |
| **Performance metrics comparison** | Standard tearsheet stats (Sharpe, win rate, drawdown, profit factor) | Low | Already have session replay with PnL - extend to comparative view |
| **Trade-by-trade listing** | Need to see individual trades to debug | Low | CSV/table export with filters, sortable by PnL |
| **Trade filtering by outcome** | Focus on winners vs losers | Low | Filter by PnL > 0 or < 0, sort by magnitude |
| **Strategy parameter display** | Know what settings each strategy used | Low | Show Kelly fraction, quality threshold, sizing rules per strategy |
| **Profit factor calculation** | Industry standard metric (total profit / total loss) | Low | Single calculation, critical for comparing strategies |
| **Maximum drawdown** | Risk assessment essential | Low | Already tracking equity curve - find peak-to-trough |
| **Win rate %** | Table stakes metric | Low | Count(winning trades) / Count(total trades) |
| **Average win vs average loss** | Understand risk/reward profile | Low | Mean PnL of winners vs mean PnL of losers |
| **Trade count per strategy** | Validate activity levels | Low | Conservative took MORE trades - this metric reveals strategy behavior |

**Sources:**
- [Top 5 Metrics for Evaluating Trading Strategies](https://www.luxalgo.com/blog/top-5-metrics-for-evaluating-trading-strategies/)
- [Trading Performance: Strategy Metrics](https://www.quantifiedstrategies.com/trading-performance/)
- [How to Compare Two Trading Strategies Using Backtest Results](https://www.fxreplay.com/learn/how-to-compare-two-trading-strategies-using-backtest-results)

## Differentiators

Features that set product apart. Advanced analysis capabilities.

| Feature | Value Proposition | Complexity | Implementation Notes |
|---------|-------------------|------------|---------------------|
| **Attribution decomposition** | Answers "WHY conservative won" by breaking down: selection (which trades), sizing (position size), timing (entry/exit) | Medium | Requires simulating counterfactuals - what if strategy A used strategy B's sizing? |
| **Shared trade analysis** | Show trades ALL strategies took vs trades ONLY one strategy took | Medium | Set intersection - reveals trade selection differences |
| **Trade tagging & categorization** | Tag trades by market conditions, setup type, outcome | Medium | Follow TradesViz model - custom tags with AND/OR filtering |
| **Sizing impact analysis** | Isolate performance impact of position sizing vs trade selection | High | Run counterfactual: strategy A's trades with strategy B's sizing logic |
| **Trade timeline visualization** | See when each strategy entered/exited on same timeline | Medium | Gantt-style chart showing overlapping positions |
| **Parameter sensitivity sweep** | "What if conservative used 0.2 Kelly instead of 0.1?" | High | Already have validation pipeline - extend for multi-strategy comparison |
| **Correlation matrix** | Which strategies behave similarly? | Low | Calculate equity curve correlation between all strategy pairs |
| **Market condition segmentation** | Performance breakdown by time of day, volatility regime | Medium | Tag trades by context, calculate metrics per segment |
| **Risk-adjusted returns** | Sharpe ratio, Sortino ratio (downside deviation only) | Low | Sharpe = (return - risk_free_rate) / std_dev |
| **Monte Carlo confidence intervals** | "Is conservative's edge statistically significant?" | High | Resample trades, generate distribution of outcomes |
| **Interactive drill-down** | Click equity curve point, see trades that caused it | Medium | Link visualization to trade detail view |

**Sources:**
- [Performance Attribution Components](https://foolwealth.com/insights/what-does-performance-attribution-tell-you-about-your-portfolio)
- [Trade Tagging in TradesViz](https://www.tradesviz.com/blog/tags-complete-guide/)
- [FX Replay Comparative Features](https://www.fxreplay.com/learn/top-backtesting-features-every-trader-should-look-for-in-2026)
- [Parameter Sensitivity Analysis](https://medium.com/analytics-vidhya/sensitivity-analysis-optimization-part1-4b19b6f40a20)

## Anti-Features

Features to explicitly NOT build. Common mistakes in this domain.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Real-time optimization** | Overfitting trap - "best parameters" change constantly | Show parameter sensitivity, let user decide |
| **Automated strategy selection** | User must understand WHY, not just WHAT | Provide comparison tools, user makes decision |
| **Curve fitting tools** | Encourages overfitting to historical data | Focus on robustness testing, out-of-sample validation |
| **Too many metrics** | Analysis paralysis - 50 metrics obscures key insights | Stick to core metrics, drill down on demand |
| **Prediction/forecasting** | This is DEBUGGING past performance, not predicting future | Clearly label as historical analysis |
| **Social/sharing features** | Scope creep - this is personal analysis tool | Focus on analysis quality |
| **Walk-forward optimization UI** | Complex feature that encourages overfitting | User can manually test different periods |

**Rationale:**
Professional traders emphasize the danger of overfitting and optimization bias. Tools should illuminate WHY strategies performed as they did, not automatically generate "optimal" parameters that work on historical data but fail live.

**Sources:**
- [Robustness Testing Guide](https://www.buildalpha.com/robustness-testing-guide/)
- [Common Strategy Debugging Mistakes](https://www.optionstack.com/debug-your-trading-system/)

## Feature Dependencies

```
Foundation Layer (Build First):
├─ Trade-by-trade data structure
├─ Strategy parameter tracking
└─ Performance metrics calculation

Analysis Layer (Build Second):
├─ Side-by-side equity curves (requires: foundation)
├─ Metrics comparison table (requires: foundation)
├─ Trade filtering (requires: foundation)
└─ Correlation analysis (requires: foundation)

Advanced Layer (Build Third):
├─ Attribution decomposition (requires: shared trade analysis)
├─ Sizing impact analysis (requires: trade simulation engine)
├─ Parameter sensitivity (requires: validation pipeline extension)
└─ Monte Carlo confidence (requires: resampling infrastructure)
```

## MVP Recommendation

For MVP (user's immediate need: "Why did conservative win?"), prioritize:

### Phase 1: Comparison Foundation
1. **Side-by-side equity curves** - Visual comparison baseline
2. **Metrics comparison table** - Sharpe, profit factor, win rate, max drawdown
3. **Trade count per strategy** - Validate "conservative took MORE trades"
4. **Trade-by-trade listing** - Sortable, filterable by strategy and outcome

**Rationale:** These four features answer 80% of "why did conservative win?" by showing:
- Performance trajectory (equity curves)
- Risk/return profile (metrics)
- Activity level (trade count)
- Individual trade outcomes (listing)

### Phase 2: Attribution Analysis
5. **Shared trade analysis** - Which trades did ALL strategies take? Which did ONLY conservative take?
6. **Average position size comparison** - Did conservative size smaller?
7. **Win rate by strategy** - Did conservative avoid bad trades or just size better?

**Rationale:** Decomposes performance into selection (which trades) vs sizing (how much) vs execution (win rate on selected trades).

### Phase 3: Advanced Debugging
8. **Trade tagging** - Tag by market condition, time of day
9. **Sizing impact analysis** - Simulate "what if strategy A used conservative's sizing?"
10. **Parameter sensitivity** - Extend validation pipeline for multi-strategy comparison

**Defer to post-MVP:**
- Monte Carlo confidence intervals (complex, lower ROI for immediate debugging)
- Interactive drill-down UI (nice to have, CSV export sufficient for now)
- Timeline visualization (valuable but not critical for initial debugging)
- Market condition segmentation (useful but requires more data collection)

## Implementation Guidance

### Python Library Ecosystem

**For performance metrics and tearsheets:**
- **QuantStats** (recommended) - Actively maintained, generates HTML reports, works with pandas DataFrames
- **PyFolio** (legacy) - More risk decomposition features but less actively maintained since Quantopian closure

**For attribution analysis:**
- Custom implementation required - no off-the-shelf library for trade-level attribution
- Use pandas for data manipulation, numpy for calculations

**For visualization:**
- matplotlib/seaborn for static charts
- plotly for interactive (if building drill-down features)

**Sources:**
- [QuantStats vs PyFolio Comparison](https://www.slingacademy.com/article/comparing-quantstats-to-other-python-performance-libraries/)
- [Best Python Libraries for Algorithmic Trading](https://tradingbrokers.com/best-python-libraries-for-algorithmic-trading/)
- [Python Trading Tools 2026](https://analyzingalpha.com/python-trading-tools)

### Key Design Principles

**1. Trade-level granularity is essential**
Professional tools (TradeZella, Tradervue) emphasize trade-by-trade analysis with tagging and filtering. Don't just show aggregate metrics.

**2. Comparative view is default**
User has multiple strategies - default UI should show them side-by-side, not require switching between single-strategy views.

**3. Attribution before optimization**
First understand WHY conservative won (attribution), then decide if that insight generalizes (validation). Don't jump to "optimizing" other strategies.

**4. Counterfactual analysis is powerful**
"What if strategy A used strategy B's sizing?" questions reveal whether edge comes from selection or sizing.

## User's Specific Use Case Mapping

Given project context (12-session test, conservative profitable, conservative took MORE trades):

| Question | Features That Answer It |
|----------|------------------------|
| Which individual trades caused losses? | Trade-by-trade listing, sorted by PnL descending |
| What's different about conservative's trade selection? | Shared trade analysis - show trades conservative SKIPPED that others took |
| Where did other strategies fail? | Attribution decomposition - was it sizing? timing? selection? |
| Did conservative size smaller? | Average position size comparison |
| Did conservative have better win rate? | Win rate % comparison |
| Is conservative's edge statistically significant? | Monte Carlo confidence intervals (Phase 3) |

## Confidence Assessment

| Area | Confidence | Verification |
|------|-----------|--------------|
| Table stakes features | HIGH | Verified across multiple 2026 sources (TradingView, FX Replay, professional journals) |
| Attribution methodology | MEDIUM | Professional portfolio attribution well-documented, trade-level attribution less standardized |
| Python libraries | HIGH | QuantStats actively maintained, PyFolio legacy but functional |
| Anti-features rationale | HIGH | Overfitting dangers well-documented in quant literature |

## Summary

**Core insight:** Strategy debugging is fundamentally about DECOMPOSITION - breaking down aggregate performance into components (selection, sizing, timing) to understand causality.

**Key priorities for user's use case:**
1. Side-by-side equity curves (visual baseline)
2. Metrics comparison (quantify differences)
3. Shared trade analysis (identify selection differences)
4. Sizing comparison (isolate sizing impact)

**Critical success factor:** Trade-level granularity. Must track individual trades with full context (entry/exit, size, strategy, outcome) to enable forensic analysis.

**Recommended approach:**
- Phase 1: Build comparison foundation (4 features, ~1-2 days)
- Validate with user: "Does this answer your questions?"
- Phase 2: Add attribution if needed (3 features, ~2-3 days)
- Phase 3: Advanced features only if clear ROI

**Library recommendation:** QuantStats for metrics, custom pandas/numpy for attribution.

---

## Sources Summary

This research synthesized findings from:
- Professional backtesting platforms: [TradingView](https://chartwisehub.com/tradingview-strategy-tester/), [TrendSpider](https://trendspider.com/product/strategy-development-and-backtesting-tools/), [FX Replay](https://www.fxreplay.com/)
- Trading journals: [TradesViz](https://www.tradesviz.com/blog/tags-complete-guide/), [TradeZella](https://www.tradezella.com/backtesting)
- Performance analytics: [QuantStats](https://www.luxalgo.com/blog/top-5-metrics-for-evaluating-trading-strategies/), [LuxAlgo](https://www.quantifiedstrategies.com/trading-performance/)
- Strategy debugging: [OptionStack](https://www.optionstack.com/debug-your-trading-system/), [Build Alpha](https://www.buildalpha.com/robustness-testing-guide/)
- Python ecosystem: [Comparing QuantStats to PyFolio](https://www.slingacademy.com/article/comparing-quantstats-to-other-python-performance-libraries/), [Python Trading Tools 2026](https://analyzingalpha.com/python-trading-tools)

All sources verified for 2026 relevance and cross-referenced for consistency.
