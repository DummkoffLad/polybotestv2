"""Unit tests for ReportGenerator."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import json
import sys
from io import StringIO

import pytest
import pandas as pd

from src.analysis.reports import ReportGenerator


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Create a temporary output directory."""
    return tmp_path / "reports"


@pytest.fixture
def sample_replay_result():
    """Create sample replay result."""
    from dataclasses import dataclass

    @dataclass
    class MockReplayResult:
        session_id: str = "test_session_123"
        strategy_name: str = "mirror"
        session_duration_minutes: float = 45.5
        events_processed: int = 100
        realized_pnl: Decimal = Decimal("5.25")
        unrealized_pnl: Decimal = Decimal("1.75")
        total_pnl: Decimal = Decimal("7.00")

    return MockReplayResult()


@pytest.fixture
def sample_trade_summary():
    """Create sample trade summary."""
    return {
        'total_trades': 10,
        'wins': 6,
        'losses': 3,
        'open': 1,
        'win_rate': Decimal("66.67"),
        'total_realized_pnl': Decimal("5.25"),
        'total_unrealized_pnl': Decimal("1.75"),
        'avg_win': Decimal("1.2500"),
        'avg_loss': Decimal("-0.8333"),
        'best_trade': Decimal("2.5000"),
        'worst_trade': Decimal("-1.5000"),
    }


@pytest.fixture
def sample_drawdown_metrics():
    """Create sample drawdown metrics."""
    return {
        'max_drawdown_pct': -12.5,
        'max_drawdown_value': -2.5,
        'drawdown_start': datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc),
        'drawdown_bottom': datetime(2026, 1, 30, 10, 30, 0, tzinfo=timezone.utc),
        'drawdown_duration_min': 30.0,
        'recovery_time_min': 15.0,
        'current_drawdown_pct': -5.0,
    }


@pytest.fixture
def sample_slippage_stats():
    """Create sample slippage stats."""
    return {
        'total_trades': 10,
        'total_volume': 100.0,
        'total_slippage_cost': 0.5,
        'avg_execution_slippage_bps': 25.0,
        'avg_delay_slippage_bps': 15.0,
        'total_slippage_bps': 40.0,
        'slippage_as_pct_of_volume': 0.5,
    }


@pytest.fixture
def sample_sizing_stats():
    """Create sample sizing stats."""
    return {
        'total_trades': 10,
        'avg_sizing_ratio': 0.95,
        'undersized_count': 3,
        'proportional_count': 6,
        'oversized_count': 1,
        'total_dollar_difference': -5.0,
    }


@pytest.fixture
def sample_selection_stats():
    """Create sample selection stats."""
    return {
        'total_skipped': 5,
        'skip_reasons': {'cap': 3, 'capital': 2},
        'total_missed_pnl': -1.25,
        'known_outcomes': 3,
    }


def test_console_summary_prints_without_error(
    tmp_output_dir,
    sample_replay_result,
    sample_trade_summary,
    sample_drawdown_metrics,
    sample_slippage_stats,
    sample_sizing_stats,
    sample_selection_stats
):
    """Test that console summary prints without error."""
    generator = ReportGenerator(tmp_output_dir)

    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        generator.print_console_summary(
            sample_replay_result,
            sample_trade_summary,
            sample_drawdown_metrics,
            sample_slippage_stats,
            sample_sizing_stats,
            sample_selection_stats
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    # Verify output contains expected sections
    assert "PERFORMANCE ANALYSIS SUMMARY" in output
    assert "Session: test_session_123" in output
    assert "Strategy: mirror" in output
    assert "P&L:" in output
    assert "Trade Attribution:" in output
    assert "Drawdown:" in output
    assert "Execution Quality:" in output
    assert "Sizing Analysis:" in output
    assert "Selection (Skipped Trades):" in output


def test_console_summary_handles_empty_data(tmp_output_dir, sample_replay_result):
    """Test console summary with empty/zero data."""
    generator = ReportGenerator(tmp_output_dir)

    empty_trade_summary = {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'open': 0,
        'win_rate': Decimal("0"),
        'total_realized_pnl': Decimal("0"),
        'total_unrealized_pnl': Decimal("0"),
        'avg_win': Decimal("0"),
        'avg_loss': Decimal("0"),
        'best_trade': Decimal("0"),
        'worst_trade': Decimal("0"),
    }

    empty_drawdown = {
        'max_drawdown_pct': 0,
        'max_drawdown_value': 0,
        'drawdown_start': datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc),
        'drawdown_bottom': datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc),
        'drawdown_duration_min': 0,
        'recovery_time_min': None,  # No recovery
        'current_drawdown_pct': 0,
    }

    empty_slippage = {
        'total_trades': 0,
        'total_volume': 0,
        'total_slippage_cost': 0,
        'avg_execution_slippage_bps': 0,
        'avg_delay_slippage_bps': 0,
        'total_slippage_bps': 0,
        'slippage_as_pct_of_volume': 0,
    }

    empty_sizing = {
        'total_trades': 0,
        'avg_sizing_ratio': 0,
        'undersized_count': 0,
        'proportional_count': 0,
        'oversized_count': 0,
        'total_dollar_difference': 0,
    }

    empty_selection = {
        'total_skipped': 0,
        'skip_reasons': {},
        'total_missed_pnl': 0,
        'known_outcomes': 0,
    }

    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        generator.print_console_summary(
            sample_replay_result,
            empty_trade_summary,
            empty_drawdown,
            empty_slippage,
            empty_sizing,
            empty_selection
        )
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    # Verify N/A handling
    assert "N/A (no trades)" in output
    assert "NOT RECOVERED" in output
    assert "0 bps" in output or "N/A (no trades)" in output


