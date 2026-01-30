# Technology Stack: Polymarket Copy Trading Bot - Simulation & Position Sizing

**Project:** Polymarket Copy Trading Bot V2
**Focus Area:** Simulation realism, position sizing optimization, risk management
**Researched:** 2026-01-30
**Overall Confidence:** MEDIUM (requires version verification via web search)

## Executive Summary

This stack focuses on **incremental improvements** to existing simulation and position sizing capabilities, not wholesale replacement. The bot already has session recording/replay framework and 7 strategies. The critical challenge is position sizing with <$100 budget while maintaining profit proportions.

**Core recommendation:** Add specialized position sizing algorithms (Kelly criterion variants) and statistical analysis tools rather than heavy backtesting frameworks. The existing replay system is sufficient; focus on optimizing the decision layer.

---

## Recommended Stack

### Position Sizing & Risk Management

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **scipy** | >=1.11.0 | Statistical optimization for Kelly criterion | Industry standard, already in scientific Python ecosystem. Provides `optimize.minimize` for fractional Kelly calculations | HIGH |
| **numpy** | >=1.24.0 | Numerical computation for position calculations | Required for vectorized position size calculations, probability distributions | HIGH |
| **pandas** | >=2.0.0 | Trade analysis and metrics computation | Essential for analyzing replay sessions, computing Sharpe ratios, drawdown analysis | HIGH |
| **statsmodels** | >=0.14.0 | Statistical analysis of strategy performance | Regression analysis for position sizing sensitivity, time series analysis for market behavior | MEDIUM |

**Rationale:** These are lightweight, well-maintained libraries that complement your existing structure. Scipy's optimization suite enables sophisticated Kelly criterion variants without external dependencies.

### Simulation Enhancement

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **Keep existing replay framework** | - | Session-based backtesting | Your recorder/replay system is domain-specific and well-suited. Don't replace it. | HIGH |
| **quantstats** | >=0.0.62 | Performance metrics and reporting | Provides industry-standard metrics (Sharpe, Sortino, Calmar, max drawdown) without building from scratch | MEDIUM |
| **ta-lib** (optional) | >=0.4.28 | Technical indicators | Only if you need additional price-based indicators beyond current strategies. Complex installation. | LOW |

**Rationale:** Don't add VectorBT, Backtrader, or similar frameworks. They're designed for price-time series backtesting, not event-driven copy trading with session replay. Your current framework is superior for this use case.

### Data Validation & Quality

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **pydantic** | >=2.5.0 | Data validation for simulation inputs | Ensure replay data integrity, validate position sizing parameters, catch config errors early | HIGH |
| **hypothesis** | >=6.92.0 | Property-based testing for position sizing | Generate edge cases for position sizing algorithms (e.g., extreme odds, tiny budgets) | MEDIUM |

**Rationale:** Position sizing at <$100 is error-prone. Pydantic validates inputs; hypothesis finds edge cases you haven't considered.

### Visualization & Analysis

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **matplotlib** | >=3.8.0 | Trade visualization, equity curves | Standard plotting for performance analysis | HIGH |
| **plotly** | >=5.18.0 | Interactive strategy comparison | Compare 7 strategies side-by-side with interactive plots | MEDIUM |
| **seaborn** | >=0.13.0 | Statistical visualization | Heatmaps for parameter sensitivity, distribution plots for sizing outcomes | MEDIUM |

**Rationale:** Visual analysis is critical for understanding why strategies diverge from leader profits. These are lightweight and integrate well.

### Development & Testing

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| **pytest** | >=8.0.0 | Unit/integration testing | Already in use, keep it | HIGH |
| **pytest-asyncio** | >=0.23.0 | Async test support | Already in use, keep it | HIGH |
| **pytest-benchmark** | >=4.0.0 | Performance testing for optimizers | Ensure optimization doesn't become bottleneck | MEDIUM |
| **mypy** | >=1.8.0 | Type checking | Already in use, critical for position sizing correctness | HIGH |

**Rationale:** Keep existing test infrastructure, add benchmark plugin for optimizer performance monitoring.

---

## Position Sizing Algorithms (Implementation Guidance)

These are **algorithms to implement**, not libraries to install.

### 1. Fractional Kelly Criterion (RECOMMENDED)

**What:** Size positions as fraction of Kelly-optimal bet
**Formula:** `f = (p * (b + 1) - 1) / b * kelly_fraction`
- `f` = fraction of bankroll to bet
- `p` = probability of winning (inferred from leader behavior)
- `b` = net odds received on bet
- `kelly_fraction` = conservative multiplier (0.25-0.5)

