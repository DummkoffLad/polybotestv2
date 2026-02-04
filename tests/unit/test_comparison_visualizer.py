"""Tests for equity curve visualization - TDD RED phase.

Tests define expected behavior for the equity curve visualization:
- create_equity_comparison() accepts dict of {strategy_name: equity_df} and returns Plotly Figure
- Figure has two subplots: equity curves (top) and drawdown (bottom)
- Each strategy has a trace in both panels
- Drawdown calculated as (equity - cummax) / cummax * 100 (negative %)
- save_equity_html() saves Figure to HTML with CDN reference
- No trade markers on equity curve (per CONTEXT.md)
"""

import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.comparison.visualizer import (
    create_equity_comparison,
    save_equity_html,
)


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def sample_equity_df():
    """Create a sample equity DataFrame for testing."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=10,
        freq="5min",
        tz=timezone.utc
    )
    # Equity curve: starts at 100, has some ups and downs
    equities = [100, 102, 101, 105, 103, 108, 106, 110, 107, 115]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df


@pytest.fixture
def sample_equity_df_with_drawdown():
    """Create equity DataFrame that clearly shows drawdown periods."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=10,
        freq="5min",
        tz=timezone.utc
    )
    # Clear pattern: peak at 110, drop to 100, then recovery
    # Expected drawdown at index 5: (100 - 110) / 110 * 100 = -9.09%
    equities = [100, 105, 108, 110, 105, 100, 102, 105, 108, 112]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df


@pytest.fixture
def multi_strategy_equity_data(sample_equity_df):
    """Create equity data for multiple strategies."""
    timestamps = sample_equity_df.index

    # Strategy 1: Conservative (original sample)
    conservative = sample_equity_df.copy()

    # Strategy 2: Aggressive (more volatile, higher final)
    aggressive_equities = [100, 98, 105, 95, 110, 100, 115, 105, 120, 125]
    aggressive = pd.DataFrame({
        "equity": aggressive_equities
    }, index=timestamps)

    # Strategy 3: Steady (less volatile, moderate growth)
    steady_equities = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    steady = pd.DataFrame({
        "equity": steady_equities
    }, index=timestamps)

    return {
        "conservative": conservative,
        "aggressive": aggressive,
        "steady": steady
    }


# =============================================================================
# TESTS FOR create_equity_comparison()
# =============================================================================

