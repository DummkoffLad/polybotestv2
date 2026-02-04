# Stack Research: Strategy Debugging and Comparison

**Domain:** Trading strategy performance analysis and debugging
**Researched:** 2026-02-03
**Confidence:** HIGH

## Executive Summary

Your existing analysis framework (attribution, equity tracking, drawdown, slippage) is **solid and sufficient** for strategy debugging. The gap is **comparison tooling** and **statistical validation**. Recommendation: Add lightweight comparison/statistical libraries, NOT heavyweight backtesting frameworks (you already have replay).

**Key finding:** Conservative's outperformance (more trades, still profitable) suggests selection logic differences. You need side-by-side strategy comparison with statistical significance testing to understand WHY.

## Recommended Additions

### 1. Strategy Comparison & Metrics: QuantStats

| Library | Version | Purpose | Integration |
|---------|---------|---------|-------------|
| quantstats | 0.0.81 | Side-by-side strategy comparison, Sharpe/Sortino/Calmar ratios, tear sheets | Feed replay results as pandas Series, generate HTML comparison reports |

**Why QuantStats:**
- Latest version 0.0.81 released Jan 13, 2026 (actively maintained)
- Three modules perfectly aligned with your needs:
  - `quantstats.stats` - Sharpe, Sortino, win rate, volatility, max drawdown, Calmar ratio
  - `quantstats.plots` - Visual comparison charts (equity curves, rolling metrics, monthly returns)
  - `quantstats.reports` - HTML tearsheets comparing multiple strategies side-by-side
- Works with pandas Series of returns (trivial to convert from your ReplayResult)
- Includes Monte Carlo simulations for risk analysis
- Zero conflict with existing code (analysis layer only)

**Installation:**
```bash
pip install quantstats==0.0.81
```

**Integration pattern:**
```python
# In new comparison module: src/analysis/comparison.py
import quantstats as qs

def compare_strategies(results: Dict[str, ReplayResult]) -> Path:
    """Generate side-by-side comparison HTML report."""
    # Convert each ReplayResult to returns series
    returns_dict = {}
    for strategy_name, result in results.items():
        equity_df = result.analysis['equity_df']
        returns = equity_df['equity'].pct_change()
        returns_dict[strategy_name] = returns

    # Generate comparison tearsheet
    qs.reports.html(returns_dict, output='strategy_comparison.html')
```

**Confidence:** HIGH (official PyPI, actively maintained, exact fit for use case)

### 2. Statistical Significance: SciPy (Already Available)

| Library | Version | Purpose | Integration |
|---------|---------|---------|-------------|
| scipy | Latest (≥1.10) | Hypothesis testing (t-tests, Mann-Whitney U) to validate strategy differences | Add to comparison module |

**Why SciPy:**
- Already in Python stdlib ecosystem (numpy dependency likely present)
- `scipy.stats.ttest_ind()` - Compare PnL distributions between strategies
- `scipy.stats.mannwhitneyu()` - Non-parametric alternative if returns aren't normal
- Validates whether conservative's edge is statistically significant vs noise

**Installation:**
```bash
pip install scipy>=1.10
```

**Integration pattern:**
```python
from scipy.stats import ttest_ind, mannwhitneyu

def test_strategy_significance(strategy_a_pnl: List[Decimal],
                                strategy_b_pnl: List[Decimal]) -> Dict:
    """Test if strategy A significantly outperforms strategy B."""
    t_stat, p_value = ttest_ind(strategy_a_pnl, strategy_b_pnl)
    return {
        'statistically_significant': p_value < 0.05,
        'p_value': p_value,
        't_statistic': t_stat
    }
```

**Confidence:** HIGH (SciPy is the standard library for statistical testing in Python)

### 3. Enhanced Visualization: Plotly (Upgrade from Matplotlib)

| Library | Version | Purpose | Integration |
|---------|---------|---------|-------------|
| plotly | 6.5.2 | Interactive HTML charts with hover details, drill-down into trade failures | Optional upgrade to existing matplotlib charts |

**Why Plotly:**
- Latest version 6.5.2 released Jan 14, 2026
- Native pandas integration (works as pandas plotting backend)
- Interactive charts with hover → see exact trade details (why skip, what price, etc.)
- HTML output → share reports easily
- Better for exploring failure modes than static matplotlib

**When to use:**
- **Now:** Keep matplotlib for fast static charts (equity curve, scatter)
- **Add Plotly for:** Interactive failure analysis dashboard showing:
  - Skip reasons by token (drill down to see which markets caused most skips)
  - Per-trade PnL timeline with hover details (leader price, our price, skip reason)
  - Strategy comparison overlays (equity curves for all 8 strategies on one chart)

**Installation:**
```bash
pip install plotly==6.5.2
```

