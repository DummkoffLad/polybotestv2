# Phase 7: Comparison Infrastructure - Research

**Researched:** 2026-02-04
**Domain:** Trading strategy comparison, visualization, and performance analysis
**Confidence:** HIGH

## Summary

Phase 7 requires infrastructure to run multiple strategies on identical session data with complete decision visibility. The standard approach combines existing visualization libraries (Plotly for interactive charts, matplotlib for static), performance analytics libraries (QuantStats for tear sheets, empyrical-reloaded for metrics), and pandas for data organization.

The research reveals that modern Python trading infrastructure emphasizes three principles: (1) use established libraries for metrics calculation to avoid hand-rolling complex financial formulas, (2) provide both interactive and static export options for different contexts, and (3) focus on decision visibility through comparison matrices that highlight strategy disagreements.

Key architectural insight: The comparison engine should replay a single session through multiple strategies sequentially (not parallel), ensuring identical market data and event ordering. This eliminates data consistency issues while allowing per-strategy state management.

**Primary recommendation:** Build a StrategyComparator that orchestrates SessionReplayer for each strategy, collects results, then generates comparison reports using Plotly for interactive HTML (equity curves, decision matrix) and QuantStats for per-strategy tear sheets. Use pandas DataFrames with Styler for metrics comparison tables with conditional formatting.

## Standard Stack

The established libraries/tools for this domain:

### Core Visualization
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| plotly | >=5.0 | Interactive financial charts | Industry standard for interactive web-based visualizations, WebGL support for large datasets, built-in financial chart types |
| matplotlib | >=3.8 | Static charts and exports | Universal Python plotting library, already in use (src/analysis/reports.py), reliable for publication-quality static images |
| pandas | >=2.0 | Data organization and HTML tables | Already in use, Styler API for conditional formatting, native HTML export with CSS styling |

### Performance Analytics
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| quantstats | >=0.0.81 | HTML tear sheets and metrics | Purpose-built for portfolio analytics, 50+ metrics, HTML reports with heatmaps, Monte Carlo simulations, latest release Jan 2026 |
| empyrical-reloaded | >=0.5.11 | Risk/performance metrics calculation | Community-maintained fork (original empyrical deprecated), Python 3.10+, used by zipline/pyfolio, prevents hand-rolling complex formulas |
| scipy | >=1.11 | Statistical calculations | Already in project (Phase 6), required dependency for quantstats, reliable for statistical comparisons |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | >=1.24 | Array operations | Already in use, required by pandas/scipy/quantstats, efficient numeric operations |
| seaborn | >=0.13.0 | Statistical visualizations | Optional, quantstats dependency, heatmaps for correlation matrices |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| plotly | bokeh | Bokeh offers similar interactivity but smaller ecosystem for financial charts; plotly has better WebGL performance and financial chart templates |
| quantstats | pyfolio | Pyfolio is Quantopian's original tool but less actively maintained; quantstats is actively developed (2026 release) and easier to use |
| empyrical-reloaded | hand-rolled metrics | Custom metrics allow exact definitions but require testing edge cases (tail risk, negative returns, zero variance); empyrical is battle-tested |
| pandas Styler | pretty-html-table | Third-party library adds dependency; pandas Styler is built-in and sufficient for comparison tables |

**Installation:**
```bash
pip install plotly>=5.0 quantstats>=0.0.81 empyrical-reloaded>=0.5.11
# matplotlib, pandas, scipy, numpy already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
src/
├── comparison/              # NEW: Phase 7 infrastructure
│   ├── __init__.py
│   ├── comparator.py       # StrategyComparator orchestrator
│   ├── visualizer.py       # Plotly equity curves + decision matrix
│   ├── metrics.py          # Metrics aggregation + comparison table
│   └── tear_sheets.py      # QuantStats integration
├── analysis/                # EXISTING: Per-strategy analysis
│   ├── reports.py          # Already has equity charts (matplotlib)
│   ├── equity_tracker.py   # Already tracks equity snapshots
│   └── ...
├── framework/               # EXISTING: Session replay
│   ├── replay.py           # SessionReplayer (reuse for each strategy)
│   └── ...
└── strategies/              # EXISTING: Strategy implementations
    ├── base.py
    └── ...
```

