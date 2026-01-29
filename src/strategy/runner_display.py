"""Human-readable display runner for dry-run mode (delta-driven)."""

from __future__ import annotations

from decimal import Decimal
import time
import traceback
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..config import BotConfig

from ..core import Clock
from ..execution.base import ExecutionAdapter
from ..logging import init_logger
from ..logging.display import HumanDisplay
from .runner import StrategyRunner


class DisplayRunner(StrategyRunner):
    """Display wrapper over the delta-driven StrategyRunner."""

    def __init__(
        self,
        config: "BotConfig",
        execution_adapter: ExecutionAdapter,
        clock: Clock,
        duration_minutes: Optional[int] = None,
    ):
        super().__init__(config, execution_adapter, clock, use_live_data=True)
        
        # CRITICAL: Verify data source is available - fail fast if not
        if not self.data_source:
            raise RuntimeError("FATAL: LiveDataSource failed to initialize - cannot run without API access")
        
        # Reduce log volume: file only and at WARNING+ (to catch important issues)
        log_max_bytes = max(0, config.logging.log_max_mb) * 1024 * 1024
        self.logger = init_logger(
            mode=config.mode,
            level="WARNING",
            log_file=config.data_dir / config.logging.file_path,
            console=False,
            max_bytes=log_max_bytes if log_max_bytes > 0 else None,
            backup_count=config.logging.log_backup_count,
        )
        # Disable trace writer in dry-run display to avoid huge files
        self.trace_writer = None
        self.display = HumanDisplay(
            our_capital=config.scaling.our_capital,
            hourly_budget=config.scaling.hourly_budget,
        )
        if duration_minutes:
            self.display.set_duration(duration_minutes)
        self._duration_minutes = duration_minutes
        self._last_status_ts = 0.0
        self._status_interval_sec = 5.0
        self._cash_balance = config.scaling.our_capital
        self._last_portfolio_value = self._cash_balance
        self._last_pnl = Decimal("0")
        self._last_refresh_time = None
        self._last_refresh_monotonic = time.monotonic()
        self._last_missing_bids = 0
        self._consecutive_fetch_errors = 0
        self._max_consecutive_errors = 5
        # Force 1s polling for leader/API updates in dry-run display mode
        self.config.leader.poll_interval_sec = 1.0

    def run(self) -> None:
        """Run the strategy loop with human-readable output.
        
        CRITICAL: Errors are NOT silently ignored. Any failure that could
        affect trade detection will be surfaced to the user.
        """
        self._running = True
        
        # CRITICAL: Take baseline snapshot FIRST (required for delta detection)
        print("Taking baseline snapshot of leader positions...")
        self._initialize_baseline()
        
        if not self._initialized:
            raise RuntimeError("FATAL: Failed to initialize baseline - cannot detect leader trades")
        
        print(f"Baseline taken: {len(self.market_states)} markets tracked")
        print("Monitoring for leader trades...\n")
        
        try:
            # Prime metrics before first status
            self._refresh_metrics()
            while self._running:
                if self.display.should_stop():
                    break
                
                try:
                    self._run_cycle()
                    self._consecutive_fetch_errors = 0  # Reset on success
                except Exception as e:
                    self._consecutive_fetch_errors += 1
                    print(f"\n[ERROR] Cycle failed: {e}")
                    if self._consecutive_fetch_errors >= self._max_consecutive_errors:
                        raise RuntimeError(f"FATAL: {self._max_consecutive_errors} consecutive cycle failures - stopping") from e
                    print(f"  Retrying... ({self._consecutive_fetch_errors}/{self._max_consecutive_errors})")
                
                self._maybe_print_status()
                self._refresh_metrics()
                self.clock.sleep(self.config.leader.poll_interval_sec)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            # Call base class shutdown summary for stats
            self._print_shutdown_summary()

    def _maybe_print_status(self) -> None:
        now_ts = time.monotonic()
        if now_ts - self._last_status_ts < self._status_interval_sec:
            return
        self._last_status_ts = now_ts

        portfolio_value = self._last_portfolio_value
        pnl = self._last_pnl
        api_age_sec = max(0.0, time.monotonic() - self._last_refresh_monotonic)
        self.display.print_status_summary(
            leader_trades_seen=self._leader_trades_seen,
            filtered_count=self._filtered_decisions,
            portfolio_value=portfolio_value,
            pnl=pnl,
            timestamp=self.clock.now(),
            api_age_sec=api_age_sec,
            missing_bids=self._last_missing_bids,
        )

    def _refresh_metrics(self) -> None:
        """Refresh portfolio value/PnL from live prices each cycle."""
        self._last_portfolio_value, self._last_missing_bids = self._compute_portfolio_value()
        self._last_pnl = self._last_portfolio_value - self.config.scaling.our_capital
        self._last_refresh_time = self.clock.now()
        self._last_refresh_monotonic = time.monotonic()

    def _compute_portfolio_value(self) -> tuple[Decimal, int]:
        total = self._cash_balance
        missing_bids = 0
        positions = self.shadow.iter_positions()
        for pos in positions.values():
            if pos.shares <= 0:
                continue
            price = None
            if self.price_context:
                ctx = self.price_context.fetch(pos.token_id)
                # Worst-case valuation: use best bid only
                price = ctx.bid
            if price is None:
                price = Decimal("0")
                missing_bids += 1
            total += pos.shares * price
        return total, missing_bids

    def _execute_shadow_order(self, *args, **kwargs) -> None:
        """Override to print trades after execution."""
        token_id = kwargs.get("token_id")
        market_id = kwargs.get("market_id")
        side = kwargs.get("side")
        action = kwargs.get("action")
        trigger = kwargs.get("trigger", "")
        
        if token_id and market_id and side:
            before = self.shadow.get(token_id, market_id, side).shares
        else:
            before = Decimal("0")
        
        super()._execute_shadow_order(*args, **kwargs)
        
        if token_id and market_id and side:
            after = self.shadow.get(token_id, market_id, side).shares
        else:
            after = Decimal("0")
        
        if action in ("BUY", "SELL") and after != before:
            price = Decimal("0.5")
            if self.price_context and token_id:
                ctx = self.price_context.fetch(token_id)
                price = ctx.ask if action == "BUY" and ctx.ask else ctx.bid if action == "SELL" and ctx.bid else ctx.mid or price
            
            delta_shares = abs(after - before)
            dollars = (delta_shares * price).quantize(Decimal("0.01"))
            if action == "BUY":
                self._cash_balance -= dollars
            else:
                self._cash_balance += dollars
            
            title = "Unknown Market"
            if self.data_source:
                market = self.data_source.get_discovered_markets().get(f"{market_id}_{side}")
                if market:
                    title = market.title
            
            self.display.print_trade(
                market_id=market_id,
                title=title,
                side=str(side),
                action=action,
                dollars=dollars,
                price=price,
                shares=delta_shares,
                cash_left=self._cash_balance,
                timestamp=kwargs.get("now") or self.clock.now(),
                reason=trigger,
            )
