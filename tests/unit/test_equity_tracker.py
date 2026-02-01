"""Unit tests for equity tracking."""

import pytest
from datetime import datetime
from decimal import Decimal

from src.analysis.equity_tracker import EquitySnapshot, EquityTracker
from src.core.portfolio import Portfolio
from src.core.types import Side


# ============================================================================
# TEST HELPERS
# ============================================================================

def make_portfolio() -> Portfolio:
    """Create a Portfolio for testing."""
    return Portfolio()


# ============================================================================
# EQUITY TRACKER TESTS
# ============================================================================

def test_initial_state():
    """New tracker has starting capital, no snapshots."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    assert tracker.starting_capital == Decimal("100")
    assert len(tracker.snapshots) == 0
    assert tracker.get_final_equity() == Decimal("100")


def test_record_manual_snapshot():
    """Record snapshot, verify stored correctly."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("3"),
        open_positions=2,
        deployed_capital=Decimal("50"),
        trade_count=5,
    )

    assert len(tracker.snapshots) == 1
    snapshot = tracker.snapshots[0]

    assert snapshot.timestamp == datetime(2024, 1, 1, 12, 0, 0)
    assert snapshot.realized_pnl == Decimal("5")
    assert snapshot.unrealized_pnl == Decimal("3")
    assert snapshot.total_equity == Decimal("108")  # 100 + 5 + 3
    assert snapshot.open_positions == 2
    assert snapshot.deployed_capital == Decimal("50")
    assert snapshot.trade_count == 5


def test_multiple_snapshots_ordering():
    """Three snapshots at different times, verify chronological order."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("2"),
        open_positions=1,
        deployed_capital=Decimal("30"),
        trade_count=2,
    )

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        realized_pnl=Decimal("8"),
        unrealized_pnl=Decimal("1"),
        open_positions=2,
        deployed_capital=Decimal("40"),
        trade_count=4,
    )

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 14, 0, 0),
        realized_pnl=Decimal("10"),
        unrealized_pnl=Decimal("0"),
        open_positions=0,
        deployed_capital=Decimal("0"),
        trade_count=5,
    )

    assert len(tracker.snapshots) == 3
    assert tracker.snapshots[0].timestamp == datetime(2024, 1, 1, 12, 0, 0)
    assert tracker.snapshots[1].timestamp == datetime(2024, 1, 1, 13, 0, 0)
    assert tracker.snapshots[2].timestamp == datetime(2024, 1, 1, 14, 0, 0)

    # Verify equity progression
    assert tracker.snapshots[0].total_equity == Decimal("107")  # 100 + 5 + 2
    assert tracker.snapshots[1].total_equity == Decimal("109")  # 100 + 8 + 1
    assert tracker.snapshots[2].total_equity == Decimal("110")  # 100 + 10 + 0


def test_total_equity_calculation():
    """starting=100, realized=5, unrealized=3 -> equity=108."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("3"),
        open_positions=1,
        deployed_capital=Decimal("20"),
        trade_count=3,
    )

    snapshot = tracker.snapshots[0]
    assert snapshot.total_equity == Decimal("108")


def test_to_dataframe_structure():
    """Verify DataFrame has correct columns, datetime index."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("3"),
        open_positions=1,
        deployed_capital=Decimal("20"),
        trade_count=2,
    )

    df = tracker.to_dataframe()

    # Check columns
    expected_columns = ["equity", "realized_pnl", "unrealized_pnl", "open_positions", "deployed_capital"]
    assert list(df.columns) == expected_columns

    # Check index is datetime
    assert len(df) == 1
    assert df.index[0] == datetime(2024, 1, 1, 12, 0, 0)


def test_to_dataframe_values():
    """Verify DataFrame values match snapshots exactly."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("3"),
        open_positions=2,
        deployed_capital=Decimal("50"),
        trade_count=4,
    )

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        realized_pnl=Decimal("8"),
        unrealized_pnl=Decimal("1"),
        open_positions=1,
        deployed_capital=Decimal("30"),
        trade_count=6,
    )

    df = tracker.to_dataframe()

    # First row
    assert df.iloc[0]["equity"] == 108.0  # 100 + 5 + 3
    assert df.iloc[0]["realized_pnl"] == 5.0
    assert df.iloc[0]["unrealized_pnl"] == 3.0
    assert df.iloc[0]["open_positions"] == 2
    assert df.iloc[0]["deployed_capital"] == 50.0

    # Second row
    assert df.iloc[1]["equity"] == 109.0  # 100 + 8 + 1
    assert df.iloc[1]["realized_pnl"] == 8.0
    assert df.iloc[1]["unrealized_pnl"] == 1.0
    assert df.iloc[1]["open_positions"] == 1
    assert df.iloc[1]["deployed_capital"] == 30.0


def test_get_final_equity():
    """Returns last snapshot equity."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("5"),
        unrealized_pnl=Decimal("2"),
        open_positions=1,
        deployed_capital=Decimal("20"),
        trade_count=2,
    )

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        realized_pnl=Decimal("10"),
        unrealized_pnl=Decimal("5"),
        open_positions=2,
        deployed_capital=Decimal("40"),
        trade_count=5,
    )

    # Should return last snapshot's equity
    assert tracker.get_final_equity() == Decimal("115")  # 100 + 10 + 5


def test_get_final_equity_empty():
    """Returns starting_capital when no snapshots."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    assert tracker.get_final_equity() == Decimal("100")


def test_get_return_pct():
    """Starting 100, final 110 -> 10%."""
    tracker = EquityTracker(starting_capital=Decimal("100"))

    tracker.record_manual_snapshot(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        realized_pnl=Decimal("10"),
        unrealized_pnl=Decimal("0"),
        open_positions=0,
        deployed_capital=Decimal("0"),
        trade_count=5,
    )

    return_pct = tracker.get_return_pct()
    assert return_pct == Decimal("10.00")


def test_record_snapshot_from_portfolio():
    """Test record_snapshot using Portfolio.calculate_pnl()."""
    tracker = EquityTracker(starting_capital=Decimal("100"))
    portfolio = make_portfolio()

    # Apply a trade to portfolio
    portfolio.apply_buy(
        token_id="0xabc",
        market_id="market1",
        side=Side.UP,
        shares=Decimal("25"),
        price=Decimal("0.40"),
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )

    # Record snapshot with current prices (higher than entry)
    current_prices = {"0xabc": Decimal("0.50")}
    tracker.record_snapshot(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        portfolio=portfolio,
        current_prices=current_prices,
    )

    assert len(tracker.snapshots) == 1
    snapshot = tracker.snapshots[0]

    # Realized should be 0 (no exits yet)
    assert snapshot.realized_pnl == Decimal("0")

    # Unrealized = (25 * 0.50) - (25 * 0.40) = 12.5 - 10 = 2.5
    assert snapshot.unrealized_pnl == Decimal("2.5")

    # Total equity = 100 + 0 + 2.5 = 102.5
    assert snapshot.total_equity == Decimal("102.5")

    # One open position
    assert snapshot.open_positions == 1

    # Deployed capital = 25 * 0.40 = 10
    assert snapshot.deployed_capital == Decimal("10")