### Pattern 1: Sequential Replay with Result Collection
**What:** Run each strategy through SessionReplayer independently, collect ReplayResult objects, then compare
**When to use:** When strategies need isolated state and identical market data
**Example:**
```python
# Source: Based on backtesting.py patterns and AWS Batch architecture research
from src.framework.replay import SessionReplayer
from src.strategies import ConservativeStrategy, AggressiveStrategy

class StrategyComparator:
    def __init__(self, session_id: str, strategies: List[Strategy]):
        self.session_id = session_id
        self.strategies = strategies
        self.results = {}  # strategy_name -> ReplayResult

    def run_comparison(self) -> ComparisonResult:
        """Run all strategies on same session, collect results."""
        for strategy in self.strategies:
            replayer = SessionReplayer(self.session_id)
            result = replayer.replay(strategy, track_analysis=True)
            self.results[strategy.name] = result

        return self._build_comparison()

    def _build_comparison(self) -> ComparisonResult:
        """Aggregate results into comparison structure."""
        return ComparisonResult(
            session_id=self.session_id,
            strategy_results=self.results,
            equity_curves=self._build_equity_curves(),
            metrics_table=self._build_metrics_table(),
            decision_matrix=self._build_decision_matrix()
        )
```

### Pattern 2: Equity Curve Overlay with Drawdown Shading
**What:** Overlay multiple strategy equity curves on single chart with drawdown zones
**When to use:** For visual performance comparison (user requirement from CONTEXT.md)
**Example:**
```python
# Source: Plotly financial charts documentation + user requirements
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def create_comparison_chart(equity_data: Dict[str, pd.DataFrame]) -> go.Figure:
    """Create overlaid equity curves with drawdown shading.

    Args:
        equity_data: {strategy_name: DataFrame with 'equity' column}

    Returns:
        Plotly Figure with equity curves + drawdown overlay
    """
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=('Equity Curves', 'Drawdown %'),
        row_heights=[0.7, 0.3]
    )

    # Top panel: Overlay equity curves
    for strategy_name, df in equity_data.items():
        fig.add_trace(
            go.Scatter(x=df.index, y=df['equity'],
                      name=strategy_name, mode='lines'),
            row=1, col=1
        )

    # Bottom panel: Drawdown as shaded zones (user requirement)
    for strategy_name, df in equity_data.items():
        drawdown_pct = calculate_drawdown_series(df)
        fig.add_trace(
            go.Scatter(x=df.index, y=drawdown_pct,
                      name=f"{strategy_name} DD",
                      fill='tozeroy', mode='lines'),
            row=2, col=1
        )

    fig.update_layout(height=800, showlegend=True)
    return fig
```

### Pattern 3: Metrics Comparison with Conditional Formatting
**What:** Build pandas DataFrame with strategies as columns, metrics as rows, use Styler for color coding
**When to use:** For metrics comparison table (user requirement: green=best, red=worst per metric)
**Example:**
```python
# Source: Pandas Styler documentation + user requirements
import pandas as pd

def create_metrics_table(results: Dict[str, ReplayResult]) -> pd.DataFrame:
    """Create comparison table with conditional formatting.

    User requirements:
    - Green for best value per metric
    - Red for worst value per metric
    - Statistical confidence in separate section
    """
    metrics = {
        'Sharpe Ratio': {},
        'Win Rate %': {},
        'Profit Factor': {},
        'Max Drawdown %': {},
        'Total Return %': {},
    }

    for strategy_name, result in results.items():
        metrics['Sharpe Ratio'][strategy_name] = calculate_sharpe(result)
        metrics['Win Rate %'][strategy_name] = result.win_count / (result.win_count + result.loss_count) * 100
        # ... calculate other metrics

    df = pd.DataFrame(metrics).T

    # Apply conditional formatting (user requirement)
    def highlight_best_worst(row):
        """Green for best, red for worst per metric."""
        # For Max Drawdown, lower is better; for others, higher is better
        is_inverse = row.name == 'Max Drawdown %'

        if is_inverse:
            best_idx = row.idxmin()
            worst_idx = row.idxmax()
        else:
            best_idx = row.idxmax()
            worst_idx = row.idxmin()

        styles = [''] * len(row)
        styles[row.index.get_loc(best_idx)] = 'background-color: #90EE90'  # green
        styles[row.index.get_loc(worst_idx)] = 'background-color: #FFB6C1'  # red
        return styles

    styled_df = df.style.apply(highlight_best_worst, axis=1)
    return styled_df
```

