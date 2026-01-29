"""Live Trade Mirroring strategy runner.

Mirrors leader's filled trades in real-time on hourly crypto UP/DOWN markets.
Primary signal = leader's LIVE filled-trade feed (no baseline, no history replay).

This is a SECOND strategy, selectable via config.strategy = "mirror".
The existing leader-delta strategy is NOT modified.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Dict, Optional, Set, List, Tuple

from ..config import BotConfig
from ..core.types import (
    ExecutionMode,
    Side,
    OrderType,
    OrderStatus,
    OrderRequest,
    Exposure,
    LeaderSnapshot,
    MarketId,
)
from ..core.clock import Clock
from ..core.scaling import ScalingCalculator
from ..data.live_source import LiveDataSource
from ..data.models import PolymarketTrade
from ..data.blockchain_detector import BlockchainDetector, BlockchainTrade
from ..data.ws_price import WebSocketPriceService
from ..execution.base import ExecutionAdapter
from ..logging.logger import BotLogger
from .delta_state import ShadowPortfolio
from .burst_buffer import BurstBuffer, BurstAction
from .mirror_stats import MirrorStats
from .session_recorder import SessionRecorder, create_config_snapshot, LeaderTradeEvent, PriceContext, PositionState, Decision

logger = logging.getLogger(__name__)

# Minimum valid price for Polymarket
MIN_VALID_PRICE = Decimal("0.001")
MAX_VALID_PRICE = Decimal("1.0")
MARKET_ORDER_MIN_DOLLARS = Decimal("1.0")

# ============================================================================
# LATENCY & SLIPPAGE COMPENSATION CONSTANTS
# ============================================================================
# These compensate for the structural disadvantages of copy trading:
# 1. Detection latency (6-10s) means we always buy AFTER the leader moved price
# 2. Market orders PAY the spread; leader may EARN the spread as market maker
# 3. Our order creates additional price impact
#
# The leader's REPORTED price is NOT the price we'll get.
# We need to estimate OUR likely fill price to make informed decisions.

# Estimated spread + slippage for market orders on hourly crypto markets
# Conservative: actual spread can be 2-8% on illiquid moments
ESTIMATED_SPREAD_PCT = Decimal("2.0")  # 2% bid-ask spread
ESTIMATED_SLIPPAGE_PCT = Decimal("1.0")  # 1% additional slippage
TOTAL_EXECUTION_COST_PCT = ESTIMATED_SPREAD_PCT + ESTIMATED_SLIPPAGE_PCT  # 3%

# Price drift per second of latency (empirical estimate for volatile hourly markets)
# Hourly BTC/ETH markets can move 0.2-0.5% per second during active periods
PRICE_DRIFT_PER_SECOND_PCT = Decimal("0.15")

# Maximum acceptable total cost (latency drift + execution cost) before skipping
# If we'd pay more than this vs leader's price, skip the trade
MAX_TOTAL_COST_PCT = Decimal("8.0")  # Skip if total cost > 8%

# Minimum edge required - only copy if we expect some profit after costs
MIN_EXPECTED_EDGE_PCT = Decimal("0.5")  # Need at least 0.5% expected edge


@dataclass
class LeaderPosition:
    """Track leader's position for profit/loss analysis."""
    token_id: str
    market_id: str
    side: str  # "UP" or "DOWN"
    shares: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")  # Total dollars spent
    
    @property
    def avg_price(self) -> Decimal:
        """Leader's average entry price."""
        if self.shares > Decimal("0"):
            return (self.cost_basis / self.shares).quantize(Decimal("0.0001"))
        return Decimal("0")


class LeaderPositionTracker:
    """Tracks leader's positions to determine if their sells are profitable.
    
    This allows us to distinguish between:
    - Leader taking profit (sold above their avg entry) -> we might skip if we'd lose
    - Leader cutting loss (sold below their avg entry) -> we should follow
    """
    
    def __init__(self):
        self._positions: Dict[str, LeaderPosition] = {}  # key = token_id
    
    def _key(self, token_id: str) -> str:
        return token_id
    
    def record_buy(
        self, 
        token_id: str, 
        market_id: str, 
        side: str, 
        shares: Decimal, 
        price: Decimal
    ) -> None:
        """Record a leader BUY to update their avg entry price."""
        key = self._key(token_id)
        if key not in self._positions:
            self._positions[key] = LeaderPosition(
                token_id=token_id,
                market_id=market_id,
                side=side,
            )
        pos = self._positions[key]
        pos.shares += shares
        pos.cost_basis += shares * price
    
    def record_sell(
        self, 
        token_id: str, 
        shares: Decimal, 
        price: Decimal
    ) -> Tuple[bool, Decimal, Decimal]:
        """Record a leader SELL and return profit/loss info.
        
        Returns:
            (is_profitable, leader_avg_entry, sell_price)
            - is_profitable: True if leader sold above their avg entry
            - leader_avg_entry: Leader's average entry price for this position
            - sell_price: The price leader sold at
        """
        key = self._key(token_id)
        pos = self._positions.get(key)
        
        if pos is None or pos.shares <= Decimal("0"):
            # No position tracked - assume leader profited (conservative)
            return True, Decimal("0"), price
        
        leader_avg_entry = pos.avg_price
        is_profitable = price >= leader_avg_entry
        
        # Update position (reduce shares proportionally)
        sell_shares = min(shares, pos.shares)
        if pos.shares > Decimal("0") and sell_shares > Decimal("0"):
            ratio = sell_shares / pos.shares
            pos.cost_basis -= pos.cost_basis * ratio
            pos.shares -= sell_shares
        
        # Clean up if fully closed
        if pos.shares < Decimal("0.001"):
            pos.shares = Decimal("0")
            pos.cost_basis = Decimal("0")
        
        return is_profitable, leader_avg_entry, price
    
    def get_avg_entry(self, token_id: str) -> Optional[Decimal]:
        """Get leader's average entry price for a position."""
        key = self._key(token_id)
        pos = self._positions.get(key)
        if pos and pos.shares > Decimal("0"):
            return pos.avg_price
        return None


