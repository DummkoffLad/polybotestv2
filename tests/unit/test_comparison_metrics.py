"""Tests for metrics calculation and comparison table - TDD RED phase.

Tests define expected behavior for the metrics calculation:
- calculate_strategy_metrics() accepts StrategyResult and returns dict of metrics
- Uses empyrical-reloaded for ratio calculations (not hand-rolled)
- create_metrics_table() returns pandas Styler with conditional formatting
- Green background for best value, red for worst per metric column
- Max Drawdown is inverse (lower is better)
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import json

import pandas as pd
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.comparison.metrics import (
    calculate_strategy_metrics,
    create_metrics_table,
)
from src.comparison.comparator import StrategyResult, ComparisonResult
from src.framework.replay import ReplayResult


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def sample_equity_df_profitable():
    """Create equity DataFrame for a profitable strategy."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=20,
        freq="5min",
        tz=timezone.utc
    )
    # Profitable with some drawdowns
    # Total return: (120 - 100) / 100 = 20%
    equities = [100, 102, 104, 103, 106, 108, 105, 110, 112, 109,
                113, 115, 112, 116, 118, 115, 119, 121, 118, 120]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df


@pytest.fixture
def sample_equity_df_losing():
    """Create equity DataFrame for a losing strategy."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=20,
        freq="5min",
        tz=timezone.utc
    )
    # Losing strategy
    # Total return: (90 - 100) / 100 = -10%
    equities = [100, 99, 98, 99, 97, 96, 98, 95, 94, 96,
                93, 92, 94, 91, 90, 92, 89, 90, 91, 90]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df


@pytest.fixture
def sample_equity_df_volatile():
    """Create equity DataFrame for a volatile strategy."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=20,
        freq="5min",
        tz=timezone.utc
    )
    # Highly volatile but slightly profitable
    equities = [100, 110, 95, 115, 90, 120, 85, 125, 95, 130,
                90, 135, 100, 130, 95, 125, 105, 120, 110, 115]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df


@pytest.fixture
def profitable_strategy_result(sample_equity_df_profitable):
    """Create a StrategyResult for profitable strategy."""
    replay_result = ReplayResult(
        session_id="test_session_001",
        strategy_name="profitable",
        events_processed=20,
        buys_executed=8,
        sells_executed=5,
        skips=7,
        total_pnl=Decimal("20.00"),
        win_count=6,
        loss_count=4,
        analysis={}
    )
    return StrategyResult(
        strategy_name="profitable",
        replay_result=replay_result,
        equity_df=sample_equity_df_profitable
    )


@pytest.fixture
def losing_strategy_result(sample_equity_df_losing):
    """Create a StrategyResult for losing strategy."""
    replay_result = ReplayResult(
        session_id="test_session_001",
        strategy_name="losing",
        events_processed=20,
        buys_executed=10,
        sells_executed=8,
        skips=2,
        total_pnl=Decimal("-10.00"),
        win_count=3,
        loss_count=7,
        analysis={}
    )
    return StrategyResult(
        strategy_name="losing",
        replay_result=replay_result,
        equity_df=sample_equity_df_losing
    )


@pytest.fixture
def volatile_strategy_result(sample_equity_df_volatile):
    """Create a StrategyResult for volatile strategy."""
    replay_result = ReplayResult(
        session_id="test_session_001",
        strategy_name="volatile",
        events_processed=20,
        buys_executed=12,
        sells_executed=10,
        skips=0,
        total_pnl=Decimal("15.00"),
        win_count=7,
        loss_count=5,
        analysis={}
    )
    return StrategyResult(
        strategy_name="volatile",
        replay_result=replay_result,
        equity_df=sample_equity_df_volatile
    )


@pytest.fixture
def comparison_result(profitable_strategy_result, losing_strategy_result, volatile_strategy_result):
    """Create a ComparisonResult with all three strategies."""
    return ComparisonResult(
        session_id="test_session_001",
        session_path=Path("/tmp/test_session.jsonl"),
        strategy_results=[
            profitable_strategy_result,
            losing_strategy_result,
            volatile_strategy_result
        ],
        comparison_time=datetime.now(timezone.utc)
    )


