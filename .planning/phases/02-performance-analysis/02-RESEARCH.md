# Phase 2: Performance Analysis - Research

**Researched:** 2026-01-31
**Domain:** Trade tracking, equity curve analytics, profit attribution, and execution analysis for algorithmic trading
**Confidence:** HIGH

## Summary

This phase requires tracking every trade from entry to outcome, building equity curves with drawdown metrics, and analyzing profit leakage by comparing our execution against the leader's actual results. The codebase already has strong foundations: Portfolio class tracks realized/unrealized PnL, SessionReplayer records trades with prices, and FollowMetricsTracker measures follow quality.

The standard approach for performance analysis in Python trading systems is:
- **pandas** for time-series equity curve construction and drawdown calculations
- **matplotlib** for equity curve and drawdown visualization
- **JSONL** for trade event persistence (already in use via SessionRecorder)
- **pandas DataFrames** for slippage analysis and profit attribution

Key findings:
- Portfolio class already tracks realized/unrealized PnL per position with cost basis
- SessionReplayer collects ExecutedTrade records with full trade details
- FollowMetricsTracker provides leader vs. us comparison framework
- Equity curve = cumulative PnL over time; drawdown = peak-to-trough decline
- Slippage measurement: arrival price (leader's fill) vs. execution price (our fill)
- Transaction Cost Analysis (TCA) benchmarks: arrival price, TWAP, market impact

**Primary recommendation:** Extend existing Portfolio/SessionReplayer infrastructure to emit timestamped PnL snapshots, use pandas for equity curve construction with cummax() for drawdown tracking, add SlippageAnalyzer class to measure execution gaps, and matplotlib for chart generation. Persist enriched trade data to JSONL with per-trade attribution fields.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pandas | 2.0+ | Time-series equity curve, drawdown calculation | Industry standard for financial time-series, built-in date/time indexing |
| matplotlib | 3.8+ | Equity curve and drawdown charts | Standard Python plotting library, integrates with pandas |
| Decimal | stdlib | Financial precision for PnL | Already in use; required for accurate money calculations |
| json | stdlib | Trade data persistence (JSONL format) | Already in use via SessionRecorder; simple, human-readable |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | 1.24+ | Drawdown calculations (cummax, corrcoef) | Required by pandas; FollowMetricsTracker already uses it |
| pathlib | stdlib | File path handling | Already in use; cross-platform path operations |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| JSONL | SQLite database | JSONL simpler, human-readable, already in use; SQLite better for queries but overhead |
| matplotlib | Plotly/Seaborn | matplotlib lightweight, standard; Plotly interactive but heavier; Seaborn prettier but extra dep |
| pandas DataFrame | Custom classes | pandas has built-in resampling, rolling windows, cummax; custom would reinvent wheel |

**Installation:**
```bash
# Already in Python stdlib: json, pathlib, Decimal
# Need to add:
pip install pandas>=2.0 matplotlib>=3.8 numpy>=1.24
```

## Architecture Patterns

### Recommended Project Structure
```
src/
├── analysis/
│   ├── __init__.py
│   ├── equity_tracker.py      # EquityTracker: build equity curve from trades
│   ├── drawdown.py             # DrawdownAnalyzer: calculate max drawdown, recovery
│   ├── attribution.py          # TradeAttributor: link trades to final outcomes
│   ├── slippage.py             # SlippageAnalyzer: measure execution gaps
│   └── reports.py              # ReportGenerator: console + file output with charts
```

### Pattern 1: Timestamped PnL Snapshots
**What:** Emit (timestamp, capital) pairs on every trade event and periodic intervals
**When to use:** Building equity curves with full fidelity for drawdown analysis
**Example:**
```python
# Source: Codebase Portfolio + pandas time-series best practices
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List

@dataclass
class EquitySnapshot:
    """Point-in-time equity snapshot."""
    timestamp: datetime
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    open_positions: int
    deployed_capital: Decimal

class EquityTracker:
    """Tracks equity over time for curve construction."""

    def __init__(self, starting_capital: Decimal):
        self.starting_capital = starting_capital
        self.snapshots: List[EquitySnapshot] = []

    def record_snapshot(self, timestamp: datetime, portfolio, current_prices):
        """Record equity at a point in time."""
        realized, unrealized = portfolio.calculate_pnl(current_prices)
        total = self.starting_capital + realized + unrealized

        snapshot = EquitySnapshot(
            timestamp=timestamp,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_equity=total,
            open_positions=len(portfolio.get_positions()),
            deployed_capital=portfolio.get_total_deployed()
        )
        self.snapshots.append(snapshot)

    def to_dataframe(self):
        """Convert to pandas DataFrame for analysis."""
        import pandas as pd
        return pd.DataFrame([
            {
                'timestamp': s.timestamp,
                'equity': float(s.total_equity),
                'realized_pnl': float(s.realized_pnl),
                'unrealized_pnl': float(s.unrealized_pnl),
                'open_positions': s.open_positions,
            }
            for s in self.snapshots
        ]).set_index('timestamp')
```

### Pattern 2: Drawdown Calculation with pandas
**What:** Calculate max drawdown from equity curve using cummax()
**When to use:** Post-session analysis to measure risk metrics
**Example:**
```python
# Source: PyQuantLab equity curve tutorial + pandas docs
import pandas as pd
from decimal import Decimal

class DrawdownAnalyzer:
    """Calculate drawdown metrics from equity curve."""

    def analyze(self, equity_df: pd.DataFrame) -> dict:
        """Calculate drawdown statistics.

        Args:
            equity_df: DataFrame with 'equity' column and datetime index

        Returns:
            Dict with max_drawdown, max_drawdown_duration, recovery_time
        """
        # Calculate running maximum (peak)
        peak = equity_df['equity'].cummax()

        # Drawdown = (equity - peak) / peak
        drawdown = (equity_df['equity'] - peak) / peak

        # Max drawdown (most negative value)
        max_dd = drawdown.min()
        max_dd_pct = max_dd * 100

        # Find max drawdown period
        dd_idx = drawdown.idxmin()  # Timestamp of max drawdown

        # Duration: time from peak to trough
        peak_before_dd = peak[:dd_idx].idxmax()
        duration = (dd_idx - peak_before_dd).total_seconds() / 60  # minutes

        # Recovery: time from trough to recovery (if recovered)
        recovery_time = None
        peak_at_dd = peak.loc[dd_idx]
        after_dd = equity_df.loc[dd_idx:]
        recovered = after_dd[after_dd['equity'] >= peak_at_dd]
        if len(recovered) > 0:
            recovery_idx = recovered.index[0]
            recovery_time = (recovery_idx - dd_idx).total_seconds() / 60

        return {
            'max_drawdown_pct': float(max_dd_pct),
            'max_drawdown_value': float(max_dd * equity_df['equity'].iloc[0]),
            'drawdown_start': peak_before_dd,
            'drawdown_bottom': dd_idx,
            'drawdown_duration_min': duration,
            'recovery_time_min': recovery_time,
        }
```

### Pattern 3: Per-Trade Attribution
**What:** Link each trade to its final outcome (win/loss/open)
**When to use:** Identifying which trades contributed most to final PnL
**Example:**
```python
# Source: Codebase ExecutedTrade + financial attribution analysis
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

@dataclass
class AttributedTrade:
    """Trade with outcome attribution."""
    # Entry
    entry_timestamp: datetime
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    entry_shares: Decimal
    entry_price: Decimal
    entry_cost: Decimal

    # Exit (if closed)
    exit_timestamp: Optional[datetime] = None
    exit_shares: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    exit_proceeds: Optional[Decimal] = None

    # Outcome
    status: str = "open"  # "win", "loss", "open", "resolved"
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    # Leader comparison
    leader_dollars: Decimal = Decimal("0")
    leader_price: Decimal = Decimal("0")

    def close_trade(self, timestamp: datetime, shares: Decimal, price: Decimal):
        """Mark trade as closed with exit details."""
        self.exit_timestamp = timestamp
        self.exit_shares = shares
        self.exit_price = price
        self.exit_proceeds = shares * price

        # Calculate PnL
        if self.action == "BUY":
            # Bought low, sold high = profit
            self.realized_pnl = self.exit_proceeds - self.entry_cost
        else:
            # Sold high, bought back low = profit
            self.realized_pnl = self.entry_cost - self.exit_proceeds

        # Determine outcome
        if self.realized_pnl > 0:
            self.status = "win"
        elif self.realized_pnl < 0:
            self.status = "loss"
        else:
            self.status = "breakeven"

    def update_unrealized(self, current_price: Decimal):
        """Update unrealized PnL if still open."""
        if self.status == "open":
            current_value = self.entry_shares * current_price
            self.unrealized_pnl = current_value - self.entry_cost

class TradeAttributor:
    """Tracks trades and attributes outcomes."""

    def __init__(self):
        self.trades: dict[str, AttributedTrade] = {}  # token_id -> trade

    def record_entry(self, event, decision) -> str:
        """Record trade entry, return trade_id."""
        trade = AttributedTrade(
            entry_timestamp=event.trade.timestamp,
            market_id=event.trade.market_id,
            token_id=event.trade.token_id,
            side=event.trade.side.value,
            action=decision.action.value,
            entry_shares=decision.shares,
            entry_price=event.prices.ask if decision.action.value == "BUY" else event.prices.bid,
            entry_cost=decision.dollars,
            leader_dollars=event.trade.dollars,
            leader_price=event.trade.price,
        )
        self.trades[event.trade.token_id] = trade
        return event.trade.token_id

    def record_exit(self, token_id: str, timestamp: datetime, shares: Decimal, price: Decimal):
        """Record trade exit."""
        if token_id in self.trades:
            self.trades[token_id].close_trade(timestamp, shares, price)

    def get_summary(self) -> dict:
        """Summarize trade outcomes."""
        wins = [t for t in self.trades.values() if t.status == "win"]
        losses = [t for t in self.trades.values() if t.status == "loss"]
        open_trades = [t for t in self.trades.values() if t.status == "open"]

        return {
            'total_trades': len(self.trades),
            'wins': len(wins),
            'losses': len(losses),
            'open': len(open_trades),
            'win_rate': len(wins) / len(self.trades) if self.trades else 0,
            'total_realized_pnl': sum(t.realized_pnl for t in self.trades.values()),
            'avg_win': sum(t.realized_pnl for t in wins) / len(wins) if wins else Decimal("0"),
            'avg_loss': sum(t.realized_pnl for t in losses) / len(losses) if losses else Decimal("0"),
        }
```

### Pattern 4: Slippage Measurement
**What:** Measure execution quality by comparing our fill prices to leader's fills and market prices
**When to use:** Analyzing profit leakage and execution costs
**Example:**
```python
# Source: Transaction Cost Analysis (TCA) benchmarks
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class SlippageMeasurement:
    """Slippage breakdown for a single trade."""
    token_id: str
    action: str  # "BUY" or "SELL"

    # Prices
    leader_price: Decimal  # What leader paid
    our_price: Decimal     # What we paid
    market_price_at_signal: Decimal  # Market price when we detected signal

    # Slippage components
    execution_slippage_bps: Decimal  # Our price vs leader price
    delay_slippage_bps: Decimal      # Market price vs our price
    total_slippage_bps: Decimal      # Total cost

    # Dollar impact
    trade_size_dollars: Decimal
    slippage_cost_dollars: Decimal

class SlippageAnalyzer:
    """Analyze execution slippage and delay costs."""

    def measure_slippage(self, trade, leader_trade, market_price_at_detection) -> SlippageMeasurement:
        """Calculate slippage for a single trade.

        Args:
            trade: Our executed trade
            leader_trade: Leader's trade we're following
            market_price_at_detection: Mid price when signal detected
        """
        # Execution slippage: our price vs leader price
        if trade.action == "BUY":
            # Higher price = worse for buys
            exec_slip = ((trade.our_price - leader_trade.leader_price) / leader_trade.leader_price) * 10000
        else:
            # Lower price = worse for sells
            exec_slip = ((leader_trade.leader_price - trade.our_price) / leader_trade.leader_price) * 10000

        # Delay slippage: market moved between signal and execution
        if trade.action == "BUY":
            delay_slip = ((trade.our_price - market_price_at_detection) / market_price_at_detection) * 10000
        else:
            delay_slip = ((market_price_at_detection - trade.our_price) / market_price_at_detection) * 10000

        total_slip = exec_slip + delay_slip

        # Dollar cost of slippage
        if trade.action == "BUY":
            # If we paid 2% more, cost = 0.02 * trade_size
            slippage_cost = (total_slip / 10000) * trade.our_dollars
        else:
            # If we received 2% less, cost = 0.02 * trade_size
            slippage_cost = (total_slip / 10000) * trade.our_dollars

        return SlippageMeasurement(
            token_id=trade.token_id,
            action=trade.action,
            leader_price=leader_trade.leader_price,
            our_price=trade.our_price,
            market_price_at_signal=market_price_at_detection,
            execution_slippage_bps=exec_slip,
            delay_slippage_bps=delay_slip,
            total_slippage_bps=total_slip,
            trade_size_dollars=trade.our_dollars,
            slippage_cost_dollars=slippage_cost,
        )

    def aggregate_slippage(self, measurements: list[SlippageMeasurement]) -> dict:
        """Calculate aggregate slippage statistics."""
        total_volume = sum(m.trade_size_dollars for m in measurements)
        total_cost = sum(m.slippage_cost_dollars for m in measurements)

        # Volume-weighted average slippage
        vwap_exec = sum(m.execution_slippage_bps * m.trade_size_dollars for m in measurements) / total_volume
        vwap_delay = sum(m.delay_slippage_bps * m.trade_size_dollars for m in measurements) / total_volume

        return {
            'total_trades': len(measurements),
            'total_volume': float(total_volume),
            'total_slippage_cost': float(total_cost),
            'avg_execution_slippage_bps': float(vwap_exec),
            'avg_delay_slippage_bps': float(vwap_delay),
            'total_slippage_bps': float(vwap_exec + vwap_delay),
            'slippage_as_pct_of_volume': float((total_cost / total_volume) * 100) if total_volume > 0 else 0,
        }
```

### Pattern 5: Chart Generation with matplotlib
**What:** Generate equity curve + drawdown visualization
**When to use:** End-of-session reporting for visual analysis
**Example:**
```python
# Source: PyQuantLab matplotlib tutorial + financial charting best practices
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

class ChartGenerator:
    """Generate equity curve and drawdown charts."""

    def plot_equity_and_drawdown(self, equity_df: pd.DataFrame, output_path: Path):
        """Create 2-panel chart: equity curve + drawdown.

        Args:
            equity_df: DataFrame with 'equity' column and datetime index
            output_path: Where to save the PNG file
        """
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

        # Top panel: Equity curve
        ax1.plot(equity_df.index, equity_df['equity'], linewidth=2, color='steelblue')
        ax1.set_ylabel('Equity ($)', fontsize=12)
        ax1.set_title('Equity Curve', fontsize=14, fontweight='bold')
        ax1.grid(True, alpha=0.3)

        # Add starting capital line
        starting = equity_df['equity'].iloc[0]
        ax1.axhline(y=starting, color='gray', linestyle='--', alpha=0.5, label='Starting Capital')
        ax1.legend()

        # Bottom panel: Drawdown
        peak = equity_df['equity'].cummax()
        drawdown = (equity_df['equity'] - peak) / peak * 100  # As percentage

        ax2.fill_between(drawdown.index, 0, drawdown, color='red', alpha=0.3)
        ax2.plot(drawdown.index, drawdown, linewidth=1.5, color='darkred')
        ax2.set_ylabel('Drawdown (%)', fontsize=12)
        ax2.set_xlabel('Time', fontsize=12)
        ax2.set_title('Drawdown from Peak', fontsize=14, fontweight='bold')
        ax2.grid(True, alpha=0.3)

        # Annotate max drawdown
        max_dd_idx = drawdown.idxmin()
        max_dd_val = drawdown.min()
        ax2.annotate(f'Max DD: {max_dd_val:.2f}%',
                    xy=(max_dd_idx, max_dd_val),
                    xytext=(max_dd_idx, max_dd_val - 5),
                    arrowprops=dict(arrowstyle='->', color='black'),
                    fontsize=10, color='black')

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

    def plot_trade_scatter(self, attributed_trades: list, output_path: Path):
        """Scatter plot of trade outcomes."""
        import numpy as np

        wins = [t for t in attributed_trades if t.status == "win"]
        losses = [t for t in attributed_trades if t.status == "loss"]

        fig, ax = plt.subplots(figsize=(10, 6))

        # Plot wins
        if wins:
            win_times = [t.entry_timestamp for t in wins]
            win_pnl = [float(t.realized_pnl) for t in wins]
            ax.scatter(win_times, win_pnl, color='green', alpha=0.6, s=100, label='Wins')

        # Plot losses
        if losses:
            loss_times = [t.entry_timestamp for t in losses]
            loss_pnl = [float(t.realized_pnl) for t in losses]
            ax.scatter(loss_times, loss_pnl, color='red', alpha=0.6, s=100, label='Losses')

        ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        ax.set_ylabel('Trade PnL ($)', fontsize=12)
        ax.set_xlabel('Trade Entry Time', fontsize=12)
        ax.set_title('Trade Outcomes', fontsize=14, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
```

### Anti-Patterns to Avoid
- **Float for money calculations:** Always use Decimal; float has precision errors that accumulate
- **Calculating drawdown from returns:** Calculate from equity curve directly; more accurate
- **Single timestamp per trade:** Need entry + exit timestamps for attribution and holding period analysis
- **Ignoring open positions in equity curve:** Must mark-to-market open positions at current prices
- **Not persisting raw trade data:** Charts can be regenerated; raw data cannot be recovered

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Time-series resampling | Manual bucketing loops | pandas resample() | Handles timezone, irregular intervals, multiple aggregations |
| Drawdown calculation | Manual peak tracking | pandas cummax() | Vectorized, handles edge cases (all-time-low), battle-tested |
| Chart generation | Custom plotting code | matplotlib with pandas integration | df.plot() auto-handles datetime axes, formatting |
| JSONL parsing | Manual file reading | json.loads() per line | Handles encoding, errors, malformed JSON gracefully |
| Trade matching (entry/exit) | Manual dict tracking | pandas merge on token_id + timestamp window | Handles partial fills, multiple exits |

**Key insight:** pandas is purpose-built for financial time-series analysis with datetime indexing, resampling, rolling windows, and cummax for drawdown. matplotlib integrates seamlessly with pandas DataFrames. Don't recreate these capabilities.

## Common Pitfalls

### Pitfall 1: Not Marking Open Positions to Market
**What goes wrong:** Equity curve only reflects realized PnL; open positions ignored until closed
**Why it happens:** Easier to track closed trades; unrealized PnL requires current prices
**How to avoid:** Record PnL snapshot on every trade event AND periodic intervals with mark-to-market
**Warning signs:** Equity curve flat when positions open, spikes when positions close

### Pitfall 2: Drawdown Calculated from Returns Instead of Equity
**What goes wrong:** Max drawdown understated; recovery time incorrect
**Why it happens:** Return-based formulas simpler but less accurate
**How to avoid:** Build equity curve first (cumulative), then calculate drawdown from equity
**Warning signs:** Drawdown too small compared to visual chart inspection

### Pitfall 3: Ignoring Sizing When Measuring Slippage
**What goes wrong:** Slippage percentages misleading; small trades skew averages
**Why it happens:** Simple average slippage treats $1 trade same as $100 trade
**How to avoid:** Use volume-weighted average slippage (VWAP); weight by trade size
**Warning signs:** Slippage stats don't match dollar impact on PnL

### Pitfall 4: Trade Attribution Without Exit Tracking
**What goes wrong:** Can't link trades to final outcomes; "open" trades stay open forever
**Why it happens:** Entry events easy to capture; exit events require sell-side detection
**How to avoid:** Track both BUY and SELL events; match by token_id to close trades
**Warning signs:** All trades marked "open"; realized PnL always zero

### Pitfall 5: Chart Timestamps Without Timezone Awareness
**What goes wrong:** Charts show wrong times; hard to correlate with logs
**Why it happens:** datetime.now() vs datetime.now(timezone.utc) inconsistency
**How to avoid:** Always use timezone-aware datetimes; store UTC, display local
**Warning signs:** Chart times don't match log timestamps; off by hours

### Pitfall 6: Over-Aggregating Time Series Data
**What goes wrong:** Lose intraday drawdown detail; max drawdown underestimated
**Why it happens:** Aggregating to 5-min or 1-hour buckets loses tick-level extremes
**How to avoid:** Snapshot equity on every trade event + periodic intervals (2-10 seconds)
**Warning signs:** Drawdown on minute-bars less severe than visual inspection of equity

## Code Examples

Verified patterns from codebase and industry sources:

### Integrating with Existing SessionReplayer
```python
# Source: src/framework/replay.py + new EquityTracker integration
from src.framework.replay import SessionReplayer
from src.analysis.equity_tracker import EquityTracker
from src.analysis.drawdown import DrawdownAnalyzer

def replay_with_equity_tracking(session_path, strategy):
    """Replay session while building equity curve."""

    replayer = SessionReplayer(session_path, strategy)
    count = replayer.load()

    # Initialize equity tracker
    config = replayer._merge_config()
    starting_capital = Decimal(str(config.get('scaling', {}).get('our_capital', '100')))
    tracker = EquityTracker(starting_capital)

    # Run replay
    result = replayer.run(collect_trades=True)

    # Build equity snapshots from trade history
    current_prices = replayer.final_prices
    for i, event in enumerate(replayer.events):
        # Record snapshot after each trade
        tracker.record_snapshot(
            timestamp=event.trade.timestamp,
            portfolio=strategy.portfolio,
            current_prices={k: v.mid or v.bid for k, v in current_prices.items()}
        )

    # Convert to DataFrame and analyze
    equity_df = tracker.to_dataframe()
    dd_analyzer = DrawdownAnalyzer()
    dd_metrics = dd_analyzer.analyze(equity_df)

    print(f"Max Drawdown: {dd_metrics['max_drawdown_pct']:.2f}%")
    print(f"Recovery Time: {dd_metrics['recovery_time_min']:.1f} minutes")

    return result, equity_df, dd_metrics
```

### Persisting Enriched Trade Data
```python
# Source: src/framework/recorder.py + attribution enrichment
import json
from pathlib import Path
from datetime import datetime
from decimal import Decimal

class EnrichedTradeRecorder:
    """Records trades with full attribution metadata."""

    def __init__(self, output_file: Path):
        self.output_file = output_file
        self.file = None

    def start_session(self):
        """Open file for writing."""
        self.file = open(self.output_file, 'w', encoding='utf-8')

    def record_trade(self, attributed_trade):
        """Write enriched trade to JSONL."""
        record = {
            'type': 'attributed_trade',
            'timestamp': attributed_trade.entry_timestamp.isoformat(),
            'market_id': attributed_trade.market_id,
            'token_id': attributed_trade.token_id,
            'side': attributed_trade.side,
            'action': attributed_trade.action,
            'entry_shares': str(attributed_trade.entry_shares),
            'entry_price': str(attributed_trade.entry_price),
            'entry_cost': str(attributed_trade.entry_cost),
            'exit_timestamp': attributed_trade.exit_timestamp.isoformat() if attributed_trade.exit_timestamp else None,
            'exit_price': str(attributed_trade.exit_price) if attributed_trade.exit_price else None,
            'status': attributed_trade.status,
            'realized_pnl': str(attributed_trade.realized_pnl),
            'unrealized_pnl': str(attributed_trade.unrealized_pnl),
            'leader_dollars': str(attributed_trade.leader_dollars),
            'leader_price': str(attributed_trade.leader_price),
        }
        self.file.write(json.dumps(record) + '\n')
        self.file.flush()

    def end_session(self):
        """Close file."""
        if self.file:
            self.file.close()
            self.file = None
```

### Console Summary Report
```python
# Source: Industry TCA reporting + codebase replay summary pattern
def print_performance_summary(result, equity_df, dd_metrics, slippage_stats, trade_summary):
    """Print comprehensive performance summary to console."""

    print()
    print("=" * 70)
    print("  PERFORMANCE ANALYSIS SUMMARY")
    print("=" * 70)

    # PnL
    print()
    print("  P&L:")
    print(f"    Realized:       ${result.realized_pnl:+.2f}")
    print(f"    Unrealized:     ${result.unrealized_pnl:+.2f}")
    print(f"    Total:          ${result.total_pnl:+.2f}")

    # Trade attribution
    print()
    print("  Trade Attribution:")
    print(f"    Total trades:   {trade_summary['total_trades']}")
    print(f"    Wins:           {trade_summary['wins']} ({trade_summary['win_rate']*100:.1f}%)")
    print(f"    Losses:         {trade_summary['losses']}")
    print(f"    Open:           {trade_summary['open']}")
    print(f"    Avg win:        ${trade_summary['avg_win']:+.2f}")
    print(f"    Avg loss:       ${trade_summary['avg_loss']:+.2f}")

    # Drawdown
    print()
    print("  Drawdown:")
    print(f"    Max drawdown:   {dd_metrics['max_drawdown_pct']:.2f}%")
    print(f"    DD duration:    {dd_metrics['drawdown_duration_min']:.1f} minutes")
    if dd_metrics['recovery_time_min']:
        print(f"    Recovery time:  {dd_metrics['recovery_time_min']:.1f} minutes")
    else:
        print(f"    Recovery time:  NOT RECOVERED")

    # Slippage
    print()
    print("  Execution Quality:")
    print(f"    Avg exec slippage:  {slippage_stats['avg_execution_slippage_bps']:.1f} bps")
    print(f"    Avg delay cost:     {slippage_stats['avg_delay_slippage_bps']:.1f} bps")
    print(f"    Total slippage:     {slippage_stats['total_slippage_bps']:.1f} bps")
    print(f"    Slippage cost:      ${slippage_stats['total_slippage_cost']:.2f}")
    print(f"    As % of volume:     {slippage_stats['slippage_as_pct_of_volume']:.2f}%")

    print()
    print("=" * 70)
    print()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual equity tracking | pandas DataFrame with datetime index | ~2015 | Automatic resampling, built-in time operations |
| Custom drawdown calculation | pandas cummax() method | pandas 0.18+ (2016) | Vectorized, handles edge cases correctly |
| Static reports | Interactive charts (Plotly) | ~2020 | Better for exploration; matplotlib still standard for static |
| CSV for trade data | JSONL for event streams | ~2018 | Append-only, preserves event order, human-readable |
| Simple slippage (%) | TCA with multiple benchmarks | ~2020 | Separates delay cost from execution cost |

**Deprecated/outdated:**
- Return-based drawdown: Use equity-based drawdown for accuracy
- Daily resampling for intraday strategies: Use tick-level or second-level snapshots
- Excel for equity curves: Use pandas + matplotlib for reproducibility
- Ignoring unrealized PnL: Mark-to-market is industry standard

## Open Questions

Things that couldn't be fully resolved:

1. **Periodic Snapshot Frequency**
   - What we know: Need both event-driven (on trades) and periodic (calendar time) snapshots
   - What's unclear: Optimal interval for periodic snapshots (2s, 5s, 10s trade-off between accuracy and data volume)
   - Recommendation: Start with 5-second intervals; adjust based on session duration and storage

2. **Market Resolution Detection**
   - What we know: Polymarket markets resolve; positions auto-close with final PnL
   - What's unclear: How to detect resolution vs. manual sale (API provides resolution data?)
   - Recommendation: Check Polymarket API for market status; if closed, mark all positions as resolved

3. **Profit Leakage Baseline (Selection Gap)**
   - What we know: Need to compare our PnL vs. "if we followed every leader trade"
   - What's unclear: Whether to run full simulation of 100% follow strategy as baseline
   - Recommendation: Implement "full follow" benchmark simulation for selection gap measurement

4. **Report File Format**
   - What we know: Need both console summary and persistent file reports
   - What's unclear: JSON vs. HTML vs. plain text for detailed reports
   - Recommendation: Console summary (text), detailed metrics (JSON), charts (PNG), optional HTML combining all

5. **Cross-Session Analysis**
   - What we know: Data persisted to disk enables multi-session analysis
   - What's unclear: Whether Phase 2 includes cross-session aggregation or single-session only
   - Recommendation: Design for single-session analysis; structure data to enable Phase 3+ aggregation

## Sources

### Primary (HIGH confidence)
- **Codebase analysis** (local files):
  - `src/core/portfolio.py` - Existing PnL tracking infrastructure
  - `src/framework/replay.py` - ExecutedTrade, ReplayResult structures
  - `src/simulation/follow_metrics.py` - Follow quality metrics pattern
  - `src/framework/recorder.py` - JSONL event persistence pattern
- **Python pandas documentation** - https://pandas.pydata.org/docs/
  - Time series functionality: https://pandas.pydata.org/docs/user_guide/timeseries.html
  - DataFrame API: https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html
- **matplotlib documentation** - https://matplotlib.org/stable/contents.html
  - Financial plotting: https://matplotlib.org/stable/gallery/index.html

### Secondary (MEDIUM confidence)
- [Equity Curve + Max Drawdown on One Chart with Matplotlib | PyQuantLab](https://pyquantlab.medium.com/equity-curve-max-drawdown-on-one-chart-with-matplotlib-1f6a40a8ac99) - Verified pattern for 2-panel equity+drawdown charts (Sept 2025)
- [Event-Driven Backtesting with Python - Part VII | QuantStart](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-VII/) - Verified PnL tracking in event-driven systems
- [Execution Slippage Measurement | QuestDB](https://questdb.com/glossary/execution-slippage-measurement/) - Verified TCA methodology and slippage calculation
- [Execution Insights Through TCA | Talos](https://www.talos.com/insights/execution-insights-through-transaction-cost-analysis-tca-benchmarks-and-slippage) - Verified benchmarks: arrival price, TWAP, market impact
- [How to compute drawdown on an investment | PyQuant News](https://www.pyquantnews.com/the-pyquant-newsletter/how-to-compute-drawdown-on-an-investment) - Verified cummax() pattern for drawdown (2025)
- [Advanced Trading Infrastructure - Portfolio Class | QuantStart](https://www.quantstart.com/articles/Advanced-Trading-Infrastructure-Portfolio-Class/) - Verified Position class tracking realized/unrealized PnL
- [Pandas in Financial Market Data Analysis | PyQuant News](https://www.pyquantnews.com/free-python-resources/pandas-in-financial-market-data-analysis) - Verified pandas time-series capabilities
- [Data Persistence — Python 3.14 docs](https://docs.python.org/3/library/persistence.html) - Verified JSON, CSV, SQLite tradeoffs

### Tertiary (LOW confidence - for awareness)
- [GitHub - CoinAlpha/pnl-analysis](https://github.com/CoinAlpha/pnl-analysis) - Example Jupyter notebook for PnL calculation
- [mplfinance library](https://pypi.org/project/mplfinance/) - Alternative for OHLC charts (not needed for equity curves)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pandas/matplotlib industry standard, already using Decimal/JSON/JSONL
- Architecture: HIGH - Built on existing Portfolio, SessionReplayer, FollowMetricsTracker patterns
- Pitfalls: HIGH - Based on codebase analysis (timezone handling, mark-to-market) + financial analysis common mistakes
- Code examples: HIGH - Adapted from codebase patterns + verified pandas/TCA methodologies
- Slippage analysis: MEDIUM - TCA benchmarks verified from multiple sources, codebase provides trade data

**Research date:** 2026-01-31
**Valid until:** 2026-03-31 (60 days - pandas/matplotlib stable, TCA methodologies established)

**Notes for planner:**
- Portfolio class already has calculate_pnl(); extend to emit snapshots
- SessionReplayer already collects trades with collect_trades=True
- FollowMetricsTracker demonstrates time-bucketing pattern (2-second buckets)
- JSONL format already proven via SessionRecorder; extend for enriched trades
- matplotlib charts complement console output; both needed per requirements
- Three gap metrics (price, sizing, selection) require three separate measurements
- Drawdown from equity curve (not returns) for accuracy
- Volume-weighted slippage to match dollar impact
- Mark-to-market open positions for accurate equity curve