class MirrorRunner:
    """Live trade mirroring strategy.

    Polls leader trades at ~1s, dedupes, aggregates bursts,
    and mirrors BUY/SELL with scaling, caps, and ratio awareness.
    """

    def __init__(
        self,
        config: BotConfig,
        adapter: ExecutionAdapter,
        clock: Clock,
        duration_minutes: Optional[int] = None,
        record_session: bool = False,
    ):
        self.config = config
        self.adapter = adapter
        self.clock = clock
        self.duration_minutes = duration_minutes
        self.record_session = record_session

        # Mirror-specific config
        self.mcfg = config.mirror_strategy

        # Data source
        self.data_source = LiveDataSource(
            leader_address=config.leader.address,
        )
        if hasattr(config, "trader") and config.trader.address:
            self.data_source.my_address = config.trader.address

        # Give adapter access to data source for token_id lookup
        if hasattr(self.adapter, "set_data_source"):
            self.adapter.set_data_source(self.data_source)

        # Scaling
        self.scaling_calc = ScalingCalculator(config.scaling)

        # Shadow portfolio
        self.shadow = ShadowPortfolio()

        # Logger — file-only for structured JSON; terminal uses print()
        log_file = Path(config.logging.file_path).parent / "mirror.jsonl"
        self.bot_logger = BotLogger(
            mode=config.mode,
            name="mirror",
            level=config.logging.level,
            log_file=log_file,
            console=False,
            max_bytes=config.logging.log_max_mb * 1024 * 1024 if config.logging.log_max_mb else None,
            backup_count=config.logging.log_backup_count,
        )

        # Burst buffer - accumulates partial fills until $1 minimum order size
        self.burst_buffer = BurstBuffer()  # Uses MIN_ORDER_DOLLARS = $1

        # Blockchain detector (fast leader trade detection via Polygon RPC)
        self.blockchain = BlockchainDetector(
            leader_address=config.leader.address,
        )

        # WebSocket price service (real-time bid/ask)
        self.ws_prices = WebSocketPriceService()

        # Stats
        self.stats = MirrorStats()
        
        # Leader position tracker - for profit/loss analysis on sells
        self.leader_tracker = LeaderPositionTracker()
        
        # Session recorder (optional, enabled with --record flag)
        self.session_recorder: Optional[SessionRecorder] = None
        if record_session:
            sessions_dir = config.data_dir / "sessions"
            config_snapshot = create_config_snapshot(config)
            self.session_recorder = SessionRecorder(
                output_dir=sessions_dir,
                config_snapshot=config_snapshot,
            )

        # State
        self._running = False
        self._seen_trade_hashes: Set[str] = set()
        self._last_trade_timestamp: int = 0
        self._hourly_budget_used = Decimal("0")
        self._current_hour: Optional[datetime] = None
        self._hourly_updown_markets: Set[str] = set()
        self._last_snapshot_time: float = 0.0
        self._leader_ratios: Dict[str, Decimal] = {}  # market_id -> pct of total
        self._leader_updown_ratios: Dict[str, Dict[str, Decimal]] = {}  # market_id -> {UP: pct, DOWN: pct}
        self._leader_capital: Optional[Decimal] = None
        self._scale_ratio: Decimal = Decimal("0")
        self._cycle_count: int = 0

        # Market close times cache
        self._market_close_times: Dict[str, Optional[datetime]] = {}

    def run(self) -> None:
        """Main loop: poll leader trades, mirror with burst aggregation."""
        self._running = True
        start_time = time.time()

        mode_str = str(self.config.mode).upper()
        print()
        print("=" * 60)
        print(f"  MIRROR STRATEGY  [{mode_str}]")
        print("=" * 60)
        print(f"  Leader:    {self.config.leader.address}")
        print(f"  Capital:   ${self.config.scaling.our_capital}  (reserve {self.mcfg.cash_reserve_pct}%)")
        print(f"  Limits:    {self.mcfg.per_market_cap_pct}% per market, staleness {self.mcfg.staleness_window_sec}s")
        print(f"  Detection: Blockchain (Polygon RPC) + Data API")
        print(f"  Prices:    WebSocket + HTTP fallback")
        print("=" * 60)

        # Initialize: take baseline snapshot to discover markets
        try:
            self._initialize()
        except Exception as e:
            print(f"ERROR: Initialization failed: {e}")
            return

        poll_interval = self.config.leader.poll_interval_sec

        try:
            while self._running:
                cycle_start = time.time()
                self._cycle_count += 1

                # Duration check
                if self.duration_minutes:
                    elapsed_min = (time.time() - start_time) / 60
                    if elapsed_min >= self.duration_minutes:
                        print(f"\nDuration limit reached ({self.duration_minutes}m). Stopping.")
                        break

                try:
                    self._run_cycle()
                except Exception as e:
                    self.bot_logger.error(f"Cycle error: {e}")
                    logger.exception("Mirror cycle error")

                # Status line with portfolio view
                if self.stats.should_print_status():
                    portfolio_summary = self._build_portfolio_summary()
                    self.stats.print_status_line(portfolio_summary)

                # Sleep to maintain cadence
                elapsed = time.time() - cycle_start
                sleep_time = max(0, poll_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            self._shutdown()

    def _initialize(self) -> None:
        """Initialize: discover markets, mark existing trades as seen."""
        print("\nInitializing...")

        # Take snapshot to discover markets and get leader capital
        snapshot = self.data_source.build_leader_snapshot(self.clock.now())
        self._leader_capital = self.scaling_calc.get_leader_capital(snapshot)
        self._scale_ratio = self.scaling_calc.compute_scale_ratio(self._leader_capital)
        
        # Update burst buffer with current scale ratio
        self.burst_buffer.set_scale_ratio(self._scale_ratio)

        print(f"  Leader capital: ${self._leader_capital}  |  Scale ratio: {self._scale_ratio:.4f}")

        # Discover active hourly up/down markets
        for market_id in snapshot.exposures:
            if self.data_source.is_hourly_updown_market(market_id):
                self._hourly_updown_markets.add(market_id)

        print(f"  Active hourly markets: {len(self._hourly_updown_markets)}")

        # Mark existing trades as seen (don't replay history)
        existing_trades = self.data_source.fetch_trades(
            self.config.leader.address, limit=50
        )
        for trade in existing_trades:
            h = self._trade_hash(trade)
            self._seen_trade_hashes.add(h)
            self.data_source._discover_market_from_trade(trade)
            if trade.timestamp > self._last_trade_timestamp:
                self._last_trade_timestamp = trade.timestamp

        print(f"  Existing trades marked: {len(self._seen_trade_hashes)}")

        # Update leader ratios from snapshot
        self._update_leader_ratios(snapshot)

        # Set initial hour
        self._current_hour = self.clock.now().replace(minute=0, second=0, microsecond=0)

        # Initialize blockchain detector
        try:
            start_block = self.blockchain.initialize()
            for trade in existing_trades:
                if trade.transaction_hash:
                    self.blockchain.mark_seen(trade.transaction_hash)
            print(f"  Blockchain: starting at block {start_block}")
        except Exception as e:
            print(f"  Blockchain: FAILED ({e}) - Data API only")

        # Start WebSocket price service
        try:
            self.ws_prices.start()
            token_ids = [m.token_id for m in self.data_source._token_to_market.values()]
            if token_ids:
                self.ws_prices.subscribe_many(token_ids)
            print(f"  WebSocket: {len(token_ids)} price feeds")
        except Exception as e:
            print(f"  WebSocket: FAILED ({e}) - HTTP only")

        self.stats.markets_tracked = len(self._hourly_updown_markets)
        
        # Start session recorder if enabled
        if self.session_recorder:
            self.session_recorder.start()
        
        print("\n  Ready. Listening for leader trades...\n")

    def _run_cycle(self) -> None:
        """Single cycle: poll trades, process bursts, periodic snapshot."""
        now = self.clock.now()

        # Check hour boundary
        self._check_hour_boundary(now)

        # 1a. Primary: blockchain detection (fast, ~6-10s latency)
        self._poll_blockchain_trades(now)

        # 1b. Fallback: Data API (slower, every 10 cycles for coverage)
        if self._cycle_count % 10 == 0:
            self._poll_leader_trades(now)

        # 2. Process accumulated micro-trades that have reached threshold
        # (This is now handled automatically by the burst buffer's add_trade)
        # No need for separate expiry processing since we use persistent accumulators

        # 3. Periodic snapshot for market discovery + ratio estimation
        if time.time() - self._last_snapshot_time >= self.mcfg.snapshot_interval_sec:
            self._periodic_snapshot(now)

        # 4. Check end-of-hour flattening
        self._check_expiry_flattening(now)

    def _poll_blockchain_trades(self, now: datetime) -> int:
        """Poll blockchain for leader trades (primary, fast detection)."""
        self.stats.blockchain_polls += 1

        try:
            raw_trades = self.blockchain.poll()
        except Exception as e:
            self.bot_logger.error(f"Blockchain poll error: {e}")
            return 0

        if not raw_trades:
            return 0

        self.stats.blockchain_trades_detected += len(raw_trades)
        token_lookup = self.data_source._token_to_market

        now_unix = int(now.timestamp())
        new_count = 0

        for bt in raw_trades:
            # Enrich with market info from our cache
            if not self.blockchain.enrich_trade(bt, token_lookup):
                # Token not in our discovered markets cache - skip for now
                self.stats.record_skip("unknown_token")
                self.bot_logger.debug(
                    f"Blockchain trade unknown token: {bt.tx_hash[:16]}..."
                )
                continue

            self.stats.blockchain_trades_enriched += 1

            # Build a dedup hash: tx_hash + log_index
            h = f"{bt.tx_hash}_{bt.log_index}"
            if h in self._seen_trade_hashes:
                self.stats.leader_events_deduped += 1
                continue
            self._seen_trade_hashes.add(h)
            # Also add bare tx_hash to catch Data API duplicates
            if bt.tx_hash not in self._seen_trade_hashes:
                self._seen_trade_hashes.add(bt.tx_hash)

            self.stats.leader_events_seen += 1

            # Staleness check
            age = now_unix - bt.block_timestamp
            if age > self.mcfg.staleness_window_sec:
                self.stats.leader_events_stale += 1
                continue

            # Market filter: only hourly up/down
            market_id = bt.condition_id
            if not self.data_source.is_hourly_updown_market(market_id):
                self.stats.record_skip("market_filter")
                continue

            # Track market
            if market_id not in self._hourly_updown_markets:
                self._hourly_updown_markets.add(market_id)
                self.stats.markets_tracked = len(self._hourly_updown_markets)

            # Subscribe to WebSocket price for this token
            self.ws_prices.subscribe(bt.token_id)

            side_str = bt.side
            action = bt.action
            token_id = bt.token_id
            leader_dollars = bt.dollar_value

            latency = now_unix - bt.block_timestamp
            self.bot_logger.info(
                f"BLOCKCHAIN {action} detected",
                data={
                    "market_id": market_id[:16],
                    "side": side_str,
                    "leader_dollars": str(leader_dollars),
                    "shares": str(bt.shares),
                    "price": str(bt.price),
                    "latency_sec": latency,
                    "tx": bt.tx_hash[:20],
                },
            )

            label = self._get_market_label(market_id, side_str)
            
            # Use burst buffer for smart noise filtering:
            # - Large trades (>=$1): Execute immediately
            # - Micro trades (<$1): Accumulate until they reach $1
            buffered_action = self.burst_buffer.add_trade(
                market_id=market_id,
                side=side_str,
                action=action,
                token_id=token_id,
                leader_dollars=leader_dollars,
                leader_price=bt.price,
            )
            
            if buffered_action is not None:
                # Trade ready to execute (either large or accumulated to threshold)
                if buffered_action.is_immediate:
                    print(
                        f"  [{self._elapsed_str()}] LEADER {action}  {label:<10s}  "
                        f"${leader_dollars:.2f}  @{bt.price:.4f}  "
                        f"({latency}s ago)"
                    )
                else:
                    # Accumulated micro-trades reached threshold
                    print(
                        f"  [{self._elapsed_str()}] ACCUM {action}  {label:<10s}  "
                        f"${buffered_action.leader_dollars:.2f} (accumulated micro-trades)"
                    )
                
                self._execute_burst_action(buffered_action, now, is_followup=not buffered_action.is_immediate)
            else:
                # Micro-trade accumulated, not yet at threshold
                leader_acc, our_acc = self.burst_buffer.get_accumulated(market_id, side_str)
                self.bot_logger.debug(
                    f"MICRO-TRADE accumulated: ${leader_dollars:.2f} -> total ${leader_acc:.2f} (ours: ${our_acc:.2f})",
                    data={"market_id": market_id[:16], "side": side_str}
                )

            new_count += 1

        if new_count > 0:
            self.bot_logger.info(
                f"BLOCKCHAIN POLL: {len(raw_trades)} events, {new_count} new"
            )

        return new_count

    def _poll_leader_trades(self, now: datetime) -> int:
        """Poll leader's recent trades via Data API (fallback)."""
        try:
            # CRITICAL: Do NOT use since_timestamp filter.
            # API returns trades unordered, causing monotonic cursor to reject valid trades.
            # Rely solely on hash-based deduplication.
            trades = self.data_source.fetch_trades(
                self.config.leader.address,
                limit=50,
            )
        except Exception as e:
            self.bot_logger.error(f"Error fetching leader trades: {e}")
            return 0

        if not trades:
            return 0

        # Collect ages for debug logging
        now_unix = int(now.timestamp())
        ages = []
        new_count = 0
        dedup_count = 0
        stale_count = 0

        # Process oldest first
        for trade in reversed(trades):
            # Dedupe
            h = self._trade_hash(trade)
            if h in self._seen_trade_hashes:
                dedup_count += 1
                self.stats.leader_events_deduped += 1
                continue
            self._seen_trade_hashes.add(h)
            self.stats.leader_events_seen += 1

            # Update last timestamp
            if trade.timestamp > self._last_trade_timestamp:
                self._last_trade_timestamp = trade.timestamp

            # Staleness check
            age = now_unix - trade.timestamp
            ages.append(age)
            if age > self.mcfg.staleness_window_sec:
                stale_count += 1
                self.stats.leader_events_stale += 1
                continue

            # IMPORTANT: Discover market from trade BEFORE checking if hourly
            # This populates _discovered_markets which is_hourly_updown_market needs
            self.data_source._discover_market_from_trade(trade)

            # Market filter: only hourly up/down
            market_id = trade.condition_id
            if not self.data_source.is_hourly_updown_market(market_id):
                self.stats.record_skip("market_filter")
                continue

            # Track market
            if market_id not in self._hourly_updown_markets:
                self._hourly_updown_markets.add(market_id)
                self.stats.markets_tracked = len(self._hourly_updown_markets)

            # Determine side
            side_str = "UP" if trade.outcome.upper() in ("YES", "UP") else "DOWN"
            action = trade.side.upper()  # "BUY" or "SELL"
            token_id = trade.asset

            if not token_id:
                side_enum = Side.UP if side_str == "UP" else Side.DOWN
                token_id = self.data_source.get_token_id(market_id, side_enum)
            if not token_id:
                self.stats.record_skip("no_token")
                continue

            # Subscribe to WebSocket price for this token
            self.ws_prices.subscribe(token_id)

            leader_dollars = trade.dollar_value
            label = self._get_market_label(market_id, side_str)
            
            # Track leader's position for profit/loss analysis
            # We need this to know if leader is taking profit vs cutting loss
            if trade.price and trade.price > Decimal("0"):
                leader_shares = leader_dollars / trade.price
                if action == "BUY":
                    self.leader_tracker.record_buy(
                        token_id=token_id,
                        market_id=market_id,
                        side=side_str,
                        shares=leader_shares,
                        price=trade.price,
                    )

            # Use burst buffer for smart noise filtering
            buffered_action = self.burst_buffer.add_trade(
                market_id=market_id,
                side=side_str,
                action=action,
                token_id=token_id,
                leader_dollars=leader_dollars,
                leader_price=trade.price,
            )
            
            if buffered_action is not None:
                if buffered_action.is_immediate:
                    self.bot_logger.debug(f"DATA API: Large trade ${leader_dollars:.2f} - executing")
                else:
                    self.bot_logger.info(f"DATA API: Accumulated micro-trades -> ${buffered_action.leader_dollars:.2f}")
                    print(
                        f"  [{self._elapsed_str()}] ACCUM {action}  {label:<10s}  "
                        f"${buffered_action.leader_dollars:.2f} (accumulated)"
                    )
                
                self._execute_burst_action(buffered_action, now, is_followup=not buffered_action.is_immediate)

            new_count += 1

        # Per-poll debug line (show when there's any new activity)
        if ages:
            min_age = min(ages)
            max_age = max(ages)
            median_age = sorted(ages)[len(ages) // 2]
        else:
            min_age = max_age = median_age = 0

        # Log at INFO level if there are new trades or stale trades, DEBUG otherwise
        poll_msg = (
            f"POLL raw={len(trades)} new={new_count} dup={dedup_count} stale={stale_count} "
            f"age_sec(min={min_age} med={median_age} max={max_age})"
        )
        if new_count > 0 or stale_count > 0:
            self.bot_logger.info(poll_msg)
        else:
            self.bot_logger.debug(poll_msg)

        return new_count

    def _execute_burst_action(
        self, action: BurstAction, now: datetime, is_followup: bool
    ) -> bool:
        """Execute a burst action (immediate or aggregated follow-up).

        Returns True if the action was executed, False if skipped.
        """
        side = Side.UP if action.side == "UP" else Side.DOWN

        if is_followup:
            self.stats.burst_followups += 1

        # Scale leader dollars to our size
        our_dollars = action.leader_dollars * self._scale_ratio
        
        # ===== SIZE-PROPORTIONAL BOOST =====
        # When leader makes larger trades, they're more confident.
        # Give a small boost to larger trades (within caps).
        # - Trades < $2: No boost (could be noise)
        # - Trades $2-$5: 10% boost
        # - Trades $5-$10: 20% boost  
        # - Trades > $10: 30% boost
        # This helps us follow strong conviction trades more aggressively.
        if action.leader_dollars >= Decimal("10"):
            size_boost = Decimal("1.30")
        elif action.leader_dollars >= Decimal("5"):
            size_boost = Decimal("1.20")
        elif action.leader_dollars >= Decimal("2"):
            size_boost = Decimal("1.10")
        else:
            size_boost = Decimal("1.0")
        
        our_dollars = our_dollars * size_boost
        
        if size_boost > Decimal("1.0"):
            self.bot_logger.debug(
                f"Size boost {size_boost}x for ${action.leader_dollars:.2f} leader trade"
            )

        executed = False
        if action.action == "BUY":
            executed = self._execute_buy(
                market_id=action.market_id,
                token_id=action.token_id,
                side=side,
                side_str=action.side,
                scaled_dollars=our_dollars,
                leader_dollars=action.leader_dollars,
                now=now,
                is_followup=is_followup,
                leader_price=action.leader_price,  # Pass leader's price for cost analysis
            )
        elif action.action == "SELL":
            executed = self._execute_sell(
                market_id=action.market_id,
                token_id=action.token_id,
                side=side,
                side_str=action.side,
                scaled_dollars=our_dollars,
                leader_dollars=action.leader_dollars,
                now=now,
                is_followup=is_followup,
                leader_sell_price=action.leader_price,  # Pass for profit/loss analysis
            )

        if executed:
            # Record executed amount in LEADER dollars
            self.burst_buffer.record_executed(action.market_id, action.side, action.leader_dollars)

        return executed

    def _record_trade_decision(
        self,
        market_id: str,
        token_id: str,
        side_str: str,
        action_type: str,  # "BUY" or "SELL"
        leader_dollars: Decimal,
        leader_price: Optional[Decimal],
        our_decision: str,  # "BUY", "SELL", or "SKIP"
        skip_reason: Optional[str] = None,
        our_dollars: Optional[Decimal] = None,
        our_shares: Optional[Decimal] = None,
        price_drift_pct: Optional[Decimal] = None,
        total_cost_pct: Optional[Decimal] = None,
        our_would_profit: Optional[bool] = None,
        our_pnl_pct: Optional[Decimal] = None,
        leader_profited: Optional[bool] = None,
        leader_pnl_pct: Optional[Decimal] = None,
        source: str = "blockchain",
        latency_sec: int = 0,
        tx_hash: Optional[str] = None,
    ) -> None:
        """Record a trade decision to the session recorder."""
        if not self.session_recorder or not self.session_recorder.is_active:
            return
        
        now = datetime.now(timezone.utc)
        
        # Build leader trade event
        leader_shares = (leader_dollars / leader_price) if leader_price and leader_price > Decimal("0") else Decimal("0")
        leader_trade = LeaderTradeEvent(
            timestamp=now.isoformat(),
            market_id=market_id,
            token_id=token_id,
            side=side_str,
            action=action_type,
            leader_dollars=str(leader_dollars),
            leader_price=str(leader_price) if leader_price else "0",
            leader_shares=str(leader_shares),
            source=source,
            latency_sec=latency_sec,
            tx_hash=tx_hash,
        )
        
        # Build price context
        bid = self._get_price(token_id, is_sell=True)
        ask = self._get_price(token_id, is_sell=False)
        spread_pct = None
        if bid and ask and bid > Decimal("0"):
            spread_pct = ((ask - bid) / bid * Decimal("100")).quantize(Decimal("0.01"))
        
        price_ctx = PriceContext(
            token_id=token_id,
            bid=str(bid) if bid else None,
            ask=str(ask) if ask else None,
            spread_pct=str(spread_pct) if spread_pct else None,
            price_source="websocket" if self.ws_prices else "http",
        )
        
        # Build position state (our current position before this trade)
        pos = self.shadow.get(token_id, market_id, Side.UP if side_str == "UP" else Side.DOWN)
        position_state = None
        if pos and pos.shares > Decimal("0"):
            current_price = bid if action_type == "SELL" else ask
            if current_price is not None:
                current_value = pos.shares * current_price
            else:
                current_value = Decimal("0")
            
            # ShadowPosition uses 'notional' as cost basis
            cost_basis = pos.notional if pos.notional is not None else Decimal("0")
            if current_value and cost_basis > Decimal("0"):
                pnl = current_value - cost_basis
                pnl_pct = (pnl / cost_basis * 100)
            else:
                pnl = Decimal("0")
                pnl_pct = Decimal("0")
            
            position_state = PositionState(
                token_id=token_id,
                market_id=market_id,
                side=side_str,
                shares_held=str(pos.shares),
                avg_entry_price=str(pos.avg_price) if pos.avg_price else "0",
                cost_basis=str(cost_basis),
                current_value=str(current_value),
                unrealized_pnl=str(pnl),
                unrealized_pnl_pct=str(pnl_pct.quantize(Decimal("0.01"))),
            )
        
        # Build decision
        decision = Decision(
            action=our_decision,
            skip_reason=skip_reason,
            our_dollars=str(our_dollars) if our_dollars else None,
            our_shares=str(our_shares) if our_shares else None,
            scale_ratio=str(self._scale_ratio),
            price_drift_pct=str(price_drift_pct) if price_drift_pct is not None else None,
            estimated_total_cost_pct=str(total_cost_pct) if total_cost_pct is not None else None,
            our_would_profit=our_would_profit,
            our_pnl_pct=str(our_pnl_pct) if our_pnl_pct is not None else None,
            leader_profited=leader_profited,
            leader_pnl_pct=str(leader_pnl_pct) if leader_pnl_pct is not None else None,
        )
        
        # Record
        self.session_recorder.record_trade(
            leader_trade=leader_trade,
            price_context=price_ctx,
            position_state=position_state,
            decision=decision,
        )

    def _execute_buy(
        self,
        market_id: str,
        token_id: str,
        side: Side,
        side_str: str,
        scaled_dollars: Decimal,
        leader_dollars: Decimal,
        now: datetime,
        is_followup: bool,
        leader_price: Optional[Decimal] = None,
        latency_seconds: int = 0,
    ) -> bool:
        """Execute a BUY order with caps, reserve, ratio enforcement.

        Returns True if order was placed, False if skipped.
        
        NEW: Now includes latency-aware cost estimation to avoid adverse selection.
        """
        dollars = scaled_dollars
        skip_reason: Optional[str] = None
        price_drift_pct: Optional[Decimal] = None
        total_cost_pct: Optional[Decimal] = None

        # =========================================================================
        # LATENCY & COST-AWARE EXECUTION CHECK (NEW)
        # =========================================================================
        # This is the KEY fix for underperformance: estimate our REAL costs
        # before executing, and skip trades where costs eat all the edge.
        
        current_ask = self._get_price(token_id, is_sell=False)
        
        if current_ask and leader_price and leader_price > Decimal("0"):
            # Calculate how much worse our price is vs leader's entry
            price_drift_pct = ((current_ask - leader_price) / leader_price) * Decimal("100")
            
            # Add estimated execution costs on top
            total_cost_pct = price_drift_pct + TOTAL_EXECUTION_COST_PCT
            
            if total_cost_pct > MAX_TOTAL_COST_PCT:
                skip_reason = "cost_too_high"
                self.stats.record_skip(skip_reason)
                self.bot_logger.info(
                    f"SKIP BUY: cost too high",
                    data={
                        "market_id": market_id[:16],
                        "leader_price": str(leader_price),
                        "current_ask": str(current_ask),
                        "price_drift_pct": str(price_drift_pct),
                        "total_cost_pct": str(total_cost_pct),
                        "max_allowed_pct": str(MAX_TOTAL_COST_PCT),
                    }
                )
                # Record the skipped trade
                self._record_trade_decision(
                    market_id=market_id,
                    token_id=token_id,
                    side_str=side_str,
                    action_type="BUY",
                    leader_dollars=leader_dollars,
                    leader_price=leader_price,
                    our_decision="SKIP",
                    skip_reason=skip_reason,
                    our_dollars=scaled_dollars,
                    price_drift_pct=price_drift_pct,
                    total_cost_pct=total_cost_pct,
                )
                return False
            
            # Log the cost analysis
            if total_cost_pct > Decimal("2.0"):
                self.bot_logger.debug(
                    f"BUY cost estimate: {total_cost_pct:.2f}% (drift={price_drift_pct:.2f}% + exec={TOTAL_EXECUTION_COST_PCT}%)"
                )
        
        # =========================================================================

        # --- Reserve check ---
        deployable = self.config.scaling.our_capital * (
            Decimal("1") - self.mcfg.cash_reserve_pct / Decimal("100")
        )
        currently_deployed = self._get_total_deployed()
        available = deployable - currently_deployed
        if available <= Decimal("0"):
            skip_reason = "reserve"
            self.stats.record_skip(skip_reason)
            self.bot_logger.debug("Reserve exhausted", data={
                "market_id": market_id,
                "deployable": str(deployable),
                "currently_deployed": str(currently_deployed),
                "available": str(available),
            })
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False
        dollars = min(dollars, available)

        # --- Hourly budget check ---
        budget_remaining = self.config.scaling.hourly_budget - self._hourly_budget_used
        if budget_remaining <= Decimal("0"):
            skip_reason = "budget"
            self.stats.record_skip(skip_reason)
            self.bot_logger.debug("Hourly budget exhausted", data={
                "market_id": market_id,
                "hourly_budget": str(self.config.scaling.hourly_budget),
                "budget_used": str(self._hourly_budget_used),
                "budget_remaining": str(budget_remaining),
            })
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False
        dollars = min(dollars, budget_remaining)

        # --- Per-market cap ---
        market_exposure = self._get_market_exposure(market_id)
        market_cap = self.config.scaling.our_capital * self.mcfg.per_market_cap_pct / Decimal("100")
        market_room = market_cap - market_exposure
        if market_room <= Decimal("0"):
            skip_reason = "market_cap"
            self.stats.record_skip("cap")
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False
        dollars = min(dollars, market_room)

        # --- Per-side cap (reuse existing config) ---
        current_side_dollars = self._get_side_exposure(market_id, side)
        side_cap = self.config.caps.per_side
        side_room = side_cap - current_side_dollars
        if side_room <= Decimal("0"):
            skip_reason = "side_cap"
            self.stats.record_skip("cap")
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False
        dollars = min(dollars, side_room)

        # --- Global exposure cap ---
        global_used = self._get_total_deployed()
        global_cap = self.config.caps.global_capital
        global_room = global_cap - global_used
        if global_room <= Decimal("0"):
            skip_reason = "global_cap"
            self.stats.record_skip("cap")
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False
        dollars = min(dollars, global_room)

        # --- Ratio enforcement (SOFT, dollar-weighted) ---
        # Only constrain AFTER 80% of capital is deployed.
        # Before that, let the bot freely build positions without ratio blocking.
        # NOTE: Raised from 50% to 80% to be less restrictive early on.
        ratio_threshold = self.config.scaling.our_capital * Decimal("0.8")
        if currently_deployed > ratio_threshold and self._leader_ratios:
            dollars = self._apply_ratio_constraint(market_id, side_str, dollars)
            if dollars <= Decimal("0"):
                skip_reason = "ratio"
                self.stats.record_skip(skip_reason)
                self._record_trade_decision(
                    market_id=market_id, token_id=token_id, side_str=side_str,
                    action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                    our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                    price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
                )
                return False

        # --- Minimum bump ---
        if Decimal("0") < dollars < MARKET_ORDER_MIN_DOLLARS:
            # Can we bump to $1?
            if (MARKET_ORDER_MIN_DOLLARS <= available
                    and MARKET_ORDER_MIN_DOLLARS <= market_room
                    and MARKET_ORDER_MIN_DOLLARS <= side_room
                    and MARKET_ORDER_MIN_DOLLARS <= global_room
                    and MARKET_ORDER_MIN_DOLLARS <= budget_remaining):
                dollars = MARKET_ORDER_MIN_DOLLARS
                self.bot_logger.debug("Bumped buy to $1 minimum", data={
                    "market_id": market_id, "side": side_str,
                })
            else:
                skip_reason = "min_order"
                self.stats.record_skip(skip_reason)
                self._record_trade_decision(
                    market_id=market_id, token_id=token_id, side_str=side_str,
                    action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                    our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                    price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
                )
                return False

        if dollars <= Decimal("0"):
            skip_reason = "cap"
            self.stats.record_skip(skip_reason)
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False

        # --- Expiry check ---
        if self._is_near_expiry(market_id, now, self.mcfg.expiry_close_sec):
            skip_reason = "expiry"
            self.stats.record_skip(skip_reason)
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="SKIP", skip_reason=skip_reason, our_dollars=scaled_dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )
            return False

        # --- Place order ---
        success = self._place_order(
            market_id=market_id,
            token_id=token_id,
            side=side,
            action="BUY",
            now=now,
            is_followup=is_followup,
            dollars=dollars,
            leader_dollars=leader_dollars,
        )
        
        # Record executed trade
        if success:
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="BUY", leader_dollars=leader_dollars, leader_price=leader_price,
                our_decision="BUY", our_dollars=dollars,
                price_drift_pct=price_drift_pct, total_cost_pct=total_cost_pct,
            )

        return success

    def _execute_sell(
        self,
        market_id: str,
        token_id: str,
        side: Side,
        side_str: str,
        scaled_dollars: Decimal,
        leader_dollars: Decimal,
        now: datetime,
        is_followup: bool,
        leader_sell_price: Optional[Decimal] = None,
    ) -> bool:
        """Execute a SELL order with loss protection.
        
        LOSS PROTECTION LOGIC:
        - Block sells where WE would lose money, UNLESS the leader also lost money.
        - If leader took profit but we'd take a loss -> SKIP (don't lock in our loss)
        - If leader took a loss -> FOLLOW (they're cutting risk, we should too)

        Returns True if order was placed, False if skipped.
        """
        # Tracking variables for recording
        skip_reason: Optional[str] = None
        our_would_profit: Optional[bool] = None
        our_pnl_pct: Optional[Decimal] = None
        leader_profited: Optional[bool] = None
        leader_pnl_pct: Optional[Decimal] = None
        
        pos = self.shadow.get(token_id, market_id, side)
        if pos.shares <= Decimal("0"):
            skip_reason = "no_position"
            self.stats.record_skip(skip_reason)
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="SELL", leader_dollars=leader_dollars, leader_price=leader_sell_price,
                our_decision="SKIP", skip_reason=skip_reason,
            )
            return False

        # Calculate shares to sell from scaled dollars
        current_bid = self._get_price(token_id, is_sell=True)
        if not current_bid or current_bid <= Decimal("0"):
            # Cannot determine price - skip this sell
            skip_reason = "no_price"
            self.stats.record_skip(skip_reason)
            self.bot_logger.warning("SELL skipped: no price available", data={
                "market_id": market_id, "side": side_str, "token_id": token_id[:16],
            })
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="SELL", leader_dollars=leader_dollars, leader_price=leader_sell_price,
                our_decision="SKIP", skip_reason=skip_reason,
            )
            return False
        
        # =====================================================================
        # LOSS PROTECTION: Block sells that lock in OUR loss unless leader also lost
        # Only active if block_loss_sells_if_leader_profit is enabled in config
        # =====================================================================
        if self.config.safety.block_loss_sells_if_leader_profit:
            our_avg_entry = pos.avg_price
            
            if our_avg_entry and our_avg_entry > Decimal("0"):
                # Calculate if WE would profit or lose
                our_would_profit = current_bid >= our_avg_entry
                our_pnl_pct = ((current_bid - our_avg_entry) / our_avg_entry * Decimal("100")).quantize(Decimal("0.1"))
                
                if not our_would_profit:
                    # We would take a loss - check if leader also took a loss
                    if leader_sell_price and leader_sell_price > Decimal("0"):
                        # Get leader's avg entry for this position
                        leader_avg_entry = self.leader_tracker.get_avg_entry(token_id)
                        
                        if leader_avg_entry and leader_avg_entry > Decimal("0"):
                            leader_profited = leader_sell_price >= leader_avg_entry
                            leader_pnl_pct = ((leader_sell_price - leader_avg_entry) / leader_avg_entry * Decimal("100")).quantize(Decimal("0.1"))
                            
                            if leader_profited:
                                # BLOCK: Leader took profit, but we'd take a loss
                                # Don't follow this sell - our entry was worse
                                skip_reason = "leader_profit_our_loss"
                                self.stats.record_skip(skip_reason)
                                self.bot_logger.warning(
                                    "SELL blocked: leader profited but we'd lose",
                                    data={
                                        "market_id": market_id,
                                        "side": side_str,
                                        "our_avg_entry": str(our_avg_entry),
                                        "current_bid": str(current_bid),
                                        "our_pnl_pct": str(our_pnl_pct),
                                        "leader_avg_entry": str(leader_avg_entry),
                                        "leader_sell_price": str(leader_sell_price),
                                        "leader_pnl_pct": str(leader_pnl_pct),
                                    }
                                )
                                print(
                                    f"  [BLOCKED] SELL {side_str} - Leader profit +{leader_pnl_pct}%, "
                                    f"but we'd lose {our_pnl_pct}%"
                                )
                                self._record_trade_decision(
                                    market_id=market_id, token_id=token_id, side_str=side_str,
                                    action_type="SELL", leader_dollars=leader_dollars, leader_price=leader_sell_price,
                                    our_decision="SKIP", skip_reason=skip_reason,
                                    our_would_profit=our_would_profit, our_pnl_pct=our_pnl_pct,
                                    leader_profited=leader_profited, leader_pnl_pct=leader_pnl_pct,
                                )
                                return False
                            else:
                                # Leader also took a loss - follow them (cutting risk)
                                self.bot_logger.info(
                                    "SELL allowed: leader also cutting loss",
                                    data={
                                        "market_id": market_id,
                                        "side": side_str,
                                        "leader_loss": True,
                                    }
                                )
                        else:
                            # No leader entry data - be conservative, allow the sell
                            # (Leader might be cutting a loss we don't know about)
                            pass
                    else:
                        # No leader sell price - can't determine, allow the sell
                        pass
        
        # Record leader's sell for future reference
        if leader_sell_price and leader_sell_price > Decimal("0"):
            leader_shares = leader_dollars / leader_sell_price
            self.leader_tracker.record_sell(token_id, leader_shares, leader_sell_price)
        
        # Calculate shares to sell
        shares_to_sell = (scaled_dollars / current_bid).quantize(Decimal("0.01"))

        # Never sell more than we have
        shares_to_sell = min(shares_to_sell, pos.shares)

        if shares_to_sell <= Decimal("0"):
            skip_reason = "zero_shares"
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="SELL", leader_dollars=leader_dollars, leader_price=leader_sell_price,
                our_decision="SKIP", skip_reason=skip_reason,
                our_would_profit=our_would_profit, our_pnl_pct=our_pnl_pct,
                leader_profited=leader_profited, leader_pnl_pct=leader_pnl_pct,
            )
            return False

        success = self._place_order(
            market_id=market_id,
            token_id=token_id,
            side=side,
            action="SELL",
            now=now,
            is_followup=is_followup,
            shares=shares_to_sell,
            leader_dollars=leader_dollars,
        )
        
        # Record executed sell
        if success:
            our_dollars = shares_to_sell * current_bid
            self._record_trade_decision(
                market_id=market_id, token_id=token_id, side_str=side_str,
                action_type="SELL", leader_dollars=leader_dollars, leader_price=leader_sell_price,
                our_decision="SELL", our_dollars=our_dollars, our_shares=shares_to_sell,
                our_would_profit=our_would_profit, our_pnl_pct=our_pnl_pct,
                leader_profited=leader_profited, leader_pnl_pct=leader_pnl_pct,
            )

        return success

    def _place_order(
        self,
        market_id: str,
        token_id: str,
        side: Side,
        action: str,
        now: datetime,
        is_followup: bool = False,
        dollars: Optional[Decimal] = None,
        shares: Optional[Decimal] = None,
        leader_dollars: Optional[Decimal] = None,
    ) -> bool:
        """Place a market order via the execution adapter and update shadow.

        Returns True if order was successfully placed, False otherwise.
        """
        is_sell = action == "SELL"

        # Get price for shadow tracking
        price = self._get_price(token_id, is_sell=is_sell)

        if price is None or price <= Decimal("0"):
            if is_sell:
                # For sells, use avg_price from our position (the price we bought at)
                # This is a valid price for tracking purposes
                pos = self.shadow.get(token_id, market_id, side)
                if pos.avg_price > Decimal("0"):
                    price = pos.avg_price
                else:
                    # No avg_price means we don't have a valid position to sell
                    self.stats.record_skip("no_price")
                    self.bot_logger.warning("SELL skipped: no price and no avg_price", data={
                        "market_id": market_id, "token_id": token_id[:16],
                    })
                    return False
            else:
                self.stats.record_skip("no_price")
                return False

        # Clamp price
        price = max(MIN_VALID_PRICE, min(MAX_VALID_PRICE, price))

        # Compute missing value
        if shares is None and dollars is not None and price > Decimal("0"):
            shares = (dollars / price).quantize(Decimal("0.01"))
        elif dollars is None and shares is not None:
            dollars = (shares * price).quantize(Decimal("0.01"))

        if not shares or shares <= Decimal("0"):
            return False

        # For sells, clamp to owned shares
        if is_sell:
            pos = self.shadow.get(token_id, market_id, side)
            shares = min(shares, pos.shares)
            if shares <= Decimal("0"):
                return False

        # Build and place order
        correlation_id = f"mirror_{market_id[:8]}_{action}_{int(time.time())}"

        request = OrderRequest(
            market_id=market_id,
            side=side,
            order_type=OrderType.MARKET,
            dollars=dollars if action == "BUY" else None,
            shares=shares if action == "SELL" else None,
            correlation_id=correlation_id,
        )

        self.bot_logger.info(f"MIRROR {action}", data={
            "market_id": market_id,
            "side": str(side),
            "dollars": str(dollars),
            "shares": str(shares),
            "price": str(price),
        })

        response = self.adapter.place_order(request)

        if response.success or self.config.mode != ExecutionMode.LIVE:
            # Update shadow portfolio
            fill_shares = response.filled_shares if response.filled_shares else shares
            fill_price = price

            self.shadow.apply_fill(
                token_id=token_id,
                market_id=market_id,
                side=side,
                action=action,
                shares=fill_shares,
                price=fill_price,
                timestamp=now,
            )

            # Update stats and budget
            fill_dollars = fill_shares * fill_price
            if action == "BUY":
                self.stats.buy_count += 1
                self.stats.buy_dollars += fill_dollars
                self._hourly_budget_used += fill_dollars
            else:
                self.stats.sell_count += 1
                self.stats.sell_dollars += fill_dollars
                # Credit back sells to hourly budget - selling frees up capital
                # to reinvest within the same hour
                self._hourly_budget_used = max(
                    Decimal("0"), self._hourly_budget_used - fill_dollars
                )

            self.bot_logger.info(f"MIRROR {action} FILLED", data={
                "market_id": market_id,
                "side": str(side),
                "filled_shares": str(fill_shares),
                "filled_dollars": str(fill_dollars),
            })

            # Terminal output
            side_str = "UP" if side == Side.UP else "DOWN"
            self._print_trade_event(
                action, market_id, side_str,
                fill_dollars, fill_price,
                leader_dollars or fill_dollars,
            )
            return True
        else:
            self.bot_logger.warning(f"MIRROR {action} REJECTED", data={
                "market_id": market_id,
                "error": response.error_message,
            })
            side_str = "UP" if side == Side.UP else "DOWN"
            label = self._get_market_label(market_id, side_str)
            print(f"  [{self._elapsed_str()}] FAIL {label:<10s}  {action} rejected: {response.error_message}")
            return False

    # ===== RATIO TRACKING =====

    def _apply_ratio_constraint(
        self, market_id: str, side_str: str, dollars: Decimal
    ) -> Decimal:
        """Apply soft ratio constraint. Reduce buys, never augment.

        Returns adjusted dollar amount (may be 0 if even $1 worsens divergence).
        """
        if not self._leader_ratios:
            return dollars  # No data yet, don't constrain

        # Market distribution check
        total_deployed = self._get_total_deployed()
        if total_deployed <= Decimal("0"):
            return dollars  # No positions yet, allow entry

        leader_market_pct = self._leader_ratios.get(market_id, Decimal("0"))
        our_market_pct = (self._get_market_exposure(market_id) / total_deployed) * Decimal("100") if total_deployed > Decimal("0") else Decimal("0")

        margin = self.mcfg.ratio_margin_pct

        # Would this buy push our market allocation too high?
        new_market_exposure = self._get_market_exposure(market_id) + dollars
        new_total = total_deployed + dollars
        new_market_pct = (new_market_exposure / new_total) * Decimal("100") if new_total > Decimal("0") else Decimal("0")

        if new_market_pct > leader_market_pct + margin:
            # Scale down to stay within margin
            # target_pct = leader_market_pct + margin
            target_pct = leader_market_pct + margin
            # new_market_exposure / (total_deployed + x) = target_pct/100
            # current_market + x = target_pct/100 * (total_deployed + x)
            # x - target_pct/100 * x = target_pct/100 * total - current_market
            # x * (1 - target_pct/100) = target_pct/100 * total - current_market
            current_market = self._get_market_exposure(market_id)
            target_frac = target_pct / Decimal("100")

            denominator = Decimal("1") - target_frac
            if denominator > Decimal("0"):
                max_dollars = (target_frac * total_deployed - current_market) / denominator
                max_dollars = max(Decimal("0"), max_dollars)
                dollars = min(dollars, max_dollars)
            else:
                dollars = Decimal("0")

        # UP/DOWN ratio check within market
        updown = self._leader_updown_ratios.get(market_id)
        if updown and self._get_market_exposure(market_id) > Decimal("0"):
            leader_side_pct = updown.get(side_str, Decimal("50"))
            market_exp = self._get_market_exposure(market_id)
            our_side_exp = self._get_side_exposure(
                market_id, Side.UP if side_str == "UP" else Side.DOWN
            )
            our_side_pct = (our_side_exp / market_exp) * Decimal("100") if market_exp > Decimal("0") else Decimal("50")

            if our_side_pct > leader_side_pct + margin:
                # Scale down
                target_side_pct = leader_side_pct + margin
                target_side_exp = market_exp * target_side_pct / Decimal("100")
                room = target_side_exp - our_side_exp
                room = max(Decimal("0"), room)
                dollars = min(dollars, room)

        # If reduced below $1 and would still worsen divergence, block
        if Decimal("0") < dollars < MARKET_ORDER_MIN_DOLLARS:
            # Would even $1 worsen divergence?
            test_market_exp = self._get_market_exposure(market_id) + MARKET_ORDER_MIN_DOLLARS
            test_total = total_deployed + MARKET_ORDER_MIN_DOLLARS
            test_pct = (test_market_exp / test_total) * Decimal("100") if test_total > Decimal("0") else Decimal("0")

            if test_pct > leader_market_pct + margin:
                return Decimal("0")  # Even $1 is too much
            else:
                dollars = MARKET_ORDER_MIN_DOLLARS  # Bump is OK

        return dollars

    def _update_leader_ratios(self, snapshot: LeaderSnapshot) -> None:
        """Update leader's market and UP/DOWN distribution from snapshot."""
        total = sum(exp.gross_dollars for exp in snapshot.exposures.values())
        if total <= Decimal("0"):
            return

        self._leader_ratios = {}
        self._leader_updown_ratios = {}

        for market_id, exp in snapshot.exposures.items():
            if not self.data_source.is_hourly_updown_market(market_id):
                continue
            gross = exp.gross_dollars
            if gross <= Decimal("0"):
                continue

            self._leader_ratios[market_id] = (gross / total * Decimal("100")).quantize(Decimal("0.01"))

            up_pct = (exp.up_dollars / gross * Decimal("100")).quantize(Decimal("0.01")) if gross > Decimal("0") else Decimal("50")
            down_pct = Decimal("100") - up_pct
            self._leader_updown_ratios[market_id] = {"UP": up_pct, "DOWN": down_pct}

    # ===== SNAPSHOT (LIMITED USE) =====

    def _periodic_snapshot(self, now: datetime) -> None:
        """Periodic snapshot for market discovery and ratio estimation only."""
        self._last_snapshot_time = time.time()
        self.stats.snapshot_polls += 1

        try:
            snapshot = self.data_source.build_leader_snapshot(now)
        except Exception as e:
            self.bot_logger.error(f"Snapshot error: {e}")
            return

        # Update leader capital estimate
        self._leader_capital = self.scaling_calc.get_leader_capital(snapshot)
        self._scale_ratio = self.scaling_calc.compute_scale_ratio(self._leader_capital)
        
        # Update burst buffer with new scale ratio
        self.burst_buffer.set_scale_ratio(self._scale_ratio)

        # Discover new markets
        for market_id in snapshot.exposures:
            if self.data_source.is_hourly_updown_market(market_id):
                if market_id not in self._hourly_updown_markets:
                    self._hourly_updown_markets.add(market_id)
                    self.bot_logger.info("New hourly market from snapshot", data={
                        "market_id": market_id,
                    })
                    # Subscribe to WebSocket prices for this market's tokens
                    for _, mkt in self.data_source._discovered_markets.items():
                        if mkt.condition_id == market_id:
                            self.ws_prices.subscribe(mkt.token_id)

        self.stats.markets_tracked = len(self._hourly_updown_markets)

        # Update leader ratios
        self._update_leader_ratios(snapshot)

        # Catch missing sells: if leader has 0 exposure but we still hold
        for market_id in list(self._hourly_updown_markets):
            leader_exp = snapshot.exposures.get(market_id)
            if leader_exp and leader_exp.gross_dollars > Decimal("0.50"):
                continue  # Leader still active

            # Check if we hold this market
            my_snapshot = self.shadow.build_snapshot(
                now, self.config.scaling.our_capital, self._hourly_budget_used
            )
            my_exp = my_snapshot.exposures.get(market_id)
            if my_exp and my_exp.gross_dollars > Decimal("0.50"):
                self.bot_logger.warning("SNAPSHOT CATCH: Leader exited but we still hold", data={
                    "market_id": market_id,
                    "our_gross": str(my_exp.gross_dollars),
                })
                self._flatten_market(market_id, now, reason="snapshot_catch")

    # ===== END OF HOUR =====

    def _check_expiry_flattening(self, now: datetime) -> None:
        """Check if any markets are near expiry and flatten."""
        for market_id in list(self._hourly_updown_markets):
            # 30s before close: sell everything
            if self._is_near_expiry(market_id, now, 30.0):
                my_snapshot = self.shadow.build_snapshot(
                    now, self.config.scaling.our_capital, self._hourly_budget_used
                )
                my_exp = my_snapshot.exposures.get(market_id)
                if my_exp and my_exp.gross_dollars > Decimal("0"):
                    self.bot_logger.info("EXPIRY FLATTEN", data={
                        "market_id": market_id,
                        "our_gross": str(my_exp.gross_dollars),
                    })
                    self._flatten_market(market_id, now, reason="expiry")

    def _flatten_market(self, market_id: str, now: datetime, reason: str) -> None:
        """Sell all positions in a market."""
        for token_id, pos in self.shadow.iter_positions().items():
            if pos.market_id != market_id or pos.shares <= Decimal("0"):
                continue

            self._place_order(
                market_id=market_id,
                token_id=token_id,
                side=pos.side,
                action="SELL",
                now=now,
                is_followup=True,  # Not a trade response, don't count as trade skip
                shares=pos.shares,
            )

    def _is_near_expiry(self, market_id: str, now: datetime, threshold_sec: float) -> bool:
        """Check if market is within threshold_sec of closing."""
        if market_id not in self._market_close_times:
            self._market_close_times[market_id] = self.data_source.get_market_close_time(market_id)

        close_time = self._market_close_times.get(market_id)
        if close_time is None:
            return False

        # Make now timezone-aware if needed
        if now.tzinfo is None:
            now_aware = now.replace(tzinfo=timezone.utc)
        else:
            now_aware = now

        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=timezone.utc)

        remaining = (close_time - now_aware).total_seconds()
        return remaining <= threshold_sec

    # ===== HELPERS =====

    def _get_market_label(self, market_id: str, side_str: str = "") -> str:
        """Get short human-readable label like 'BTC-UP' from market_id."""
        for _, mkt in self.data_source._discovered_markets.items():
            if mkt.condition_id == market_id:
                title = mkt.title.lower()
                crypto = "???"
                if "bitcoin" in title or "btc" in title:
                    crypto = "BTC"
                elif "ethereum" in title or "eth" in title:
                    crypto = "ETH"
                elif "solana" in title or "sol" in title:
                    crypto = "SOL"
                elif "xrp" in title:
                    crypto = "XRP"
                if side_str:
                    return f"{crypto}-{side_str}"
                return crypto
        if side_str:
            return f"{market_id[:8]}-{side_str}"
        return market_id[:8]

    def _elapsed_str(self) -> str:
        """Get elapsed time as MM:SS string."""
        elapsed = time.time() - self.stats._start_time
        m, s = divmod(int(elapsed), 60)
        return f"{m:02d}:{s:02d}"

    def _print_trade_event(
        self, action: str, market_id: str, side_str: str,
        our_dollars: Decimal, price: Decimal, leader_dollars: Decimal,
        source: str = "",
    ) -> None:
        """Print a human-readable trade event line to terminal."""
        label = self._get_market_label(market_id, side_str)
        tag = "BUY " if action == "BUY" else "SELL"
        src = f" [{source}]" if source else ""
        print(
            f"  [{self._elapsed_str()}] {tag}  {label:<10s}  "
            f"${our_dollars:.2f} @ {price:.4f}  "
            f"(leader ${leader_dollars:.2f}){src}"
        )

    def _print_skip_event(
        self, market_id: str, side_str: str,
        reason: str, leader_dollars: Decimal,
    ) -> None:
        """Print a skip event (only for actionable skips, not dedup/stale)."""
        label = self._get_market_label(market_id, side_str)
        tag = "SKIP"
        print(
            f"  [{self._elapsed_str()}] {tag}  {label:<10s}  "
            f"reason={reason}  (leader ${leader_dollars:.2f})"
        )

    def _build_portfolio_summary(self) -> str:
        """Build compact portfolio summary for status line.
        
        Shows: cost basis, current value (using best bid), PnL, and exposure by crypto.
        Uses best bid price to value positions (what we could sell for).
        """
        total_cost = Decimal("0")
        total_current_value = Decimal("0")
        
        # Aggregate exposure by crypto symbol
        crypto_exposure: Dict[str, Dict[str, Decimal]] = {
            "BTC": {"UP": Decimal("0"), "DOWN": Decimal("0")},
            "ETH": {"UP": Decimal("0"), "DOWN": Decimal("0")},
            "SOL": {"UP": Decimal("0"), "DOWN": Decimal("0")},
            "XRP": {"UP": Decimal("0"), "DOWN": Decimal("0")},
        }

        for token_id, pos in self.shadow.iter_positions().items():
            if pos.shares <= Decimal("0"):
                continue
            
            # Cost basis (what we paid)
            cost = pos.notional if pos.notional > Decimal("0") else (pos.shares * pos.avg_price)
            total_cost += cost
            
            # Get current market price (best bid - what we can sell for)
            current_price = self._get_price(token_id, is_sell=True)
            if current_price and current_price > Decimal("0"):
                current_value = pos.shares * current_price
            else:
                current_value = cost  # Fallback to cost if no price
            total_current_value += current_value
            
            # Get market info to extract crypto symbol
            market_info = self.data_source.get_market_for_token(token_id)
            if not market_info:
                continue
            
            title_lower = market_info.title.lower()
            crypto = None
            if "bitcoin" in title_lower or "btc" in title_lower:
                crypto = "BTC"
            elif "ethereum" in title_lower or "eth" in title_lower:
                crypto = "ETH"
            elif "solana" in title_lower or "sol" in title_lower:
                crypto = "SOL"
            elif "xrp" in title_lower:
                crypto = "XRP"
            
            if crypto:
                side_str = "UP" if pos.side == Side.UP else "DOWN"
                crypto_exposure[crypto][side_str] += current_value

        # Calculate PnL
        pnl = total_current_value - total_cost
        pnl_pct = (pnl / total_cost * 100) if total_cost > Decimal("0") else Decimal("0")
        
        # Get available capital
        snapshot = self.shadow.build_snapshot(
            datetime.now(timezone.utc),
            self.config.scaling.our_capital,
            self._hourly_budget_used,
        )
        available = snapshot.available_capital

        # Build compact summary string
        # Format: "Cost $X.XX | Value $X.XX | PnL $X.XX (X.X%) | Free $X.XX | BTC:U$X/D$X ..."
        if total_cost > Decimal("0"):
            pnl_sign = "+" if pnl >= 0 else ""
            parts = [
                f"Cost ${total_cost:.2f}",
                f"Value ${total_current_value:.2f}",
                f"PnL {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)",
                f"Free ${available:.2f}",
            ]
        else:
            parts = [f"$0.00 deployed, ${available:.2f} free"]
        
        for crypto in ["BTC", "ETH", "SOL", "XRP"]:
            up = crypto_exposure[crypto]["UP"]
            down = crypto_exposure[crypto]["DOWN"]
            if up > Decimal("0") or down > Decimal("0"):
                parts.append(f"{crypto}:U${up:.0f}/D${down:.0f}")
        
        return " | ".join(parts)

    def _trade_hash(self, trade: PolymarketTrade) -> str:
        """Create a unique hash for a trade."""
        if trade.transaction_hash:
            return trade.transaction_hash
        return f"{trade.condition_id}_{trade.timestamp}_{trade.side}_{trade.size}_{trade.price}"

    def _get_price(self, token_id: str, is_sell: bool) -> Optional[Decimal]:
        """Get current price for a token from WebSocket cache.

        WebSocket-only implementation. Returns None with alert if unavailable.
        """
        # Try WebSocket cache (real-time, ~0.5s latency)
        ws_bid, ws_ask = self.ws_prices.get_best_bid_ask(token_id)
        if ws_bid is not None or ws_ask is not None:
            if is_sell:
                return ws_bid or ws_ask
            else:
                return ws_ask or ws_bid

        ws_mid = self.ws_prices.get_mid(token_id)
        if ws_mid is not None:
            return ws_mid

        # WebSocket unavailable - show alert
        print(f"⚠️ WEBSOCKET UNAVAILABLE PLEASE CHECK - No price for token {token_id[:16]}...")
        return None

    def _get_total_deployed(self) -> Decimal:
        """Get total deployed capital from shadow portfolio."""
        snapshot = self.shadow.build_snapshot(
            datetime.now(timezone.utc),
            self.config.scaling.our_capital,
            self._hourly_budget_used,
        )
        return snapshot.used_capital

    def _get_market_exposure(self, market_id: str) -> Decimal:
        """Get current exposure in a specific market."""
        snapshot = self.shadow.build_snapshot(
            datetime.now(timezone.utc),
            self.config.scaling.our_capital,
            self._hourly_budget_used,
        )
        exp = snapshot.exposures.get(market_id)
        return exp.gross_dollars if exp else Decimal("0")

    def _get_side_exposure(self, market_id: str, side: Side) -> Decimal:
        """Get current exposure on a specific side of a market."""
        snapshot = self.shadow.build_snapshot(
            datetime.now(timezone.utc),
            self.config.scaling.our_capital,
            self._hourly_budget_used,
        )
        exp = snapshot.exposures.get(market_id)
        if not exp:
            return Decimal("0")
        return exp.up_dollars if side == Side.UP else exp.down_dollars

    def _check_hour_boundary(self, now: datetime) -> None:
        """Reset hourly budget at hour boundary."""
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        if self._current_hour != current_hour:
            if self._current_hour is not None:
                self.bot_logger.info("Hour boundary crossed, resetting budget", data={
                    "old_hour": self._current_hour.isoformat(),
                    "new_hour": current_hour.isoformat(),
                    "budget_used": str(self._hourly_budget_used),
                })
            self._current_hour = current_hour
            self._hourly_budget_used = Decimal("0")

            # Clear market close time cache (new hour = new markets)
            self._market_close_times.clear()
            # Markets from previous hour are no longer active
            self._hourly_updown_markets.clear()

    def _shutdown(self) -> None:
        """Shutdown: flush bursts, print summary, finish recording."""
        # Flush remaining bursts
        remaining = self.burst_buffer.flush_all()
        now = self.clock.now()
        for action in remaining:
            self._execute_burst_action(action, now, is_followup=True)

        # Print final portfolio state
        snapshot = self.shadow.build_snapshot(
            now, self.config.scaling.our_capital, self._hourly_budget_used
        )
        print()
        print("=" * 60)
        print("FINAL SHADOW PORTFOLIO")
        print("=" * 60)
        
        # Calculate PnL
        realized_pnl = Decimal("0")  # TODO: track realized PnL from closed positions
        unrealized_pnl = Decimal("0")
        positions_held = 0
        
        for token_id, pos in self.shadow.iter_positions().items():
            if pos.shares > Decimal("0"):
                positions_held += 1
                value = pos.shares * pos.avg_price
                # Get current price for unrealized PnL
                current_price = self._get_price(token_id, is_sell=True)
                if current_price and current_price > Decimal("0"):
                    current_value = pos.shares * current_price
                    # ShadowPosition uses 'notional' as cost basis
                    pos_cost = pos.notional if pos.notional else Decimal("0")
                    pos_pnl = current_value - pos_cost
                    unrealized_pnl += pos_pnl
                    pnl_pct = (pos_pnl / pos_cost * 100) if pos_cost > 0 else Decimal("0")
                    print(f"  {pos.market_id[:12]}... {pos.side} ${value:.2f} ({pos.shares:.2f} @ ${pos.avg_price:.3f})  PnL: ${pos_pnl:.2f} ({pnl_pct:+.1f}%)")
                else:
                    print(f"  {pos.market_id[:12]}... {pos.side} ${value:.2f} ({pos.shares:.2f} @ ${pos.avg_price:.3f})")
        
        print("=" * 60)
        print(f"Total deployed: ${snapshot.used_capital:.2f}")
        print(f"Available: ${snapshot.available_capital:.2f}")
        print(f"Budget used: ${self._hourly_budget_used:.2f}")
        print(f"Unrealized PnL: ${unrealized_pnl:.2f}")
        print("=" * 60)
        print()

        # Print summary
        self.stats.print_summary()
        
        # Finish session recording
        if self.session_recorder:
            self.session_recorder.finish(
                leader_trades_seen=self.stats.leader_events_seen,
                leader_trades_stale=self.stats.leader_events_stale,
                positions_held=positions_held,
                capital_deployed=snapshot.used_capital,
                capital_reserved=snapshot.available_capital,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                starting_capital=self.config.scaling.our_capital,
            )

        # Cleanup
        try:
            self.ws_prices.stop()
        except Exception:
            pass
        try:
            self.blockchain.close()
        except Exception:
            pass
        if self.data_source:
            self.data_source.close()

    def stop(self) -> None:
        """Stop the strategy loop."""
        self._running = False