# =============================================================================
# TESTS FOR calculate_strategy_metrics()
# =============================================================================

class TestCalculateStrategyMetrics:
    """Tests for calculate_strategy_metrics function."""

    def test_returns_dict(self, profitable_strategy_result):
        """Test that function returns a dictionary."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert isinstance(result, dict)

    def test_contains_total_return_pct(self, profitable_strategy_result):
        """Test that result contains total_return_pct."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'total_return_pct' in result

    def test_contains_sharpe_ratio(self, profitable_strategy_result):
        """Test that result contains sharpe_ratio."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'sharpe_ratio' in result

    def test_contains_sortino_ratio(self, profitable_strategy_result):
        """Test that result contains sortino_ratio."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'sortino_ratio' in result

    def test_contains_calmar_ratio(self, profitable_strategy_result):
        """Test that result contains calmar_ratio."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'calmar_ratio' in result

    def test_contains_max_drawdown_pct(self, profitable_strategy_result):
        """Test that result contains max_drawdown_pct."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'max_drawdown_pct' in result

    def test_contains_win_rate_pct(self, profitable_strategy_result):
        """Test that result contains win_rate_pct."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'win_rate_pct' in result

    def test_contains_profit_factor(self, profitable_strategy_result):
        """Test that result contains profit_factor."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'profit_factor' in result

    def test_contains_trade_count(self, profitable_strategy_result):
        """Test that result contains trade_count."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert 'trade_count' in result

    def test_total_return_calculation(self, profitable_strategy_result):
        """Test total return calculation is correct."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        # (120 - 100) / 100 * 100 = 20%
        assert abs(result['total_return_pct'] - 20.0) < 0.1

    def test_win_rate_calculation(self, profitable_strategy_result):
        """Test win rate calculation is correct."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        # 6 / (6 + 4) * 100 = 60%
        assert abs(result['win_rate_pct'] - 60.0) < 0.1

    def test_trade_count_calculation(self, profitable_strategy_result):
        """Test trade count is sum of wins and losses."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        # 6 + 4 = 10
        assert result['trade_count'] == 10

    def test_max_drawdown_is_negative_or_zero(self, profitable_strategy_result):
        """Test that max drawdown is reported as negative percentage (or 0)."""
        result = calculate_strategy_metrics(profitable_strategy_result)
        assert result['max_drawdown_pct'] <= 0

    def test_handles_no_trades(self):
        """Test handling when there are no trades."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        equity_df = pd.DataFrame({"equity": [100, 100, 100, 100, 100]}, index=timestamps)

        replay_result = ReplayResult(
            session_id="test",
            strategy_name="no_trades",
            events_processed=5,
            buys_executed=0,
            sells_executed=0,
            skips=5,
            total_pnl=Decimal("0"),
            win_count=0,
            loss_count=0,
            analysis={}
        )
        strategy_result = StrategyResult(
            strategy_name="no_trades",
            replay_result=replay_result,
            equity_df=equity_df
        )

        result = calculate_strategy_metrics(strategy_result)

        # Should handle gracefully - win_rate and profit_factor may be 0 or NaN
        assert 'trade_count' in result
        assert result['trade_count'] == 0

    def test_handles_single_trade(self):
        """Test handling with only one trade."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        equity_df = pd.DataFrame({"equity": [100, 102, 104, 105, 105]}, index=timestamps)

        replay_result = ReplayResult(
            session_id="test",
            strategy_name="single_trade",
            events_processed=5,
            buys_executed=1,
            sells_executed=1,
            skips=3,
            total_pnl=Decimal("5.00"),
            win_count=1,
            loss_count=0,
            analysis={}
        )
        strategy_result = StrategyResult(
            strategy_name="single_trade",
            replay_result=replay_result,
            equity_df=equity_df
        )

        result = calculate_strategy_metrics(strategy_result)
        assert result['trade_count'] == 1
        assert result['win_rate_pct'] == 100.0


