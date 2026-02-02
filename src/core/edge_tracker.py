"""
EdgeTracker: Per-token edge tracking with rolling window.

Tracks trade results per token and calculates win rate, average win/loss percentages
for use in Kelly criterion position sizing.

Key features:
- Rolling window (fixed-size deque) per token
- Returns None when insufficient data (cold start)
- All calculations use Decimal with quantize("0.01")
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """
    Represents a completed trade result.

    Attributes:
        token_id: Token identifier (e.g., "SOL", "ETH")
        entry_price: Entry price (Decimal)
        exit_price: Exit price (Decimal)
        pnl_pct: P&L percentage (Decimal, e.g., 0.10 for +10%)
        timestamp: Trade timestamp (UTC)
    """
    token_id: str
    entry_price: Decimal
    exit_price: Decimal
    pnl_pct: Decimal
    timestamp: datetime


class EdgeTracker:
    """
    Tracks per-token edge statistics using a rolling window.

    Maintains trade history per token and calculates:
    - Win rate (percentage of winning trades)
    - Average win percentage
    - Average loss percentage

    Returns None for insufficient data (cold start period).
    """

    def __init__(self, lookback_trades: int = 50, min_trades_for_kelly: int = 20):
        """
        Initialize EdgeTracker.

        Args:
            lookback_trades: Rolling window size (deque maxlen)
            min_trades_for_kelly: Minimum trades required before returning stats
        """
        self.lookback_trades = lookback_trades
        self.min_trades_for_kelly = min_trades_for_kelly

        # Per-token trade history (rolling window)
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=lookback_trades)
        )

        logger.info(
            f"EdgeTracker initialized: lookback={lookback_trades}, "
            f"min_trades={min_trades_for_kelly}"
        )

    def record_trade(self, result: TradeResult) -> None:
        """
        Record a trade result.

        Args:
            result: TradeResult to record
        """
        self._history[result.token_id].append(result)

        logger.debug(
            f"Recorded trade: {result.token_id} pnl={result.pnl_pct:.4f} "
            f"(total={len(self._history[result.token_id])})"
        )

    def has_sufficient_data(self, token_id: str) -> bool:
        """
        Check if token has sufficient data for Kelly calculation.

        Args:
            token_id: Token to check

        Returns:
            True if count >= min_trades_for_kelly, False otherwise
        """
        return len(self._history.get(token_id, [])) >= self.min_trades_for_kelly

    def get_edge_stats(self, token_id: str) -> Optional[Tuple[Decimal, Decimal, Decimal, int]]:
        """
        Get edge statistics for a token.

        Args:
            token_id: Token to get stats for

        Returns:
            Tuple of (win_rate, avg_win_pct, avg_loss_pct, count) if sufficient data,
            None otherwise.

            - win_rate: Decimal in [0, 1] (e.g., 0.60 for 60%)
            - avg_win_pct: Decimal average win percentage (e.g., 0.15 for 15%)
            - avg_loss_pct: Decimal average loss percentage (e.g., 0.05 for 5%)
            - count: Number of trades in window

        Notes:
            - Win = pnl_pct > 0
            - Loss = pnl_pct < 0
            - Breakeven (pnl_pct == 0) counts as win for win_rate but excluded from averages
            - If no losses, avg_loss_pct defaults to 0.01 (avoid div by zero)
            - If no wins, avg_win_pct = 0
        """
        if not self.has_sufficient_data(token_id):
            return None

        history = self._history[token_id]
        count = len(history)

        # Separate wins and losses
        wins = [t.pnl_pct for t in history if t.pnl_pct > 0]
        losses = [abs(t.pnl_pct) for t in history if t.pnl_pct < 0]
        breakevens = sum(1 for t in history if t.pnl_pct == 0)

        # Win rate calculation (breakevens count as wins)
        win_count = len(wins) + breakevens
        win_rate = (Decimal(str(win_count)) / Decimal(str(count))).quantize(Decimal("0.01"))

        # Average win calculation
        if wins:
            avg_win = (sum(wins) / Decimal(str(len(wins)))).quantize(Decimal("0.01"))
        else:
            avg_win = Decimal("0.00")

        # Average loss calculation (default to 0.01 if no losses)
        if losses:
            avg_loss = (sum(losses) / Decimal(str(len(losses)))).quantize(Decimal("0.01"))
        else:
            avg_loss = Decimal("0.01")

        logger.debug(
            f"Edge stats for {token_id}: win_rate={win_rate}, "
            f"avg_win={avg_win}, avg_loss={avg_loss}, count={count}"
        )

        return (win_rate, avg_win, avg_loss, count)