### Pattern 4: Decision Matrix with Divergence Highlighting
**What:** Show event × strategy matrix with position size + PnL, highlight rows where strategies diverged
**When to use:** For decision visibility (user requirement: show disagreements)
**Example:**
```python
# Source: User requirements from CONTEXT.md
def create_decision_matrix(results: Dict[str, ReplayResult]) -> pd.DataFrame:
    """Build matrix showing which trades each strategy took vs skipped.

    User requirements:
    - Show position size + resulting PnL per cell
    - Highlight rows where strategies diverged
    - No drill-down (summary view only)

    Returns:
        DataFrame: rows=events, columns=strategies, cells=decision+PnL
    """
    # Collect all unique events across strategies
    all_events = set()
    for result in results.values():
        all_events.update([(t.timestamp, t.market_id) for t in result.trades])

    matrix_data = {}
    for strategy_name, result in results.items():
        decisions = {}
        for event in all_events:
            trade = find_trade_for_event(result.trades, event)
            if trade:
                # Format: "position_size → PnL"
                decisions[event] = f"{trade.our_shares:.1f} → ${trade.pnl:+.2f}"
            else:
                decisions[event] = "SKIP"
        matrix_data[strategy_name] = decisions

    df = pd.DataFrame(matrix_data)

    # Highlight divergence rows (user requirement)
    def highlight_divergence(row):
        """Highlight if strategies disagreed (some took, some skipped)."""
        took_count = sum(1 for val in row if val != "SKIP")
        diverged = 0 < took_count < len(row)  # Some took, some skipped

        if diverged:
            return ['background-color: #FFFFCC'] * len(row)  # yellow
        return [''] * len(row)

    styled_df = df.style.apply(highlight_divergence, axis=1)
    return styled_df
```

### Pattern 5: QuantStats Tear Sheet Generation
**What:** Generate HTML tear sheet per strategy for deep performance analysis
**When to use:** For comprehensive per-strategy analysis (requirement COMP-05)
**Example:**
```python
# Source: QuantStats documentation
import quantstats as qs

def generate_tear_sheets(results: Dict[str, ReplayResult], output_dir: Path):
    """Generate QuantStats HTML tear sheets for each strategy.

    Note: QuantStats analyzes return series, not discrete trades.
    Convert equity curve to returns series.
    """
    qs.extend_pandas()

    for strategy_name, result in results.items():
        # Convert equity snapshots to return series
        equity_df = result.equity_tracker.to_dataframe()
        returns = equity_df['equity'].pct_change().dropna()

        # Generate tear sheet
        output_file = output_dir / f"{strategy_name}_tearsheet.html"
        qs.reports.html(
            returns,
            output=str(output_file),
            title=f"{strategy_name} Performance Analysis",
            benchmark=None  # Could add SPY or other benchmark
        )

        print(f"Tear sheet saved: {output_file}")
```

### Anti-Patterns to Avoid

- **Parallel replay with shared state:** Don't run strategies in parallel using same SessionReplayer instance; each strategy needs isolated state. Sequential replay ensures data consistency.

- **Hand-rolling financial metrics:** Don't calculate Sharpe/Sortino/Calmar ratios manually; use empyrical-reloaded. Custom implementations miss edge cases (negative returns, zero variance, tail risk).

- **Trade markers on equity curves:** User requirement explicitly says "no trade markers on equity curve—keep it clean." Show trades in decision matrix only.

- **Inline statistical confidence:** User requirement says "statistical confidence shown in separate section, not inline with main metrics." Don't clutter metrics table.

- **Mixing time resolutions:** Use consistent time resolution across all strategies. If one strategy records equity per-trade and another per-minute, comparison is invalid.

- **Loading full plotly.js inline:** For HTML exports, use CDN reference (3MB smaller) unless offline viewing is required. Check user context before choosing.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Sharpe ratio calculation | `(mean_return - risk_free) / std_dev` | empyrical-reloaded: `ep.sharpe_ratio(returns)` | Hand-rolled version fails on edge cases: negative returns make ratio interpretation unclear; zero variance causes division by zero; doesn't handle annualization correctly; ignores autocorrelation in returns |
| Sortino ratio calculation | Filter downside deviations manually | empyrical-reloaded: `ep.sortino_ratio(returns, required_return)` | Downside deviation calculation is subtle: must use only returns below target; requires partial moments; different conventions for denominator; MAR selection affects interpretation |
| Maximum drawdown | Loop through equity to find peaks/troughs | empyrical-reloaded: `ep.max_drawdown(returns)` | Naive loops miss edge cases: multiple drawdowns of same magnitude; underwater periods vs recovery; start date affects calculation; percentage vs absolute drawdown |
| Calmar ratio | `annual_return / max_drawdown` | empyrical-reloaded: `ep.calmar_ratio(returns)` | Simple division misses: period selection (36 months standard); annualization method; handling of negative drawdowns; recovery time consideration |
| Value at Risk (VaR) | Percentile of return distribution | empyrical-reloaded: `ep.value_at_risk(returns, cutoff)` | Hand-rolled VaR ignores: distribution assumptions (normal vs empirical); time horizon adjustments; confidence interval estimation; tail risk beyond VaR |
| Interactive financial charts | Matplotlib with custom zoom handlers | Plotly: `plotly.graph_objects.Candlestick` | Custom interactivity requires: implementing pan/zoom/hover; handling large datasets efficiently (WebGL); mobile responsiveness; export options; plotly does this out-of-box |
| HTML tear sheets | Custom HTML + CSS generation | QuantStats: `qs.reports.html(returns, output='report.html')` | Manual HTML generation misses: responsive design; interactive charts; comprehensive metric coverage (50+ metrics); Monte Carlo simulations; benchmark comparisons; monthly heatmaps |
| Drawdown visualization | Loop to calculate running max | pandas/numpy: `equity / equity.cummax() - 1` | Vectorized approach is 10-100x faster; handles edge cases (flat equity); already tested; integrates with plotting libraries |