**Why:** Mathematically optimal for log-utility, but full Kelly is too aggressive for small budgets. Use 0.25-0.5 fraction.

**Implementation:**
```python
from scipy.optimize import minimize_scalar
import numpy as np

def fractional_kelly(prob_win: float, odds: float, kelly_fraction: float = 0.25) -> float:
    """
    Returns optimal bet fraction of bankroll.

    Args:
        prob_win: Probability of winning (0-1)
        odds: Decimal odds - 1 (e.g., 2.5 odds = 1.5)
        kelly_fraction: Conservative multiplier (0.25 = quarter Kelly)
    """
    if prob_win <= 0 or prob_win >= 1:
        return 0.0

    kelly_optimal = (prob_win * (odds + 1) - 1) / odds
    return max(0.0, kelly_optimal * kelly_fraction)
```

**Confidence:** HIGH - Well-established in betting literature

### 2. Fixed Fraction with Risk Floor

**What:** Bet fixed percentage of bankroll, but never risk more than floor amount
**Formula:** `bet = min(bankroll * fraction, risk_floor)`

**Why:** Simpler than Kelly, prevents catastrophic loss on small budgets

**Implementation:**
```python
def fixed_fraction_with_floor(
    bankroll: float,
    fraction: float = 0.05,
    risk_floor: float = 5.0
) -> float:
    """
    Fixed fraction betting with minimum risk floor.

    Args:
        bankroll: Current capital
        fraction: Percent of bankroll to risk (0.05 = 5%)
        risk_floor: Minimum bet size in dollars
    """
    return min(bankroll * fraction, max(risk_floor, bankroll * fraction))
```

**Confidence:** HIGH - Simple, robust

### 3. Proportional Scaling with Budget Constraint

**What:** Scale leader's position proportionally to budget ratio, with hard cap
**Formula:** `follower_size = leader_size * (follower_budget / leader_budget) * scale_factor`

**Why:** Preserves leader's conviction signals while respecting budget constraints

**Implementation:**
```python
def proportional_scaling(
    leader_size: float,
    leader_budget: float,
    follower_budget: float,
    scale_factor: float = 0.8,  # Conservative multiplier
    min_bet: float = 1.0,
    max_bet_fraction: float = 0.2  # Max 20% of budget per bet
) -> float:
    """
    Scale leader position to follower budget.

    Returns 0 if scaled bet below min_bet threshold.
    """
    raw_scaled = leader_size * (follower_budget / leader_budget) * scale_factor
    max_bet = follower_budget * max_bet_fraction

    scaled = min(raw_scaled, max_bet)
    return scaled if scaled >= min_bet else 0.0
```

**Confidence:** HIGH - Core algorithm for copy trading

### 4. Volatility-Adjusted Sizing (ADVANCED)

**What:** Adjust position size based on market volatility
**Why:** Reduce size in choppy markets, increase in stable trends

**Requires:** Historical price volatility data from replay sessions

**Implementation:**
```python
import numpy as np

def volatility_adjusted_size(
    base_size: float,
    price_history: list[float],
    target_volatility: float = 0.05
) -> float:
    """
    Adjust position size based on recent price volatility.

    Args:
        base_size: Position size from other method (Kelly, proportional, etc.)
        price_history: Recent prices for volatility calculation
        target_volatility: Target volatility level (5% = 0.05)
    """
    if len(price_history) < 2:
        return base_size

    returns = np.diff(price_history) / price_history[:-1]
    realized_vol = np.std(returns)

    vol_scalar = target_volatility / max(realized_vol, 0.01)  # Avoid division by zero
    return base_size * min(vol_scalar, 2.0)  # Cap at 2x adjustment
```

**Confidence:** MEDIUM - Requires careful tuning, may be overkill for <$100 budget

---

## Risk Management Tools

### Libraries to Add

| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| **None** | - | Keep risk management in-house | Your domain is specific enough that generic tools don't apply |

### Implement Internally

**Risk metrics to track:**

1. **Max Drawdown** - Maximum peak-to-trough decline
2. **Sharpe Ratio** - Risk-adjusted return (quantstats provides this)
3. **Win Rate** - Percentage of profitable trades
4. **Profit Factor** - Gross profit / gross loss ratio
5. **Kelly Criterion Deviation** - How far actual sizing deviates from Kelly optimal

