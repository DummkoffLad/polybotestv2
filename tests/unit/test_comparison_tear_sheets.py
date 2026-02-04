"""Tests for tear sheet generation - TDD RED phase.

Tests define expected behavior for QuantStats tear sheet generation:
- generate_single_tear_sheet: Generate HTML tear sheet for one strategy
- generate_tear_sheets: Generate tear sheets for all strategies
- Return series conversion from equity DataFrame
"""

# Configure matplotlib to use non-interactive backend before any imports
import matplotlib
matplotlib.use('Agg')

import pytest
import tempfile
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.comparison.comparator import (
    StrategyComparator,
    ComparisonResult,
    StrategyResult,
)
from src.comparison.tear_sheets import (
    generate_single_tear_sheet,
    generate_tear_sheets,
)
from src.strategies.base import (
    Strategy,
    StrategyConfig,
    TradeDecision,
    DecisionAction,
)
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from src.framework.replay import ReplayResult


# =============================================================================
# TEST FIXTURES
# =============================================================================

def create_test_session_file(events: list) -> Path:
    """Create a temporary session file with given events.

    Returns path to the temp file (caller must clean up).
    """
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)

    # Write session start
    session_start = {
        "type": "session_start",
        "session_id": "test_session_ts",
        "timestamp": "2026-02-04T10:00:00+00:00",
        "config": {
            "scaling": {
                "our_capital": "100",
                "leader_estimated_capital": "900",
                "k_factor": "0.85"
            },
            "mirror_strategy": {
                "cash_reserve_pct": "10",
                "per_market_cap_pct": "30"
            }
        }
    }
    tmp.write(json.dumps(session_start) + '\n')

    # Write events
    for event in events:
        tmp.write(json.dumps(event) + '\n')

    tmp.close()
    return Path(tmp.name)


def make_test_event(
    timestamp: str,
    token_id: str,
    action: str,
    dollars: float,
    price: float,
    bid: float,
    ask: float,
    market_id: str = None
) -> dict:
    """Create a test market event dict."""
    if market_id is None:
        market_id = f"market_{token_id[:8]}"
    return {
        "type": "market_event",
        "timestamp": timestamp,
        "leader_trade": {
            "timestamp": timestamp,
            "market_id": market_id,
            "token_id": token_id,
            "side": "UP",
            "action": action,
            "leader_dollars": dollars,
            "leader_price": price,
            "leader_shares": dollars / price if price > 0 else 0,
            "source": "test"
        },
        "price_context": {
            "token_id": token_id,
            "bid": bid,
            "ask": ask,
            "last": price,
            "timestamp": timestamp
        }
    }


class AlwaysBuyStrategy(Strategy):
    """Test strategy that always buys (with tracking)."""

    def __init__(self, strategy_name: str = "always_buy"):
        self._name = strategy_name
        self._events_received = []
        self._fills = []
        self._initialized = False

    @property
    def name(self) -> str:
        return self._name

    def initialize(self, config: StrategyConfig) -> None:
        self._events_received = []
        self._fills = []
        self._initialized = True
        self._config = config

    def on_session_start(self) -> None:
        pass

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._events_received.append(event)
        # Always buy $5
        return TradeDecision.buy(
            dollars=Decimal("5.00"),
            shares=Decimal("5.00") / event.prices.ask if event.prices.ask else Decimal("5"),
            price=event.prices.ask
        )

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        self._fills.append((event, decision))

    def get_state(self) -> Dict[str, Any]:
        return {
            "events_received": len(self._events_received),
            "fills": len(self._fills),
            "positions": {},
            "total_deployed": "0"
        }

    def calculate_pnl(self, final_prices) -> Tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")

    def on_session_end(self) -> Dict[str, Any]:
        return {}


# =============================================================================
# TEST: generate_single_tear_sheet()
# =============================================================================

