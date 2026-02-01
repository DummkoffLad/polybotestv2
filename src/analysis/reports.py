"""Report generation: console summaries and charts for performance analysis."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend for headless environments
import matplotlib.pyplot as plt
import pandas as pd


class ReportGenerator:
    """Generate performance reports: console summary, charts, and JSON output."""

    def __init__(self, output_dir: Path = Path("data/reports")):
        """Initialize report generator.

        Args:
            output_dir: Directory for saving charts and reports
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def print_console_summary(
        self,
        replay_result: Any,
        trade_summary: Dict,
        drawdown_metrics: Dict,
        slippage_stats: Dict,
        sizing_stats: Dict,
        selection_stats: Dict
    ) -> None:
        """Print formatted console summary.

        Args:
            replay_result: ReplayResult with session metadata
            trade_summary: TradeAttributor summary dict
            drawdown_metrics: DrawdownAnalyzer metrics
            slippage_stats: SlippageAnalyzer aggregate slippage
            sizing_stats: SlippageAnalyzer aggregate sizing
            selection_stats: SlippageAnalyzer aggregate selection
        """
        print("=" * 70)
        print("  PERFORMANCE ANALYSIS SUMMARY")
        print("=" * 70)
        print()

        # Session metadata
        session_id = replay_result.session_id or "N/A"
        strategy_name = replay_result.strategy_name or "N/A"
        duration_min = replay_result.session_duration_minutes
        events = replay_result.events_processed

        print(f"  Session: {session_id} | Strategy: {strategy_name}")
        print(f"  Duration: {duration_min:.1f} min | Events: {events}")
        print()

        # P&L summary
        realized = replay_result.realized_pnl
        unrealized = replay_result.unrealized_pnl
        total = replay_result.total_pnl

        print("  P&L:")
        print(f"    Realized:       ${realized:+.2f}")
        print(f"    Unrealized:     ${unrealized:+.2f}")
        print(f"    Total:          ${total:+.2f}")
        print()

        # Trade attribution
        total_trades = trade_summary.get('total_trades', 0)
        wins = trade_summary.get('wins', 0)
        losses = trade_summary.get('losses', 0)
        open_trades = trade_summary.get('open', 0)
        win_rate = float(trade_summary.get('win_rate', 0))
        avg_win = float(trade_summary.get('avg_win', 0))
        avg_loss = float(trade_summary.get('avg_loss', 0))
        best_trade = float(trade_summary.get('best_trade', 0))
        worst_trade = float(trade_summary.get('worst_trade', 0))

        print("  Trade Attribution:")
        print(f"    Total trades:   {total_trades}")
        if total_trades > 0:
            print(f"    Wins:           {wins} ({win_rate:.1f}%)")
            print(f"    Losses:         {losses}")
            print(f"    Open:           {open_trades}")
            print(f"    Avg win:        ${avg_win:+.4f}")
            print(f"    Avg loss:       ${avg_loss:+.4f}")
            print(f"    Best trade:     ${best_trade:+.4f}")
            print(f"    Worst trade:    ${worst_trade:+.4f}")
        else:
            print("    N/A (no trades)")
        print()

        # Drawdown
        max_dd_pct = drawdown_metrics.get('max_drawdown_pct', 0)
        dd_duration = drawdown_metrics.get('drawdown_duration_min', 0)
        recovery = drawdown_metrics.get('recovery_time_min')
        current_dd = drawdown_metrics.get('current_drawdown_pct', 0)

        print("  Drawdown:")
        print(f"    Max drawdown:   {max_dd_pct:.2f}%")
        print(f"    DD duration:    {dd_duration:.1f} min")
        if recovery is not None:
            print(f"    Recovery:       {recovery:.1f} min")
        else:
            print(f"    Recovery:       NOT RECOVERED")
        print(f"    Current DD:     {current_dd:.2f}%")
        print()

        # Execution quality
        exec_bps = slippage_stats.get('avg_execution_slippage_bps', 0)
        delay_bps = slippage_stats.get('avg_delay_slippage_bps', 0)
        total_bps = slippage_stats.get('total_slippage_bps', 0)
        cost = slippage_stats.get('total_slippage_cost', 0)

        print("  Execution Quality:")
        if slippage_stats.get('total_trades', 0) > 0:
            print(f"    Avg exec slip:  {exec_bps:.1f} bps")
            print(f"    Avg delay cost: {delay_bps:.1f} bps")
            print(f"    Total slippage: {total_bps:.1f} bps")
            print(f"    Slippage cost:  ${cost:.4f}")
        else:
            print("    N/A (no trades)")
        print()

        # Sizing analysis
        avg_ratio = sizing_stats.get('avg_sizing_ratio', 0)
        undersized = sizing_stats.get('undersized_count', 0)
        oversized = sizing_stats.get('oversized_count', 0)

        print("  Sizing Analysis:")
        if sizing_stats.get('total_trades', 0) > 0:
            print(f"    Avg sizing ratio: {avg_ratio:.2f}x")
            print(f"    Undersized:     {undersized}")
            print(f"    Oversized:      {oversized}")
        else:
            print("    N/A (no trades)")
        print()

        # Selection (skipped trades)
        skipped_count = selection_stats.get('total_skipped', 0)
        skip_reasons = selection_stats.get('skip_reasons', {})

        print("  Selection (Skipped Trades):")
        print(f"    Trades skipped: {skipped_count}")
        if skip_reasons:
            # Top 3 reasons
            top_reasons = sorted(skip_reasons.items(), key=lambda x: -x[1])[:3]
            reason_str = ", ".join(f"{r}({c})" for r, c in top_reasons)
            print(f"    Top reasons:    {reason_str}")
        else:
            print("    Top reasons:    N/A")

        print("=" * 70)
        print()

    def generate_equity_chart(
        self,
        equity_df: pd.DataFrame,
        session_id: str
    ) -> Path:
        """Generate equity curve + drawdown chart.

        Args:
            equity_df: DataFrame with 'equity' column and datetime index
            session_id: Session identifier for filename

        Returns:
            Path to saved PNG file
        """
        from .drawdown import DrawdownAnalyzer

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

        # Top panel: Equity curve
        ax1.plot(equity_df.index, equity_df['equity'], linewidth=2, color='#2E86AB')
        starting_capital = equity_df['equity'].iloc[0]
        ax1.axhline(starting_capital, color='gray', linestyle='--', alpha=0.5, label='Starting Capital')
        ax1.set_ylabel('Equity ($)', fontsize=12)
        ax1.set_title('Equity Curve', fontsize=14, fontweight='bold')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Bottom panel: Drawdown percentage
        analyzer = DrawdownAnalyzer()
        drawdown_series = analyzer.get_drawdown_series(equity_df)

        ax2.fill_between(drawdown_series.index, drawdown_series.values, 0,
                         color='#C1292E', alpha=0.3)
        ax2.plot(drawdown_series.index, drawdown_series.values,
                linewidth=1.5, color='#C1292E')

        # Annotate max drawdown
        if len(drawdown_series) > 0 and drawdown_series.min() < 0:
            max_dd_idx = drawdown_series.idxmin()
            max_dd_val = drawdown_series.min()
            ax2.annotate(f'Max DD: {max_dd_val:.2f}%',
                        xy=(max_dd_idx, max_dd_val),
                        xytext=(10, -20),
                        textcoords='offset points',
                        fontsize=10,
                        bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.7),
                        arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'))

        ax2.set_ylabel('Drawdown (%)', fontsize=12)
        ax2.set_xlabel('Time', fontsize=12)
        ax2.set_title('Drawdown from Peak', fontsize=14, fontweight='bold')
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()

        # Save
        output_path = self.output_dir / f"equity_{session_id}.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close(fig)

        return output_path

    def generate_trade_scatter(
        self,
        attributed_trades: List[Any],
        session_id: str
    ) -> Path:
        """Generate trade scatter plot.

        Args:
            attributed_trades: List of AttributedTrade objects
            session_id: Session identifier for filename

        Returns:
            Path to saved PNG file
        """
        fig, ax = plt.subplots(figsize=(12, 6))

        # Separate trades by status
        wins = [t for t in attributed_trades if t.status == 'win']
        losses = [t for t in attributed_trades if t.status == 'loss']
        open_trades = [t for t in attributed_trades if t.status == 'open']

        # Plot wins (green)
        if wins:
            win_times = [t.entry_timestamp for t in wins]
            win_pnl = [float(t.realized_pnl) for t in wins]
            ax.scatter(win_times, win_pnl, color='#2ECC71', s=50, alpha=0.7, label='Wins')

        # Plot losses (red)
        if losses:
            loss_times = [t.entry_timestamp for t in losses]
            loss_pnl = [float(t.realized_pnl) for t in losses]
            ax.scatter(loss_times, loss_pnl, color='#E74C3C', s=50, alpha=0.7, label='Losses')

        # Plot open (gray)
        if open_trades:
            open_times = [t.entry_timestamp for t in open_trades]
            open_pnl = [float(t.unrealized_pnl) for t in open_trades]
            ax.scatter(open_times, open_pnl, color='#95A5A6', s=50, alpha=0.5, label='Open', marker='^')

        # Zero line
        ax.axhline(0, color='black', linestyle='-', linewidth=0.8, alpha=0.5)

        ax.set_xlabel('Entry Time', fontsize=12)
        ax.set_ylabel('Realized PnL ($)', fontsize=12)
        ax.set_title('Trade Performance Scatter', fontsize=14, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        # Save
        output_path = self.output_dir / f"trades_{session_id}.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close(fig)

        return output_path

    def save_json_report(
        self,
        replay_result: Any,
        trade_summary: Dict,
        drawdown_metrics: Dict,
        slippage_stats: Dict,
        sizing_stats: Dict,
        selection_stats: Dict,
        session_id: str
    ) -> Path:
        """Save machine-readable JSON report.

        Args:
            replay_result: ReplayResult with session metadata
            trade_summary: TradeAttributor summary dict
            drawdown_metrics: DrawdownAnalyzer metrics
            slippage_stats: SlippageAnalyzer aggregate slippage
            sizing_stats: SlippageAnalyzer aggregate sizing
            selection_stats: SlippageAnalyzer aggregate selection
            session_id: Session identifier for filename

        Returns:
            Path to saved JSON file
        """
        # Convert Decimal to str, datetime to isoformat
        report = {
            'session_id': session_id,
            'strategy_name': replay_result.strategy_name,
            'session_duration_minutes': float(replay_result.session_duration_minutes),
            'events_processed': replay_result.events_processed,
            'pnl': {
                'realized': str(replay_result.realized_pnl),
                'unrealized': str(replay_result.unrealized_pnl),
                'total': str(replay_result.total_pnl),
            },
            'trade_attribution': {
                'total_trades': trade_summary.get('total_trades', 0),
                'wins': trade_summary.get('wins', 0),
                'losses': trade_summary.get('losses', 0),
                'open': trade_summary.get('open', 0),
                'win_rate': str(trade_summary.get('win_rate', Decimal('0'))),
                'total_realized_pnl': str(trade_summary.get('total_realized_pnl', Decimal('0'))),
                'total_unrealized_pnl': str(trade_summary.get('total_unrealized_pnl', Decimal('0'))),
                'avg_win': str(trade_summary.get('avg_win', Decimal('0'))),
                'avg_loss': str(trade_summary.get('avg_loss', Decimal('0'))),
                'best_trade': str(trade_summary.get('best_trade', Decimal('0'))),
                'worst_trade': str(trade_summary.get('worst_trade', Decimal('0'))),
            },
            'drawdown': {
                'max_drawdown_pct': drawdown_metrics.get('max_drawdown_pct', 0),
                'max_drawdown_value': drawdown_metrics.get('max_drawdown_value', 0),
                'drawdown_start': drawdown_metrics.get('drawdown_start').isoformat() if drawdown_metrics.get('drawdown_start') else None,
                'drawdown_bottom': drawdown_metrics.get('drawdown_bottom').isoformat() if drawdown_metrics.get('drawdown_bottom') else None,
                'drawdown_duration_min': drawdown_metrics.get('drawdown_duration_min', 0),
                'recovery_time_min': drawdown_metrics.get('recovery_time_min'),
                'current_drawdown_pct': drawdown_metrics.get('current_drawdown_pct', 0),
            },
            'slippage': slippage_stats,
            'sizing': sizing_stats,
            'selection': selection_stats,
        }

        output_path = self.output_dir / f"report_{session_id}.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return output_path