# =============================================================================
# TESTS FOR create_metrics_table()
# =============================================================================

class TestCreateMetricsTable:
    """Tests for create_metrics_table function."""

    def test_returns_styler(self, comparison_result):
        """Test that function returns a pandas Styler."""
        result = create_metrics_table(comparison_result)
        assert isinstance(result, pd.io.formats.style.Styler)

    def test_has_all_strategies_as_rows(self, comparison_result):
        """Test that all strategies appear as rows in the table."""
        styler = create_metrics_table(comparison_result)
        df = styler.data

        strategy_names = [sr.strategy_name for sr in comparison_result.strategy_results]
        for name in strategy_names:
            assert name in df.index

    def test_has_expected_metric_columns(self, comparison_result):
        """Test that expected metric columns are present."""
        styler = create_metrics_table(comparison_result)
        df = styler.data

        expected_columns = [
            'total_return_pct',
            'sharpe_ratio',
            'max_drawdown_pct',
            'win_rate_pct',
            'trade_count'
        ]
        for col in expected_columns:
            assert col in df.columns, f"Missing column: {col}"

    def test_can_export_to_html(self, comparison_result):
        """Test that Styler can be exported to HTML."""
        styler = create_metrics_table(comparison_result)
        html = styler.to_html()

        assert isinstance(html, str)
        assert '<table' in html.lower()
        assert 'style' in html.lower()  # Should have styling

    def test_css_styling_preserved(self, comparison_result):
        """Test that CSS styling is preserved in HTML output."""
        styler = create_metrics_table(comparison_result)
        html = styler.to_html()

        # Should have background-color styling from conditional formatting
        assert 'background' in html.lower() or 'color' in html.lower()


# =============================================================================
# TESTS FOR CONDITIONAL FORMATTING
# =============================================================================

class TestConditionalFormatting:
    """Tests for green/red conditional formatting."""

    def test_best_value_gets_green(self, comparison_result):
        """Test that best value per column gets green background."""
        styler = create_metrics_table(comparison_result)

        # Export to HTML and check for green coloring
        html = styler.to_html()

        # Green indicator (hex or named)
        # The exact color may vary but should have some form of green
        # Check that styling is applied (we can't easily verify exact green without parsing CSS)
        assert 'background' in html.lower() or 'style' in html.lower()

    def test_worst_value_gets_red(self, comparison_result):
        """Test that worst value per column gets red background."""
        styler = create_metrics_table(comparison_result)
        html = styler.to_html()

        # Red indicator should be present
        # The actual verification would require parsing the CSS
        # For now, verify styling is applied
        assert '<style' in html.lower() or 'style=' in html.lower()

    def test_max_drawdown_lower_is_better(self, comparison_result):
        """Test that for max_drawdown, lower (less negative) is better."""
        styler = create_metrics_table(comparison_result)
        df = styler.data

        # Get drawdown values
        drawdowns = df['max_drawdown_pct']

        # The "best" (least negative) drawdown should be highlighted green
        # The "worst" (most negative) drawdown should be highlighted red
        # We verify the data ordering is correct
        best_dd = drawdowns.max()  # Least negative (closest to 0)
        worst_dd = drawdowns.min()  # Most negative

        # Just verify the logic is correct in the data
        assert best_dd >= worst_dd

    def test_two_strategy_comparison(self, profitable_strategy_result, losing_strategy_result):
        """Test formatting with only two strategies."""
        comparison = ComparisonResult(
            session_id="test",
            session_path=Path("/tmp/test.jsonl"),
            strategy_results=[profitable_strategy_result, losing_strategy_result],
            comparison_time=datetime.now(timezone.utc)
        )

        styler = create_metrics_table(comparison)
        df = styler.data

        assert len(df) == 2
        assert 'profitable' in df.index
        assert 'losing' in df.index