**Implementation:**
```python
from decimal import Decimal
import pandas as pd

class RiskMetrics:
    """Compute risk metrics from replay results."""

    @staticmethod
    def max_drawdown(equity_curve: list[Decimal]) -> Decimal:
        """Maximum drawdown from peak."""
        peak = equity_curve[0]
        max_dd = Decimal(0)

        for value in equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)

        return max_dd

    @staticmethod
    def sharpe_ratio(returns: list[Decimal], risk_free_rate: Decimal = Decimal(0)) -> Decimal:
        """Annualized Sharpe ratio."""
        if not returns:
            return Decimal(0)

        mean_return = sum(returns) / len(returns)
        std_return = Decimal(pd.Series([float(r) for r in returns]).std())

        if std_return == 0:
            return Decimal(0)

        return (mean_return - risk_free_rate) / std_return * Decimal(252).sqrt()

    @staticmethod
    def profit_factor(trades: list[dict]) -> Decimal:
        """Gross profit / gross loss."""
        gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))

        return gross_profit / gross_loss if gross_loss > 0 else Decimal('inf')
```

**Confidence:** HIGH - Standard metrics, straightforward implementation

---

## Backtesting Best Practices

### Don't Add These Frameworks

| Framework | Why NOT to Use | Confidence |
|-----------|---------------|------------|
| **Backtrader** | Designed for price-based OHLCV backtesting. Your replay system is event-driven and superior for copy trading. | HIGH |
| **VectorBT** | Overkill for your use case. Fast but designed for vectorized backtesting of technical strategies, not copy trading. | HIGH |
| **Zipline** | Abandoned project (last update 2021), focused on equity trading, not prediction markets. | HIGH |
| **PyAlgoTrade** | Unmaintained, OHLCV-focused, doesn't support prediction market semantics. | HIGH |

**Rationale:** Your session recorder/replay framework is **already a domain-specific backtesting engine**. It captures real market events, leader trades, and allows strategy comparison. Don't replace it.

### Best Practices for Your Existing System

1. **Separate Data Collection from Replay**
   ✅ Already doing this with `recorder.py` and `replay.py`

2. **Parameterize Strategies for Grid Search**
   ✅ Already have `run_grid` in optimizer
   Enhance: Add Bayesian optimization (scikit-optimize) for smarter parameter search

3. **Record Ground Truth**
   ✅ Session files capture actual leader trades
   Enhance: Add market metadata (volume, spread, time-to-resolution)

4. **Metrics-Driven Comparison**
   ⚠️ Currently prints summary tables
   Enhance: Export to structured format (JSON/CSV) for statistical analysis

5. **Walk-Forward Validation**
   ❌ Not currently implemented
   Add: Split sessions into train/test periods, validate strategies on unseen data

6. **Overfitting Detection**
   ❌ Not currently implemented
   Add: Compare in-sample vs out-of-sample performance

### Recommended Additions to Replay Framework

```python
# Add to simulation/optimizer.py

from dataclasses import dataclass
from typing import List, Dict
import json

@dataclass
class BacktestReport:
    """Structured backtest output for analysis."""
    strategy_name: str
    parameters: Dict[str, Any]
    total_pnl: Decimal
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    win_rate: float
    profit_factor: Decimal
    num_trades: int
    avg_position_size: Decimal

    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str)

    @classmethod
    def from_optimization_result(cls, result: OptimizationResult) -> 'BacktestReport':
        """Convert existing result to structured report."""
        # Implementation here
        pass
```

**Confidence:** HIGH - These are standard backtesting practices

---

## Installation

### Core Position Sizing Stack
```bash
# Scientific computing (position sizing, optimization)
pip install scipy>=1.11.0 numpy>=1.24.0 pandas>=2.0.0

# Statistical analysis
pip install statsmodels>=0.14.0

# Performance metrics
pip install quantstats>=0.0.62

# Data validation
pip install pydantic>=2.5.0

# Visualization
pip install matplotlib>=3.8.0 seaborn>=0.13.0 plotly>=5.18.0

# Testing enhancements
pip install pytest-benchmark>=4.0.0 hypothesis>=6.92.0
```

### Optional (Advanced)
```bash
# Bayesian optimization for hyperparameter tuning
pip install scikit-optimize>=0.9.0

# Technical indicators (only if needed)
# WARNING: ta-lib has complex installation (requires C dependencies)
# pip install TA-Lib>=0.4.28
```