def test_equity_chart_creates_file(tmp_output_dir):
    """Test equity chart generation creates PNG file."""
    generator = ReportGenerator(tmp_output_dir)

    # Create sample equity DataFrame
    timestamps = pd.date_range(start='2026-01-30 10:00', periods=10, freq='5min')
    equity_values = [100.0, 102.0, 101.5, 103.0, 102.5, 104.0, 103.0, 105.0, 106.0, 107.5]

    equity_df = pd.DataFrame({
        'equity': equity_values,
        'realized_pnl': [0.0, 2.0, 1.5, 3.0, 2.5, 4.0, 3.0, 5.0, 6.0, 7.5],
        'unrealized_pnl': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        'open_positions': [0, 1, 1, 2, 2, 3, 3, 2, 1, 0],
        'deployed_capital': [0.0, 10.0, 10.0, 20.0, 20.0, 30.0, 30.0, 20.0, 10.0, 0.0],
    }, index=timestamps)

    # Generate chart
    output_path = generator.generate_equity_chart(equity_df, "test_session")

    # Verify file created
    assert output_path.exists()
    assert output_path.suffix == '.png'
    assert output_path.stat().st_size > 0


def test_trade_scatter_creates_file(tmp_output_dir):
    """Test trade scatter chart generation creates PNG file."""
    from dataclasses import dataclass
    from datetime import timedelta

    @dataclass
    class MockTrade:
        entry_timestamp: datetime
        status: str
        realized_pnl: Decimal
        unrealized_pnl: Decimal

    generator = ReportGenerator(tmp_output_dir)

    # Create sample trades
    base_time = datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc)
    trades = [
        MockTrade(base_time, 'win', Decimal('1.25'), Decimal('0')),
        MockTrade(base_time + timedelta(minutes=5), 'win', Decimal('0.75'), Decimal('0')),
        MockTrade(base_time + timedelta(minutes=10), 'loss', Decimal('-0.50'), Decimal('0')),
        MockTrade(base_time + timedelta(minutes=15), 'win', Decimal('2.00'), Decimal('0')),
        MockTrade(base_time + timedelta(minutes=20), 'open', Decimal('0'), Decimal('0.50')),
    ]

    # Generate chart
    output_path = generator.generate_trade_scatter(trades, "test_session")

    # Verify file created
    assert output_path.exists()
    assert output_path.suffix == '.png'
    assert output_path.stat().st_size > 0


def test_json_report_creates_valid_json(
    tmp_output_dir,
    sample_replay_result,
    sample_trade_summary,
    sample_drawdown_metrics,
    sample_slippage_stats,
    sample_sizing_stats,
    sample_selection_stats
):
    """Test JSON report creation and validation."""
    generator = ReportGenerator(tmp_output_dir)

    # Generate report
    output_path = generator.save_json_report(
        sample_replay_result,
        sample_trade_summary,
        sample_drawdown_metrics,
        sample_slippage_stats,
        sample_sizing_stats,
        sample_selection_stats,
        "test_session"
    )

    # Verify file created
    assert output_path.exists()
    assert output_path.suffix == '.json'

    # Load and validate JSON
    with open(output_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Verify structure
    assert report['session_id'] == "test_session"
    assert report['strategy_name'] == "mirror"
    assert 'pnl' in report
    assert 'trade_attribution' in report
    assert 'drawdown' in report
    assert 'slippage' in report
    assert 'sizing' in report
    assert 'selection' in report


def test_json_report_decimal_serialization(
    tmp_output_dir,
    sample_replay_result,
    sample_trade_summary,
    sample_drawdown_metrics,
    sample_slippage_stats,
    sample_sizing_stats,
    sample_selection_stats
):
    """Test that Decimals are serialized as strings in JSON."""
    generator = ReportGenerator(tmp_output_dir)

    output_path = generator.save_json_report(
        sample_replay_result,
        sample_trade_summary,
        sample_drawdown_metrics,
        sample_slippage_stats,
        sample_sizing_stats,
        sample_selection_stats,
        "test_session"
    )

    # Load JSON
    with open(output_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Verify Decimals are strings
    assert isinstance(report['pnl']['realized'], str)
    assert report['pnl']['realized'] == "5.25"
    assert isinstance(report['trade_attribution']['avg_win'], str)
    assert report['trade_attribution']['avg_win'] == "1.2500"
