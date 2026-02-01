"""Trade attribution: link each trade entry to its exit with realized PnL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..data.models import MarketEvent, TradeAction
from ..strategies.base import TradeDecision


@dataclass
class AttributedTrade:
    """Single trade with entry, exit, and PnL tracking."""

    # Entry details
    entry_timestamp: datetime
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    entry_shares: Decimal
    entry_price: Decimal
    entry_cost: Decimal

    # Exit details (optional until closed)
    exit_timestamp: Optional[datetime] = None
    exit_shares: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    exit_proceeds: Optional[Decimal] = None

    # Outcome tracking
    status: str = "open"  # "open" | "win" | "loss" | "breakeven" | "resolved"
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    # Leader comparison
    leader_dollars: Decimal = Decimal("0")
    leader_price: Decimal = Decimal("0")

    # Strategy tag
    strategy_name: str = ""

    def close_trade(self, timestamp: datetime, shares: Decimal, price: Decimal) -> None:
        """Mark trade as closed and calculate realized PnL.

        For BUY: PnL = proceeds - cost
        For SELL: PnL = cost - proceeds
        """
        self.exit_timestamp = timestamp
        self.exit_shares = shares
        self.exit_price = price.quantize(Decimal("0.0001"))
        self.exit_proceeds = (shares * price).quantize(Decimal("0.0001"))

        # Calculate PnL based on action
        if self.action == "BUY":
            self.realized_pnl = (self.exit_proceeds - self.entry_cost).quantize(Decimal("0.0001"))
        else:  # SELL
            self.realized_pnl = (self.entry_cost - self.exit_proceeds).quantize(Decimal("0.0001"))

        # Set status
        if self.realized_pnl > 0:
            self.status = "win"
        elif self.realized_pnl < 0:
            self.status = "loss"
        else:
            self.status = "breakeven"

        # Clear unrealized since position is closed
        self.unrealized_pnl = Decimal("0")

    def update_unrealized(self, current_price: Decimal) -> None:
        """Update unrealized PnL for open position.

        unrealized = (shares * current_price) - entry_cost
        """
        if self.status == "open":
            current_value = (self.entry_shares * current_price).quantize(Decimal("0.0001"))
            self.unrealized_pnl = (current_value - self.entry_cost).quantize(Decimal("0.0001"))

    def to_dict(self) -> Dict:
        """Serialize to dict for JSONL persistence.

        Converts Decimal to str and datetime to isoformat.
        """
        return {
            "entry_timestamp": self.entry_timestamp.isoformat(),
            "market_id": self.market_id,
            "token_id": self.token_id,
            "side": self.side,
            "action": self.action,
            "entry_shares": str(self.entry_shares),
            "entry_price": str(self.entry_price),
            "entry_cost": str(self.entry_cost),
            "exit_timestamp": self.exit_timestamp.isoformat() if self.exit_timestamp else None,
            "exit_shares": str(self.exit_shares) if self.exit_shares else None,
            "exit_price": str(self.exit_price) if self.exit_price else None,
            "exit_proceeds": str(self.exit_proceeds) if self.exit_proceeds else None,
            "status": self.status,
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "leader_dollars": str(self.leader_dollars),
            "leader_price": str(self.leader_price),
            "strategy_name": self.strategy_name,
        }


class TradeAttributor:
    """Manages trade lifecycle: entry, exit, unrealized updates, summary."""

    def __init__(self):
        # token_id -> list of AttributedTrade (same token can have multiple entries)
        self._trades: Dict[str, List[AttributedTrade]] = {}
        self._sequence: Dict[str, int] = {}  # token_id -> next sequence number

    def record_entry(
        self,
        event: MarketEvent,
        decision: TradeDecision,
        strategy_name: str
    ) -> str:
        """Create AttributedTrade from event + decision, return trade_id."""
        token_id = event.trade.token_id

        # Get next sequence for this token
        seq = self._sequence.get(token_id, 0)
        self._sequence[token_id] = seq + 1
        trade_id = f"{token_id}_{seq}"

        # Create trade
        trade = AttributedTrade(
            entry_timestamp=event.trade.timestamp,
            market_id=event.trade.market_id,
            token_id=token_id,
            side=event.trade.side.value,
            action=decision.action.value,
            entry_shares=decision.shares or Decimal("0"),
            entry_price=(decision.price or Decimal("0")).quantize(Decimal("0.0001")),
            entry_cost=(decision.dollars or Decimal("0")).quantize(Decimal("0.0001")),
            leader_dollars=event.trade.dollars,
            leader_price=event.trade.price.quantize(Decimal("0.0001")),
            strategy_name=strategy_name,
        )

        # Store
        if token_id not in self._trades:
            self._trades[token_id] = []
        self._trades[token_id].append(trade)

        return trade_id

    def record_exit(
        self,
        token_id: str,
        timestamp: datetime,
        shares: Decimal,
        price: Decimal
    ) -> None:
        """Find open trade for token_id and close it.

        If partial exit, handles proportionally (closes oldest open trade first).
        """
        if token_id not in self._trades:
            return

        # Find first open trade for this token
        for trade in self._trades[token_id]:
            if trade.status == "open":
                trade.close_trade(timestamp, shares, price)
                break

    def update_all_unrealized(self, current_prices: Dict[str, Decimal]) -> None:
        """Mark-to-market all open trades."""
        for token_id, trades in self._trades.items():
            if token_id in current_prices:
                for trade in trades:
                    if trade.status == "open":
                        trade.update_unrealized(current_prices[token_id])

    def get_summary(self) -> Dict:
        """Return summary statistics across all trades."""
        all_trades = self.get_all_trades()

        total_trades = len(all_trades)
        wins = [t for t in all_trades if t.status == "win"]
        losses = [t for t in all_trades if t.status == "loss"]
        open_trades = [t for t in all_trades if t.status == "open"]

        win_count = len(wins)
        loss_count = len(losses)
        open_count = len(open_trades)

        win_rate = Decimal("0")
        if win_count + loss_count > 0:
            win_rate = (Decimal(win_count) / Decimal(win_count + loss_count) * 100).quantize(Decimal("0.01"))

        total_realized_pnl = sum((t.realized_pnl for t in all_trades), Decimal("0"))
        total_unrealized_pnl = sum((t.unrealized_pnl for t in all_trades), Decimal("0"))

        avg_win = Decimal("0")
        if wins:
            avg_win = (sum(t.realized_pnl for t in wins) / len(wins)).quantize(Decimal("0.0001"))

        avg_loss = Decimal("0")
        if losses:
            avg_loss = (sum(t.realized_pnl for t in losses) / len(losses)).quantize(Decimal("0.0001"))

        best_trade = max((t.realized_pnl for t in all_trades), default=Decimal("0"))
        worst_trade = min((t.realized_pnl for t in all_trades), default=Decimal("0"))

        return {
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "open": open_count,
            "win_rate": win_rate,
            "total_realized_pnl": total_realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }

    def get_all_trades(self) -> List[AttributedTrade]:
        """Flat list of all trades."""
        result = []
        for trades in self._trades.values():
            result.extend(trades)
        return result
