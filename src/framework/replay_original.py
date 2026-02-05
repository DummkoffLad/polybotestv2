"""Session Replayer - replays recorded sessions through strategies."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from ..strategies.base import Strategy, StrategyConfig, DecisionAction
from ..simulation.follow_metrics import FollowMetricsTracker

logger = logging.getLogger(__name__)


@dataclass
class ExecutedTrade:
    """Record of a trade executed during replay."""
    sequence: int
    timestamp: datetime
    action: str  # "BUY" or "SELL"
    market_id: str
    token_id: str
    side: str  # "UP" or "DOWN"
    leader_dollars: Decimal
    leader_price: Decimal
    our_dollars: Decimal
    our_shares: Decimal
    our_price: Decimal  # Price we executed at (ask for buy, bid for sell)


@dataclass
class ReplayResult:
    """Result of replaying a session."""
    session_id: str
    strategy_name: str
    events_processed: int = 0
    buys_executed: int = 0
    sells_executed: int = 0
    skips: int = 0
    buy_dollars: Decimal = Decimal("0")
    sell_dollars: Decimal = Decimal("0")
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    events_dropped_no_prices: int = 0
    events_dropped_duplicates: int = 0  # Duplicate events filtered
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    open_cost_basis: Decimal = Decimal("0")
    # Market resolution simulation (LOW→0, HIGH→0.99)
    resolved_pnl: Decimal = Decimal("0")  # PnL if markets resolve at extremes
    win_count: int = 0  # Trades that won
    loss_count: int = 0  # Trades that lost
    # Detailed trade history
    trades: List["ExecutedTrade"] = field(default_factory=list)
    # Session timing
    session_start: Optional[datetime] = None
    session_end: Optional[datetime] = None
    session_duration_minutes: float = 0.0
    # Follow quality metrics
    follow_metrics: Optional[Dict[str, Any]] = None
    # Performance analysis (optional, populated when track_analysis=True)
    analysis: Optional[Dict[str, Any]] = None


@dataclass 
class TimedPriceSnapshot:
    """A price snapshot with timestamp for chronological ordering."""
    timestamp: datetime
    prices: Dict[str, PriceSnapshot]  # token_id -> PriceSnapshot


class SessionReplayer:
    """Replays a recorded session through any strategy.
    
    Now supports price_snapshot events for accurate historical pricing.
    During replay, prices are looked up from the most recent snapshot
    before each trade, providing more realistic simulation.
    """
    
    def __init__(self, session_path: Path, strategy: Strategy, config_overrides: Dict = None):
        self.session_path = Path(session_path)
        self.strategy = strategy
        self.config_overrides = config_overrides or {}
        self.session_id = ""
        self.original_config: Dict[str, Any] = {}
        self.events: List[MarketEvent] = []
        self.final_prices: Dict[str, PriceSnapshot] = {}
        self._dropped_no_prices: int = 0
        self._dropped_duplicates: int = 0  # Track duplicate events
        self._seen_trades: set = set()  # Content-based dedup for trades
        
        # Session timing
        self._session_start: Optional[datetime] = None
        self._session_end: Optional[datetime] = None
        
        # Price history for accurate replay
        self._price_snapshots: List[TimedPriceSnapshot] = []  # Chronological price snapshots
        self._current_prices: Dict[str, PriceSnapshot] = {}  # Latest known prices for each token
    
    def load(self) -> int:
        """Load session file. Returns event count."""
        if not self.session_path.exists():
            raise FileNotFoundError(f"Not found: {self.session_path}")
        
        with open(self.session_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                
                # Handle prefixed log format: [MODE=XXX] {...}
                json_str = line.strip()
                if json_str.startswith("[MODE="):
                    bracket_end = json_str.find("]")
                    if bracket_end > 0:
                        json_str = json_str[bracket_end + 1:].strip()
                
                try:
                    data = json.loads(json_str)
                except:
                    continue
                
                # Track timestamps for session duration calculation
                record_ts = data.get("timestamp")
                if record_ts:
                    try:
                        ts = datetime.fromisoformat(record_ts)
                        if self._session_start is None or ts < self._session_start:
                            self._session_start = ts
                        if self._session_end is None or ts > self._session_end:
                            self._session_end = ts
                    except:
                        pass
                
                # Original format: type-based events
                t = data.get("type")
                if t == "session_start":
                    self.session_id = data.get("session_id", "")
                    self.original_config = data.get("config", {})
                    # Session start timestamp
                    if record_ts:
                        try:
                            self._session_start = datetime.fromisoformat(record_ts)
                        except:
                            pass
                elif t == "price_snapshot":
                    # Parse price snapshot for historical price tracking
                    self._parse_price_snapshot(data)
                elif t in ("market_event", "leader_trade"):
                    event = self._parse_event(data)
                    if event:
                        # Content-based dedup to filter duplicate OrderFilled events
                        dedup_key = self._make_dedup_key(event)
                        if dedup_key in self._seen_trades:
                            self._dropped_duplicates += 1
                            continue
                        self._seen_trades.add(dedup_key)
                        self.events.append(event)
                        self.final_prices[event.prices.token_id] = event.prices
                
                # New log format: message-based events
                msg = data.get("message", "")
                event_data = data.get("data", {})
                if "BLOCKCHAIN BUY" in msg or "BLOCKCHAIN SELL" in msg:
                    event = self._parse_log_event(data)
                    if event:
                        # Content-based dedup to filter duplicate OrderFilled events
                        dedup_key = self._make_dedup_key(event)
                        if dedup_key in self._seen_trades:
                            self._dropped_duplicates += 1
                            continue
                        self._seen_trades.add(dedup_key)
                        self.events.append(event)
                        self.final_prices[event.prices.token_id] = event.prices
        
        if self._dropped_no_prices:
            logger.warning(f"Dropped {self._dropped_no_prices} events with no real bid/ask prices")
        
        if self._dropped_duplicates:
            logger.info(f"Filtered {self._dropped_duplicates} duplicate trade events")
        
        # Log price snapshot stats
        if self._price_snapshots:
            logger.info(f"Loaded {len(self._price_snapshots)} price snapshots covering {len(self._current_prices)} tokens")
        
        return len(self.events)
    
    def _make_dedup_key(self, event: MarketEvent) -> str:
        """Generate content-based dedup key for a trade event.
        
        Same trade can emit 2 OrderFilled events (leader as maker AND taker)
        with different log_index. We dedupe by economic content instead.
        Key: tx_hash + token_id + action + dollar_value
        
        Note: We don't include tx_hash if not available (e.g., log replay format).
        In that case we use token_id + action + dollars + timestamp as a fallback.
        """
        trade = event.trade
        tx_hash = getattr(trade, 'tx_hash', None) or ""
        
        if tx_hash:
            # Session file format with tx_hash - use content-based key
            return f"{tx_hash}_{trade.token_id}_{trade.action.value}_{trade.dollars}"
        else:
            # Log format without tx_hash - use timestamp-based key
            return f"{trade.timestamp}_{trade.token_id}_{trade.action.value}_{trade.dollars}"

    def _parse_event(self, data: Dict) -> Optional[MarketEvent]:
        try:
            trade_data = data.get("leader_trade", {})
            price_data = data.get("price_context", {})
            
            side = TradeSide.UP if trade_data.get("side") == "UP" else TradeSide.DOWN
            action = TradeAction.BUY if trade_data.get("action") == "BUY" else TradeAction.SELL
            
            ts = trade_data.get("timestamp", "")
            timestamp = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            
            trade = LeaderTrade(
                timestamp=timestamp,
                market_id=trade_data.get("market_id", ""),
                token_id=trade_data.get("token_id", ""),
                side=side,
                action=action,
                dollars=Decimal(str(trade_data.get("leader_dollars", 0))),
                price=Decimal(str(trade_data.get("leader_price", 0))),
                shares=Decimal(str(trade_data.get("leader_shares", 0))),
                source=trade_data.get("source", "replay"),
                tx_hash=trade_data.get("tx_hash"),  # Preserve tx_hash for dedup
            )
            
            prices = PriceSnapshot(
                token_id=price_data.get("token_id", trade.token_id),
                bid=Decimal(price_data["bid"]) if price_data.get("bid") else None,
                ask=Decimal(price_data["ask"]) if price_data.get("ask") else None,
                spread_pct=Decimal(price_data["spread_pct"]) if price_data.get("spread_pct") else None,
            )
            
            return MarketEvent(trade=trade, prices=prices, context=data.get("context", {}))
        except Exception:
            return None
    
    def _parse_price_snapshot(self, data: Dict) -> None:
        """Parse a price_snapshot event and add to price history."""
        try:
            ts = data.get("timestamp", "")
            timestamp = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            
            prices_data = data.get("prices", {})
            prices: Dict[str, PriceSnapshot] = {}
            
            for token_id, price_info in prices_data.items():
                bid = Decimal(price_info["bid"]) if price_info.get("bid") else None
                ask = Decimal(price_info["ask"]) if price_info.get("ask") else None
                spread_pct = Decimal(price_info["spread_pct"]) if price_info.get("spread_pct") else None
                
                if bid is not None and ask is not None:
                    prices[token_id] = PriceSnapshot(
                        token_id=token_id,
                        bid=bid,
                        ask=ask,
                        spread_pct=spread_pct,
                    )
                    # Update current prices (latest known price for each token)
                    self._current_prices[token_id] = prices[token_id]
                    # Also update final_prices for resolution calculation
                    self.final_prices[token_id] = prices[token_id]
            
            if prices:
                self._price_snapshots.append(TimedPriceSnapshot(
                    timestamp=timestamp,
                    prices=prices,
                ))
        except Exception as e:
            logger.debug(f"Failed to parse price snapshot: {e}")
    
    def get_price_at_time(self, token_id: str, timestamp: datetime) -> Optional[PriceSnapshot]:
        """Get the most recent price snapshot for a token before the given timestamp.
        
        This allows strategies to access historical prices during replay,
        not just the price at trade time.
        """
        # Find the most recent snapshot before the timestamp
        best_snapshot = None
        for snapshot in self._price_snapshots:
            if snapshot.timestamp <= timestamp:
                if token_id in snapshot.prices:
                    best_snapshot = snapshot.prices[token_id]
            else:
                break  # Snapshots are chronological, no need to continue
        
        return best_snapshot
    
    def get_all_prices_at_time(self, timestamp: datetime) -> Dict[str, PriceSnapshot]:
        """Get all known prices at a given timestamp.
        
        Returns the most recent snapshot for each token before the timestamp.
        """
        result: Dict[str, PriceSnapshot] = {}
        for snapshot in self._price_snapshots:
            if snapshot.timestamp <= timestamp:
                result.update(snapshot.prices)
            else:
                break
        return result
    
    def _parse_log_event(self, data: Dict) -> Optional[MarketEvent]:
        """Parse event from log format (BLOCKCHAIN BUY/SELL messages).

        Only produces events if real bid/ask data is present in the log.
        Will NOT fabricate prices — that would make replay results unreliable.
        """
        try:
            msg = data.get("message", "")
            event_data = data.get("data", {})

            if "BUY" in msg:
                action = TradeAction.BUY
            elif "SELL" in msg:
                action = TradeAction.SELL
            else:
                return None

            # Require real bid/ask from log data — refuse to fabricate
            raw_bid = event_data.get("bid") or event_data.get("best_bid")
            raw_ask = event_data.get("ask") or event_data.get("best_ask")
            if not raw_bid or not raw_ask:
                logger.debug(f"Log event dropped: no real bid/ask data in log entry")
                self._dropped_no_prices += 1
                return None

            bid = Decimal(str(raw_bid))
            ask = Decimal(str(raw_ask))
            if bid <= 0 or ask <= 0 or bid > ask:
                logger.debug(f"Log event dropped: invalid prices bid={bid} ask={ask}")
                self._dropped_no_prices += 1
                return None

            side = TradeSide.UP if event_data.get("side") == "UP" else TradeSide.DOWN

            ts = data.get("timestamp", "")
            try:
                ts_clean = ts.replace(" ET", "").replace(" UTC", "")
                timestamp = datetime.fromisoformat(ts_clean)
            except Exception:
                timestamp = datetime.now(timezone.utc)

            market_id = event_data.get("market_id", "")
            token_id = event_data.get("token_id", market_id)
            price = Decimal(str(event_data.get("price", 0)))
            dollars = Decimal(str(event_data.get("leader_dollars", 0)))
            shares = Decimal(str(event_data.get("shares", 0)))

            trade = LeaderTrade(
                timestamp=timestamp,
                market_id=market_id,
                token_id=token_id,
                side=side,
                action=action,
                dollars=dollars,
                price=price,
                shares=shares,
                source="log_replay",
            )

            spread_pct = ((ask - bid) / ask * 100) if ask > 0 else None
            prices = PriceSnapshot(
                token_id=token_id,
                bid=bid,
                ask=ask,
                spread_pct=spread_pct,
            )

            return MarketEvent(trade=trade, prices=prices, context=event_data)
        except Exception:
            return None
    
    def run(self, simulate_resolution: bool = False, collect_trades: bool = False,
            track_follow_metrics: bool = True, track_analysis: bool = False) -> ReplayResult:
        """Run the replay.

        Args:
            simulate_resolution: If True, calculate PnL assuming markets resolve
                                 at extreme prices (UP→0.99 win, DOWN→0 lose).
            collect_trades: If True, record each executed trade for analysis.
            track_follow_metrics: If True, track follow quality metrics.
            track_analysis: If True, run full performance analysis (attribution, equity, drawdown, slippage).
        """
        config = self._merge_config()
        strategy_config = StrategyConfig.from_dict(config)

        self.strategy.initialize(strategy_config)
        self.strategy.on_session_start()

        # Initialize follow metrics tracker
        follow_tracker = FollowMetricsTracker() if track_follow_metrics else None

        # Initialize analysis trackers if requested
        attributor = None
        equity_tracker = None
        slippage_analyzer = None
        slippage_measurements = []
        sizing_gaps = []
        selection_gaps = []

        if track_analysis:
            from ..analysis.attribution import TradeAttributor
            from ..analysis.equity_tracker import EquityTracker
            from ..analysis.slippage import SlippageAnalyzer

            attributor = TradeAttributor()

            # Get starting capital from config
            starting_capital = Decimal(str(config.get('scaling', {}).get('our_capital', '100')))
            equity_tracker = EquityTracker(starting_capital)

            slippage_analyzer = SlippageAnalyzer()

            # Get leader capital for sizing gap calculation
            leader_capital = Decimal(str(config.get('scaling', {}).get('leader_estimated_capital', '900')))
        
        # Calculate session duration
        duration_minutes = 0.0
        if self._session_start and self._session_end:
            duration_minutes = (self._session_end - self._session_start).total_seconds() / 60.0
        
        result = ReplayResult(
            session_id=self.session_id, 
            strategy_name=self.strategy.name,
            events_dropped_no_prices=self._dropped_no_prices,
            events_dropped_duplicates=self._dropped_duplicates,
            session_start=self._session_start,
            session_end=self._session_end,
            session_duration_minutes=duration_minutes,
        )

        for i, event in enumerate(self.events):
            result.events_processed += 1
            # Add all prices to event context for strategies that need them
            event.context['all_prices'] = self.get_all_prices_at_time(event.trade.timestamp)
            decision = self.strategy.on_event(event)
            trade = event.trade
            prices = event.prices
            
            # Track leader trade for follow metrics
            if follow_tracker:
                action_str = "BUY" if trade.action == TradeAction.BUY else "SELL"
                follow_tracker.record_leader_trade(
                    timestamp=trade.timestamp,
                    token_id=trade.token_id,
                    action=action_str,
                    shares=trade.shares,
                    sequence=i
                )
            
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                self.strategy.on_fill(event, decision)

                # Track our response for follow metrics
                if follow_tracker:
                    our_action = "BUY" if decision.action == DecisionAction.BUY else "SELL"
                    follow_tracker.record_our_trade(
                        timestamp=trade.timestamp,  # Same time (instant response in sim)
                        token_id=trade.token_id,
                        action=our_action,
                        shares=decision.shares or Decimal("0"),
                        leader_sequence=i
                    )

                # Record the trade if requested
                if collect_trades:
                    exec_price = prices.ask if decision.action == DecisionAction.BUY else prices.bid
                    result.trades.append(ExecutedTrade(
                        sequence=i + 1,
                        timestamp=trade.timestamp,
                        action=decision.action.value,
                        market_id=trade.market_id,
                        token_id=trade.token_id,
                        side=trade.side.value,
                        leader_dollars=trade.dollars,
                        leader_price=trade.price,
                        our_dollars=decision.dollars or Decimal("0"),
                        our_shares=decision.shares or Decimal("0"),
                        our_price=exec_price or Decimal("0"),
                    ))

                # Track for analysis if requested
                if track_analysis and attributor:
                    # Record entry for attribution
                    attributor.record_entry(event, decision, self.strategy.name)

                    # Measure slippage
                    exec_price = prices.ask if decision.action == DecisionAction.BUY else prices.bid
                    market_mid = (prices.bid + prices.ask) / Decimal("2") if prices.bid and prices.ask else exec_price
                    action_str = "BUY" if decision.action == DecisionAction.BUY else "SELL"

                    measurement = slippage_analyzer.measure_trade_slippage(
                        our_price=exec_price or Decimal("0"),
                        leader_price=trade.price,
                        market_mid=market_mid or Decimal("0"),
                        action=action_str,
                        trade_size=decision.dollars or Decimal("0"),
                        token_id=trade.token_id
                    )
                    slippage_measurements.append(measurement)

                    # Measure sizing gap
                    gap = slippage_analyzer.measure_sizing_gap(
                        leader_dollars=trade.dollars,
                        our_dollars=decision.dollars or Decimal("0"),
                        leader_capital=leader_capital,
                        our_capital=starting_capital,
                        token_id=trade.token_id
                    )
                    sizing_gaps.append(gap)

                    # For SELL, record exit
                    if decision.action == DecisionAction.SELL:
                        attributor.record_exit(
                            token_id=trade.token_id,
                            timestamp=trade.timestamp,
                            shares=decision.shares or Decimal("0"),
                            price=exec_price or Decimal("0")
                        )

                if decision.action == DecisionAction.BUY:
                    result.buys_executed += 1
                    result.buy_dollars += decision.dollars or Decimal("0")
                else:
                    result.sells_executed += 1
                    result.sell_dollars += decision.dollars or Decimal("0")
            else:
                result.skips += 1
                reason = decision.skip_reason or "unknown"
                result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1

                # Track skip for follow metrics
                if follow_tracker:
                    follow_tracker.record_skip(i, trade.token_id, reason)

                # Track selection gap if analysis enabled
                if track_analysis and slippage_analyzer:
                    action_str = "BUY" if trade.action == TradeAction.BUY else "SELL"
                    sel_gap = slippage_analyzer.record_skipped_trade(
                        token_id=trade.token_id,
                        leader_action=action_str,
                        leader_dollars=trade.dollars,
                        leader_price=trade.price,
                        skip_reason=reason
                    )
                    selection_gaps.append(sel_gap)

            # Record equity snapshot after each event if tracking analysis
            if track_analysis and equity_tracker:
                # Get current portfolio state from strategy
                realized, unrealized = self.strategy.calculate_pnl(self.final_prices)
                state = self.strategy.get_state()
                positions = state.get("positions", {})
                deployed = Decimal(str(state.get("total_deployed", "0")))

                equity_tracker.record_manual_snapshot(
                    timestamp=trade.timestamp,
                    realized_pnl=realized,
                    unrealized_pnl=unrealized,
                    open_positions=len(positions),
                    deployed_capital=deployed,
                    trade_count=result.buys_executed + result.sells_executed
                )
        
        # Compute PnL using final recorded prices
        realized, unrealized = self.strategy.calculate_pnl(self.final_prices)
        result.realized_pnl = realized
        result.unrealized_pnl = unrealized
        result.total_pnl = realized + unrealized

        # Count open positions
        state = self.strategy.get_state()
        positions = state.get("positions", {})
        result.open_positions = len(positions)
        result.open_cost_basis = Decimal(str(state.get("total_deployed", "0")))
        
        # Calculate market resolution PnL if requested
        if simulate_resolution:
            resolved, wins, losses = self._calculate_resolved_pnl()
            result.resolved_pnl = realized + resolved
            result.win_count = wins
            result.loss_count = losses
        
        # Calculate follow metrics
        if follow_tracker:
            result.follow_metrics = follow_tracker.calculate_metrics()

        # Build analysis results if tracking enabled
        if track_analysis and attributor and equity_tracker:
            from ..analysis.drawdown import DrawdownAnalyzer

            # Update all open trades with current prices
            current_prices_dict = {token_id: price.bid if price.bid else Decimal("0")
                                   for token_id, price in self.final_prices.items()}
            attributor.update_all_unrealized(current_prices_dict)

            # Get trade summary
            trade_summary = attributor.get_summary()

            # Build equity DataFrame and analyze drawdown
            equity_df = equity_tracker.to_dataframe()
            drawdown_analyzer = DrawdownAnalyzer()

            if len(equity_df) > 0:
                drawdown_metrics = drawdown_analyzer.analyze(equity_df)
            else:
                # Empty equity curve
                drawdown_metrics = {
                    'max_drawdown_pct': 0.0,
                    'max_drawdown_value': 0.0,
                    'drawdown_start': None,
                    'drawdown_bottom': None,
                    'drawdown_duration_min': 0.0,
                    'recovery_time_min': None,
                    'current_drawdown_pct': 0.0,
                }

            # Aggregate slippage, sizing, selection
            slippage_stats = slippage_analyzer.aggregate_slippage(slippage_measurements)
            sizing_stats = slippage_analyzer.aggregate_sizing(sizing_gaps)
            selection_stats = slippage_analyzer.aggregate_selection(selection_gaps)

            # Store in result
            result.analysis = {
                'trade_summary': trade_summary,
                'drawdown': drawdown_metrics,
                'slippage': slippage_stats,
                'sizing': sizing_stats,
                'selection': selection_stats,
                'equity_df': equity_df,
                'attributed_trades': attributor.get_all_trades(),
            }

        self.strategy.on_session_end()
        return result
    
    def _calculate_resolved_pnl(self) -> tuple:
        """Calculate PnL assuming markets resolve at extremes.
        
        For hourly prediction markets:
        - If we hold UP and market resolves UP → shares worth $0.99 each
        - If we hold UP and market resolves DOWN → shares worth $0 each
        - If we hold DOWN and market resolves DOWN → shares worth $0.99 each  
        - If we hold DOWN and market resolves UP → shares worth $0 each
        
        For Polymarket hourly markets:
        - Each position has a side (UP or DOWN)
        - The final bid/ask for that side's token indicates likelihood of winning
        - If bid for our token > 0.5, our side is likely to win
        - Resolution: winning side tokens → $0.99, losing side → $0
        
        Returns: (unrealized_pnl, win_count, loss_count)
        """
        from ..core.types import Side
        
        unrealized = Decimal("0")
        wins = 0
        losses = 0
        
        # Get positions from strategy portfolio
        if hasattr(self.strategy, 'portfolio'):
            positions = self.strategy.portfolio.get_positions()
        else:
            return Decimal("0"), 0, 0
        
        for token_id, pos in positions.items():
            if pos.shares <= 0:
                continue
            
            # Get final price for this token
            final_price = self.final_prices.get(token_id)
            if not final_price or not final_price.bid:
                continue
            
            # For the token we hold, bid > 0.5 means our side is likely to win
            our_side_wins = final_price.bid >= Decimal("0.5")
            
            # Calculate resolution value
            if our_side_wins:
                resolution_value = pos.shares * Decimal("0.99")  # We win
                wins += 1
            else:
                resolution_value = Decimal("0")  # We lose
                losses += 1
            
            # PnL = resolution value - cost basis
            pnl = resolution_value - pos.cost_basis
            unrealized += pnl
        
        return unrealized, wins, losses
    
    def _merge_config(self) -> Dict[str, Any]:
        config = dict(self.original_config)
        for key, value in self.config_overrides.items():
            parts = key.split(".")
            target = config
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return config


def run_session_replay(session_path: str, strategy: Strategy) -> ReplayResult:
    """Convenience function to run a session replay.
    
    Args:
        session_path: Path to the session JSONL file
        strategy: The strategy instance to replay through
        
    Returns:
        ReplayResult with stats
    """
    replayer = SessionReplayer(Path(session_path), strategy)
    count = replayer.load()
    print(f"\nLoaded {count} events from {session_path}")
    if replayer._dropped_no_prices:
        print(f"  WARNING: {replayer._dropped_no_prices} events dropped (no real bid/ask prices)")

    result = replayer.run()

    # Print summary
    print(f"\n{'='*50}")
    print(f"  REPLAY SUMMARY: {result.strategy_name}")
    print(f"{'='*50}")
    print(f"  Events: {result.events_processed}")
    if result.events_dropped_no_prices:
        print(f"  Dropped (no prices): {result.events_dropped_no_prices}")
    print(f"  Buys: {result.buys_executed} (${result.buy_dollars:.2f})")
    print(f"  Sells: {result.sells_executed} (${result.sell_dollars:.2f})")
    print(f"  Skips: {result.skips}")
    if result.skip_reasons:
        print(f"  Skip reasons:")
        for reason, count in sorted(result.skip_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
    print(f"  ---")
    print(f"  Realized PnL:   ${result.realized_pnl:+.2f}")
    print(f"  Unrealized PnL: ${result.unrealized_pnl:+.2f}")
    print(f"  Total PnL:      ${result.total_pnl:+.2f}")
    if result.open_positions:
        print(f"  Open positions: {result.open_positions} (${result.open_cost_basis:.2f} deployed)")
    print(f"{'='*50}\n")

    return result


def run_session_replay_with_analysis(
    session_path: str,
    strategy: Strategy,
    output_dir: Path = Path("data/reports")
) -> ReplayResult:
    """Run session replay with full performance analysis.

    Args:
        session_path: Path to the session JSONL file
        strategy: The strategy instance to replay through
        output_dir: Directory for saving reports and charts

    Returns:
        ReplayResult with analysis field populated
    """
    from ..analysis.reports import ReportGenerator

    # Load and run replay with analysis
    replayer = SessionReplayer(Path(session_path), strategy)
    count = replayer.load()
    print(f"\nLoaded {count} events from {session_path}")
    if replayer._dropped_no_prices:
        print(f"  WARNING: {replayer._dropped_no_prices} events dropped (no real bid/ask prices)")

    result = replayer.run(track_analysis=True)

    if not result.analysis:
        print("ERROR: Analysis not generated")
        return result

    # Generate reports
    generator = ReportGenerator(output_dir)

    # Print console summary
    generator.print_console_summary(
        result,
        result.analysis['trade_summary'],
        result.analysis['drawdown'],
        result.analysis['slippage'],
        result.analysis['sizing'],
        result.analysis['selection']
    )

    # Generate charts
    session_id = result.session_id or "unknown"
    if len(result.analysis['equity_df']) > 0:
        equity_chart = generator.generate_equity_chart(result.analysis['equity_df'], session_id)
        print(f"Equity chart saved: {equity_chart}")

        trade_scatter = generator.generate_trade_scatter(result.analysis['attributed_trades'], session_id)
        print(f"Trade scatter saved: {trade_scatter}")
    else:
        print("No equity data to chart")

    # Save JSON report
    json_report = generator.save_json_report(
        result,
        result.analysis['trade_summary'],
        result.analysis['drawdown'],
        result.analysis['slippage'],
        result.analysis['sizing'],
        result.analysis['selection'],
        session_id
    )
    print(f"JSON report saved: {json_report}\n")

    return result