**Key insight:** Financial metrics have decades of research behind "correct" implementation. Sharpe ratio alone has multiple conventions (ex-ante vs ex-post, annualization methods, risk-free rate handling). Using established libraries ensures consistency with academic literature and industry practice.

## Common Pitfalls

### Pitfall 1: Data Snooping Bias in Multiple Strategy Comparison
**What goes wrong:** Testing many strategy variations on the same data increases false positives. If you test 20 strategies, there's a 64% chance one looks profitable by random luck alone.

**Why it happens:** Each strategy test is a statistical hypothesis test. With no correction, testing multiple strategies inflates Type I error rate. This is exacerbated when strategies share similar logic (e.g., conservative with different thresholds).

**How to avoid:**
- Document ALL strategies tested, not just winners (use decision log)
- Apply Bonferroni correction or similar: divide significance threshold by number of tests
- Use out-of-sample validation: if strategy A beats strategy B on session X, verify on session Y
- Phase 6 statistical validation already provides framework; extend to multi-strategy comparison

**Warning signs:** One strategy dramatically outperforms all others; winning strategy has many parameters tuned specifically for the test session; results don't replicate on different sessions.

### Pitfall 2: Sample Size Differences Between Strategies
**What goes wrong:** Aggressive strategy takes 50 trades, conservative takes 10 trades. Metrics comparison is unfair because confidence intervals differ dramatically.

**Why it happens:** Different strategies have different trade frequencies by design. Win rate of 80% with 10 trades (confidence interval ±25%) isn't comparable to 60% with 50 trades (±14%).

**How to avoid:**
- Show trade count prominently in metrics table
- Include confidence intervals or standard errors for each metric
- Use Phase 6 statistical infrastructure (bootstrap, sample size thresholds)
- Consider sample-size-adjusted metrics (e.g., t-statistic instead of raw win rate)
- Flag strategies with insufficient trades (use Phase 6 thresholds: 30 minimum, 100 basic, 200 high confidence)

**Warning signs:** Strategies with very different trade counts ranked equally; no mention of statistical significance in comparison; confidence intervals not shown.

### Pitfall 3: Time Period Bias (Session Selection)
**What goes wrong:** Testing on a single session or cherry-picked sessions gives misleading results. Strategy performs well on volatile sessions but fails on quiet sessions.

**Why it happens:** Market regime matters. A session with one large leader trade favors aggressive strategies; a session with many small trades favors conservative. Single-session comparison doesn't reveal this.

**How to avoid:**
- Test on multiple sessions with different characteristics (Phase 6 regime classification helps)
- Show per-regime performance: high volatility sessions vs low volatility
- Use Phase 6 regime analysis: overnight hours, volatility threshold
- Aggregate metrics across sessions, not just single-session comparison
- Document session selection criteria to avoid cherry-picking

**Warning signs:** Only one session tested; session happens to have unusual characteristics; no regime breakdown; results dramatically different on different sessions.

### Pitfall 4: Overfitting to Comparison Metrics
**What goes wrong:** Optimizing strategies to win on Sharpe ratio, then comparing them on Sharpe ratio. Circular logic leads to overfit strategies that fail in live trading.

**Why it happens:** If strategy development uses same metrics as final comparison, you're implicitly optimizing for those metrics. Sharpe ratio can be gamed (reduce trade frequency, avoid edge cases).

**How to avoid:**
- Separate development metrics from evaluation metrics
- Use multiple uncorrelated metrics (Sharpe, Sortino, Calmar, profit factor, max drawdown)
- Include "robustness" metrics: performance consistency across sessions, regime stability
- Don't iterate on strategy parameters based on comparison output without out-of-sample testing