### Update requirements.txt
```txt
# Existing dependencies (keep these)
PyYAML>=6.0
python-dotenv>=1.0
httpx>=0.27
py-clob-client>=0.34
websockets>=12.0
pytest>=8.0
pytest-asyncio>=0.23
mypy>=1.8
ruff>=0.2

# NEW: Position sizing & optimization
scipy>=1.11.0
numpy>=1.24.0
pandas>=2.0.0
statsmodels>=0.14.0

# NEW: Performance analysis
quantstats>=0.0.62

# NEW: Data validation
pydantic>=2.5.0

# NEW: Visualization
matplotlib>=3.8.0
seaborn>=0.13.0
plotly>=5.18.0

# NEW: Testing enhancements
pytest-benchmark>=4.0.0
hypothesis>=6.92.0

# OPTIONAL: Advanced optimization
# scikit-optimize>=0.9.0
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not Alternative | Confidence |
|----------|-------------|-------------|---------------------|------------|
| Backtesting Framework | Keep existing replay system | Backtrader | OHLCV-focused, not event-driven. Your system is superior. | HIGH |
| Backtesting Framework | Keep existing replay system | VectorBT | Overkill, requires vectorization of strategy logic | HIGH |
| Position Sizing | Implement Kelly variants | pyкелли (external lib) | Unmaintained, simple enough to implement in-house | MEDIUM |
| Position Sizing | scipy.optimize | Custom optimization | Scipy is battle-tested, no need to reinvent | HIGH |
| Risk Metrics | quantstats | Build from scratch | quantstats provides 40+ metrics out-of-box | MEDIUM |
| Risk Metrics | quantstats | empyrical (Quantopian) | Quantopian shutdown, empyrical maintenance unclear | MEDIUM |
| Parameter Optimization | Grid search (current) | Bayesian optimization (scikit-optimize) | Grid search works for small spaces, Bayesian better for large | MEDIUM |
| Parameter Optimization | Grid search (current) | Optuna | Optuna is excellent but overkill unless >10 parameters | LOW |
| Data Validation | pydantic | marshmallow | Pydantic has better type integration, faster | HIGH |
| Visualization | matplotlib/seaborn/plotly | bokeh | Plotly has better interactivity for web dashboards | MEDIUM |

---

## Architecture Integration

### Where These Fit in Current Structure

```
src/
├── simulation/
│   ├── optimizer.py           # ADD: Import scipy, pandas, quantstats
│   ├── full_optimizer.py      # ADD: BacktestReport class
│   ├── position_sizer.py      # NEW: Kelly criterion implementations
│   ├── risk_metrics.py        # NEW: RiskMetrics class
│   └── limit_order_sim.py     # ENHANCE: Pydantic validation
│
├── strategies/
│   ├── base.py                # ENHANCE: Add position_sizer parameter
│   └── */strategy.py          # USE: position_sizer for all sizing decisions
│
└── framework/
    ├── replay.py              # ENHANCE: Export BacktestReport
    └── runner.py              # KEEP: No changes needed
```

### Integration Pattern

```python
# Example: Integrate position sizing into strategy

from src.simulation.position_sizer import FractionalKellySizer

class MirrorStrategy(BaseStrategy):
    def __init__(self, position_sizer: PositionSizer = None, **kwargs):
        super().__init__(**kwargs)
        self.position_sizer = position_sizer or FractionalKellySizer(
            kelly_fraction=0.25,
            min_bet=1.0,
            max_bet_fraction=0.2
        )

    def on_leader_trade(self, market_id: str, leader_size: Decimal, odds: float):
        # Instead of proportional scaling in strategy logic...
        # Use position sizer
        prob_win = self._infer_probability(odds)  # Your existing logic

        position_size = self.position_sizer.calculate(
            bankroll=self.current_capital,
            leader_size=leader_size,
            leader_budget=Decimal("800"),
            prob_win=prob_win,
            odds=odds - 1  # Convert to net odds
        )

        if position_size >= self.min_bet:
            self.place_order(market_id, position_size, odds)