**Integration pattern:**
```python
import plotly.express as px

def plot_skip_analysis(results: List[ReplayResult]) -> str:
    """Interactive chart: skip reasons across strategies."""
    # Build DataFrame from skip_reasons dicts
    df = build_skip_reasons_df(results)
    fig = px.bar(df, x='strategy', y='count', color='reason',
                 title='Skip Reason Breakdown by Strategy',
                 hover_data=['token_id', 'price_at_skip'])
    return fig.to_html()
```

**Confidence:** HIGH (official PyPI, industry standard for interactive viz)

### 4. Data Wrangling: Pandas (Already Present, Validate Version)

| Library | Version | Purpose | Current Status |
|---------|---------|---------|----------------|
| pandas | ≥2.0 | DataFrame manipulation for multi-strategy comparison | Already in requirements.txt (2.0+) |

**Why validate version:**
- Pandas 2.0+ has performance improvements for large comparisons
- Your requirements.txt shows `pandas>=2.0` ✓ (GOOD)
- No changes needed

**Confidence:** HIGH (already validated)

### 5. Statistical Plotting: Seaborn (Optional, for Publication-Quality Charts)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| seaborn | 0.13.2 | Statistical visualizations (violin plots, box plots, distribution comparisons) | Optional: For understanding PnL distributions across strategies |

**Why Seaborn:**
- Current version 0.13.2 (stable)
- Built on matplotlib, drop-in addition
- Useful for: violin plots showing PnL distribution per strategy, box plots comparing skip rates

**When NOT to use:**
- Don't add if Plotly interactive charts are sufficient
- Primarily useful for static publication-quality statistical charts

**Installation (if needed):**
```bash
pip install seaborn==0.13.2
```

**Confidence:** MEDIUM (useful but not critical, Plotly can do similar)

## Integration Points

### With Existing Replay System

Your `SessionReplayer.run(track_analysis=True)` already produces:
- `result.analysis['equity_df']` → Feed to QuantStats for metrics
- `result.analysis['attributed_trades']` → Feed to comparison module
- `result.skip_reasons` → Feed to failure analysis

**Proposed new module:** `src/analysis/comparison.py`

```python
from typing import Dict, List
import quantstats as qs
from scipy.stats import ttest_ind
from pathlib import Path

class StrategyComparator:
    """Compare multiple strategies across sessions."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results: Dict[str, List[ReplayResult]] = {}

    def add_result(self, strategy_name: str, result: ReplayResult):
        """Accumulate results for a strategy."""
        if strategy_name not in self.results:
            self.results[strategy_name] = []
        self.results[strategy_name].append(result)

    def generate_comparison_report(self) -> Path:
        """Generate HTML comparison report using QuantStats."""
        # Convert results to returns series
        returns = {}
        for strategy, result_list in self.results.items():
            combined_equity = combine_equity_curves(result_list)
            returns[strategy] = combined_equity.pct_change()

        # Generate comparison tearsheet
        output_path = self.output_dir / "strategy_comparison.html"
        qs.reports.html(returns, output=str(output_path))
        return output_path

    def test_significance(self, strategy_a: str, strategy_b: str) -> Dict:
        """Test if strategy_a significantly outperforms strategy_b."""
        pnl_a = [r.total_pnl for r in self.results[strategy_a]]
        pnl_b = [r.total_pnl for r in self.results[strategy_b]]

        t_stat, p_value = ttest_ind(pnl_a, pnl_b)
        return {
            'strategies': f"{strategy_a} vs {strategy_b}",
            'significant': p_value < 0.05,
            'p_value': p_value,
            't_statistic': t_stat,
            'interpretation': 'Significantly different' if p_value < 0.05 else 'Not significantly different'
        }
```

### With Existing JSONL Sessions

No changes to session recording format needed. Comparison runs AFTER replay:

```python
# New script: scripts/compare_strategies.py
from src.framework.replay import SessionReplayer
from src.analysis.comparison import StrategyComparator
from src.strategies import get_all_strategies

def compare_all_strategies_on_sessions(session_paths: List[Path]):
    """Run all strategies on all sessions, generate comparison."""
    comparator = StrategyComparator(Path("data/reports"))

    for session_path in session_paths:
        for strategy in get_all_strategies():
            replayer = SessionReplayer(session_path, strategy)
            replayer.load()
            result = replayer.run(track_analysis=True)
            comparator.add_result(strategy.name, result)

    # Generate reports
    html_report = comparator.generate_comparison_report()
    print(f"Comparison report: {html_report}")

    # Test conservative vs others
    for other in ['mirror', 'aggressive', 'momentum']:
        sig_test = comparator.test_significance('conservative', other)
        print(f"Conservative vs {other}: p={sig_test['p_value']:.4f} ({sig_test['interpretation']})")
```

## Not Recommended

### 1. Heavyweight Backtesting Frameworks

