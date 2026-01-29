"""Instrumentation counters for the Mirror strategy.

Tracks all events, trades, skips, and provides console output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict


@dataclass
class MirrorStats:
    """Counters for live trade mirroring strategy."""

    # Leader events
    leader_events_seen: int = 0
    leader_events_deduped: int = 0
    leader_events_stale: int = 0

    # Bursts
    bursts_triggered: int = 0
    burst_followups: int = 0

    # Trades executed
    buy_count: int = 0
    buy_dollars: Decimal = Decimal("0")
    sell_count: int = 0
    sell_dollars: Decimal = Decimal("0")

    # Execution-level skip reasons (for debugging)
    execution_skips: Dict[str, int] = field(default_factory=lambda: {
        "cap": 0,
        "reserve": 0,
        "ratio": 0,
        "min_order": 0,
        "no_position": 0,
        "expiry": 0,
        "market_filter": 0,
        "spread": 0,
        "no_price": 0,
        "no_token": 0,
        "budget": 0,
        "unknown_token": 0,
        "cost_too_high": 0,  # Skip due to latency + execution cost being too high
        "micro_accumulated": 0,  # Micro-trade accumulated (not a skip, just tracking)
        "leader_profit_our_loss": 0,  # Skip sell: leader profited but we'd lose
    })

    # Blockchain detection
    blockchain_trades_detected: int = 0
    blockchain_trades_enriched: int = 0
    blockchain_polls: int = 0

    # Snapshot polls
    snapshot_polls: int = 0
    markets_tracked: int = 0

    # Timing
    _start_time: float = field(default_factory=time.time)
    _last_status_time: float = field(default_factory=time.time)
    _status_interval: float = 15.0

    @property
    def trades_skipped(self) -> int:
        """Computed: events seen minus stale minus executed.

        If we saw 19 events, 0 were stale, and bought 0, skip = 19.
        This is the user-facing metric: how many leader trades we didn't act on.
        """
        return max(
            0,
            self.leader_events_seen
            - self.leader_events_stale
            - self.buy_count
            - self.sell_count,
        )

    @property
    def _top_skip_reasons(self) -> str:
        """Get top 3 skip reasons as compact string."""
        active = {k: v for k, v in self.execution_skips.items() if v > 0}
        if not active:
            return ""
        # Sort by count descending
        top = sorted(active.items(), key=lambda x: -x[1])[:3]
        return ", ".join(f"{k}={v}" for k, v in top)

    def record_skip(self, reason: str) -> None:
        """Record a skipped execution.

        Args:
            reason: Skip reason key (e.g., "no_price", "cap", "reserve")
        """
        if reason in self.execution_skips:
            self.execution_skips[reason] += 1
        else:
            self.execution_skips[reason] = 1

    def should_print_status(self) -> bool:
        """Check if it's time to print a status line."""
        now = time.time()
        if now - self._last_status_time >= self._status_interval:
            self._last_status_time = now
            return True
        return False

    def _elapsed_str(self) -> str:
        elapsed = time.time() - self._start_time
        m, s = divmod(int(elapsed), 60)
        return f"{m:02d}:{s:02d}"

    def print_status_line(self, portfolio_summary: str = "") -> None:
        """Print a readable status block to terminal.

        Args:
            portfolio_summary: Optional portfolio summary string to append
        """
        skip_reasons = self._top_skip_reasons
        reasons_str = f"  ({skip_reasons})" if skip_reasons else ""

        pnl_str = ""
        if self.buy_dollars > 0:
            net = self.buy_dollars - self.sell_dollars
            pnl_str = f"  Net deployed: ${net:.2f}"

        print(
            f"\n--- [{self._elapsed_str()}] STATUS "
            f"| Markets: {self.markets_tracked} "
            f"| Events: {self.leader_events_seen} seen, {self.leader_events_deduped} dup, {self.leader_events_stale} stale "
            f"---"
        )
        print(
            f"  Trades:  BUY {self.buy_count} (${self.buy_dollars:.2f})  "
            f"SELL {self.sell_count} (${self.sell_dollars:.2f})  "
            f"| Skip: {self.trades_skipped}{reasons_str}{pnl_str}"
        )
        print(
            f"  Chain:   {self.blockchain_trades_detected} detected, "
            f"{self.blockchain_trades_enriched} enriched "
            f"({self.blockchain_polls} polls)"
        )
        if portfolio_summary:
            print(f"  Portfolio: {portfolio_summary}")
        print()

    def print_summary(self) -> None:
        """Print full shutdown summary."""
        elapsed = time.time() - self._start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        print()
        print("=" * 60)
        print("         MIRROR STRATEGY - SESSION SUMMARY")
        print("=" * 60)
        print(f"  Duration:  {mins}m {secs}s")
        print(f"  Markets:   {self.markets_tracked}")
        print()

        print("  LEADER EVENTS")
        print(f"    Seen:    {self.leader_events_seen}")
        print(f"    Deduped: {self.leader_events_deduped}")
        print(f"    Stale:   {self.leader_events_stale}")
        print(f"    Skipped: {self.trades_skipped}")
        print()

        print("  BLOCKCHAIN")
        print(f"    Polls:    {self.blockchain_polls}")
        print(f"    Detected: {self.blockchain_trades_detected}")
        print(f"    Enriched: {self.blockchain_trades_enriched}")
        print()

        print("  TRADES EXECUTED")
        buy_str = f"{self.buy_count} orders, ${self.buy_dollars:.2f}"
        sell_str = f"{self.sell_count} orders, ${self.sell_dollars:.2f}"
        print(f"    Buys:  {buy_str}")
        print(f"    Sells: {sell_str}")
        if self.buy_dollars > 0:
            net = self.buy_dollars - self.sell_dollars
            print(f"    Net:   ${net:.2f}")
        print()

        # Skip reasons
        active_skips = {k: v for k, v in sorted(self.execution_skips.items()) if v > 0}
        if active_skips:
            print("  SKIP REASONS")
            for reason, count in active_skips.items():
                print(f"    {reason}: {count}")
        else:
            print("  SKIP REASONS: (none)")

        print("=" * 60)
