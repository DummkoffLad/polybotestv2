"""Session file loading and parsing logic."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from .models import TimedPriceSnapshot

logger = logging.getLogger(__name__)


class SessionLoader:
    """Loads and parses session files into events and price snapshots."""

    def __init__(self, session_path: Path):
        self.session_path = Path(session_path)
        self.session_id = ""
        self.original_config: Dict[str, Any] = {}
        self.events: List[MarketEvent] = []
        self.final_prices: Dict[str, PriceSnapshot] = {}
        self._dropped_no_prices: int = 0
        self._dropped_duplicates: int = 0
        self._seen_trades: set = set()

        # Session timing
        self._session_start: Optional[datetime] = None
        self._session_end: Optional[datetime] = None

        # Price history for accurate replay
        self._price_snapshots: List[TimedPriceSnapshot] = []
        self._current_prices: Dict[str, PriceSnapshot] = {}

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
        """Parse event from type-based format."""
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

        Only includes snapshots from the same hour as the requested timestamp.
        Hourly markets resolve each hour, so prices from previous hours are stale.
        """
        target_hour = timestamp.hour
        result: Dict[str, PriceSnapshot] = {}
        for snapshot in self._price_snapshots:
            if snapshot.timestamp <= timestamp:
                if snapshot.timestamp.hour == target_hour:
                    result.update(snapshot.prices)
            else:
                break
        return result

    def get_last_prices_for_hour(self, hour: int) -> Dict[str, PriceSnapshot]:
        """Get the latest known prices for a given hour (end-of-hour prices).

        Returns the accumulated prices from ALL snapshots within that hour,
        giving the most accurate view of prices at hour boundary / resolution time.
        This matches what the live runner sees when it resolves at the hour boundary.
        """
        result: Dict[str, PriceSnapshot] = {}
        for snapshot in self._price_snapshots:
            if snapshot.timestamp.hour == hour:
                result.update(snapshot.prices)
            elif snapshot.timestamp.hour > hour or (hour == 23 and snapshot.timestamp.hour == 0):
                break  # Past this hour, stop
        return result