**Warning signs:** Strategy A dominates on all metrics; metrics are highly correlated; strategy parameters seem tuned to specific metric thresholds.

### Pitfall 5: Ignoring Transaction Costs in Comparison
**What goes wrong:** Strategy A takes 100 trades with $1 profit each, strategy B takes 10 trades with $10 profit each. Without transaction costs, both show $100 profit. With $0.50 per trade cost, strategy A nets $50 while B nets $95.

**Why it happens:** Comparison infrastructure focuses on strategy decisions (buy/sell/skip) but doesn't account for execution costs (slippage, spread, fees). High-frequency strategies get penalized more.

**How to avoid:**
- Use existing slippage infrastructure (src/analysis/slippage.py already tracks execution quality)
- Apply consistent cost model across all strategies (spread_cost_pct, slippage_cost_pct from StrategyConfig)
- Show both gross and net metrics in comparison table
- Include "cost efficiency" metric: profit per dollar of transaction cost

**Warning signs:** High-frequency strategy outperforms despite similar profit per trade; no mention of transaction costs; comparison uses gross PnL only.

### Pitfall 6: Plotly File Size Explosion
**What goes wrong:** Interactive HTML file is 15MB and takes 30 seconds to load because it contains full plotly.js library + all data points inlined.

**Why it happens:** Plotly defaults to self-contained HTML (includes full plotly.js ~5MB). If you include high-resolution data (per-second equity snapshots for hour-long session = thousands of points), file size explodes.

**How to avoid:**
- Use CDN reference instead of inlined plotly.js: `include_plotlyjs='cdn'` (saves 3MB)
- Downsample equity curves for visualization (per-minute instead of per-second)
- Use WebGL traces for large datasets: `go.Scattergl` instead of `go.Scatter`
- Provide both interactive HTML and static PNG export (user requirement: "generate both")
- Consider Plotly Dash for truly large datasets (server-side rendering)

**Warning signs:** HTML file >10MB; browser lags when opening report; complaints about sharing file size; loading time >5 seconds.

## Code Examples

Verified patterns from official sources:

### Creating Multi-Strategy Comparison Report
```python
# Source: Integration of quantstats, plotly, pandas patterns
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import quantstats as qs
from src.framework.replay import SessionReplayer
from src.comparison.comparator import StrategyComparator

def run_full_comparison(session_id: str, strategies: List[Strategy], output_dir: Path):
    """Complete comparison workflow: replay → visualize → export.

    This is the main entry point for Phase 7 comparison infrastructure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Run comparison (sequential replay)
    comparator = StrategyComparator(session_id, strategies)
    comparison = comparator.run_comparison()

    # Step 2: Generate equity curve comparison (Plotly interactive)
    equity_fig = create_equity_comparison(comparison.equity_curves)
    equity_fig.write_html(
        output_dir / f"{session_id}_equity_comparison.html",
        include_plotlyjs='cdn'  # 3MB smaller, requires internet
    )

    # Step 3: Generate metrics comparison table (pandas Styler)
    metrics_df = create_metrics_table(comparison.strategy_results)
    metrics_df.to_html(
        output_dir / f"{session_id}_metrics.html",
        escape=False  # Allow HTML/CSS from Styler
    )

    # Step 4: Generate decision matrix (pandas Styler)
    decision_df = create_decision_matrix(comparison.strategy_results)
    decision_df.to_html(
        output_dir / f"{session_id}_decisions.html",
        escape=False
    )

    # Step 5: Generate QuantStats tear sheets (per-strategy deep dive)
    for strategy_name, result in comparison.strategy_results.items():
        equity_df = result.equity_tracker.to_dataframe()
        returns = equity_df['equity'].pct_change().dropna()

        qs.reports.html(
            returns,
            output=str(output_dir / f"{session_id}_{strategy_name}_tearsheet.html"),
            title=f"{strategy_name} Performance Analysis - Session {session_id}"
        )

    # Step 6: Generate consolidated summary report
    generate_summary_html(comparison, output_dir / f"{session_id}_summary.html")

    print(f"Comparison complete. Reports saved to: {output_dir}")
    return comparison
```