class TestCreateEquityComparison:
    """Tests for create_equity_comparison function."""

    def test_accepts_dict_returns_figure(self, sample_equity_df):
        """Test that function accepts dict and returns Plotly Figure."""
        equity_data = {"test_strategy": sample_equity_df}

        result = create_equity_comparison(equity_data)

        assert isinstance(result, go.Figure)

    def test_figure_has_two_subplots(self, sample_equity_df):
        """Test that Figure has two subplots (equity + drawdown)."""
        equity_data = {"test_strategy": sample_equity_df}

        fig = create_equity_comparison(equity_data)

        # Figure should have layout with subplots
        # Check for xaxis and xaxis2 (indicating two rows)
        assert "xaxis" in fig.layout
        assert "xaxis2" in fig.layout or hasattr(fig.layout, 'xaxis2')

    def test_each_strategy_has_equity_trace(self, multi_strategy_equity_data):
        """Test that each strategy has a trace in equity panel."""
        fig = create_equity_comparison(multi_strategy_equity_data)

        # Find traces in top panel (row 1)
        strategy_names = list(multi_strategy_equity_data.keys())
        equity_traces = [
            t for t in fig.data
            if hasattr(t, 'name') and t.name in strategy_names
        ]

        assert len(equity_traces) >= len(strategy_names)

    def test_each_strategy_has_drawdown_trace(self, multi_strategy_equity_data):
        """Test that each strategy has a drawdown trace (fill='tozeroy')."""
        fig = create_equity_comparison(multi_strategy_equity_data)

        # Drawdown traces should have fill='tozeroy' for shaded zones
        drawdown_traces = [
            t for t in fig.data
            if hasattr(t, 'fill') and t.fill == 'tozeroy'
        ]

        # Should have at least one drawdown trace per strategy
        assert len(drawdown_traces) >= len(multi_strategy_equity_data)

    def test_single_strategy_works(self, sample_equity_df):
        """Test that single strategy case works correctly."""
        equity_data = {"only_one": sample_equity_df}

        fig = create_equity_comparison(equity_data)

        # Should still have valid figure
        assert isinstance(fig, go.Figure)
        # Should have at least 2 traces (equity + drawdown for the single strategy)
        assert len(fig.data) >= 2

    def test_no_trade_markers_on_equity(self, sample_equity_df):
        """Test that equity traces have no markers (per CONTEXT.md requirement)."""
        equity_data = {"test_strategy": sample_equity_df}

        fig = create_equity_comparison(equity_data)

        # Check that traces use 'lines' mode without markers
        for trace in fig.data:
            if hasattr(trace, 'mode') and trace.mode:
                # Should not have 'markers' in mode
                assert 'markers' not in trace.mode.lower(), \
                    f"Trace {trace.name} has markers, violating CONTEXT.md requirement"

    def test_empty_equity_df_raises_error(self):
        """Test that empty DataFrame raises appropriate error."""
        empty_df = pd.DataFrame(columns=["equity"])
        equity_data = {"empty": empty_df}

        with pytest.raises((ValueError, KeyError)):
            create_equity_comparison(equity_data)

    def test_different_length_dataframes_handled(self):
        """Test that DataFrames with different lengths are handled."""
        timestamps1 = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        timestamps2 = pd.date_range("2026-02-04 10:00:00", periods=10, freq="5min", tz=timezone.utc)

        df1 = pd.DataFrame({"equity": [100, 102, 101, 103, 105]}, index=timestamps1)
        df2 = pd.DataFrame({"equity": [100, 99, 101, 102, 103, 104, 105, 106, 107, 108]}, index=timestamps2)

        equity_data = {"short": df1, "long": df2}

        # Should not raise, should handle gracefully
        fig = create_equity_comparison(equity_data)
        assert isinstance(fig, go.Figure)


# =============================================================================
# TESTS FOR DRAWDOWN CALCULATION
# =============================================================================

class TestDrawdownCalculation:
    """Tests for drawdown calculation in visualization."""

    def test_drawdown_values_are_negative_or_zero(self, sample_equity_df_with_drawdown):
        """Test that drawdown values are negative (or zero at peak)."""
        equity_data = {"test": sample_equity_df_with_drawdown}

        fig = create_equity_comparison(equity_data)

        # Find drawdown trace (has fill='tozeroy')
        drawdown_traces = [t for t in fig.data if hasattr(t, 'fill') and t.fill == 'tozeroy']
        assert len(drawdown_traces) > 0

        # All drawdown values should be <= 0
        for trace in drawdown_traces:
            y_values = trace.y
            for y in y_values:
                if y is not None:
                    assert y <= 0, f"Drawdown value {y} should be <= 0"

    def test_drawdown_calculation_correct(self, sample_equity_df_with_drawdown):
        """Test that drawdown is calculated as (equity - cummax) / cummax * 100."""
        equity_data = {"test": sample_equity_df_with_drawdown}

        fig = create_equity_comparison(equity_data)

        # Calculate expected drawdown manually
        equity = sample_equity_df_with_drawdown["equity"]
        cummax = equity.expanding().max()
        expected_drawdown = ((equity - cummax) / cummax * 100).values

        # Find drawdown trace
        drawdown_traces = [t for t in fig.data if hasattr(t, 'fill') and t.fill == 'tozeroy']
        assert len(drawdown_traces) > 0

        actual_drawdown = list(drawdown_traces[0].y)

        # Compare with tolerance for floating point
        for i, (expected, actual) in enumerate(zip(expected_drawdown, actual_drawdown)):
            assert abs(expected - actual) < 0.01, \
                f"Drawdown at index {i}: expected {expected:.2f}, got {actual:.2f}"


