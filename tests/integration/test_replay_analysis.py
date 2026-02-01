"""Integration tests for replay with analysis pipeline."""

from pathlib import Path
from decimal import Decimal

import pytest

# Conditional pandas import
pd = pytest.importorskip("pandas", reason="pandas required for analysis integration tests")

from src.strategies import get_strategy
from src.framework.replay import SessionReplayer, run_session_replay_with_analysis


# Use the same session file as other integration tests
SESSION_FILE = Path("data/sessions/session_20260129_191609.jsonl")


def test_session_file_exists():
    """Verify the session file exists before running tests."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    assert SESSION_FILE.exists(), f"Session file should exist: {SESSION_FILE}"


def test_replay_with_analysis_produces_results():
    """Test replay with track_analysis=True produces analysis results."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    # Use mirror strategy (simplest baseline)
    strategy = get_strategy("mirror")

    # Load and replay with analysis
    replayer = SessionReplayer(SESSION_FILE, strategy)
    count = replayer.load()
    assert count > 0, "Should load events from session"

    result = replayer.run(track_analysis=True)

    # Verify analysis field populated
    assert result.analysis is not None, "Analysis should be populated when track_analysis=True"

    # Verify all expected analysis keys present
    expected_keys = ['trade_summary', 'drawdown', 'slippage', 'sizing', 'selection', 'equity_df', 'attributed_trades']
    for key in expected_keys:
        assert key in result.analysis, f"Analysis should have '{key}' key"

    # Verify trade_summary has expected structure
    trade_summary = result.analysis['trade_summary']
    assert 'total_trades' in trade_summary
    assert 'wins' in trade_summary
    assert 'losses' in trade_summary
    assert 'open' in trade_summary
    assert 'win_rate' in trade_summary

    # Verify we tracked trades (mirror strategy executes trades in this session)
    assert trade_summary['total_trades'] > 0, "Should have tracked trades"

    # Verify drawdown has expected keys
    drawdown = result.analysis['drawdown']
    assert 'max_drawdown_pct' in drawdown
    assert 'drawdown_duration_min' in drawdown
    assert 'current_drawdown_pct' in drawdown

    # Verify slippage metrics
    slippage = result.analysis['slippage']
    assert 'total_trades' in slippage
    assert 'avg_execution_slippage_bps' in slippage
    assert 'avg_delay_slippage_bps' in slippage

    # Verify sizing metrics
    sizing = result.analysis['sizing']
    assert 'total_trades' in sizing
    assert 'avg_sizing_ratio' in sizing
    assert 'undersized_count' in sizing
    assert 'oversized_count' in sizing

    # Verify selection metrics
    selection = result.analysis['selection']
    assert 'total_skipped' in selection
    assert 'skip_reasons' in selection

    # Verify existing replay fields still populated correctly
    assert result.buys_executed > 0, "Should execute buys"
    assert result.sells_executed > 0, "Should execute sells"
    assert result.realized_pnl != Decimal("0"), "Should have realized PnL"
    assert result.events_processed > 0, "Should process events"


