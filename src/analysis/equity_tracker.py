"""Equity tracking: timestamped capital snapshots for equity curve construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List

from ..core.portfolio import Portfolio


@dataclass
class EquitySnapshot:
    """Single point-in-time equity snapshot."""

    timestamp: datetime
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal  # starting_capital + realized + unrealized
    open_positions: int
    deployed_capital: Decimal
    trade_count: int  # cumulative trades so far


class EquityTracker:
    """Records timestamped equity snapshots and produces pandas DataFrame."""

    def __init__(self, starting_capital: Decimal):
        self.starting_capital = starting_capital
        self.snapshots: List[EquitySnapshot] = []

    def record_snapshot(
        self,
        timestamp: datetime,
        portfolio: Portfolio,
        current_prices: Dict[str, Decimal]
    ) -> None:
        """Record snapshot from Portfolio state.

        Calls portfolio.calculate_pnl(current_prices) to get realized + unrealized.
        Creates EquitySnapshot with total_equity = starting_capital + realized + unrealized.
        """
        realized_pnl, unrealized_pnl = portfolio.calculate_pnl(current_prices)
        total_equity = self.starting_capital + realized_pnl + unrealized_pnl

        positions = portfolio.get_positions()
        open_positions = len(positions)
        deployed_capital = portfolio.get_total_deployed()

        # Trade count is not directly tracked by Portfolio, so we'll use 0 here
        # In real usage, caller would track this separately
        snapshot = EquitySnapshot(
            timestamp=timestamp,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_equity=total_equity,
            open_positions=open_positions,
            deployed_capital=deployed_capital,
            trade_count=0,
        )

        self.snapshots.append(snapshot)

    def record_manual_snapshot(
        self,
        timestamp: datetime,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        open_positions: int,
        deployed_capital: Decimal,
        trade_count: int
    ) -> None:
        """Record snapshot when caller computes values directly.

        Used in replay integration where portfolio state comes from strategy.
        """
        total_equity = self.starting_capital + realized_pnl + unrealized_pnl

        snapshot = EquitySnapshot(
            timestamp=timestamp,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_equity=total_equity,
            open_positions=open_positions,
            deployed_capital=deployed_capital,
            trade_count=trade_count,
        )

        self.snapshots.append(snapshot)

    def to_dataframe(self):
        """Convert snapshots to pandas DataFrame.

        Returns DataFrame with columns: equity, realized_pnl, unrealized_pnl,
        open_positions, deployed_capital. Index = timestamp.

        Uses lazy import for pandas (only imported when needed).
        """
        import pandas as pd

        if not self.snapshots:
            # Return empty DataFrame with correct columns
            return pd.DataFrame(
                columns=["equity", "realized_pnl", "unrealized_pnl", "open_positions", "deployed_capital"]
            )

        data = {
            "equity": [float(s.total_equity) for s in self.snapshots],
            "realized_pnl": [float(s.realized_pnl) for s in self.snapshots],
            "unrealized_pnl": [float(s.unrealized_pnl) for s in self.snapshots],
            "open_positions": [s.open_positions for s in self.snapshots],
            "deployed_capital": [float(s.deployed_capital) for s in self.snapshots],
        }
        index = [s.timestamp for s in self.snapshots]

        return pd.DataFrame(data, index=index)

    def get_final_equity(self) -> Decimal:
        """Return last snapshot's total_equity, or starting_capital if empty."""
        if not self.snapshots:
            return self.starting_capital
        return self.snapshots[-1].total_equity

    def get_return_pct(self) -> Decimal:
        """Calculate return percentage.

        (final_equity - starting_capital) / starting_capital * 100
        """
        final_equity = self.get_final_equity()
        if self.starting_capital == 0:
            return Decimal("0")

        return ((final_equity - self.starting_capital) / self.starting_capital * 100).quantize(Decimal("0.01"))