```

---

## Version Verification Needed

**CRITICAL:** The following versions are based on my training data (January 2025) and MUST be verified with current releases:

| Library | Stated Version | Verification Method | Confidence |
|---------|---------------|---------------------|------------|
| scipy | >=1.11.0 | WebSearch "scipy latest version 2026" | MEDIUM |
| numpy | >=1.24.0 | WebSearch "numpy latest version 2026" | MEDIUM |
| pandas | >=2.0.0 | WebSearch "pandas latest version 2026" | MEDIUM |
| statsmodels | >=0.14.0 | WebSearch "statsmodels latest version 2026" | LOW |
| quantstats | >=0.0.62 | WebSearch "quantstats latest version 2026" | LOW |
| pydantic | >=2.5.0 | WebSearch "pydantic latest version 2026" | MEDIUM |
| hypothesis | >=6.92.0 | WebSearch "hypothesis python latest version 2026" | LOW |
| matplotlib | >=3.8.0 | WebSearch "matplotlib latest version 2026" | MEDIUM |
| plotly | >=5.18.0 | WebSearch "plotly python latest version 2026" | MEDIUM |
| seaborn | >=0.13.0 | WebSearch "seaborn latest version 2026" | MEDIUM |
| pytest-benchmark | >=4.0.0 | WebSearch "pytest-benchmark latest version 2026" | LOW |

**Action Required:** Before implementation, verify all versions using WebSearch with year 2026 in query.

---

## Confidence Assessment

| Area | Confidence | Rationale |
|------|------------|-----------|
| Position Sizing Algorithms | HIGH | Kelly criterion is mathematically well-established, implementation straightforward |
| Library Recommendations | MEDIUM | Libraries are standard Python scientific stack, but versions need verification |
| Integration Approach | HIGH | Existing codebase structure is clean, integration points are clear |
| Backtesting Practices | HIGH | Recommendation to keep existing system is well-justified |
| Risk Metrics | HIGH | Standard trading metrics, implementations are straightforward |
| Version Numbers | LOW | Training data is 1-6 months stale, versions MUST be verified |

---

## Sources

**Verification Required:**
- All library versions: Requires WebSearch "[library] latest version 2026" for each
- quantstats documentation: Requires WebFetch https://github.com/ranaroussi/quantstats
- scipy.optimize API: Requires Context7 query for scipy library
- pydantic v2 changes: Requires Context7 query for pydantic library

**Training Data Sources (pre-January 2025):**
- Kelly Criterion: Standard betting theory literature
- Fractional Kelly: Investment sizing papers (Thorp, etc.)
- Python scientific stack: scipy, numpy, pandas documentation
- Trading metrics: Standard finance textbook definitions

**Existing Codebase:**
- Session replay framework: src/framework/replay.py
- Current optimizer: src/simulation/optimizer.py, full_optimizer.py
- Strategy structure: src/strategies/base.py

---

## Next Steps for Implementation

1. **Verify versions** (HIGH PRIORITY)
   - WebSearch each library for current 2026 version
   - Update version pins in this document

2. **Implement position_sizer.py** (CORE)
   - Start with FractionalKellySizer class
   - Add ProportionalScalingSizer for comparison
   - Unit tests with hypothesis for edge cases

3. **Implement risk_metrics.py** (CORE)
   - RiskMetrics class with standard calculations
   - Integrate quantstats for report generation

4. **Enhance optimizer.py** (ENHANCEMENT)
   - Add BacktestReport dataclass
   - Export results to JSON/CSV for analysis
   - Add walk-forward validation option

5. **Integrate into strategies** (INTEGRATION)
   - Modify base.py to accept position_sizer parameter
   - Update all 7 strategies to use position_sizer
   - Compare results with current proportional scaling

6. **Validation** (TESTING)
   - Pytest-benchmark for optimizer performance
   - Hypothesis for position sizer edge cases
   - Compare Kelly vs proportional on historical sessions

---

## Anti-Patterns to Avoid

### DON'T: Replace Existing Replay System
**Why:** Your event-driven session replay is domain-specific and well-designed. Generic backtesting frameworks are inferior for copy trading.

### DON'T: Use Full Kelly
**Why:** Full Kelly is mathematically optimal but psychologically brutal. With <$100 budget, one bad streak ends the game. Use 0.25-0.5 Kelly fraction.

### DON'T: Add TA-Lib Unless Necessary
**Why:** Complex installation (C dependencies), most indicators irrelevant for copy trading. You're following a leader, not generating technical signals.

### DON'T: Over-optimize on Limited Data
**Why:** With session-based data, you have limited samples. Heavy optimization leads to overfitting. Keep strategies simple, focus on position sizing.

### DON'T: Ignore Transaction Costs
**Why:** At <$100 budget, fees are proportionally larger. Ensure simulator includes Polymarket's fee structure (currently does this).

### DON'T: Chase Leader's Exact Profits
**Why:** With 1/8th the budget, you CAN'T match absolute profits. Focus on matching profit RATE (% return), not dollar amounts.

---

## Summary

**Core recommendation:** Add scipy/numpy/pandas for position sizing optimization, quantstats for metrics, pydantic for validation. Keep existing replay framework. Implement Kelly criterion variants internally rather than using external position sizing libraries.

**Critical path:** position_sizer.py → integrate into base.py → validate with historical sessions → compare Kelly vs current approach

**Confidence gaps:** Library versions need verification. Algorithm implementations are well-established but need validation with Polymarket's specific market mechanics (binary outcomes, USDC denomination).

**Budget impact:** All recommended libraries are free and open-source. No additional costs beyond development time.