class TestGenerateSingleTearSheet:
    """Tests for generate_single_tear_sheet function."""

    def test_generates_html_file(self, tmp_path):
        """generate_single_tear_sheet creates an HTML file."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
            make_test_event("2026-02-04T10:02:00+00:00", "token_a", "BUY", 10.0, 0.52, 0.51, 0.53),
            make_test_event("2026-02-04T10:03:00+00:00", "token_a", "BUY", 10.0, 0.54, 0.53, 0.55),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("test_strat")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_path = tmp_path / "tearsheet.html"
            result_path = generate_single_tear_sheet(
                comparison.strategy_results[0],
                output_path,
                session_id="test_session"
            )

            # Should create an HTML file
            assert result_path.exists()
            assert result_path.suffix == ".html"
        finally:
            session_path.unlink()

    def test_returns_output_path(self, tmp_path):
        """generate_single_tear_sheet returns the path to the generated file."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
            make_test_event("2026-02-04T10:02:00+00:00", "token_a", "BUY", 10.0, 0.52, 0.51, 0.53),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("test_strat")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_path = tmp_path / "tearsheet.html"
            result_path = generate_single_tear_sheet(
                comparison.strategy_results[0],
                output_path,
                session_id=""
            )

            assert result_path == output_path
        finally:
            session_path.unlink()

    def test_title_includes_strategy_name(self, tmp_path):
        """Generated tear sheet title includes the strategy name."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
            make_test_event("2026-02-04T10:02:00+00:00", "token_a", "BUY", 10.0, 0.52, 0.51, 0.53),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("my_awesome_strategy")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_path = tmp_path / "tearsheet.html"
            generate_single_tear_sheet(
                comparison.strategy_results[0],
                output_path,
                session_id=""
            )

            # Read the HTML and check for strategy name
            html_content = output_path.read_text()
            assert "my_awesome_strategy" in html_content
        finally:
            session_path.unlink()


class TestGenerateTearSheets:
    """Tests for generate_tear_sheets function."""

    def test_generates_tearsheet_per_strategy(self, tmp_path):
        """generate_tear_sheets creates one tear sheet per strategy."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
            make_test_event("2026-02-04T10:02:00+00:00", "token_a", "BUY", 10.0, 0.52, 0.51, 0.53),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [
                AlwaysBuyStrategy("strat_alpha"),
                AlwaysBuyStrategy("strat_beta"),
            ]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_dir = tmp_path / "tearsheets"
            result = generate_tear_sheets(comparison, output_dir)

            # Should return dict with strategy_name -> Path
            assert isinstance(result, dict)
            assert "strat_alpha" in result
            assert "strat_beta" in result
            assert result["strat_alpha"].exists()
            assert result["strat_beta"].exists()
        finally:
            session_path.unlink()

    def test_creates_output_directory(self, tmp_path):
        """generate_tear_sheets creates the output directory if needed."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("strat_alpha")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            # Use a nested path that doesn't exist
            output_dir = tmp_path / "nested" / "dir" / "tearsheets"
            assert not output_dir.exists()

            generate_tear_sheets(comparison, output_dir)

            assert output_dir.exists()
        finally:
            session_path.unlink()

    def test_returns_dict_of_paths(self, tmp_path):
        """generate_tear_sheets returns dict mapping strategy name to path."""
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("test_strat")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_dir = tmp_path / "tearsheets"
            result = generate_tear_sheets(comparison, output_dir)

            assert isinstance(result, dict)
            for name, path in result.items():
                assert isinstance(name, str)
                assert isinstance(path, Path)
        finally:
            session_path.unlink()


class TestReturnSeriesConversion:
    """Tests for equity DataFrame to return series conversion."""

    def test_handles_constant_equity(self, tmp_path):
        """Handles edge case of constant equity (no changes)."""
        # When equity is constant, returns will be all zeros
        # This shouldn't crash the tear sheet generation
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
        ]
        session_path = create_test_session_file(events)

        try:
            # Use strategy that tracks events but equity stays constant
            strategies = [AlwaysBuyStrategy("test_strat")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            output_path = tmp_path / "tearsheet.html"

            # Should not raise exception even with constant equity
            result_path = generate_single_tear_sheet(
                comparison.strategy_results[0],
                output_path,
                session_id=""
            )
            assert result_path.exists()
        finally:
            session_path.unlink()


class TestErrorHandling:
    """Tests for error handling in tear sheet generation."""

    def test_empty_equity_raises_error(self, tmp_path):
        """Empty equity data raises appropriate error."""
        # Create a mock StrategyResult with empty equity
        from src.framework.replay import ReplayResult

        mock_replay = ReplayResult(
            session_id="test",
            strategy_name="test_strat"
        )

        mock_result = StrategyResult(
            strategy_name="test_strat",
            replay_result=mock_replay,
            equity_df=pd.DataFrame()  # Empty DataFrame
        )

        output_path = tmp_path / "tearsheet.html"

        # Should raise an appropriate error
        with pytest.raises((ValueError, RuntimeError)):
            generate_single_tear_sheet(mock_result, output_path, session_id="")

    def test_invalid_output_path_raises_error(self, tmp_path):
        """Invalid output path raises appropriate error.

        Note: This test uses platform-specific invalid path characters.
        On Windows, characters like <>:"|?* are invalid in filenames.
        """
        import sys
        events = [
            make_test_event("2026-02-04T10:01:00+00:00", "token_a", "BUY", 10.0, 0.5, 0.49, 0.51),
        ]
        session_path = create_test_session_file(events)

        try:
            strategies = [AlwaysBuyStrategy("test_strat")]
            comparator = StrategyComparator(session_path, strategies)
            comparison = comparator.run_comparison()

            # Use a path with invalid characters for the platform
            if sys.platform == 'win32':
                # Windows: use invalid characters like <>:"|?*
                invalid_path = Path(str(tmp_path) + "/invalid<>file.html")
            else:
                # Unix: try null byte which is always invalid
                invalid_path = Path("/tmp/invalid\x00file.html")

            # Should raise an appropriate error
            with pytest.raises((OSError, PermissionError, FileNotFoundError, ValueError)):
                generate_single_tear_sheet(
                    comparison.strategy_results[0],
                    invalid_path,
                    session_id=""
                )
        finally:
            session_path.unlink()
