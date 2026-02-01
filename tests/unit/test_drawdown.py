"""Unit tests for DrawdownAnalyzer."""

import pandas as pd
import pytest
from datetime import datetime, timedelta
from src.analysis.drawdown import DrawdownAnalyzer


def make_equity_df(values, start_time=None):
    """Helper to create equity DataFrame for testing.

    Args:
        values: List of equity values
        start_time: Optional starting datetime (defaults to 2026-01-01)

    Returns:
        DataFrame with 'equity' column and datetime index
    """
    if start_time is None:
        start_time = datetime(2026, 1, 1, 12, 0, 0)

    timestamps = [start_time + timedelta(minutes=i) for i in range(len(values))]
    return pd.DataFrame({'equity': values}, index=pd.DatetimeIndex(timestamps))


class TestDrawdownAnalyzer:
    """Tests for DrawdownAnalyzer."""

    def test_no_drawdown_monotonic_increase(self):
        """Monotonically increasing equity has zero drawdown."""
        df = make_equity_df([100, 110, 120])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        assert result['max_drawdown_pct'] == 0.0
        assert result['max_drawdown_value'] == 0.0
        assert result['drawdown_duration_min'] == 0.0
        assert result['recovery_time_min'] == 0.0

    def test_simple_drawdown(self):
        """Simple drawdown from 100 to 80 (20% drop)."""
        df = make_equity_df([100, 80, 100])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        # Max drawdown: (80 - 100) / 100 = -20%
        assert result['max_drawdown_pct'] == pytest.approx(-20.0)
        assert result['max_drawdown_value'] == pytest.approx(-20.0)
        assert result['drawdown_duration_min'] == 1.0  # 1 minute from peak to trough
        # Recovery in 1 more minute (index 2 recovers to peak)
        assert result['recovery_time_min'] == pytest.approx(1.0)

    def test_drawdown_with_recovery(self):
        """Drawdown with recovery: 100 -> 120 -> 96 -> 120."""
        df = make_equity_df([100, 120, 96, 120])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        # Max drawdown: (96 - 120) / 120 = -20%
        assert result['max_drawdown_pct'] == pytest.approx(-20.0)
        assert result['max_drawdown_value'] == pytest.approx(-24.0)
        # Duration: 1 minute from peak (index 1) to trough (index 2)
        assert result['drawdown_duration_min'] == 1.0
        # Recovery: 1 minute from trough (index 2) to recovery (index 3)
        assert result['recovery_time_min'] == pytest.approx(1.0)

    def test_drawdown_without_recovery(self):
        """Drawdown without recovery: 100 -> 120 -> 90, not recovered."""
        df = make_equity_df([100, 120, 90])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        # Max drawdown: (90 - 120) / 120 = -25%
        assert result['max_drawdown_pct'] == pytest.approx(-25.0)
        assert result['recovery_time_min'] is None

    def test_multiple_drawdowns_finds_max(self):
        """Two dips, finds the maximum drawdown."""
        # Pattern: 100 -> 90 -> 100 -> 120 -> 80
        # First dip: -10% (90 from peak 100)
        # Second dip: -33.33% (80 from peak 120)
        df = make_equity_df([100, 90, 100, 120, 80])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        # Max drawdown: (80 - 120) / 120 = -33.33%
        assert result['max_drawdown_pct'] == pytest.approx(-33.333, rel=1e-2)
        assert result['drawdown_start'] == df.index[3]  # Peak at 120
        assert result['drawdown_bottom'] == df.index[4]  # Trough at 80

    def test_current_drawdown(self):
        """Current drawdown shows when ending below peak."""
        df = make_equity_df([100, 120, 110])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        # Current drawdown: (110 - 120) / 120 = -8.33%
        assert result['current_drawdown_pct'] == pytest.approx(-8.333, rel=1e-2)

    def test_single_row_no_drawdown(self):
        """Single data point has no drawdown."""
        df = make_equity_df([100])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        assert result['max_drawdown_pct'] == 0.0
        assert result['max_drawdown_value'] == 0.0
        assert result['drawdown_duration_min'] == 0.0
        assert result['recovery_time_min'] == 0.0
        assert result['current_drawdown_pct'] == 0.0

    def test_constant_equity_no_drawdown(self):
        """All values constant has no drawdown."""
        df = make_equity_df([100, 100, 100, 100])
        analyzer = DrawdownAnalyzer()

        result = analyzer.analyze(df)

        assert result['max_drawdown_pct'] == 0.0
        assert result['max_drawdown_value'] == 0.0
        assert result['current_drawdown_pct'] == 0.0

    def test_empty_dataframe_raises(self):
        """Empty DataFrame raises ValueError."""
        df = pd.DataFrame({'equity': []})
        analyzer = DrawdownAnalyzer()

        with pytest.raises(ValueError, match="Empty equity DataFrame"):
            analyzer.analyze(df)

    def test_drawdown_series_shape(self):
        """get_drawdown_series returns Series with same index."""
        df = make_equity_df([100, 120, 96, 120])
        analyzer = DrawdownAnalyzer()

        series = analyzer.get_drawdown_series(df)

        assert len(series) == len(df)
        assert all(series.index == df.index)
        # At peak (index 1), drawdown should be 0
        assert series.iloc[1] == pytest.approx(0.0)
        # At trough (index 2), drawdown should be -20%
        assert series.iloc[2] == pytest.approx(-20.0)
