"""Main strategy runner.

Orchestrates the trading loop:
1. Poll leader state (events-first architecture)
2. Update market state machines
3. Compute decisions
4. Execute orders
5. Log and trace

EVENTS-FIRST ARCHITECTURE:
- Trade events are the PRIMARY trigger
- Snapshots are SECONDARY (for verification only)
- SELL events execute IMMEDIATELY (never blocked)
- BUY events can be filtered/batched
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Set, Any

if TYPE_CHECKING:
    from ..config import BotConfig

from ..core import (
    Clock,
    ExecutionMode,
    MarketPhase,
    MarketId,
    Exposure,
    LeaderSnapshot,
    MySnapshot,
    GlobalStateMachine,
    DecisionEngine,
    Decision,
    Side,
    OrderRequest,
    OrderType,
)
from ..execution.base import ExecutionAdapter
from ..logging import BotLogger, init_logger
from .delta_state import ShadowPortfolio, MarketCopyState

# Try to import live data source
try:
    from ..data import LiveDataSource
    HAS_LIVE_DATA = True
except ImportError:
    HAS_LIVE_DATA = False
    LiveDataSource = None


# Simple price context for delta strategy (since price_context.py was removed)
@dataclass
class PriceContext:
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mid: Optional[Decimal] = None
    spread: Optional[Decimal] = None
    source: str = "unknown"


class StrategyRunner:
    """Main strategy loop runner.
    
    CRITICAL INVARIANTS:
    1. Baseline snapshot taken at startup - history before that is ignored
    2. Trading only on valid triggers (event, delta > epsilon, resync, safety)
    3. SELL priority over BUY - NEVER blocked
    4. Never sell more than owned
    5. Shadow positions never go negative
    6. Circuit breakers pause trading on errors
    
    EVENTS-FIRST ARCHITECTURE:
    - Trade events are PRIMARY trigger (fast path)
    - Position snapshots are SECONDARY (verification only)
    - SELLs execute immediately, BUYs can be delayed/filtered
    """
    
    # Circuit breaker constants
    MAX_CONSECUTIVE_API_ERRORS = 5
    MAX_UNKNOWN_FILLS = 3
    LEADER_NEAR_ZERO_THRESHOLD = Decimal("0.50")  # $0.50
    LEADER_NEAR_ZERO_CYCLES = 3  # Force exit after 3 cycles of leader near zero
    
    def __init__(
        self,
        config: "BotConfig",
        execution_adapter: ExecutionAdapter,
        clock: Clock,
        use_live_data: bool = True,
    ):
        self.config = config
        self.execution = execution_adapter
        self.clock = clock
        self.use_live_data = use_live_data and HAS_LIVE_DATA
        
        # Initialize components
        self.state_machine = GlobalStateMachine(config.get_state_machine_config())
        self.decision_engine = DecisionEngine(
            config.get_decision_config(),
            config.mode,
        )
        
        # Persistent shadow portfolio (used in DRY_RUN/PAPER)
        # LIVE mode uses real API positions, shadow is only for tracking
        self.shadow = ShadowPortfolio()
        self.market_states: Dict[MarketId, MarketCopyState] = {}
        self._seen_trade_keys: Set[str] = set()
        self._last_reconcile_time: Optional[datetime] = None
        
        # BASELINE SNAPSHOT - Time zero reference (set on first cycle)
        self._baseline_snapshot: Optional[LeaderSnapshot] = None
        self._baseline_time: Optional[datetime] = None
        self._initialized = False
        
        # Circuit breaker state
        self._consecutive_api_errors = 0
        self._unknown_fills_count = 0
        self._circuit_breaker_active = False
        self._circuit_breaker_reason: Optional[str] = None
        self._circuit_breaker_until: Optional[datetime] = None
        
        # Leader near-zero tracking (force exit logic)
        self._leader_near_zero_cycles: Dict[MarketId, int] = {}
        
        # Session statistics for shutdown summary
        self._stats = {
            "trades_placed": 0,
            "trades_skipped": 0,
            "trades_blocked_caps": 0,
            "trades_blocked_minimum": 0,
            "buys_executed": 0,
            "sells_executed": 0,
            "total_bought_dollars": Decimal("0"),
            "total_sold_dollars": Decimal("0"),
            "max_exposure_reached": Decimal("0"),
            "safety_triggers": 0,
            "expiry_triggers": 0,
            "force_exits": 0,
            "circuit_breaker_activations": 0,
            "invariant_violations": 0,
        }
        
        # Initialize logging
        log_max_bytes = max(0, config.logging.log_max_mb) * 1024 * 1024
        self.logger = init_logger(
            mode=config.mode,
            level=config.logging.level,
            log_file=config.data_dir / config.logging.file_path if config.logging.output in ("file", "both") else None,
            console=config.logging.output in ("console", "both"),
            max_bytes=log_max_bytes if log_max_bytes > 0 else None,
            backup_count=config.logging.log_backup_count,
        )
        
        # Initialize live data source if available
        self.data_source = None
        if self.use_live_data and config.leader.address:
            try:
                # Use trader.address for LIVE mode reconciliation
                my_address = config.trader.address if config.trader.address else None
                
                self.data_source = LiveDataSource(
                    leader_address=config.leader.address,
                    my_address=my_address,
                    timeout=config.api.timeout_sec,
                )
                self.logger.info("Live data source initialized")
                
                # Connect data source to execution adapter for token ID lookup
                # Token IDs are discovered from leader activity, not hardcoded
                if hasattr(execution_adapter, 'set_data_source'):
                    execution_adapter.set_data_source(self.data_source)
                    self.logger.info("Data source connected to execution adapter")
                    
            except Exception as e:
                self.logger.warning(f"Could not initialize live data source: {e}")
                self.data_source = None
        
        if config.mode == ExecutionMode.LIVE and self.data_source and not self.data_source.my_address:
            self.logger.warning("Live reconcile disabled (my_address not configured)")
        
        # Instrumentation timing
        self._last_instrumentation_log = time.time()
        self._instrumentation_interval = 10.0  # Log counters every 10 seconds
        
        # Market filtering: track which markets are hourly up/down
        self._hourly_updown_markets: Set[str] = set()
        
        # State
        self._running = False
        self._cycle_count = 0
        self._hourly_budget_used = Decimal("0")
        self._current_hour: Optional[datetime] = None
        self._pending_order_ids: List[str] = []
        self._leader_trades_seen = 0
        self._filtered_decisions = 0
        
        # TRADE-FIRST TRACKING: Track seen trade transaction hashes
        # This is the PRIMARY signal - leader's actual trades from /trades API
        # UNIFIED: Use _seen_trade_keys for BOTH trade-first and legacy detection
        self._seen_trade_hashes: Set[str] = set()  # Transaction hashes
        self._last_trade_timestamp: int = 0  # Unix timestamp of last seen trade

        # FIX: Track which market/side had trade-first action THIS CYCLE
        # to prevent double-execution when delta logic runs in the same cycle
        self._trade_first_acted_this_cycle: Dict[str, Set[str]] = {}  # market_id -> set of side strings

        # FIX: Separate snapshot audit to slow loop (every 20s, not every cycle)
        self._last_snapshot_audit_time: Optional[datetime] = None
        self._snapshot_audit_interval: float = 20.0  # seconds between snapshot audits

        # FIX: Cache leader entry prices to avoid API call per BUY
        self._leader_entry_price_cache: Dict[str, Dict[str, Decimal]] = {}  # market_id -> {side_str: price}
        self._leader_entry_price_cache_time: Optional[float] = None
        self._leader_entry_price_cache_ttl: float = 30.0  # refresh every 30s

        # Full instrumentation counters per spec
        self._counters = {
            "markets_tracked_count": 0,
            "event_polls_total": 0,
            "events_seen_total": 0,
            "events_accepted_total": 0,
            "events_deduped_total": 0,
            "events_out_of_order_dropped_total": 0,
            "snapshot_polls_total": 0,
            "snapshot_corrections_total": 0,
            "orders_submitted_buy": 0,
            "orders_submitted_sell": 0,
            "orders_skipped_drift": 0,
            "orders_skipped_spread": 0,
            "orders_skipped_slippage": 0,
            "orders_skipped_min": 0,
            "orders_skipped_cap": 0,
            "sells_executed_total": 0,
            "sells_skipped_total": 0,
        }
    
    def run(self) -> None:
        """Run the main strategy loop.
        
        On startup:
        1. Take baseline snapshot of leader positions (time zero)
        2. Do NOT act on any history before baseline
        3. Initialize all markets in BURST mode
        
        On shutdown:
        1. Print summary of all activity
        """
        self._running = True
        
        # Log startup
        self.logger.log_startup_banner(self.config.get_summary())
        
        try:
            # CRITICAL: Take baseline snapshot FIRST
            self._initialize_baseline()
            
            while self._running:
                # Check circuit breaker before each cycle
                if self._check_circuit_breaker():
                    self.clock.sleep(self.config.leader.poll_interval_sec)
                    continue
                
                self._run_cycle()
                self.clock.sleep(self.config.leader.poll_interval_sec)
                
        except Exception as e:
            self.logger.critical(f"Fatal error in strategy loop: {e}")
            self._stats["invariant_violations"] += 1
            raise
        finally:
            # Print shutdown summary
            self._print_shutdown_summary()
            self.logger.info("Strategy runner stopped")
    
    # Minimum exposure to track a market (filter out dust positions)
    MIN_MEANINGFUL_EXPOSURE = Decimal("1.00")  # $1 minimum
    
    def _initialize_baseline(self) -> None:
        """Take baseline snapshot of leader positions.
        
        This is TIME ZERO. We do NOT act on any history before this.
        All markets start fresh from this moment.
        
        CRITICAL: The baseline observation is recorded in dedupe state
        but marked as is_baseline=True, so it will NOT trigger any trades.
        Only FUTURE changes from this baseline will be processed.
        
        IMPORTANT: Only tracks markets with MEANINGFUL exposure (>$1).
        Dust positions are ignored.
        
        MARKET FILTERING: Only tracks HOURLY UP/DOWN markets per requirements.
        This reduces tracked markets from ~55 to ~4.
        """
        now = self.clock.now()
        self.logger.info("Taking baseline snapshot (time zero)...")
        
        # This will raise if it fails - no silent failures
        baseline = self._fetch_leader_snapshot(now)
        
        self._baseline_snapshot = baseline
        self._baseline_time = now
        self._initialized = True
        
        # === TRADE-FIRST BASELINE ===
        # Mark all existing leader trades as "seen" so we don't re-process history
        # COUNT them in _leader_trades_seen so display shows total leader activity
        # Only EXECUTE on NEW trades from this point forward
        if self.data_source:
            try:
                existing_trades = self.data_source.fetch_trades(
                    self.data_source.leader_address,
                    limit=100,  # Get recent history
                )
                for trade in existing_trades:
                    trade_hash = trade.transaction_hash
                    if not trade_hash:
                        trade_hash = f"{trade.condition_id}_{trade.timestamp}_{trade.side}_{trade.size}_{trade.price}"
                    self._seen_trade_hashes.add(trade_hash)
                    
                    # ALSO add to legacy _seen_trade_keys with MATCHING key format
                    if trade.transaction_hash:
                        legacy_key = f"{trade.transaction_hash}_{trade.timestamp}"
                    else:
                        legacy_key = f"{trade.condition_id}_{trade.timestamp}_{trade.side}_{trade.size}_{trade.price}"
                    self._seen_trade_keys.add(legacy_key)
                    
                    if trade.timestamp > self._last_trade_timestamp:
                        self._last_trade_timestamp = trade.timestamp
                    
                    # COUNT the trade (display shows total leader trades observed)
                    self._leader_trades_seen += 1
                
                self.logger.info("Trade baseline recorded", data={
                    "existing_trades_marked": len(existing_trades),
                    "leader_trades_counted": self._leader_trades_seen,
                    "last_trade_timestamp": self._last_trade_timestamp,
                })
            except Exception as e:
                self.logger.warning(f"Could not fetch trade baseline: {e}")
        
        # Filter to only meaningful exposures AND hourly up/down markets
        active_markets = 0
        dust_markets = 0
        non_hourly_markets = 0
        
        for market_id, exposure in baseline.exposures.items():
            # Skip dust positions
            if exposure.gross_dollars < self.MIN_MEANINGFUL_EXPOSURE:
                dust_markets += 1
                continue
            
            # MARKET FILTERING: Only track hourly up/down markets
            # When no data source is available (test/mock mode), accept all markets
            is_hourly = True  # default to accept when no filtering available
            if self.data_source:
                is_hourly = self.data_source.is_hourly_updown_market(market_id)

            if not is_hourly:
                non_hourly_markets += 1
                self.logger.debug("Skipping non-hourly market", data={
                    "market_id": market_id,
                    "gross_dollars": str(exposure.gross_dollars),
                })
                continue
            
            # Track as hourly up/down market
            self._hourly_updown_markets.add(market_id)
            
            active_markets += 1
            state = self._get_market_state(market_id)
            
            # === CRITICAL: Initialize dedupe state with baseline ===
            # The check_and_update will mark this as BASELINE and NOT process it
            up_accepted, up_reason, _ = state.up_dedupe.check_and_update(
                curr_dollars=exposure.up_dollars,
                curr_shares=exposure.up_shares,
                curr_ts=now,
            )
            down_accepted, down_reason, _ = state.down_dedupe.check_and_update(
                curr_dollars=exposure.down_dollars,
                curr_shares=exposure.down_shares,
                curr_ts=now,
            )
            
            # These should always be BASELINE (not processed)
            assert not up_accepted, f"Baseline UP should not be accepted: {up_reason}"
            assert not down_accepted, f"Baseline DOWN should not be accepted: {down_reason}"
            
            # Record for instrumentation
            state.record_dedupe_decision(up_reason)
            state.record_dedupe_decision(down_reason)
            
            # Legacy state tracking
            state.last_leader_exposure = exposure
            state.last_leader_snapshot_time = now
            # Mark as NOT burst-aligned yet - will align after stabilization
            state.burst_aligned = False
            
            self.logger.debug("Baseline recorded", data={
                "market_id": market_id,
                "up_dollars": str(exposure.up_dollars),
                "down_dollars": str(exposure.down_dollars),
                "up_reason": up_reason,
                "down_reason": down_reason,
            })
        
        self.logger.info(
            "Baseline snapshot taken",
            data={
                "time": now.isoformat(),
                "active_markets": active_markets,
                "hourly_updown_markets": len(self._hourly_updown_markets),
                "non_hourly_markets_skipped": non_hourly_markets,
                "dust_markets_ignored": dust_markets,
                "total_positions": len(baseline.exposures),
                "total_assets": str(baseline.total_assets),
            }
        )
    
    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is active.
        
        Returns True if trading should be paused.
        """
        if not self._circuit_breaker_active:
            return False
        
        now = self.clock.now()
        if self._circuit_breaker_until and now >= self._circuit_breaker_until:
            # Cooldown expired, deactivate
            self.logger.info(
                "Circuit breaker cooldown expired, resuming",
                data={"reason": self._circuit_breaker_reason}
            )
            self._circuit_breaker_active = False
            self._circuit_breaker_reason = None
            self._circuit_breaker_until = None
            return False
        
        return True
    
    def _activate_circuit_breaker(self, reason: str) -> None:
        """Activate circuit breaker and pause trading."""
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        cooldown = self.config.circuit_breakers.breaker_cooldown_sec
        self._circuit_breaker_until = self.clock.now() + __import__('datetime').timedelta(seconds=cooldown)
        self._stats["circuit_breaker_activations"] += 1
        
        self.logger.error(
            f"CIRCUIT BREAKER ACTIVATED: {reason}",
            data={"cooldown_sec": cooldown}
        )
    
    def _get_leader_entry_price(self, market_id: MarketId, side: Side) -> Optional[Decimal]:
        """Get leader's entry price for a position.

        FIX: Uses cached leader positions to avoid API call per BUY.
        Cache is refreshed every 30s from the leader snapshot that is
        already being fetched each cycle.
        """
        if not self.data_source:
            return None

        now = time.time()
        # Refresh cache if stale or empty
        if (self._leader_entry_price_cache_time is None or
                now - self._leader_entry_price_cache_time > self._leader_entry_price_cache_ttl):
            try:
                positions = self.data_source.fetch_positions(self.config.leader.address)
                cache: Dict[str, Dict[str, Decimal]] = {}
                for pos in positions:
                    outcome = pos.outcome.lower() if pos.outcome else ""
                    s = "UP" if outcome in ("yes", "up") else "DOWN"
                    if pos.avg_price and pos.avg_price > 0:
                        cache.setdefault(pos.condition_id, {})[s] = pos.avg_price
                self._leader_entry_price_cache = cache
                self._leader_entry_price_cache_time = now
            except Exception as e:
                self.logger.debug(f"Could not refresh leader entry price cache: {e}")

        side_str = "UP" if side == Side.UP else "DOWN"
        return self._leader_entry_price_cache.get(market_id, {}).get(side_str)
    
    def _check_session_loss_limit(self) -> bool:
        """Check if session losses exceed the configured limit.
        
        Returns True if we should STOP trading (loss limit hit).
        """
        if not self._capital_initialized():
            return False
        
        max_loss_pct = self.config.safety.max_session_loss_pct
        if max_loss_pct <= 0:
            return False  # Disabled
        
        # Calculate current session P&L
        starting_capital = self.config.scaling.our_capital
        current_value = self._calculate_current_portfolio_value()
        
        if current_value is None:
            return False  # Can't calculate, don't block
        
        loss_pct = ((starting_capital - current_value) / starting_capital) * Decimal("100")
        
        if loss_pct >= max_loss_pct:
            self.logger.critical(
                f"SESSION LOSS LIMIT HIT: {loss_pct:.1f}% loss (limit: {max_loss_pct}%)",
                data={"starting": str(starting_capital), "current": str(current_value)}
            )
            return True
        
        return False
    
    def _capital_initialized(self) -> bool:
        """Check if capital has been initialized."""
        return self.config._capital_initialized
    
    def _calculate_current_portfolio_value(self) -> Optional[Decimal]:
        """Calculate current portfolio value including cash and positions."""
        try:
            total = self.config.scaling.our_capital - self._hourly_budget_used
            
            for pos in self.shadow.iter_positions().values():
                if pos.shares <= 0:
                    continue
                
                # Use average price for valuation (no HTTP price context)
                price = pos.avg_price
                
                if price and price > 0:
                    total += pos.shares * price
            
            return total
        except Exception:
            return None
    
    def _print_shutdown_summary(self) -> None:
        """Print summary of all trading activity on shutdown."""
        print()
        print("=" * 70)
        print("                    SHUTDOWN SUMMARY")
        print("=" * 70)
        
        if self._baseline_time:
            elapsed = (self.clock.now() - self._baseline_time).total_seconds()
            print(f"Session Duration: {int(elapsed // 60)}m {int(elapsed % 60)}s")
        
        print(f"Cycles Executed: {self._cycle_count}")
        print(f"Markets Tracked (Hourly Up/Down): {len(self._hourly_updown_markets)}")
        print()
        
        # TRADE-FIRST stats
        print("--- TRADE-FIRST (Leader /trades API) ---")
        print(f"  Leader Trades Seen: {self._leader_trades_seen}")
        print(f"  Unique Trade Hashes: {len(self._seen_trade_hashes)}")
        print(f"  Last Trade Timestamp: {self._last_trade_timestamp}")
        print()
        
        # Aggregate dedupe stats across all markets
        total_accepted = 0
        total_baseline = 0
        total_stale = 0
        total_duplicate = 0
        for state in self.market_states.values():
            total_accepted += state.updates_accepted
            total_baseline += state.updates_rejected_baseline
            total_stale += state.updates_rejected_stale
            total_duplicate += state.updates_rejected_duplicate
        
        print("--- DEDUPE (Position Changes) ---")
        print(f"  Accepted (real deltas): {total_accepted}")
        print(f"  Rejected:")
        print(f"    Baseline (time zero): {total_baseline}")
        print(f"    Stale/out-of-order: {total_stale}")
        print(f"    Duplicate (no change): {total_duplicate}")
        print()
        
        print("--- TRADES ---")
        print(f"  Placed: {self._stats['trades_placed']}")
        print(f"    Buys:  {self._stats['buys_executed']} (${self._stats['total_bought_dollars']:.2f})")
        print(f"    Sells: {self._stats['sells_executed']} (${self._stats['total_sold_dollars']:.2f})")
        print(f"  Skipped: {self._stats['trades_skipped']}")
        print(f"    Blocked by caps: {self._stats['trades_blocked_caps']}")
        print(f"    Below minimum: {self._stats['trades_blocked_minimum']}")
        print()
        
        print("--- EXPOSURE ---")
        print(f"  Max Exposure Reached: ${self._stats['max_exposure_reached']:.2f}")
        print(f"  Hourly Budget Used: ${self._hourly_budget_used:.2f}")
        print()
        
        print("--- SAFETY ---")
        print(f"  Safety Triggers: {self._stats['safety_triggers']}")
        print(f"  Expiry Triggers: {self._stats['expiry_triggers']}")
        print(f"  Force Exits: {self._stats['force_exits']}")
        print(f"  Circuit Breaker Activations: {self._stats['circuit_breaker_activations']}")
        print(f"  Invariant Violations: {self._stats['invariant_violations']}")
        print()
        print("=" * 70)
    
    def stop(self) -> None:
        """Stop the strategy loop."""
        self._running = False
    
    def _run_cycle(self) -> None:
        """Run a single strategy cycle.
        
        EVENTS-FIRST ARCHITECTURE:
        1. Process trade events (PRIMARY trigger - fast path)
        2. Process position deltas (SECONDARY - verification)
        3. Resync drift trigger
        4. Safety/expiry trigger
        
        SELL events execute IMMEDIATELY (never blocked).
        BUY events can be filtered/batched.
        """
        # Must have baseline before trading
        if not self._initialized:
            self.logger.warning("Not initialized - skipping cycle")
            return
        
        self._cycle_count += 1
        now = self.clock.now()
        
        # Log instrumentation counters every ~10s
        self._maybe_log_instrumentation()
        
        # Reset hourly budget at hour boundary
        self._check_hour_boundary(now)
        
        # Fetch leader state
        try:
            leader_snapshot = self._fetch_leader_snapshot(now)
            # Reset error counter on success
            self._consecutive_api_errors = 0
        except Exception as e:
            self._consecutive_api_errors += 1
            self.logger.error(f"API error fetching leader snapshot: {e}")
            if self._consecutive_api_errors >= self.MAX_CONSECUTIVE_API_ERRORS:
                self._activate_circuit_breaker(f"Too many consecutive API errors: {e}")
            # Re-raise so callers know the cycle failed
            raise
        
        # Update state machines for all active markets
        market_phases = self._update_state_machines(now, leader_snapshot)
        
        # Build our snapshot (LIVE uses real positions, DRY_RUN/PAPER uses shadow)
        my_snapshot = self._build_my_snapshot(now)
        if my_snapshot is None:
            raise RuntimeError("Failed to build my snapshot - cannot continue without position data")
        
        # Track max exposure for summary
        current_exposure = sum(exp.gross_dollars for exp in my_snapshot.exposures.values())
        if current_exposure > self._stats["max_exposure_reached"]:
            self._stats["max_exposure_reached"] = current_exposure
        
        # Check for leader near-zero (force exit logic)
        self._check_leader_near_zero(now, leader_snapshot, my_snapshot)
        
        # === TRADE-FIRST ARCHITECTURE ===
        # FIX: Clear per-cycle tracking to prevent double-execution
        self._trade_first_acted_this_cycle = {}

        # 1. PRIMARY: Process leader's actual trades from /trades API
        #    This is the fastest, most reliable signal - we copy actual BUY/SELL events
        self._counters["event_polls_total"] += 1
        new_trades = self._process_leader_trades(now, my_snapshot)
        if new_trades > 0:
            self.logger.info(f"TRADE-FIRST: Processed {new_trades} new leader trades")

        # 2. SECONDARY: Snapshot audit on SLOW cadence (every ~20s, not every cycle)
        #    Position comparison catches anything MISSED by trade API
        #    Does NOT re-process trades already handled by trade-first
        if self._last_snapshot_audit_time is None:
            self._last_snapshot_audit_time = now  # skip first cycle (baseline just taken)
        elif (now - self._last_snapshot_audit_time).total_seconds() >= self._snapshot_audit_interval:
            self._counters["snapshot_polls_total"] += 1
            self._last_snapshot_audit_time = now
            # Rebuild my_snapshot to reflect any trade-first fills from this cycle
            my_snapshot = self._build_my_snapshot(now) or my_snapshot
            self._process_delta_logic(now, leader_snapshot, my_snapshot, market_phases)
    
    def _check_leader_near_zero(
        self,
        now: datetime,
        leader_snapshot: LeaderSnapshot,
        my_snapshot: MySnapshot,
    ) -> None:
        """Check if leader has near-zero exposure but we still hold.
        
        If leader is near zero for several cycles while we hold exposure,
        force exit to avoid being stuck.
        """
        for market_id, my_exp in my_snapshot.exposures.items():
            if my_exp.gross_dollars < self.LEADER_NEAR_ZERO_THRESHOLD:
                # We have no significant exposure, nothing to worry about
                self._leader_near_zero_cycles.pop(market_id, None)
                continue
            
            leader_exp = leader_snapshot.exposures.get(market_id)
            leader_gross = leader_exp.gross_dollars if leader_exp else Decimal("0")
            
            if leader_gross < self.LEADER_NEAR_ZERO_THRESHOLD:
                # Leader is near zero, but we have exposure
                count = self._leader_near_zero_cycles.get(market_id, 0) + 1
                self._leader_near_zero_cycles[market_id] = count
                
                if count >= self.LEADER_NEAR_ZERO_CYCLES:
                    self.logger.warning(
                        f"FORCE EXIT: Leader near zero for {count} cycles but we hold ${my_exp.gross_dollars}",
                        data={"market_id": market_id}
                    )
                    self._force_exit_market(market_id, now, trigger="LEADER_NEAR_ZERO")
                    self._stats["force_exits"] += 1
                    self._leader_near_zero_cycles.pop(market_id, None)
            else:
                # Leader has exposure again, reset counter
                self._leader_near_zero_cycles.pop(market_id, None)
    
    def _force_exit_market(self, market_id: MarketId, now: datetime, trigger: str) -> None:
        """Force exit all positions in a market."""
        self._exit_market_all(market_id, now, trigger=trigger)
    
    def _maybe_log_instrumentation(self) -> None:
        """Log instrumentation counters every ~10 seconds.

        Prints to both logger AND console per spec requirement.
        """
        now = time.time()
        if now - self._last_instrumentation_log < self._instrumentation_interval:
            return

        self._last_instrumentation_log = now

        # Update market count
        self._counters["markets_tracked_count"] = len(self._hourly_updown_markets)
        self._counters["sells_executed_total"] = self._stats["sells_executed"]

        # Aggregate dedupe stats
        total_accepted = 0
        total_deduped = 0
        total_stale = 0
        for state in self.market_states.values():
            total_accepted += state.updates_accepted
            total_deduped += state.updates_rejected_duplicate
            total_stale += state.updates_rejected_stale
        self._counters["events_deduped_total"] = total_deduped
        self._counters["events_out_of_order_dropped_total"] = total_stale

        # Build console summary line
        c = self._counters
        s = self._stats
        summary = (
            f"[COUNTERS] "
            f"mkts={c['markets_tracked_count']} "
            f"polls={c['event_polls_total']} "
            f"ev_seen={c['events_seen_total']} "
            f"ev_acc={c['events_accepted_total']} "
            f"dedup={c['events_deduped_total']} "
            f"stale={c['events_out_of_order_dropped_total']} "
            f"snap_polls={c['snapshot_polls_total']} "
            f"snap_corr={c['snapshot_corrections_total']} "
            f"buy_sub={s['buys_executed']} "
            f"sell_sub={s['sells_executed']} "
            f"skip_drft={c['orders_skipped_drift']} "
            f"skip_sprd={c['orders_skipped_spread']} "
            f"skip_min={c['orders_skipped_min']} "
            f"skip_cap={c['orders_skipped_cap']} "
            f"sell_skip={c['sells_skipped_total']}"
        )
        print(summary)
        self.logger.info("Instrumentation counters", data={
            "counters": dict(c),
            "session_stats": {
                "trades_placed": s["trades_placed"],
                "buys_executed": s["buys_executed"],
                "sells_executed": s["sells_executed"],
                "hourly_budget_used": str(self._hourly_budget_used),
            },
        })
    
    def _check_hour_boundary(self, now: datetime) -> None:
        """Reset hourly budget at hour boundary."""
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        
        if self._current_hour != current_hour:
            if self._current_hour is not None:
                self.logger.info(
                    "Hour boundary crossed, resetting budget",
                    data={"old_hour": self._current_hour.isoformat(), "new_hour": current_hour.isoformat()},
                )
            self._current_hour = current_hour
            self._hourly_budget_used = Decimal("0")
            
            # Trigger market open for new hour
            # TODO: Determine active markets and call on_market_open
    
    def _fetch_leader_snapshot(self, now: datetime) -> Optional[LeaderSnapshot]:
        """Fetch current leader state.
        
        Uses LiveDataSource if available, otherwise returns mock data.
        
        CRITICAL: Errors are NOT silently ignored. If we have a data source
        configured but it fails, we raise an exception rather than continuing
        with stale or missing data.
        """
        if self.data_source:
            try:
                snapshot = self.data_source.build_leader_snapshot(now)
                
                # Validate snapshot data
                if snapshot.total_assets < 0:
                    raise ValueError(f"Invalid leader total_assets: {snapshot.total_assets}")
                
                for market_id, exp in snapshot.exposures.items():
                    if exp.up_dollars < 0 or exp.down_dollars < 0:
                        raise ValueError(f"Invalid leader exposure in {market_id}: up={exp.up_dollars}, down={exp.down_dollars}")
                    if exp.up_shares < 0 or exp.down_shares < 0:
                        raise ValueError(f"Invalid leader shares in {market_id}: up={exp.up_shares}, down={exp.down_shares}")
                
                return snapshot
                
            except Exception as e:
                self.logger.error(f"Error fetching leader snapshot: {e}")
                # Re-raise - do NOT silently return None when we have a data source
                # The caller should handle the error appropriately
                raise
        
        # Mock data for testing without API - ONLY when no data source is configured
        # This is only for unit tests, not for any real operation
        if self.config.mode == ExecutionMode.LIVE:
            raise RuntimeError("LIVE mode requires live data source")
        
        # Return empty mock only if no data source was ever configured (unit tests)
        return LeaderSnapshot(
            timestamp=now,
            exposures={},
            total_assets=Decimal("1000"),
            recent_trades=[],
        )
    
    def _fetch_my_snapshot(self, now: datetime) -> Optional[MySnapshot]:
        """Fetch current our state.
        
        Uses LiveDataSource if available, otherwise returns mock data.
        """
        if self.data_source:
            try:
                return self.data_source.build_my_snapshot(
                    now=now,
                    hourly_budget_used=self._hourly_budget_used,
                    pending_orders=self._pending_order_ids,
                )
            except Exception as e:
                self.logger.error(f"Error fetching my snapshot: {e}")
                return None
        
        # Mock data for testing/dry-run without API
        return MySnapshot(
            timestamp=now,
            exposures={},
            available_capital=self.config.scaling.our_capital,
            used_capital=Decimal("0"),
            pending_orders=self._pending_order_ids,
            hourly_budget_used=self._hourly_budget_used,
        )

    def _build_my_snapshot(self, now: datetime) -> Optional[MySnapshot]:
        """Build my snapshot using shadow state (or reconcile in LIVE)."""
        # For LIVE, reconcile periodically with actual positions
        if self.config.mode == ExecutionMode.LIVE and self.data_source and self.data_source.my_address:
            if self._should_reconcile(now):
                try:
                    positions = self.data_source.fetch_positions(self.data_source.my_address)
                    self._reconcile_shadow_from_positions(positions, now)
                    self._last_reconcile_time = now
                except Exception as e:
                    self.logger.warning("Live reconcile failed", data={"error": str(e)})
        
        return self.shadow.build_snapshot(
            timestamp=now,
            available_capital=self.config.scaling.our_capital,
            hourly_budget_used=self._hourly_budget_used,
        )

    def _should_reconcile(self, now: datetime) -> bool:
        if self._last_reconcile_time is None:
            return True
        elapsed = (now - self._last_reconcile_time).total_seconds()
        return elapsed >= self.config.copy_trading.reconcile_interval_sec

    def _reconcile_shadow_from_positions(self, positions, now: datetime) -> None:
        """Reconcile shadow portfolio from live positions.
        
        Args:
            positions: List of PolymarketPosition objects from API
            now: Current timestamp
        """
        from ..data.models import PolymarketPosition
        
        for pos in positions:
            # Handle both PolymarketPosition objects and dicts
            if isinstance(pos, PolymarketPosition):
                token_id = pos.asset
                condition_id = pos.condition_id
                outcome = pos.outcome
                size = pos.size
                avg_price = pos.avg_price
            elif isinstance(pos, dict):
                token_id = pos.get("asset", "")
                condition_id = pos.get("conditionId", pos.get("condition_id", ""))
                outcome = pos.get("outcome", "")
                size = Decimal(str(pos.get("size", "0")))
                avg_price = Decimal(str(pos.get("avgPrice", pos.get("avg_price", "0"))))
            else:
                self.logger.error(f"Unknown position type: {type(pos)}")
                continue
            
            side = Side.UP if outcome.upper() in ("YES", "UP") else Side.DOWN
            
            if not token_id or not condition_id:
                self.logger.warning("Invalid position data", data={
                    "token_id": token_id,
                    "condition_id": condition_id,
                })
                continue
            
            # Validate data
            if size < 0:
                self.logger.error("Negative position size from API", data={
                    "token_id": token_id,
                    "size": str(size),
                })
                raise ValueError(f"Invalid position: negative size {size}")
            
            if avg_price < 0:
                self.logger.error("Negative avg_price from API", data={
                    "token_id": token_id,
                    "avg_price": str(avg_price),
                })
                raise ValueError(f"Invalid position: negative avg_price {avg_price}")
            
            shadow_pos = self.shadow.get(token_id, condition_id, side)
            shadow_pos.shares = size
            shadow_pos.avg_price = avg_price
            shadow_pos.notional = size * avg_price
            shadow_pos.last_update = now

    def _get_market_state(self, market_id: MarketId) -> MarketCopyState:
        if market_id not in self.market_states:
            self.market_states[market_id] = MarketCopyState(market_id=market_id)
        return self.market_states[market_id]

    def _process_delta_logic(
        self,
        now: datetime,
        leader_snapshot: LeaderSnapshot,
        my_snapshot: MySnapshot,
        market_phases: Dict[MarketId, MarketPhase],
    ) -> None:
        """Delta-driven trading logic with STRICT per-side dedupe.
        
        CRITICAL INVARIANTS:
        1. Baseline observations are recorded but NOT acted upon
        2. Same position value is NEVER processed twice
        3. Stale/out-of-order updates are REJECTED
        4. Position delta is AUTHORITATIVE (not trade events)
        """
        scale_ratio = self.decision_engine.scaling_calc.compute_scale_ratio(
            self.decision_engine.scaling_calc.get_leader_capital(leader_snapshot)
        )
        
        # Only process markets we're tracking (hourly up/down only)
        # MARKET FILTERING: Skip non-hourly markets
        active_markets = set(self.market_states.keys())
        
        # Also check leader's current markets for new hourly up/down activity
        for market_id in leader_snapshot.exposures:
            if leader_snapshot.exposures[market_id].gross_dollars >= self.MIN_MEANINGFUL_EXPOSURE:
                # Only add if it's a hourly up/down market
                if self.data_source and self.data_source.is_hourly_updown_market(market_id):
                    if market_id not in self._hourly_updown_markets:
                        self._hourly_updown_markets.add(market_id)
                        self.logger.info("New hourly up/down market discovered", data={
                            "market_id": market_id,
                            "gross_dollars": str(leader_snapshot.exposures[market_id].gross_dollars),
                        })
                    active_markets.add(market_id)
        
        # Filter to only tracked hourly up/down markets
        active_markets = active_markets.intersection(self._hourly_updown_markets)
        
        for market_id in active_markets:
            phase = market_phases.get(market_id, MarketPhase.UNKNOWN)
            state = self._get_market_state(market_id)
            leader_exposure = leader_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
            
            # === PER-SIDE DEDUPE CHECK (AUTHORITATIVE) ===
            up_accepted, up_reason, up_delta = state.up_dedupe.check_and_update(
                curr_dollars=leader_exposure.up_dollars,
                curr_shares=leader_exposure.up_shares,
                curr_ts=now,
            )
            down_accepted, down_reason, down_delta = state.down_dedupe.check_and_update(
                curr_dollars=leader_exposure.down_dollars,
                curr_shares=leader_exposure.down_shares,
                curr_ts=now,
            )
            
            # Record instrumentation
            state.record_dedupe_decision(up_reason)
            state.record_dedupe_decision(down_reason)
            
            # Log dedupe decisions (ALWAYS, for debugging)
            self.logger.debug("Dedupe check", data={
                "market_id": market_id,
                "snapshot_ts": now.isoformat(),
                "up": {
                    "curr_dollars": str(leader_exposure.up_dollars),
                    "accepted": up_accepted,
                    "reason": up_reason,
                    "delta": str(up_delta) if up_delta else None,
                },
                "down": {
                    "curr_dollars": str(leader_exposure.down_dollars),
                    "accepted": down_accepted,
                    "reason": down_reason,
                    "delta": str(down_delta) if down_delta else None,
                },
            })
            
            # If BOTH sides rejected, no action needed
            if not up_accepted and not down_accepted:
                # Still update legacy tracking for compatibility
                state.last_leader_exposure = leader_exposure
                state.last_leader_snapshot_time = now
                continue
            
            # === COMPUTE DELTAS FROM DEDUPE-ACCEPTED VALUES ===
            delta_up = up_delta if up_delta is not None else Decimal("0")
            delta_down = down_delta if down_delta is not None else Decimal("0")
            
            # Check epsilon threshold (percentage-based or minimum absolute)
            # Trigger if: (delta >= epsilon_pct% of position) OR (delta >= epsilon_min_dollars)
            epsilon_min = self.config.copy_trading.epsilon_min_dollars
            epsilon_pct = self.config.copy_trading.epsilon_pct / Decimal("100")
            
            # Get previous position values for percentage calculation
            prev_up = state.up_dedupe.last_position_dollars or Decimal("0")
            prev_down = state.down_dedupe.last_position_dollars or Decimal("0")
            
            # Calculate thresholds (use larger of percentage or absolute minimum)
            up_threshold = max(prev_up * epsilon_pct, epsilon_min) if prev_up > 0 else epsilon_min
            down_threshold = max(prev_down * epsilon_pct, epsilon_min) if prev_down > 0 else epsilon_min
            
            up_significant = abs(delta_up) >= up_threshold
            down_significant = abs(delta_down) >= down_threshold
            
            if not (up_significant or down_significant):
                # Below epsilon, but still accepted by dedupe
                self.logger.debug("Delta below epsilon", data={
                    "market_id": market_id,
                    "delta_up": str(delta_up),
                    "delta_down": str(delta_down),
                    "up_threshold": str(up_threshold),
                    "down_threshold": str(down_threshold),
                })
                state.last_leader_exposure = leader_exposure
                state.last_leader_snapshot_time = now
                continue
            
            # === VALID TRIGGER - PROCESS ===
            leader_delta_trigger = True
            
            # Optional trade event trigger (secondary, NOT authoritative)
            trade_trigger = self._check_trade_trigger(state, leader_snapshot, market_id)
            
            # RESYNC trigger
            resync_trigger = self._check_resync_trigger(state, now, leader_exposure, my_snapshot, scale_ratio)
            
            # Update activity time
            state.last_activity_time = now
            
            # Log decision context with full instrumentation
            my_exp = my_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
            prev_exposure = state.last_leader_exposure
            self.logger.info("Decision context", data={
                "trigger": {
                    "leader_delta": leader_delta_trigger,
                    "leader_event": trade_trigger,
                    "resync": resync_trigger,
                },
                "market_id": market_id,
                "phase": str(phase),
                "snapshot_ts": now.isoformat(),
                "scale_ratio": str(scale_ratio),
                "leader_prev": prev_exposure.to_dict() if prev_exposure else None,
                "leader_now": leader_exposure.to_dict(),
                "leader_delta": {
                    "up": str(delta_up),
                    "down": str(delta_down),
                    "up_accepted": up_accepted,
                    "down_accepted": down_accepted,
                },
                "my_exposure": my_exp.to_dict(),
                "dedupe_stats": {
                    "accepted": state.updates_accepted,
                    "rejected_baseline": state.updates_rejected_baseline,
                    "rejected_stale": state.updates_rejected_stale,
                    "rejected_duplicate": state.updates_rejected_duplicate,
                },
            })
            
            # Expiry safety (override)
            if self._check_expiry_safety(market_id, now):
                state.last_leader_exposure = leader_exposure
                state.last_leader_snapshot_time = now
                continue
            
            # BURST handling - align once after stabilization
            if phase == MarketPhase.BURST and not state.burst_aligned:
                if self.state_machine.get_or_create(market_id).should_enter_during_burst():
                    self._burst_align(
                        market_id=market_id,
                        leader_exposure=leader_exposure,
                        my_snapshot=my_snapshot,
                        scale_ratio=scale_ratio,
                        now=now,
                        state=state,
                    )
                    state.burst_aligned = True
                state.last_leader_exposure = leader_exposure
                state.last_leader_snapshot_time = now
                continue
            
            # FOLLOW / RESYNC handling: SELL first, then BUY
            # Only process sides that were ACCEPTED by dedupe
            # FIX: Skip sides already handled by trade-first this cycle
            acted_sides = self._trade_first_acted_this_cycle.get(market_id, set())
            effective_delta_up = delta_up if (up_accepted and "UP" not in acted_sides) else Decimal("0")
            effective_delta_down = delta_down if (down_accepted and "DOWN" not in acted_sides) else Decimal("0")

            if acted_sides:
                self.logger.debug("Snapshot audit skipping trade-first sides", data={
                    "market_id": market_id,
                    "acted_sides": list(acted_sides),
                    "original_delta_up": str(delta_up),
                    "original_delta_down": str(delta_down),
                })

            self._process_sells(
                market_id=market_id,
                delta_up=effective_delta_up,
                delta_down=effective_delta_down,
                leader_exposure=leader_exposure,
                prev_exposure=prev_exposure,
                scale_ratio=scale_ratio,
                now=now,
                state=state,
            )
            self._process_buys(
                market_id=market_id,
                delta_up=effective_delta_up,
                delta_down=effective_delta_down,
                scale_ratio=scale_ratio,
                my_snapshot=my_snapshot,
                now=now,
                state=state,
            )
            
            # RESYNC drift correction (one corrective order)
            if resync_trigger:
                self._process_resync(
                    market_id=market_id,
                    leader_exposure=leader_exposure,
                    my_snapshot=my_snapshot,
                    scale_ratio=scale_ratio,
                    now=now,
                    state=state,
                )
            
            state.last_leader_exposure = leader_exposure
            state.last_leader_snapshot_time = now

    def _process_leader_trades(
        self,
        now: datetime,
        my_snapshot: MySnapshot,
    ) -> int:
        """TRADE-FIRST: Process leader's actual trades from /trades API.
        
        This is the PRIMARY signal for copy-trading. We fetch the leader's
        actual BUY/SELL trades and copy them directly (scaled).
        
        Position comparison (_process_delta_logic) is SECONDARY - used only
        for drift detection and reconciliation.
        
        Returns: Number of new trades processed
        """
        if not self.data_source:
            return 0
        
        # Fetch leader's recent trades
        try:
            trades = self.data_source.fetch_trades(
                self.data_source.leader_address,
                limit=50,
                since_timestamp=self._last_trade_timestamp if self._last_trade_timestamp > 0 else None,
            )
        except Exception as e:
            self.logger.error(f"Error fetching leader trades: {e}")
            return 0
        
        if not trades:
            return 0
        
        # Calculate scale ratio
        scale_ratio = self.decision_engine.scaling_calc.compute_scale_ratio(
            self.decision_engine.scaling_calc.get_leader_capital(
                self._baseline_snapshot or LeaderSnapshot(
                    timestamp=now,
                    exposures={},
                    total_assets=Decimal("10000"),  # Fallback
                    recent_trades=[],
                )
            )
        )
        
        new_trades_processed = 0
        
        # Process each trade (NEWEST FIRST from API, we want to process in order)
        # Reverse to process oldest first
        for trade in reversed(trades):
            # DEDUPE: Skip if we've already seen this trade
            trade_hash = trade.transaction_hash
            if not trade_hash:
                # Create a synthetic hash from trade data (same format as _check_trade_trigger)
                trade_hash = f"{trade.condition_id}_{trade.timestamp}_{trade.side}_{trade.size}_{trade.price}"
            
            if trade_hash in self._seen_trade_hashes:
                continue
            
            # Mark as seen FIRST to avoid double-processing
            self._seen_trade_hashes.add(trade_hash)
            
            # ALSO add to legacy _seen_trade_keys with MATCHING key format
            # _check_trade_trigger uses: "{trade_id}_{trade_ts}" if transaction_hash, else "{market_id}_{trade_ts}_{side}_{size}_{price}"
            if trade.transaction_hash:
                legacy_key = f"{trade.transaction_hash}_{trade.timestamp}"
            else:
                legacy_key = f"{trade.condition_id}_{trade.timestamp}_{trade.side}_{trade.size}_{trade.price}"
            self._seen_trade_keys.add(legacy_key)
            
            # Update last seen timestamp
            if trade.timestamp > self._last_trade_timestamp:
                self._last_trade_timestamp = trade.timestamp
            
            # MARKET FILTERING: Only process hourly up/down markets
            market_id = trade.condition_id
            if self.data_source and not self.data_source.is_hourly_updown_market(market_id):
                self.logger.debug("Skipping non-hourly market trade", data={
                    "market_id": market_id,
                    "side": trade.side,
                })
                continue
            
            # Track as hourly up/down market if not already
            if market_id not in self._hourly_updown_markets:
                self._hourly_updown_markets.add(market_id)
                self.logger.info("New hourly up/down market from trade", data={
                    "market_id": market_id,
                    "trade_side": trade.side,
                })
            
            # Determine side from outcome
            if trade.outcome.upper() in ("YES", "UP"):
                side = Side.UP
            else:
                side = Side.DOWN
            
            # Get token ID
            token_id = trade.asset
            if not token_id:
                token_id = self._get_token_id(market_id, side)
            
            if not token_id:
                self.logger.error("Missing token_id for trade", data={
                    "market_id": market_id,
                    "side": str(side),
                    "trade": trade.to_dict(),
                })
                continue
            
            # Calculate our scaled trade
            # Leader traded X shares at price P = X*P dollars
            leader_dollars = trade.size * trade.price
            our_dollars = leader_dollars * scale_ratio
            our_shares = trade.size * scale_ratio
            
            self.logger.info("TRADE-FIRST: Processing leader trade", data={
                "market_id": market_id,
                "side": str(side),
                "action": trade.side,  # BUY or SELL
                "leader_shares": str(trade.size),
                "leader_dollars": str(leader_dollars),
                "our_scaled_shares": str(our_shares),
                "our_scaled_dollars": str(our_dollars),
                "price": str(trade.price),
                "trade_hash": trade_hash[:20] + "..." if len(trade_hash) > 20 else trade_hash,
            })
            
            # Get market state
            state = self._get_market_state(market_id)
            
            # === EXECUTE THE TRADE ===
            if trade.side.upper() == "SELL":
                # SELL PRIORITY: Execute immediately, never blocked
                # Calculate shares to sell as fraction of our position
                pos = self.shadow.get(token_id, market_id, side)
                if pos.shares <= 0:
                    self.logger.debug("No position to sell (leader trade)", data={
                        "market_id": market_id,
                        "side": str(side),
                    })
                    # Don't record as filtered - we simply don't have anything to sell
                    # This is expected at startup when we haven't bought yet
                    continue
                
                # Sell proportionally: if leader sold 50% of their position, we sell 50%
                # But we need to know leader's previous position... use share count directly
                shares_to_sell = min(our_shares, pos.shares)  # Don't sell more than we have
                
                if shares_to_sell > 0:
                    self._execute_shadow_order(
                        market_id=market_id,
                        token_id=token_id,
                        side=side,
                        shares=shares_to_sell,
                        action="SELL",
                        trigger="LEADER_TRADE",
                        now=now,
                        state=state,
                    )
                    new_trades_processed += 1
                    # FIX: Record that trade-first handled this market/side SELL
                    self._trade_first_acted_this_cycle.setdefault(market_id, set()).add(str(side))

            elif trade.side.upper() == "BUY":
                # BUY: Apply filters but bump to minimum if below
                
                # Check budget
                remaining = self.config.scaling.hourly_budget - self._hourly_budget_used
                if remaining <= 0:
                    self._record_filtered("budget_exhausted_trade", {
                        "market_id": market_id,
                        "side": str(side),
                    })
                    continue
                
                dollars_to_buy = min(our_dollars, remaining)
                
                # Apply caps
                current = my_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
                current_side = current.get_side_dollars(side)
                current_gross = current.gross_dollars
                global_used = sum(exp.gross_dollars for exp in my_snapshot.exposures.values())
                
                side_room = max(Decimal("0"), self.config.caps.per_side - current_side)
                market_room = max(Decimal("0"), self.config.caps.per_market_gross - current_gross)
                global_room = max(Decimal("0"), self.config.caps.global_capital - global_used)
                
                dollars_to_buy = min(dollars_to_buy, side_room, market_room, global_room)
                
                if dollars_to_buy <= 0:
                    self._record_filtered("caps_blocked_trade", {
                        "market_id": market_id,
                        "side": str(side),
                    })
                    continue
                
                # MINIMUM BUMP: If below minimum, BUMP to minimum (don't skip)
                min_market = self.decision_engine.cap_enforcer.config.market_min_dollars
                if dollars_to_buy < min_market:
                    self.logger.info("Bumping buy to minimum", data={
                        "original": str(dollars_to_buy),
                        "minimum": str(min_market),
                    })
                    # Check if we have room for minimum
                    if min_market <= min(remaining, side_room, market_room, global_room):
                        dollars_to_buy = min_market
                    else:
                        self._record_filtered("no_room_for_minimum_trade", {
                            "market_id": market_id,
                            "side": str(side),
                        })
                        continue
                
                # Execute the buy
                self._execute_shadow_order(
                    market_id=market_id,
                    token_id=token_id,
                    side=side,
                    dollars=dollars_to_buy,
                    action="BUY",
                    trigger="LEADER_TRADE",
                    now=now,
                    state=state,
                )
                new_trades_processed += 1
                # FIX: Record that trade-first handled this market/side BUY
                self._trade_first_acted_this_cycle.setdefault(market_id, set()).add(str(side))

            self._leader_trades_seen += 1
            self._counters["events_seen_total"] += 1
            self._counters["events_accepted_total"] += 1
        
        return new_trades_processed

    def _check_trade_trigger(
        self,
        state: MarketCopyState,
        leader_snapshot: LeaderSnapshot,
        market_id: MarketId,
    ) -> bool:
        """Check if a new trade event was observed for this market."""
        if not leader_snapshot.recent_trades:
            return False

        triggered = False
        for trade in leader_snapshot.recent_trades:
            if trade.get("condition_id") != market_id and trade.get("conditionId") != market_id:
                continue

            # Create unique key from trade data (transaction_hash may not exist)
            # Use multiple fields to ensure uniqueness
            trade_id = trade.get("transaction_hash") or trade.get("id") or ""
            trade_ts = trade.get("timestamp", 0)
            side = trade.get("side", "")
            size = trade.get("size", "")
            price = trade.get("price", "")

            # Create composite key to handle multiple trades in same second
            if trade_id:
                # If we have transaction hash, use it (most reliable)
                key = f"{trade_id}_{trade_ts}"
            else:
                # Otherwise use combination of fields to create unique key
                key = f"{market_id}_{trade_ts}_{side}_{size}_{price}"

            if key and key not in self._seen_trade_keys:
                self._seen_trade_keys.add(key)
                self._leader_trades_seen += 1
                triggered = True
                state.last_seen_trade_id = trade_id or state.last_seen_trade_id
                state.last_seen_trade_ts = trade_ts or state.last_seen_trade_ts
        return triggered

    def _record_filtered(self, reason: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Record a filtered/skipped decision with statistics tracking."""
        self._filtered_decisions += 1
        self._stats["trades_skipped"] += 1

        # Track specific reasons for instrumentation counters
        if reason in ("caps_blocked", "budget_exhausted", "caps_blocked_trade",
                       "budget_exhausted_trade"):
            self._stats["trades_blocked_caps"] += 1
            self._counters["orders_skipped_cap"] += 1
        elif reason == "below_minimum":
            self._stats["trades_blocked_minimum"] += 1
            self._counters["orders_skipped_min"] += 1
        elif reason == "price_drift_too_high":
            self._counters["orders_skipped_drift"] += 1
        elif reason == "spread_too_wide":
            self._counters["orders_skipped_spread"] += 1

        if data is None:
            data = {}
        self.logger.debug("Filtered decision", data={"reason": reason, **data})

    def _check_resync_trigger(
        self,
        state: MarketCopyState,
        now: datetime,
        leader_exposure: Exposure,
        my_snapshot: MySnapshot,
        scale_ratio: Decimal,
    ) -> bool:
        # Time-based trigger
        last = state.last_resync_time or state.last_action_time
        if last is None:
            return False
        elapsed = (now - last).total_seconds()
        if elapsed < self.config.timing.resync_interval_sec:
            return False
        
        # Only resync if market was recently active
        if state.last_activity_time is None:
            return False
        if (now - state.last_activity_time).total_seconds() > self.config.copy_trading.resync_recent_activity_sec:
            return False
        
        # Drift check (percentage-based)
        target_up = leader_exposure.up_dollars * scale_ratio
        target_down = leader_exposure.down_dollars * scale_ratio
        my_exp = my_snapshot.exposures.get(leader_exposure.market_id, Exposure(market_id=leader_exposure.market_id))
        
        # Calculate drift as percentage of target
        total_target = target_up + target_down
        if total_target <= 0:
            return False
        
        drift_abs = abs(target_up - my_exp.up_dollars) + abs(target_down - my_exp.down_dollars)
        drift_pct = (drift_abs / total_target) * Decimal("100")
        
        if drift_pct >= self.config.copy_trading.resync_drift_start_pct:
            return True
        return False

    def _burst_align(
        self,
        market_id: MarketId,
        leader_exposure: Exposure,
        my_snapshot: MySnapshot,
        scale_ratio: Decimal,
        now: datetime,
        state: MarketCopyState,
    ) -> None:
        """One-time alignment after BURST stabilization."""
        target_up = leader_exposure.up_dollars * scale_ratio
        target_down = leader_exposure.down_dollars * scale_ratio
        my_exp = my_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
        delta_up = target_up - my_exp.up_dollars
        delta_down = target_down - my_exp.down_dollars
        
        # Prefer larger delta
        if abs(delta_up) >= abs(delta_down):
            self._execute_scaled_order(market_id, Side.UP, delta_up, my_snapshot, now, state, trigger="BURST_ALIGN")
        else:
            self._execute_scaled_order(market_id, Side.DOWN, delta_down, my_snapshot, now, state, trigger="BURST_ALIGN")

    def _process_sells(
        self,
        market_id: MarketId,
        delta_up: Decimal,
        delta_down: Decimal,
        leader_exposure: Exposure,
        prev_exposure: Optional[Exposure],
        scale_ratio: Decimal,
        now: datetime,
        state: MarketCopyState,
    ) -> None:
        """SELL priority processing (fractional reduction).
        
        Leader reduces exposure → we reduce proportionally.
        If leader sells X%, we sell X% of OUR position (not absolute shares).
        """
        if prev_exposure is None:
            return
        
        for side, delta in ((Side.UP, delta_up), (Side.DOWN, delta_down)):
            if delta >= 0:
                continue
            
            prev_val = prev_exposure.get_side_dollars(side)
            now_val = leader_exposure.get_side_dollars(side)
            if prev_val <= 0:
                continue
            sell_fraction = (prev_val - now_val) / prev_val
            sell_fraction = max(Decimal("0"), min(Decimal("1"), sell_fraction))
            
            token_id = self._get_token_id(market_id, side)
            if not token_id:
                self.logger.error("Missing token_id for sell", data={"market_id": market_id, "side": str(side)})
                self._record_filtered("missing_token_id_for_sell", {"market_id": market_id, "side": str(side)})
                continue
            
            # Check if WE have a position to sell
            if not self.shadow.has_position(token_id):
                # Leader sold but we have nothing - log as skip
                self._record_filtered("no_position_to_sell", {
                    "market_id": market_id,
                    "side": str(side),
                    "leader_sold_fraction": str(sell_fraction),
                    "leader_prev": str(prev_val),
                    "leader_now": str(now_val),
                })
                continue
            
            pos = self.shadow.get(token_id, market_id, side)
            shares_to_sell = pos.shares * sell_fraction
            if shares_to_sell <= 0:
                self._record_filtered("zero_sell_shares", {
                    "market_id": market_id,
                    "side": str(side),
                    "our_shares": str(pos.shares),
                    "sell_fraction": str(sell_fraction),
                })
                continue
            
            # SELL PRIORITY: Execute immediately, NO minimum check
            # Per requirements: SELL is NEVER blocked by minimum size filter
            self._execute_shadow_order(
                market_id=market_id,
                token_id=token_id,
                side=side,
                shares=shares_to_sell,
                action="SELL",
                trigger="LEADER_DELTA",
                now=now,
                state=state,
            )

    def _process_buys(
        self,
        market_id: MarketId,
        delta_up: Decimal,
        delta_down: Decimal,
        scale_ratio: Decimal,
        my_snapshot: MySnapshot,
        now: datetime,
        state: MarketCopyState,
    ) -> None:
        """BUY processing with accumulator and minimum size handling."""
        for side, delta in ((Side.UP, delta_up), (Side.DOWN, delta_down)):
            if delta <= 0:
                continue
            scaled_delta = delta * scale_ratio
            if scaled_delta <= 0:
                continue
            
            # Accumulate
            state.intent_accumulator[str(side)] += scaled_delta
            
            # Check minimum
            min_check = self.decision_engine.cap_enforcer.check_minimum(
                state.intent_accumulator[str(side)],
                is_market_order=True,
            )
            if not min_check["meets_minimum"]:
                self.logger.debug("Accumulating below minimum", data={
                    "trigger": "LEADER_DELTA",
                    "market_id": market_id,
                    "side": str(side),
                    "accumulator": str(state.intent_accumulator[str(side)]),
                    "min_check": min_check,
                })
                continue
            
            token_id = self._get_token_id(market_id, side)
            if not token_id:
                self.logger.error("Missing token_id for buy", data={"market_id": market_id, "side": str(side)})
                continue
            
            dollars = state.intent_accumulator[str(side)]
            self._execute_scaled_order(market_id, side, dollars, my_snapshot, now, state, trigger="LEADER_DELTA")
            state.intent_accumulator[str(side)] = Decimal("0")

    def _process_resync(
        self,
        market_id: MarketId,
        leader_exposure: Exposure,
        my_snapshot: MySnapshot,
        scale_ratio: Decimal,
        now: datetime,
        state: MarketCopyState,
    ) -> None:
        """RESYNC drift correction (one order per market)."""
        target_up = leader_exposure.up_dollars * scale_ratio
        target_down = leader_exposure.down_dollars * scale_ratio
        my_exp = my_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
        
        delta_up = target_up - my_exp.up_dollars
        delta_down = target_down - my_exp.down_dollars
        
        # Calculate drift as percentage of target (stop threshold)
        total_target = target_up + target_down
        if total_target <= 0:
            return
        
        drift_abs = abs(delta_up) + abs(delta_down)
        drift_pct = (drift_abs / total_target) * Decimal("100")
        
        # If drift below stop threshold percentage, do nothing
        if drift_pct < self.config.copy_trading.resync_drift_stop_pct:
            return
        
        # Choose larger side
        if abs(delta_up) >= abs(delta_down):
            self._execute_scaled_order(market_id, Side.UP, delta_up, my_snapshot, now, state, trigger="RESYNC_DRIFT")
        else:
            self._execute_scaled_order(market_id, Side.DOWN, delta_down, my_snapshot, now, state, trigger="RESYNC_DRIFT")
        
        state.last_resync_time = now

    def _execute_scaled_order(
        self,
        market_id: MarketId,
        side: Side,
        delta_dollars: Decimal,
        my_snapshot: MySnapshot,
        now: datetime,
        state: MarketCopyState,
        trigger: str,
    ) -> None:
        """Execute a market order based on delta dollars (positive buy, negative sell)."""
        if delta_dollars == 0:
            return
        
        action = "BUY" if delta_dollars > 0 else "SELL"
        dollars = abs(delta_dollars)
        
        # Enforce caps and hourly budget for buys
        if action == "BUY":
            remaining = self.config.scaling.hourly_budget - self._hourly_budget_used
            if remaining <= 0:
                self._record_filtered("budget_exhausted", {"market_id": market_id, "side": str(side)})
                return
            dollars = min(dollars, remaining)
            
            # Per-side / per-market / global caps
            current = my_snapshot.exposures.get(market_id, Exposure(market_id=market_id))
            current_side = current.get_side_dollars(side)
            current_gross = current.gross_dollars
            global_used = sum(exp.gross_dollars for exp in my_snapshot.exposures.values())
            
            side_room = max(Decimal("0"), self.config.caps.per_side - current_side)
            market_room = max(Decimal("0"), self.config.caps.per_market_gross - current_gross)
            global_room = max(Decimal("0"), self.config.caps.global_capital - global_used)
            
            dollars = min(dollars, side_room, market_room, global_room)
            if dollars <= 0:
                self._record_filtered("caps_blocked", {"market_id": market_id, "side": str(side)})
                return
        
        # Chunking (percentage-based)
        # Max order size = max_order_pct% of our capital
        max_order_dollars = (self.config.scaling.our_capital * 
                            self.config.copy_trading.max_order_pct / Decimal("100"))
        chunk_size = min(dollars, max_order_dollars)
        chunks = min(
            self.config.copy_trading.max_order_chunks,
            int((dollars / chunk_size).to_integral_value(rounding="ROUND_CEILING")) if chunk_size > 0 else 0,
        )
        
        token_id = self._get_token_id(market_id, side)
        if not token_id:
            self.logger.error("Missing token_id for order", data={"market_id": market_id, "side": str(side)})
            self._record_filtered("missing_token_id", {"market_id": market_id, "side": str(side)})
            return
        
        for _ in range(chunks):
            amt = min(chunk_size, dollars)
            if amt <= 0:
                break
            self._execute_shadow_order(
                market_id=market_id,
                token_id=token_id,
                side=side,
                dollars=amt,
                action=action,
                trigger=trigger,
                now=now,
                state=state,
            )
            dollars -= amt
            if dollars <= 0:
                break

    def _execute_shadow_order(
        self,
        market_id: MarketId,
        token_id: str,
        side: Side,
        now: datetime,
        state: MarketCopyState,
        action: str,
        trigger: str,
        dollars: Optional[Decimal] = None,
        shares: Optional[Decimal] = None,
    ) -> None:
        """Execute order with price context and update shadow/real portfolio.
        
        CRITICAL INVARIANTS:
        - SELL can NEVER exceed owned shares
        - Price must be valid (0.001 to 1.0)
        - All caps must be enforced BEFORE execution
        - Statistics must be tracked for shutdown summary
        """
        is_sell = action == "SELL"

        # FIX: SELL must NEVER be blocked by missing price context
        # In DRY_RUN/PAPER without data source, use synthetic price (0.50 for binary options)
        # Use fallback synthetic price since we don't have HTTP price context anymore
        pos = self.shadow.get(token_id, market_id, side)
        fallback_price = pos.avg_price if pos.avg_price > 0 else Decimal("0.50")
        price_ctx = PriceContext(bid=fallback_price, ask=fallback_price, mid=fallback_price,
                                 spread=Decimal("0"), source="synthetic")

        # BUY at ask (what we pay), SELL at bid (what we receive)
        price = price_ctx.ask if action == "BUY" else price_ctx.bid
        if price is None:
            price = price_ctx.mid  # Fallback only
        
        # === SAFETY CHECKS ===
        # All orders execute as MARKET orders: BUY at ask, SELL at bid
        safety = self.config.safety

        # CRITICAL: SELL orders are NEVER blocked by safety filters
        # This is the "edge" - we must exit when leader exits, regardless of conditions
        
        # Check spread (SAFETY: skip if spread too wide - losing too much on execution)
        # SELL PRIORITY: Skip this check for SELLs
        if not is_sell and price_ctx.spread is not None:
            # Convert spread to percentage
            mid = price_ctx.mid or ((price_ctx.bid + price_ctx.ask) / 2 if price_ctx.bid and price_ctx.ask else None)
            if mid and mid > 0:
                spread_pct = (price_ctx.spread / mid) * Decimal("100")
                if spread_pct > safety.max_spread_pct:
                    self.logger.warning("Spread too wide, skipping BUY", data={
                        "trigger": trigger,
                        "market_id": market_id,
                        "token_id": token_id,
                        "spread": str(price_ctx.spread),
                        "spread_pct": str(spread_pct),
                        "max_spread_pct": str(safety.max_spread_pct),
                    })
                    self._record_filtered("spread_too_wide", {
                        "market_id": market_id, 
                        "spread_pct": str(spread_pct)
                    })
                    self._stats["safety_triggers"] += 1
                    return
        
        # Check minimum order interval (prevent rapid fire)
        # SELL PRIORITY: Skip this check for SELLs
        if not is_sell and state.last_action_time is not None:
            elapsed = (now - state.last_action_time).total_seconds()
            if elapsed < safety.min_order_interval_sec:
                self._record_filtered("order_too_soon", {
                    "market_id": market_id, 
                    "elapsed_sec": elapsed,
                    "min_interval": safety.min_order_interval_sec
                })
                return
        
        # Check max concurrent positions (for BUY only)
        if action == "BUY":
            current_positions = sum(1 for p in self.shadow.iter_positions().values() if p.shares > 0)
            if current_positions >= safety.max_concurrent_positions:
                self.logger.warning("Max concurrent positions reached", data={
                    "current": current_positions,
                    "max": safety.max_concurrent_positions
                })
                self._record_filtered("max_positions_reached", {"market_id": market_id})
                return
        
        # Check price drift protection (for BUY only)
        if action == "BUY" and safety.price_drift_enabled:
            # Get leader's last entry price for this token
            leader_entry_price = self._get_leader_entry_price(market_id, side)
            if leader_entry_price and price_ctx.ask:
                # Calculate drift: how much higher is current ask vs leader's entry
                drift_pct = ((price_ctx.ask - leader_entry_price) / leader_entry_price) * Decimal("100")
                if drift_pct > safety.max_buy_price_drift_pct:
                    self.logger.warning("Price drift too high for BUY", data={
                        "trigger": trigger,
                        "market_id": market_id,
                        "leader_entry": str(leader_entry_price),
                        "current_ask": str(price_ctx.ask),
                        "drift_pct": str(drift_pct),
                        "max_drift_pct": str(safety.max_buy_price_drift_pct),
                    })
                    self._record_filtered("price_drift_too_high", {
                        "market_id": market_id,
                        "drift_pct": str(drift_pct)
                    })
                    self._stats["safety_triggers"] += 1
                    return
        
        # Check session loss limit
        # SELL PRIORITY: Only block BUYs when loss limit is hit - must still allow SELLs to exit
        if not is_sell and self._check_session_loss_limit():
            self.logger.error("SESSION LOSS LIMIT REACHED - BLOCKING NEW BUYS")
            self._record_filtered("session_loss_limit", {"market_id": market_id})
            self._stats["safety_triggers"] += 1
            return
        
        if price is None or price <= 0:
            if not is_sell:
                self.logger.warning("No price available (BUY blocked)", data={
                    "trigger": trigger,
                    "market_id": market_id,
                    "token_id": token_id,
                    "price_source": price_ctx.source,
                })
                self._record_filtered("no_price", {"market_id": market_id, "token_id": token_id})
                return
            else:
                # FIX: SELL must NEVER be blocked - use fallback price
                pos = self.shadow.get(token_id, market_id, side)
                price = pos.avg_price if pos.avg_price > 0 else Decimal("0.50")
                self.logger.warning("SELL using fallback price (no market price)", data={
                    "trigger": trigger,
                    "market_id": market_id,
                    "token_id": token_id,
                    "fallback_price": str(price),
                })

        # CRITICAL: Validate price is reasonable (between 0.001 and 1.0 for Polymarket)
        MIN_VALID_PRICE = Decimal("0.001")
        MAX_VALID_PRICE = Decimal("1.0")
        
        if price < MIN_VALID_PRICE or price > MAX_VALID_PRICE:
            if is_sell:
                # FIX: SELL must NEVER be blocked - clamp price to valid range
                price = max(MIN_VALID_PRICE, min(MAX_VALID_PRICE, price))
                self.logger.warning("SELL price clamped to valid range", data={
                    "market_id": market_id, "token_id": token_id, "clamped_price": str(price),
                })
            else:
                error_msg = f"Price {price} outside valid range [{MIN_VALID_PRICE}, {MAX_VALID_PRICE}]"
                self.logger.error(error_msg, data={
                    "trigger": trigger,
                    "market_id": market_id,
                    "token_id": token_id,
                    "price": str(price),
                })
                self._stats["invariant_violations"] += 1
                if self.config.mode == ExecutionMode.LIVE:
                    raise ValueError(error_msg)
                self._record_filtered("invalid_price", {"market_id": market_id, "token_id": token_id, "price": str(price)})
                return
        
        if shares is None and dollars is not None:
            shares = (dollars / price).quantize(Decimal("0.01"))
        
        # CRITICAL: For SELL, NEVER exceed owned shares
        if action == "SELL":
            # Get current position (shadow for DRY_RUN/PAPER, or use real for LIVE)
            if self.config.mode == ExecutionMode.LIVE:
                # In LIVE mode, we should check real positions
                # But shadow should be reconciled, so use it as approximation
                pass
            
            pos = self.shadow.get(token_id, market_id, side)
            if pos.shares <= 0:
                self._record_filtered("no_position", {"market_id": market_id, "token_id": token_id})
                return
            if shares is None:
                self._record_filtered("no_shares", {"market_id": market_id, "token_id": token_id})
                return
            
            # STRICT: If trying to sell more than owned, clamp and log warning
            if shares > pos.shares:
                self.logger.warning(
                    f"SELL clamped: requested {shares} but only own {pos.shares}",
                    data={"market_id": market_id, "token_id": token_id}
                )
                shares = pos.shares
            
            if shares <= 0:
                self._record_filtered("zero_shares_after_clamp", {"market_id": market_id, "token_id": token_id})
                return
        
        if shares is None or shares <= 0:
            self._record_filtered("zero_shares", {"market_id": market_id, "token_id": token_id})
            return
        
        # Minimum check - BUMP UP to minimum if below (don't skip!)
        # Polymarket has $1 minimum for market orders, $5 for limit orders
        order_dollars = dollars if dollars is not None else shares * price
        if not is_sell:
            min_check = self.decision_engine.cap_enforcer.check_minimum(
                order_dollars,
                is_market_order=True,
            )
            if not min_check["meets_minimum"]:
                # BUMP UP to minimum instead of skipping
                min_required = Decimal(min_check["min_required"])
                self.logger.info("Bumping order to minimum", data={
                    "trigger": trigger,
                    "market_id": market_id,
                    "token_id": token_id,
                    "original_dollars": str(order_dollars),
                    "bumped_to": str(min_required),
                })
                order_dollars = min_required
                # Recalculate shares based on new amount
                if price and price > 0:
                    shares = (order_dollars / price).quantize(Decimal("0.01"))
        
        # Execute based on mode
        executed = False
        
        if self.config.mode == ExecutionMode.DRY_RUN:
            # DRY_RUN: simulate fills locally using shadow portfolio
            self.shadow.apply_fill(
                token_id=token_id,
                market_id=market_id,
                side=side,
                action=action,
                shares=shares,
                price=price,
                timestamp=now,
                strict=True,  # STRICT: raise if trying to sell more than owned
            )
            executed = True
            
            if action == "BUY":
                self._hourly_budget_used += order_dollars
            state.last_action_time = now
            
        else:
            # LIVE: place REAL order on Polymarket
            order = OrderRequest(
                market_id=market_id,
                side=side,
                order_type=OrderType.MARKET,
                dollars=order_dollars,
                shares=shares if action == "SELL" else None,  # Sells need shares
            )
            order.correlation_id = self.logger.new_correlation_id()
            
            self.logger.log_order_request(order.to_dict())
            result = self.execution.place_order(order)
            self.logger.log_order_response(result.to_dict())
            
            if result.success:
                executed = True
                if action == "BUY" and result.filled_dollars > 0:
                    self._hourly_budget_used += result.filled_dollars
                state.last_action_time = now
                
                # Update shadow to reflect real fill (for tracking)
                if result.filled_shares > 0:
                    self.shadow.apply_fill(
                        token_id=token_id,
                        market_id=market_id,
                        side=side,
                        action=action,
                        shares=result.filled_shares,
                        price=price,
                        timestamp=now,
                    )
            else:
                self.logger.error(
                    f"Order FAILED: {result.error_message}",
                    data={"market_id": market_id, "token_id": token_id}
                )
        
        # Update statistics
        if executed:
            self._stats["trades_placed"] += 1
            if action == "BUY":
                self._stats["buys_executed"] += 1
                self._stats["total_bought_dollars"] += order_dollars
            else:
                self._stats["sells_executed"] += 1
                self._stats["total_sold_dollars"] += order_dollars
        
        # Log the decision
        decision_data = {
            "trigger": trigger,
            "market_id": market_id,
            "token_id": token_id,
            "side": str(side),
            "action": action,
            "dollars": str(order_dollars),
            "shares": str(shares),
            "price": str(price),
            "price_source": price_ctx.source,
            "executed": executed,
        }
        self.logger.info("Decision", data=decision_data)

    def _check_expiry_safety(self, market_id: MarketId, now: datetime) -> bool:
        """Expiry/time-based safety overrides.
        
        If market close time is known and we're near close, reduce/exit exposure.
        These rules CAN DIVERGE from the leader for safety.
        """
        if not self.data_source:
            return False
        
        close_time = self.data_source.get_market_close_time(market_id)
        if not close_time:
            return False
        
        seconds_to_close = (close_time - now).total_seconds()
        if seconds_to_close <= 0:
            return False
        
        # Force close near expiry_force_seconds
        if seconds_to_close <= self.config.copy_trading.expiry_force_seconds:
            result = self._exit_market_all(market_id, now, trigger="EXPIRY_FORCE")
            if result:
                self._stats["expiry_triggers"] += 1
                self._stats["safety_triggers"] += 1
            return result
        
        # Close losing positions within expiry_close_minutes
        if seconds_to_close <= self.config.copy_trading.expiry_close_minutes * 60:
            result = self._exit_market_losing(market_id, now, trigger="EXPIRY_SAFETY")
            if result:
                self._stats["expiry_triggers"] += 1
                self._stats["safety_triggers"] += 1
            return result
        
        return False

    def _exit_market_all(self, market_id: MarketId, now: datetime, trigger: str) -> bool:
        """Sell all positions in a market."""
        sold_any = False
        if not self.data_source:
            return False
        for side in (Side.UP, Side.DOWN):
            token_id = self._get_token_id(market_id, side)
            if not token_id:
                continue
            if not self.shadow.has_position(token_id):
                continue
            pos = self.shadow.get(token_id, market_id, side)
            if pos.shares <= 0:
                continue
            self._execute_shadow_order(
                market_id=market_id,
                token_id=token_id,
                side=side,
                shares=pos.shares,
                action="SELL",
                trigger=trigger,
                now=now,
                state=self._get_market_state(market_id),
            )
            sold_any = True
        return sold_any

    def _exit_market_losing(self, market_id: MarketId, now: datetime, trigger: str) -> bool:
        """Sell losing positions in a market."""
        sold_any = False
        if not self.data_source:
            return False
        for side in (Side.UP, Side.DOWN):
            token_id = self._get_token_id(market_id, side)
            if not token_id:
                continue
            if not self.shadow.has_position(token_id):
                continue
            pos = self.shadow.get(token_id, market_id, side)
            if pos.shares <= 0 or pos.avg_price <= 0:
                continue
            # Use synthetic price context for loss check (no HTTP fallback)
            price_ctx = PriceContext(mid=pos.avg_price, source="synthetic")
            if price_ctx.mid is None:
                continue
            if price_ctx.mid < pos.avg_price:
                self._execute_shadow_order(
                    market_id=market_id,
                    token_id=token_id,
                    side=side,
                    shares=pos.shares,
                    action="SELL",
                    trigger=trigger,
                    now=now,
                    state=self._get_market_state(market_id),
                )
                sold_any = True
        return sold_any

    def _get_token_id(self, market_id: MarketId, side: Side) -> Optional[str]:
        if not self.data_source:
            return None
        return self.data_source.get_token_id(market_id, side)
    
    def _update_state_machines(
        self,
        now: datetime,
        leader_snapshot: LeaderSnapshot,
    ) -> Dict[MarketId, MarketPhase]:
        """Update state machines and return current phases."""
        phases = {}
        
        for market_id, exposure in leader_snapshot.exposures.items():
            sm = self.state_machine.get_or_create(market_id)
            
            # Check if market just opened (new hour)
            hour_start = now.replace(minute=0, second=0, microsecond=0)
            if sm.state.market_open_time is None or sm.state.market_open_time < hour_start:
                sm.on_market_open(hour_start)
                self.logger.info(
                    f"Market {market_id} opened",
                    data={"open_time": hour_start.isoformat()},
                )
            
            # Update state machine
            transition = sm.update(now, exposure)
            if transition:
                self.logger.log_state_change(
                    from_phase=sm.state.phase,
                    to_phase=sm.state.phase,  # Already updated
                    reason=transition,
                )
            
            phases[market_id] = sm.state.phase
        
        return phases
    
    def _execute_decision(self, decision: Decision) -> None:
        """Execute a decision and update trace."""
        # Log the decision
        self.logger.set_context(
            correlation_id=decision.correlation_id,
            market_id=decision.market_id,
            phase=decision.phase,
        )
        
        self.logger.log_decision(
            target=decision.target.to_dict(),
            caps_applied=decision.caps_applied,
            min_checks=decision.min_checks,
            action=decision.action.to_dict() if decision.action else None,
            action_reason=decision.action_reason,
        )
        
        # Execute if there's an action
        if decision.action:
            decision.action.correlation_id = decision.correlation_id
            
            self.logger.log_order_request(decision.action.to_dict())
            
            # Place order via execution adapter
            result = self.execution.place_order(decision.action)
            decision.result = result
            
            self.logger.log_order_response(result.to_dict())
            
            # Track pending orders
            if result.success and result.order_id:
                from ..core import OrderStatus
                if result.status == OrderStatus.PENDING:
                    self._pending_order_ids.append(result.order_id)
                elif result.status == OrderStatus.FILLED:
                    # Update hourly budget for filled orders
                    if result.filled_dollars > 0:
                        self._hourly_budget_used += result.filled_dollars
        
        self.logger.clear_context()
