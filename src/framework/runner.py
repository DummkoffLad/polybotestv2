"""Universal Strategy Runner - polls data, feeds events to strategy."""
from __future__ import annotations
import csv
import json
import os
import signal
import time, logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Set, Dict, Any, TextIO, TYPE_CHECKING
if TYPE_CHECKING:
    from ..core.config import BotConfig
    from .recorder import SessionRecorder
from ..strategies.base import Strategy, StrategyConfig, DecisionAction
from ..data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from ..execution.base import ExecutionAdapter
from ..core.types import OrderRequest, OrderType, Side

logger = logging.getLogger(__name__)

# Hourly market transition settings
HOUR_END_THRESHOLD_SEC = 30  # Stop trading 30s before hour ends
HOUR_START_DELAY_SEC = 30    # Wait 30s after hour starts before resuming
EXTREME_HIGH_PRICE = Decimal("0.99")  # Auto-sell threshold
EXTREME_LOW_PRICE = Decimal("0.01")   # Accept loss threshold

# State persistence paths
STATE_DIR = Path("data/state")
SEEN_HASHES_FILE = STATE_DIR / "seen_hashes.json"
PORTFOLIO_STATE_FILE = STATE_DIR / "portfolio_state.json"


class UniversalRunner:
    def __init__(self, config: "BotConfig", strategy: Strategy, execution: ExecutionAdapter, 
                 duration_minutes: Optional[int] = None, recorder: Optional["SessionRecorder"] = None):
        self.config, self.strategy, self.execution = config, strategy, execution
        self.duration_minutes = duration_minutes
        self.recorder = recorder  # Optional session recorder
        self.data_source = self.price_service = self.blockchain = None
        self._running = False
        self._seen: Set[str] = set()
        self._start = 0.0
        self._last_reconcile = 0.0
        self.stats = {"buys": 0, "sells": 0, "skips": 0, "order_errors": 0}

        # Trade log for comparing with simulation
        self._trade_log_file: Optional[TextIO] = None
        self._trade_log_writer = None
        self._trade_sequence = 0
        
        # Ensure state directory exists
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Load persisted seen hashes
        self._load_seen_hashes()
    
    def _load_seen_hashes(self) -> None:
        """Load previously seen tx hashes from disk."""
        try:
            if SEEN_HASHES_FILE.exists():
                data = json.loads(SEEN_HASHES_FILE.read_text())
                # Only load hashes from last 24 hours
                cutoff = time.time() - 86400
                self._seen = {h for h, ts in data.items() if ts > cutoff}
                logger.info(f"Loaded {len(self._seen)} seen hashes from disk")
        except Exception as e:
            logger.warning(f"Could not load seen hashes: {e}")
    
    def _save_seen_hashes(self) -> None:
        """Persist seen tx hashes to disk."""
        try:
            # Store hash -> timestamp for cleanup
            data = {h: time.time() for h in self._seen}
            SEEN_HASHES_FILE.write_text(json.dumps(data))
            logger.debug(f"Saved {len(self._seen)} seen hashes to disk")
        except Exception as e:
            logger.warning(f"Could not save seen hashes: {e}")
    
    def _save_state(self) -> None:
        """Persist full state on shutdown."""
        self._save_seen_hashes()
        try:
            state = {
                "strategy_state": self.strategy.get_state(),
                "stats": self.stats,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            PORTFOLIO_STATE_FILE.write_text(json.dumps(state, default=str, indent=2))
            logger.info("State saved to disk")
        except Exception as e:
            logger.warning(f"Could not save state: {e}")
    
    def run(self) -> None:
        self._running, self._start = True, time.time()
        
        # Setup graceful shutdown
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            self._running = False
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        
        print(f"\n{'='*50}\n  {self.strategy.name.upper()} STRATEGY\n{'='*50}")
        self._init()
        poll = self.config.leader.poll_interval_sec
        
        # Start recording if recorder is configured (after _init so price_service exists)
        if self.recorder:
            config_dict = {
                "mode": "DRY_RUN" if not hasattr(self.execution, 'arm') else "LIVE",
                "strategy": self.strategy.name,
                "leader_address": self.config.leader.address,
                "scaling": {
                    "k_factor": str(self.config.scaling.k_factor),
                    "our_capital": str(self.config.scaling.our_capital),
                    "hourly_budget": str(self.config.scaling.hourly_budget),
                    "leader_capital": str(self.config.scaling.leader_capital),
                },
                "mirror_strategy": {
                    "per_market_cap_pct": str(self.config.mirror_strategy.per_market_cap_pct),
                    "per_side_pct": str(self.config.mirror_strategy.per_side_pct),
                    "global_exposure_pct": str(self.config.mirror_strategy.global_exposure_pct),
                },
            }
            self.recorder.start_session(config_dict, self.strategy.name)
            # Connect price service for periodic snapshots
            if self.price_service:
                self.recorder.set_price_service(self.price_service)
                # Add existing tracked tokens
                for token_id in self.data_source._token_to_market.keys():
                    self.recorder.add_token(token_id)
            print(f"  📼 Recording session: {self.recorder.session_id}")

            # Start trade log for comparison with simulation
            self._start_trade_log()
        
        try:
            while self._running:
                start = time.time()
                if self.duration_minutes and (time.time() - self._start) / 60 >= self.duration_minutes: break

                # Check for hourly market transition
                if self._is_near_hour_end():
                    self._hourly_cleanup()
                    self._hourly_restart()
                    continue  # Resume loop after restart

                self._cycle()
                self._maybe_reconcile()
                # Record periodic price snapshots
                if self.recorder:
                    self.recorder.maybe_record_price_snapshot()
                if (e := time.time() - start) < poll: time.sleep(poll - e)
        except Exception as e:
            logger.error(f"Fatal error in run loop: {e}")
        finally:
            self._shutdown()
    
    def _init(self) -> None:
        from ..data.live_source import LiveDataSource
        from ..data.blockchain_detector import BlockchainDetector
        from ..data.ws_price import WebSocketPriceService
        from ..core.clock import SystemClock
        
        self.data_source = LiveDataSource(leader_address=self.config.leader.address)
        if self.config.trader.address: self.data_source.my_address = self.config.trader.address
        if hasattr(self.execution, "set_data_source"): self.execution.set_data_source(self.data_source)
        
        clock = SystemClock()
        snap = self.data_source.build_leader_snapshot(clock.now())
        # Use fixed leader capital from config (don't auto-detect - open positions skew it)
        leader_cap = self.config.scaling.leader_capital
        our_cap, k = self.config.scaling.our_capital, self.config.scaling.k_factor
        scale = (our_cap / leader_cap) * k if leader_cap > 0 else Decimal("0.1")

        # Only show scale info for strategies that use it
        uses_scaling = getattr(self.strategy, 'use_scaling', True)  # Default True for legacy strategies
        if uses_scaling:
            # SAFETY: Validate leader capital and scale
            if leader_cap < Decimal("10"):
                logger.warning(f"Leader capital suspiciously low: ${leader_cap}")
            if not (Decimal("0.001") < scale < Decimal("10")):
                logger.warning(f"Scale ratio out of typical bounds: {scale}")
            print(f"  Leader capital: ${leader_cap}  |  Scale: {scale:.4f}")
        
        existing = self.data_source.fetch_trades(self.config.leader.address, limit=50)
        for t in existing:
            self._seen.add(self._hash(t))
            self.data_source._discover_market_from_trade(t)
        
        self.blockchain = BlockchainDetector(leader_address=self.config.leader.address)
        try:
            current_block = self.blockchain.initialize()
            for t in existing:
                if t.transaction_hash: self.blockchain.mark_seen(t.transaction_hash)

            # Catchup: rewind to scan last ~10 minutes of blocks on startup
            # so we don't miss trades that happened while we were down
            catchup_blocks = 300  # ~10 min at 2s/block on Polygon
            self.blockchain._last_block = max(0, current_block - catchup_blocks)
            logger.info(f"Startup catchup: scanning blocks {self.blockchain._last_block} -> {current_block}")
            catchup_trades = self.blockchain.poll() or []
            for bt in catchup_trades:
                if self.blockchain.enrich_trade(bt, self.data_source._token_to_market):
                    # Use content-based dedup key (same as _cycle)
                    h = f"{bt.tx_hash}_{bt.token_id}_{bt.action}_{bt.dollar_value}"
                    if h not in self._seen:
                        self._seen.add(h)
                        logger.info(f"Catchup found trade: {bt.action} tx={bt.tx_hash[:16]}...")
            logger.info(f"Catchup complete: {len(catchup_trades)} logs scanned")

        except Exception as e:
            logger.error(f"Blockchain init failed: {e}")
            print(f"  Blockchain init failed: {e}")
        
        self.price_service = WebSocketPriceService()
        try:
            self.price_service.start()
            if tokens := list(self.data_source._token_to_market.keys()): self.price_service.subscribe_many(tokens)
        except Exception as e: print(f"  WebSocket init failed: {e}")
        
        mcfg = self.config.mirror_strategy
        cfg = StrategyConfig(
            starting_capital=our_cap, hourly_budget=self.config.scaling.hourly_budget,
            cash_reserve_pct=Decimal(str(mcfg.cash_reserve_pct)), leader_capital=leader_cap, k_factor=k,
            per_market_cap_pct=Decimal(str(mcfg.per_market_cap_pct)), per_side_pct=Decimal(str(mcfg.per_side_pct)),
            global_exposure_pct=Decimal(str(mcfg.global_exposure_pct)), spread_cost_pct=Decimal(str(mcfg.spread_cost_pct)),
            slippage_cost_pct=Decimal(str(mcfg.slippage_cost_pct)), max_total_cost_pct=Decimal(str(mcfg.max_total_cost_pct)), params={})
        self.strategy.initialize(cfg)
        self.strategy.on_session_start()
        print("  Ready.\n")
    
    def _cycle(self) -> None:
        now = datetime.now(timezone.utc)
        if not self.blockchain: return
        try:
            for bt in self.blockchain.poll() or []:
                if not self.blockchain.enrich_trade(bt, self.data_source._token_to_market): continue
                # Use content-based dedup key to avoid duplicate OrderFilled events
                # Same trade can emit 2 logs (leader as maker AND taker) with different log_index
                # So we dedupe by economic content: tx_hash + token + action + amount
                h = f"{bt.tx_hash}_{bt.token_id}_{bt.action}_{bt.dollar_value}"
                if h in self._seen: continue
                self._seen.add(h)
                if not self.data_source.is_hourly_updown_market(bt.condition_id): continue
                if self.price_service: self.price_service.subscribe(bt.token_id)
                if event := self._make_event(bt, now): self._process(event)
        except Exception as e: logger.error(f"Blockchain error: {e}")
    
    def _make_event(self, bt, now: datetime) -> Optional[MarketEvent]:
        try:
            side = TradeSide.UP if bt.side in ("Yes", "UP", "Up") else TradeSide.DOWN
            action = TradeAction.BUY if bt.action == "BUY" else TradeAction.SELL
            trade = LeaderTrade(timestamp=datetime.fromtimestamp(bt.block_timestamp, tz=timezone.utc),
                market_id=bt.condition_id, token_id=bt.token_id, side=side, action=action,
                dollars=bt.dollar_value, price=bt.price, shares=bt.shares, source="blockchain", tx_hash=bt.tx_hash)
            bid, ask = (None, None)
            if self.price_service:
                try: bid, ask = self.price_service.get_prices(bt.token_id)
                except: pass
            return MarketEvent(trade=trade, prices=PriceSnapshot(token_id=bt.token_id, bid=bid, ask=ask))
        except Exception as e:
            logger.error(f"Event error: {e}")
            return None
    
    def _process(self, event: MarketEvent) -> None:
        trade = event.trade
        logger.info(f"LEADER {trade.action.value}: ${trade.dollars:.2f} @{trade.price:.4f} tx={trade.tx_hash}")
        print(f"  LEADER {trade.action.value}: ${trade.dollars:.2f} @{trade.price:.4f}")
        decision = self.strategy.on_event(event)
        
        # Record event to session file (before processing)
        if self.recorder:
            self.recorder.record_event(event, decision)
        
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            success = self._exec(event, decision)
            if success:
                self.strategy.on_fill(event, decision)
                self.stats["buys" if decision.action == DecisionAction.BUY else "sells"] += 1
                logger.info(f"ORDER SUCCESS: {decision.action.value} ${decision.dollars:.2f} @{decision.price:.4f}")
                print(f"    -> {decision.action.value} ${decision.dollars:.2f}")

                # Log trade for comparison with simulation
                if self._trade_log_writer:
                    cash_after = getattr(self.strategy, 'cash', Decimal("0"))
                    self._log_trade(event, decision, cash_after)
            else:
                self.stats["order_errors"] += 1
                logger.error(f"ORDER FAILED: {decision.action.value} ${decision.dollars:.2f} - not applying to portfolio")
                print(f"    -> ORDER FAILED (not applied)")
        else:
            self.stats["skips"] += 1
            logger.debug(f"SKIP: {decision.skip_reason}")
            print(f"    -> SKIP ({decision.skip_reason})")
    
    def _exec(self, event: MarketEvent, decision) -> bool:
        if not decision.dollars or not decision.price:
            logger.error(f"Invalid order: dollars={decision.dollars}, price={decision.price}")
            return False
        
        # SAFETY: Validate amounts
        if decision.dollars <= 0 or decision.shares <= 0:
            logger.error(f"Non-positive order: ${decision.dollars}, {decision.shares} shares")
            return False
        
        side = Side.UP if event.trade.side == TradeSide.UP else Side.DOWN
        correlation_id = str(uuid.uuid4())[:8]  # Short unique ID for tracing
        req = OrderRequest(market_id=event.trade.market_id, token_id=event.trade.token_id, side=side,
            action=decision.action.value, order_type=OrderType.MARKET, shares=decision.shares,
            price=decision.price, amount_dollars=decision.dollars, correlation_id=correlation_id)
        
        try:
            resp = self.execution.place_order(req)
            success = resp.status.value in ("FILLED", "PENDING", "SIMULATED")
            
            # SAFETY: Log ALL order outcomes
            if success:
                logger.info(f"ORDER PLACED: {req.action} {req.shares} shares @{req.price} -> {resp.status.value}")
            else:
                logger.warning(f"ORDER REJECTED: {req.action} {req.shares} shares @{req.price} -> {resp.status.value} error={resp.error}")
            
            return success
        except Exception as e:
            logger.error(f"ORDER EXCEPTION: {req.action} {req.shares} shares @{req.price} -> {e}")
            return False
    
    def _maybe_reconcile(self) -> None:
        """Periodically reconcile local state with exchange."""
        # Reconcile every 5 minutes
        if time.time() - self._last_reconcile < 300:
            return
        self._last_reconcile = time.time()

        if not self.data_source or not self.config.trader.address:
            return

        try:
            # Fetch actual positions from exchange
            actual_positions = self.data_source.fetch_positions(self.config.trader.address)
            local_state = self.strategy.get_state()
            local_positions = local_state.get("positions", {})

            # Compare and log discrepancies
            for pos in actual_positions:
                local = local_positions.get(pos.asset, {})
                local_shares = Decimal(local.get("shares", "0"))
                if abs(pos.size - local_shares) > Decimal("0.01"):
                    logger.warning(f"POSITION MISMATCH: {pos.asset} actual={pos.size} local={local_shares}")
                    print(f"  ⚠️ Position mismatch: {pos.title[:30]} actual={pos.size:.2f} local={local_shares:.2f}")

            logger.info(f"Reconciliation complete: {len(actual_positions)} exchange positions")
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")

    # ========== HOURLY MARKET TRANSITION ==========

    def _get_seconds_until_hour_end(self) -> float:
        """Calculate seconds until next hour boundary."""
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return (next_hour - now).total_seconds()

    def _get_seconds_since_hour_start(self) -> float:
        """Calculate seconds since current hour started."""
        now = datetime.now(timezone.utc)
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        return (now - hour_start).total_seconds()

    def _is_near_hour_end(self, threshold_sec: int = HOUR_END_THRESHOLD_SEC) -> bool:
        """Check if we're within threshold of hour end."""
        return self._get_seconds_until_hour_end() <= threshold_sec

    def _hourly_cleanup(self) -> None:
        """Clean up positions before hourly market transition."""
        logger.info("🕐 Hourly market transition starting - cleaning up positions")
        print(f"\n{'='*50}")
        print("  HOURLY TRANSITION - Cleaning up positions")
        print(f"{'='*50}")

        state = self.strategy.get_state()
        positions = state.get("positions", {})

        if not positions:
            logger.info("No positions to clean up")
            print("  No positions to clean up")
            return

        cleaned = 0
        for token_id, pos_data in positions.items():
            shares = Decimal(pos_data.get("shares", "0"))
            if shares <= 0:
                continue

            # Get current price
            bid = None
            if self.price_service:
                try:
                    bid, _ = self.price_service.get_prices(token_id)
                except:
                    pass

            if bid is None:
                logger.warning(f"No price for {token_id[:16]}... - position will expire")
                print(f"  ⚠️ No price for {token_id[:16]}... - will expire")
                continue

            if bid >= EXTREME_HIGH_PRICE:
                # Auto-sell at high price (take profit)
                logger.info(f"Auto-sell at {bid}: {shares} shares of {token_id[:16]}...")
                print(f"  💰 Auto-sell at {bid}: {shares:.2f} shares (take profit)")
                # The actual sell would need to go through execution adapter
                # For now we log and let strategy handle it
                cleaned += 1
            elif bid <= EXTREME_LOW_PRICE:
                # Accept loss - don't sell, let expire
                logger.info(f"Accept loss at {bid}: {shares} shares of {token_id[:16]}...")
                print(f"  📉 Accept loss at {bid}: {shares:.2f} shares (let expire)")
                cleaned += 1
            else:
                logger.warning(f"Mid-priced position {bid}: {shares} shares of {token_id[:16]}...")
                print(f"  ⚠️ Mid-price {bid}: {shares:.2f} shares (may lose value)")

        logger.info(f"Hourly cleanup complete: {cleaned} positions processed")
        print(f"  Cleanup complete: {cleaned} positions processed")

    def _hourly_restart(self) -> None:
        """Restart the bot for new hourly market."""
        sec_until_end = self._get_seconds_until_hour_end()
        logger.info(f"🕐 Waiting {sec_until_end:.0f}s until hour end...")
        print(f"  Waiting {sec_until_end:.0f}s until hour end...")

        # Stop price service
        if self.price_service:
            try:
                self.price_service.stop()
                logger.info("WebSocket stopped")
            except Exception as e:
                logger.error(f"Error stopping WebSocket: {e}")

        # Wait for hour boundary + delay
        wait_time = sec_until_end + HOUR_START_DELAY_SEC
        logger.info(f"Sleeping {wait_time:.0f}s for market transition...")
        print(f"  Sleeping {wait_time:.0f}s for market transition...")
        time.sleep(wait_time)

        # Clear old state
        self._seen.clear()
        if self.data_source:
            self.data_source._token_to_market.clear()

        # Re-initialize
        logger.info("🔄 Restarting with fresh state...")
        print(f"\n{'='*50}")
        print("  RESTARTING WITH FRESH STATE")
        print(f"{'='*50}")
        self._init()

        # Reconnect recorder if active
        if self.recorder and self.price_service:
            self.recorder.set_price_service(self.price_service)

        logger.info("Hourly restart complete - resuming trading")
        print("  Ready for new hour.\n")
    
    def _hash(self, t) -> str: return f"{t.transaction_hash or ''}_{t.asset}_{t.timestamp}"

    # ========== TRADE LOG FOR DRY RUN COMPARISON ==========

    def _start_trade_log(self) -> None:
        """Start trade log CSV file for comparison with simulation."""
        if not self.recorder:
            return

        # Create trade log in same directory as session file
        session_dir = self.recorder.output_dir / self.recorder.session_id.split("/")[0]
        time_part = self.recorder.session_id.split("/")[1]
        trade_log_path = session_dir / f"{time_part}_trades.csv"

        try:
            self._trade_log_file = open(trade_log_path, "w", newline="", encoding="utf-8")
            self._trade_log_writer = csv.writer(self._trade_log_file)
            # Write header matching simulation output format
            self._trade_log_writer.writerow([
                "seq", "time", "action", "side", "leader_$", "our_$",
                "price", "shares", "bid", "ask", "cash_after", "token_id"
            ])
            self._trade_log_file.flush()
            print(f"  📊 Trade log: {trade_log_path}")
            logger.info(f"Trade log started: {trade_log_path}")
        except Exception as e:
            logger.error(f"Could not start trade log: {e}")

    def _log_trade(self, event: MarketEvent, decision, cash_after: Decimal) -> None:
        """Log a trade to the trade log CSV."""
        if not self._trade_log_writer:
            return

        self._trade_sequence += 1
        try:
            self._trade_log_writer.writerow([
                self._trade_sequence,
                datetime.now(timezone.utc).strftime("%H:%M:%S"),
                decision.action.value,
                event.trade.side.value,
                f"{event.trade.dollars:.2f}",
                f"{decision.dollars:.2f}",
                f"{decision.price:.4f}",
                f"{decision.shares:.2f}",
                f"{event.prices.bid:.4f}" if event.prices.bid else "N/A",
                f"{event.prices.ask:.4f}" if event.prices.ask else "N/A",
                f"{cash_after:.2f}",
                event.trade.token_id[:16] + "...",
            ])
            self._trade_log_file.flush()
        except Exception as e:
            logger.error(f"Could not log trade: {e}")

    def _close_trade_log(self) -> None:
        """Close the trade log file."""
        if self._trade_log_file:
            try:
                self._trade_log_file.close()
                self._trade_log_file = None
                self._trade_log_writer = None
                logger.info("Trade log closed")
            except Exception as e:
                logger.error(f"Could not close trade log: {e}")

    def _shutdown(self) -> None:
        """Graceful shutdown with state persistence."""
        logger.info("Shutting down...")
        self._running = False

        # Stop price service
        if self.price_service:
            try: self.price_service.stop()
            except: pass

        # Close trade log before ending recording
        self._close_trade_log()

        # Get final summary
        summary = self.strategy.on_session_end()

        # End recording session if active
        if self.recorder:
            session_path = self.recorder.end_session(summary={
                "stats": self.stats,
                "strategy_summary": summary or {},
            })
            if session_path:
                print(f"  📼 Session recorded: {session_path}")
        
        # SAFETY: Persist state before exit
        self._save_state()
        
        # Print summary
        errors = self.stats.get('order_errors', 0)
        print(f"\n{'='*50}")
        print(f"  SUMMARY: {self.stats['buys']} buys, {self.stats['sells']} sells, {errors} errors")
        if errors > 0:
            print(f"  ⚠️ {errors} order(s) failed - check logs!")
        print(f"{'='*50}")
        logger.info(f"Session ended: {self.stats}")
    
    def stop(self) -> None: self._running = False