| Library | Why NOT |
|---------|---------|
| Backtrader | You already have replay system. Backtrader is 5000+ LOC for strategy orchestration you don't need. |
| Zipline | Designed for equity markets with daily bars. Your use case is event-driven crypto prediction markets. Architectural mismatch. |
| vectorbt | Optimized for vectorized backtests on large datasets. You have ~12 sessions of JSONL events. Overkill. |
| PyAlgoTrade | Another full backtesting framework. You already have SessionReplayer. |

**Reasoning:** You're not building a backtester (you have one). You need **comparison and debugging on existing replay results**. Adding Backtrader/Zipline is architectural bloat.

### 2. PyFolio (Partially Superseded)

| Library | Why NOT |
|---------|---------|
| pyfolio | QuantStats is the modern successor with active maintenance. PyFolio development slowed significantly. Use QuantStats instead. |

**Exception:** If you need Bayesian risk analysis (PyFolio has this, QuantStats doesn't), consider `pyfolio-reloaded` fork. But for your use case (compare 8 strategies), QuantStats is sufficient.

### 3. Machine Learning Libraries (Not Yet)

| Library | Why NOT (for now) |
|---------|---------|
| scikit-learn | You're debugging WHY conservative wins, not predicting what will win. ML is for v1.2 pattern discovery, not v1.1 strategy comparison. |
| TensorFlow/PyTorch | Way premature. You have 12 sessions. Deep learning needs 1000s of examples. |

**Reasoning:** Current milestone is **understand conservative's logic** (rules-based debugging), not **predict winning patterns** (ML). Save ML for v1.2.

### 4. Over-Engineering Visualization

| Approach | Why NOT |
|----------|---------|
| Dash dashboards | Interactive web dashboard with callbacks. You need reports, not a deployed web app. HTML from Plotly/QuantStats is sufficient. |
| Tableau/PowerBI connectors | Trading bot analysis doesn't need BI tool integration. Keep it local Python. |

**Reasoning:** Generate HTML reports locally. Don't build infrastructure you don't need.

## Summary

### What to Add

| Library | Version | Why | Installation |
|---------|---------|-----|--------------|
| **quantstats** | 0.0.81 | Side-by-side strategy comparison with Sharpe/Sortino, HTML tearsheets | `pip install quantstats==0.0.81` |
| **scipy** | ≥1.10 | Statistical significance testing (t-tests) | `pip install scipy>=1.10` |
| **plotly** | 6.5.2 | Interactive failure analysis charts (optional upgrade) | `pip install plotly==6.5.2` |

### What NOT to Add

- ❌ Backtrader, Zipline, vectorbt (you have replay, don't need backtester)
- ❌ PyFolio (use QuantStats instead, more actively maintained)
- ❌ scikit-learn, TensorFlow (defer to v1.2 ML track)
- ❌ Dash, Tableau connectors (HTML reports are sufficient)

### Integration Strategy

1. **Minimal changes to existing code** - Keep replay.py, attribution.py, reports.py as-is
2. **New module:** `src/analysis/comparison.py` - Wraps QuantStats and SciPy
3. **New script:** `scripts/compare_strategies.py` - Runs all strategies on all sessions
4. **Output:** HTML reports showing:
   - Side-by-side metrics (Sharpe, Sortino, max drawdown, win rate)
   - Statistical significance tests (is conservative REALLY better?)
   - Interactive charts drilling into failure modes

### Key Insight for Conservative Debugging

Conservative took MORE trades but was ONLY profitable strategy. This suggests:
- **Selection filter difference** - Conservative's `MIN_LEADER_TRADE_PCT = 1%` and stricter cost checks likely filter noise
- **Comparison task:** Run all strategies on same sessions, compare skip reasons by token
- **Statistical test:** Is conservative's PnL distribution significantly different from mirror? (t-test will answer)

**Recommendation:** Start with QuantStats + SciPy. Add Plotly only if static charts aren't revealing enough detail.

## Sources

- [QuantStats GitHub](https://github.com/ranaroussi/quantstats)
- [QuantStats PyPI (0.0.81)](https://pypi.org/project/quantstats/)
- [Best Python Libraries for Algorithmic Trading](https://blog.quantinsti.com/python-trading-library/)
- [Python Trading Strategy Performance Evaluation](https://towardsdatascience.com/the-easiest-way-to-evaluate-the-performance-of-trading-strategies-in-python-4959fd798bb3/)
- [Plotly Express Latest Version](https://pypi.org/project/plotly/)
- [SciPy Statistical Testing](https://docs.scipy.org/doc/scipy/reference/stats.html)
- [Seaborn Documentation](https://seaborn.pydata.org/)
- [Python Roadmap 2026 for Traders](https://www.marketcalls.in/python/python-roadmap-2026-a-strategic-guide-for-traders-and-investors.html)