def test_replay_without_analysis_unchanged():
    """Test replay with track_analysis=False has no analysis field."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")

    replayer = SessionReplayer(SESSION_FILE, strategy)
    replayer.load()

    # Run WITHOUT analysis
    result = replayer.run(track_analysis=False)

    # Verify analysis field is None
    assert result.analysis is None, "Analysis should be None when track_analysis=False"

    # Verify existing fields still work
    assert result.buys_executed > 0
    assert result.sells_executed > 0
    assert result.events_processed > 0


def test_full_analysis_pipeline_end_to_end(tmp_path):
    """Test full analysis pipeline: replay + reports + charts."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")
    output_dir = tmp_path / "reports"

    # Run full pipeline with analysis
    result = run_session_replay_with_analysis(
        str(SESSION_FILE),
        strategy,
        output_dir
    )

    # Verify result has analysis
    assert result.analysis is not None

    # Verify charts created
    session_id = result.session_id or "unknown"
    equity_chart = output_dir / f"equity_{session_id}.png"
    trade_scatter = output_dir / f"trades_{session_id}.png"

    # Charts should exist if equity data present
    if len(result.analysis['equity_df']) > 0:
        assert equity_chart.exists(), "Equity chart should be created"
        assert trade_scatter.exists(), "Trade scatter should be created"
        assert equity_chart.stat().st_size > 0, "Equity chart should not be empty"
        assert trade_scatter.stat().st_size > 0, "Trade scatter should not be empty"

    # Verify JSON report created
    json_report = output_dir / f"report_{session_id}.json"
    assert json_report.exists(), "JSON report should be created"
    assert json_report.stat().st_size > 0, "JSON report should not be empty"

    # Verify JSON is valid
    import json
    with open(json_report, 'r', encoding='utf-8') as f:
        report_data = json.load(f)

    assert 'session_id' in report_data
    assert 'trade_attribution' in report_data
    assert 'drawdown' in report_data
    assert 'slippage' in report_data


def test_analysis_equity_tracking():
    """Test that equity tracking produces valid DataFrame."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")

    replayer = SessionReplayer(SESSION_FILE, strategy)
    replayer.load()
    result = replayer.run(track_analysis=True)

    equity_df = result.analysis['equity_df']

    # Verify DataFrame structure
    assert isinstance(equity_df, pd.DataFrame)
    assert len(equity_df) > 0, "Should have equity snapshots"

    # Verify columns
    expected_columns = ['equity', 'realized_pnl', 'unrealized_pnl', 'open_positions', 'deployed_capital']
    for col in expected_columns:
        assert col in equity_df.columns, f"Equity DataFrame should have '{col}' column"

    # Verify index is datetime
    assert pd.api.types.is_datetime64_any_dtype(equity_df.index)


def test_analysis_trade_attribution():
    """Test that trade attribution tracks trades correctly."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")

    replayer = SessionReplayer(SESSION_FILE, strategy)
    replayer.load()
    result = replayer.run(track_analysis=True)

    attributed_trades = result.analysis['attributed_trades']

    # Verify we have attributed trades
    assert len(attributed_trades) > 0, "Should have attributed trades"

    # Verify each trade has expected attributes
    for trade in attributed_trades[:5]:  # Check first 5
        assert hasattr(trade, 'entry_timestamp')
        assert hasattr(trade, 'token_id')
        assert hasattr(trade, 'action')
        assert hasattr(trade, 'status')
        assert hasattr(trade, 'realized_pnl')
        assert hasattr(trade, 'unrealized_pnl')

        # Status should be valid
        assert trade.status in ['open', 'win', 'loss', 'breakeven']


def test_analysis_slippage_measurement():
    """Test that slippage is measured for trades."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")

    replayer = SessionReplayer(SESSION_FILE, strategy)
    replayer.load()
    result = replayer.run(track_analysis=True)

    slippage = result.analysis['slippage']

    # If trades executed, should have slippage data
    if result.buys_executed + result.sells_executed > 0:
        assert slippage['total_trades'] > 0
        assert 'avg_execution_slippage_bps' in slippage
        assert 'avg_delay_slippage_bps' in slippage
        assert 'total_slippage_bps' in slippage
        assert 'total_slippage_cost' in slippage


def test_analysis_drawdown_calculation():
    """Test that drawdown metrics are calculated."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy("mirror")

    replayer = SessionReplayer(SESSION_FILE, strategy)
    replayer.load()
    result = replayer.run(track_analysis=True)

    drawdown = result.analysis['drawdown']

    # Verify drawdown structure
    assert 'max_drawdown_pct' in drawdown
    assert 'max_drawdown_value' in drawdown
    assert 'drawdown_duration_min' in drawdown
    assert 'current_drawdown_pct' in drawdown

    # Max drawdown should be non-positive (0 or negative)
    assert drawdown['max_drawdown_pct'] <= 0, "Max drawdown should be 0 or negative"