### Equity Curve Comparison with WebGL Optimization
```python
# Source: Plotly performance documentation + financial charts guide
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def create_equity_comparison(equity_data: Dict[str, pd.DataFrame]) -> go.Figure:
    """Create comparison chart with equity curves + drawdown overlay.

    Uses WebGL for performance with large datasets.
    Implements user requirements: drawdown as shaded zones, no trade markers.
    """
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=('Equity Curves', 'Drawdown from Peak'),
        row_heights=[0.65, 0.35]
    )

    # Top panel: Overlaid equity curves (use Scattergl for large datasets)
    colors = ['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#6A994E']

    for idx, (strategy_name, df) in enumerate(equity_data.items()):
        # Downsample if too many points (performance optimization)
        if len(df) > 5000:
            df = df.iloc[::len(df)//5000]  # Keep ~5000 points

        fig.add_trace(
            go.Scattergl(  # WebGL for better performance
                x=df.index,
                y=df['equity'],
                name=strategy_name,
                mode='lines',
                line=dict(color=colors[idx % len(colors)], width=2),
                hovertemplate='%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )

    # Bottom panel: Drawdown zones (user requirement: overlay as shaded zones)
    for idx, (strategy_name, df) in enumerate(equity_data.items()):
        # Calculate drawdown percentage
        running_max = df['equity'].expanding().max()
        drawdown_pct = ((df['equity'] - running_max) / running_max * 100)

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=drawdown_pct,
                name=f"{strategy_name}",
                mode='lines',
                fill='tozeroy',  # Shaded zone
                line=dict(color=colors[idx % len(colors)], width=1),
                fillcolor=colors[idx % len(colors)],
                opacity=0.3,
                hovertemplate='%{y:.2f}%<extra></extra>',
                showlegend=False
            ),
            row=2, col=1
        )

    # Styling
    fig.update_xaxes(title_text="Time", row=2, col=1)
    fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown (%)", row=2, col=1)

    fig.update_layout(
        height=800,
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    return fig
```

### Metrics Comparison with Statistical Significance
```python
# Source: empyrical-reloaded docs + pandas Styler + Phase 6 statistical validation
import empyrical as ep
import pandas as pd
from src.statistics.confidence import calculate_confidence_interval

def create_metrics_table_with_confidence(
    results: Dict[str, ReplayResult],
    confidence_level: float = 0.95
) -> pd.io.formats.style.Styler:
    """Create comparison table with metrics + confidence intervals.

    Integrates Phase 6 statistical validation for confidence reporting.
    Implements user requirements: color coding, separate confidence section.
    """
    metrics_data = {}
    confidence_data = {}

    for strategy_name, result in results.items():
        # Convert to return series for empyrical
        equity_df = result.equity_tracker.to_dataframe()
        returns = equity_df['equity'].pct_change().dropna()

        # Calculate metrics using empyrical (don't hand-roll)
        metrics_data[strategy_name] = {
            'Total Return %': ((equity_df['equity'].iloc[-1] / equity_df['equity'].iloc[0]) - 1) * 100,
            'Sharpe Ratio': ep.sharpe_ratio(returns),
            'Sortino Ratio': ep.sortino_ratio(returns),
            'Calmar Ratio': ep.calmar_ratio(returns),
            'Max Drawdown %': ep.max_drawdown(returns) * 100,
            'Win Rate %': (result.win_count / (result.win_count + result.loss_count) * 100) if (result.win_count + result.loss_count) > 0 else 0,
            'Profit Factor': calculate_profit_factor(result),
            'Trade Count': result.win_count + result.loss_count,
        }

        # Calculate confidence intervals (Phase 6 integration)
        confidence_data[strategy_name] = {
            'Sharpe CI': calculate_confidence_interval(returns, ep.sharpe_ratio, confidence_level),
            'Sample Size': len(returns),
            'Confidence Level': get_confidence_level(result.win_count + result.loss_count)  # Phase 6 thresholds
        }

    # Main metrics table
    metrics_df = pd.DataFrame(metrics_data).T

    # Apply conditional formatting (user requirement: green=best, red=worst)
    def highlight_best_worst(s):
        """Color code best/worst per column."""
        # Inverse metrics (lower is better)
        inverse_metrics = ['Max Drawdown %']
        is_inverse = s.name in inverse_metrics

        if len(s) < 2:
            return [''] * len(s)

        # Find best/worst
        if is_inverse:
            best_val, worst_val = s.min(), s.max()
        else:
            best_val, worst_val = s.max(), s.min()

        # Apply colors
        colors = []
        for val in s:
            if val == best_val:
                colors.append('background-color: #90EE90; font-weight: bold')  # green
            elif val == worst_val:
                colors.append('background-color: #FFB6C1')  # light red
            else:
                colors.append('')
        return colors

    styled_df = metrics_df.style.apply(highlight_best_worst, axis=0)

    # Format numbers
    styled_df = styled_df.format({
        'Total Return %': '{:.2f}%',
        'Sharpe Ratio': '{:.3f}',
        'Sortino Ratio': '{:.3f}',
        'Calmar Ratio': '{:.3f}',
        'Max Drawdown %': '{:.2f}%',
        'Win Rate %': '{:.1f}%',
        'Profit Factor': '{:.2f}',
        'Trade Count': '{:.0f}',
    })

    # User requirement: confidence in SEPARATE section
    # Add confidence table below main table (handled in HTML generation)
    styled_df.confidence_section = pd.DataFrame(confidence_data).T

    return styled_df
```