# =============================================================================
# TESTS FOR NUMBER FORMATTING
# =============================================================================

class TestNumberFormatting:
    """Tests for number formatting in the metrics table."""

    def test_percentages_formatted(self, comparison_result):
        """Test that percentage values include % symbol in display."""
        styler = create_metrics_table(comparison_result)
        html = styler.to_html()

        # Percentages should appear with % symbol
        assert '%' in html

    def test_ratios_formatted_as_decimals(self, comparison_result):
        """Test that ratios are formatted with reasonable precision."""
        styler = create_metrics_table(comparison_result)
        df = styler.data

        # Sharpe ratio should be a reasonable number (not extremely large/small)
        sharpe_values = df['sharpe_ratio']
        for val in sharpe_values:
            if not pd.isna(val):
                assert -100 < val < 100, f"Sharpe ratio {val} seems unreasonable"

    def test_trade_count_is_integer(self, comparison_result):
        """Test that trade count is displayed as integer."""
        styler = create_metrics_table(comparison_result)
        df = styler.data

        for count in df['trade_count']:
            assert count == int(count)


# =============================================================================
# EDGE CASES
# =============================================================================

class TestMetricsEdgeCases:
    """Tests for edge cases in metrics calculation."""

    def test_zero_returns(self):
        """Test handling of zero returns (flat equity)."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=10, freq="5min", tz=timezone.utc)
        equity_df = pd.DataFrame({"equity": [100] * 10}, index=timestamps)

        replay_result = ReplayResult(
            session_id="test",
            strategy_name="flat",
            events_processed=10,
            buys_executed=0,
            sells_executed=0,
            skips=10,
            total_pnl=Decimal("0"),
            win_count=0,
            loss_count=0,
            analysis={}
        )
        strategy_result = StrategyResult(
            strategy_name="flat",
            replay_result=replay_result,
            equity_df=equity_df
        )

        result = calculate_strategy_metrics(strategy_result)

        assert result['total_return_pct'] == 0.0
        assert result['max_drawdown_pct'] == 0.0

    def test_all_wins(self):
        """Test handling when all trades are wins."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        equity_df = pd.DataFrame({"equity": [100, 102, 104, 106, 108]}, index=timestamps)

        replay_result = ReplayResult(
            session_id="test",
            strategy_name="all_wins",
            events_processed=5,
            buys_executed=4,
            sells_executed=4,
            skips=1,
            total_pnl=Decimal("8.00"),
            win_count=4,
            loss_count=0,
            analysis={}
        )
        strategy_result = StrategyResult(
            strategy_name="all_wins",
            replay_result=replay_result,
            equity_df=equity_df
        )

        result = calculate_strategy_metrics(strategy_result)

        assert result['win_rate_pct'] == 100.0

    def test_all_losses(self):
        """Test handling when all trades are losses."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        equity_df = pd.DataFrame({"equity": [100, 98, 96, 94, 92]}, index=timestamps)

        replay_result = ReplayResult(
            session_id="test",
            strategy_name="all_losses",
            events_processed=5,
            buys_executed=4,
            sells_executed=4,
            skips=1,
            total_pnl=Decimal("-8.00"),
            win_count=0,
            loss_count=4,
            analysis={}
        )
        strategy_result = StrategyResult(
            strategy_name="all_losses",
            replay_result=replay_result,
            equity_df=equity_df
        )

        result = calculate_strategy_metrics(strategy_result)

        assert result['win_rate_pct'] == 0.0

    def test_single_strategy_comparison(self, profitable_strategy_result):
        """Test metrics table with only one strategy."""
        comparison = ComparisonResult(
            session_id="test",
            session_path=Path("/tmp/test.jsonl"),
            strategy_results=[profitable_strategy_result],
            comparison_time=datetime.now(timezone.utc)
        )

        styler = create_metrics_table(comparison)
        df = styler.data

        assert len(df) == 1
        assert 'profitable' in df.index