# =============================================================================
# TESTS FOR save_equity_html()
# =============================================================================

class TestSaveEquityHtml:
    """Tests for HTML export functionality."""

    def test_saves_to_file(self, sample_equity_df):
        """Test that save_equity_html creates a file."""
        equity_data = {"test": sample_equity_df}
        fig = create_equity_comparison(equity_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_equity.html"

            result = save_equity_html(fig, output_path)

            assert output_path.exists()
            assert result == output_path

    def test_returns_path_to_saved_file(self, sample_equity_df):
        """Test that function returns Path to the saved file."""
        equity_data = {"test": sample_equity_df}
        fig = create_equity_comparison(equity_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_equity.html"

            result = save_equity_html(fig, output_path)

            assert isinstance(result, Path)
            assert result == output_path

    def test_uses_cdn_for_plotly_js(self, sample_equity_df):
        """Test that HTML uses CDN reference (not inline plotly.js) per RESEARCH.md."""
        equity_data = {"test": sample_equity_df}
        fig = create_equity_comparison(equity_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_equity.html"

            save_equity_html(fig, output_path)

            # Read file and check for CDN reference
            content = output_path.read_text()

            # Should have CDN reference
            assert "cdn.plot.ly" in content or "plotly.com" in content, \
                "HTML should use CDN reference for plotly.js"

            # File should be relatively small (not contain full plotly.js inline)
            # Full plotly.js is ~3-5MB, with CDN it should be <500KB typically
            file_size = output_path.stat().st_size
            assert file_size < 2_000_000, \
                f"File size {file_size} bytes suggests inline plotly.js (expected CDN)"

    def test_html_content_valid(self, sample_equity_df):
        """Test that saved HTML is valid and contains expected content."""
        equity_data = {"test": sample_equity_df}
        fig = create_equity_comparison(equity_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_equity.html"

            save_equity_html(fig, output_path)

            content = output_path.read_text()

            # Should be valid HTML
            assert "<html" in content.lower() or "<!doctype" in content.lower()
            # Should contain plotly div
            assert "plotly" in content.lower()


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_large_dataset_performance(self):
        """Test that large datasets are handled (with potential downsampling)."""
        # Create large dataset (>5000 points per RESEARCH.md)
        timestamps = pd.date_range(
            start="2026-02-04 00:00:00",
            periods=10000,
            freq="1s",
            tz=timezone.utc
        )
        import numpy as np
        equities = 100 + np.cumsum(np.random.randn(10000) * 0.1)

        df = pd.DataFrame({"equity": equities}, index=timestamps)
        equity_data = {"large_dataset": df}

        # Should complete without error (may downsample internally)
        fig = create_equity_comparison(equity_data)
        assert isinstance(fig, go.Figure)

    def test_constant_equity_no_drawdown(self):
        """Test that constant equity results in zero drawdown."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        df = pd.DataFrame({"equity": [100, 100, 100, 100, 100]}, index=timestamps)
        equity_data = {"flat": df}

        fig = create_equity_comparison(equity_data)

        # Drawdown should be all zeros
        drawdown_traces = [t for t in fig.data if hasattr(t, 'fill') and t.fill == 'tozeroy']
        assert len(drawdown_traces) > 0

        for trace in drawdown_traces:
            for y in trace.y:
                if y is not None:
                    assert y == 0, f"Expected 0 drawdown for flat equity, got {y}"

    def test_only_increasing_equity(self):
        """Test equity that only increases (drawdown always 0)."""
        timestamps = pd.date_range("2026-02-04 10:00:00", periods=5, freq="5min", tz=timezone.utc)
        df = pd.DataFrame({"equity": [100, 101, 102, 103, 104]}, index=timestamps)
        equity_data = {"always_up": df}

        fig = create_equity_comparison(equity_data)

        drawdown_traces = [t for t in fig.data if hasattr(t, 'fill') and t.fill == 'tozeroy']
        for trace in drawdown_traces:
            for y in trace.y:
                if y is not None:
                    assert y == 0, "Drawdown should be 0 for always-increasing equity"