### Decision Matrix with Trade-Level Visibility
```python
# Source: User requirements from CONTEXT.md + pandas patterns
def create_decision_matrix(results: Dict[str, ReplayResult]) -> pd.io.formats.style.Styler:
    """Build event × strategy matrix showing decisions + PnL.

    User requirements:
    - Show position size + resulting PnL per cell
    - Highlight rows where strategies diverged
    - No drill-down (summary view only)
    """
    # Collect all unique market events
    event_registry = {}  # (timestamp, market_id) -> event_label

    for strategy_name, result in results.items():
        for trade in result.trades:
            key = (trade.timestamp, trade.market_id)
            if key not in event_registry:
                # Create readable event label
                event_registry[key] = f"{trade.timestamp.strftime('%H:%M:%S')} | {trade.token_id[:8]}... | {trade.side}"

    # Build matrix: rows=events, columns=strategies
    matrix_data = []
    event_keys = sorted(event_registry.keys())

    for event_key in event_keys:
        row = {'Event': event_registry[event_key]}

        for strategy_name, result in results.items():
            # Find trade matching this event
            trade = next(
                (t for t in result.trades
                 if (t.timestamp, t.market_id) == event_key),
                None
            )

            if trade:
                # Format: "shares → $PnL" (user requirement: show position size + PnL)
                pnl = calculate_trade_pnl(trade)
                row[strategy_name] = f"{trade.our_shares:.1f} → ${pnl:+.2f}"
            else:
                row[strategy_name] = "SKIP"

        matrix_data.append(row)

    df = pd.DataFrame(matrix_data)
    df.set_index('Event', inplace=True)

    # Highlight divergence rows (user requirement: visual emphasis on disagreements)
    def highlight_divergence(row):
        """Yellow background if strategies disagreed on this event."""
        actions = set(row.values)

        # Divergence = some took, some skipped
        has_skip = "SKIP" in actions
        has_trade = any(val != "SKIP" for val in actions)
        diverged = has_skip and has_trade

        if diverged:
            return ['background-color: #FFFFCC'] * len(row)  # yellow
        return [''] * len(row)

    styled_df = df.style.apply(highlight_divergence, axis=1)

    # Add cell coloring for wins/losses
    def color_pnl_cells(val):
        """Green for profitable trades, red for losses."""
        if val == "SKIP":
            return 'color: gray; font-style: italic'
        elif '→ $+' in val or '→ $0' in val:
            return 'color: green'
        elif '→ $-' in val:
            return 'color: red'
        return ''

    styled_df = styled_df.applymap(color_pnl_cells)

    return styled_df
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| empyrical (original) | empyrical-reloaded | 2021-2023 | Original Quantopian library deprecated when company shut down; community fork (empyrical-reloaded) now maintained, requires Python 3.10+, active development |
| pyfolio | quantstats | 2019-2020 | pyfolio development slowed after Quantopian closure; quantstats emerged as successor with better maintenance, easier API, HTML reports (released 2026-01-13) |
| matplotlib only | plotly + matplotlib hybrid | 2020-2024 | Interactive dashboards became standard for trading; matplotlib for static/publication, plotly for interactive/web-based |
| Manual metric calculation | Library-based metrics | 2015-2020 | Research on metric pitfalls (Sharpe negative returns, tail risk) drove adoption of standardized libraries |
| Inline plotly.js (5MB+ files) | CDN reference | 2022-2024 | File size concerns led to CDN-based approach (3MB savings); tradeoff is internet requirement |
| Parallel backtesting | Sequential with aggregation | Ongoing | Cloud-based parallel execution (AWS Batch + Airflow) for large-scale; single-session comparison uses sequential for data consistency |

**Deprecated/outdated:**
- **empyrical (original):** No longer maintained, last update 2020. Use empyrical-reloaded.
- **pyfolio:** Still functional but not actively developed. Use quantstats for new projects.
- **plotly.offline:** Deprecated in plotly 5.0+. Use `plotly.io` module instead.
- **pandas.DataFrame.append():** Deprecated in pandas 2.0. Use `pd.concat()` instead.

## Open Questions

Things that couldn't be fully resolved:

1. **Optimal equity curve time resolution**
   - What we know: Per-trade snapshots provide exact decision points; fixed-interval (per-minute/per-second) provides smooth curves
   - What's unclear: Which approach is better for comparison visualization? Per-trade creates jagged curves with variable time gaps; fixed-interval may miss important trade timing
   - Recommendation: Start with per-trade (simpler, exact), provide downsampling for visualization if >5000 points. Let user feedback guide refinement.

2. **QuantStats applicability to discrete trading**
   - What we know: QuantStats documentation states "analyzes return series (daily, weekly, monthly), not discrete trade data"
   - What's unclear: How well does it work for intraday discrete copy-trading? Converting per-trade equity to return series may lose information about trade timing
   - Recommendation: Implement and evaluate. If QuantStats metrics seem mismatched, supplement with custom discrete-trade metrics (but still use empyrical for individual metric calculations).

3. **Statistical comparison between strategies**
   - What we know: Phase 6 provides statistical validation for single strategy (bootstrap, paired t-test, regime analysis)
   - What's unclear: How to statistically compare strategy A vs strategy B? Paired t-test requires matched samples, but strategies may trade at different times
   - Recommendation: Use Phase 6 infrastructure for independent regime analysis per strategy. For direct comparison, use matched-pair analysis only on events where both strategies had opportunity to trade. Document clearly when sample sizes differ.

4. **Benchmark selection**
   - What we know: QuantStats supports benchmark comparison (e.g., SPY for stock strategies)
   - What's unclear: What's appropriate benchmark for Polymarket copy-trading? No clear "market index" for prediction markets
   - Recommendation: Use conservative strategy as benchmark (it's the current profitable baseline per PROJECT.md decisions). This provides relative comparison rather than absolute benchmark.

5. **Report organization: consolidated vs per-strategy**
   - What we know: User requirements allow both approaches (Claude's discretion)
   - What's unclear: Single HTML with all strategies vs multiple files? Consolidated is easier to compare; separate files are easier to share
   - Recommendation: Hybrid approach—generate consolidated summary (equity curves + metrics table + decision matrix) plus per-strategy tear sheets (QuantStats HTML). User can share summary for quick comparison or full tear sheet for deep dive.

## Sources

### Primary (HIGH confidence)
- **QuantStats GitHub & PyPI:** https://github.com/ranaroussi/quantstats, https://pypi.org/project/quantstats/ - Current version 0.0.81 (Jan 2026), features, API usage
- **empyrical-reloaded GitHub & PyPI:** https://github.com/stefan-jansen/empyrical-reloaded, https://pypi.org/project/empyrical-reloaded/ - Community-maintained fork, metrics calculation, Python 3.10+ requirement
- **Plotly Official Documentation:** https://plotly.com/python/financial-charts/, https://plotly.com/python/performance/, https://plotly.com/python/interactive-html-export/ - Financial charts, WebGL optimization, HTML export options
- **Pandas Styler Documentation:** https://pandas.pydata.org/pandas-docs/stable/user_guide/style.html - Conditional formatting, HTML export with CSS
- **Codebase Analysis:** src/analysis/reports.py, src/analysis/equity_tracker.py, src/framework/replay.py, src/strategies/base.py - Existing infrastructure

### Secondary (MEDIUM confidence)
- **Trading Strategy Comparison Best Practices:** Multiple sources on backtesting architecture, AWS Batch + Airflow patterns for multi-strategy testing
- **Financial Metrics Calculation Pitfalls:** Academic and practitioner sources on Sharpe/Sortino/Calmar ratio limitations and edge cases
- **Common Backtesting Mistakes:** Industry blogs and educational resources on data snooping, overfitting, sample size issues
- **Python Backtesting Frameworks:** Comparison of backtesting.py, backtrader, bt, pyalgotrade for architectural patterns

### Tertiary (LOW confidence)
- **2026-specific features:** Limited 2026-specific documentation found; most sources from 2020-2025. QuantStats 0.0.81 release (Jan 2026) is most recent verified 2026 update.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries verified via official PyPI/GitHub, versions checked, existing codebase uses matplotlib/pandas/scipy
- Architecture: HIGH - Patterns based on existing codebase (SessionReplayer, ReplayResult) + verified Plotly/pandas documentation
- Pitfalls: HIGH - Data snooping, sample size, overfitting are well-documented in academic literature; plotly file size confirmed in docs

**Research date:** 2026-02-04
**Valid until:** ~60 days (libraries stable; quantstats/empyrical-reloaded mature; plotly stable; pandas 2.x stable)
