"""WebSocket price feed from Polymarket CLOB."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Set

try:
    from websockets.asyncio.client import connect as ws_connect
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    ws_connect = None

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
STALE_SEC = 30.0


@dataclass
class PriceEntry:
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    timestamp: float = 0.0


class WebSocketPriceService:
    """Real-time price feed via WebSocket."""

    def __init__(self, ws_url: str = WS_URL):
        self.ws_url = ws_url
        self._prices: Dict[str, PriceEntry] = {}
        self._subscribed: Set[str] = set()
        self._pending: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._connected = False
        self._lock = threading.Lock()
        self._reconnect_count = 0
        self._max_backoff = 60  # Max seconds between reconnect attempts

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ws-price")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    def subscribe(self, token_id: str) -> None:
        with self._lock:
            if token_id not in self._subscribed:
                self._subscribed.add(token_id)
                self._pending.append(token_id)

    def subscribe_many(self, token_ids: List[str]) -> None:
        for tid in token_ids:
            self.subscribe(tid)

    def get_prices(self, token_id: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Get (bid, ask) for token."""
        entry = self._prices.get(token_id)
        if not entry or time.time() - entry.timestamp > STALE_SEC:
            return None, None
        return entry.bid, entry.ask

    def _run_loop(self) -> None:
        """Run the WebSocket event loop with automatic restart on failure."""
        backoff = 2  # Start with 2 second backoff

        while self._running:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._ws_main())
            except Exception as e:
                if self._running:
                    self._reconnect_count += 1
                    if self._reconnect_count <= 3:
                        logger.debug(f"WS loop error (attempt {self._reconnect_count}): {e}")
                    else:
                        logger.warning(f"WS loop error (attempt {self._reconnect_count}): {e}")
            finally:
                try:
                    self._loop.close()
                except:
                    pass

            # If still running, wait before retry with exponential backoff
            if self._running:
                time.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)

    async def _ws_main(self) -> None:
        if not HAS_WEBSOCKETS:
            logger.error("websockets>=16.0 not installed, price feed disabled")
            return

        backoff = 2  # Start with 2 second backoff

        while self._running:
            try:
                async with ws_connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self._connected = True
                    self._reconnect_count = 0  # Reset on successful connect
                    backoff = 2  # Reset backoff on success
                    logger.info("WS connected to Polymarket")
                    # On every (re)connect, resubscribe ALL known tokens
                    await self._resubscribe_all(ws)
                    await self._flush_pending(ws)
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            self._handle(json.loads(raw))
                        except:
                            pass
                        await self._flush_pending(ws)
            except Exception as e:
                self._connected = False
                self._reconnect_count += 1
                if self._reconnect_count <= 3:
                    logger.debug(f"WS disconnected (attempt {self._reconnect_count}): {e}")
                else:
                    logger.warning(f"WS reconnect attempt {self._reconnect_count}: {e}")
                if self._running:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._max_backoff)

    async def _resubscribe_all(self, ws) -> None:
        """Resubscribe all known tokens after reconnect."""
        with self._lock:
            all_tokens = list(self._subscribed)
        if all_tokens:
            try:
                await ws.send(json.dumps({"type": "market", "assets_ids": all_tokens}))
                logger.info(f"Resubscribed {len(all_tokens)} tokens after reconnect")
            except Exception as e:
                logger.error(f"Resubscribe failed: {e}")

    async def _flush_pending(self, ws) -> None:
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
        if pending:
            try:
                await ws.send(json.dumps({"type": "market", "assets_ids": pending}))
            except:
                with self._lock:
                    self._pending.extend(pending)

    def _handle(self, msg) -> None:
        now = time.time()
        if isinstance(msg, list):
            for item in msg:
                if isinstance(item, dict):
                    self._parse_book(item, now)
        elif isinstance(msg, dict):
            for change in msg.get("price_changes", []):
                if isinstance(change, dict):
                    self._parse_change(change, now)

    def _parse_book(self, item: dict, now: float) -> None:
        aid = item.get("asset_id", "")
        if not aid:
            return
        bids = item.get("bids", [])
        asks = item.get("asks", [])
        bid = max((Decimal(b["price"]) for b in bids if "price" in b), default=None) if bids else None
        ask = min((Decimal(a["price"]) for a in asks if "price" in a), default=None) if asks else None
        if bid or ask:
            # Full book snapshot replaces everything
            entry = PriceEntry(bid=bid, ask=ask, timestamp=now)
            if not self._validate_spread(entry):
                logger.warning(f"Book snapshot has bid > ask for {aid}: bid={bid} ask={ask}")
                return
            self._prices[aid] = entry

    def _parse_change(self, change: dict, now: float) -> None:
        aid = change.get("asset_id", "")
        if not aid:
            return
        new_bid = Decimal(change["best_bid"]) if change.get("best_bid") else None
        new_ask = Decimal(change["best_ask"]) if change.get("best_ask") else None
        if not new_bid and not new_ask:
            return
        # Merge with existing entry instead of replacing
        existing = self._prices.get(aid)
        bid = new_bid if new_bid is not None else (existing.bid if existing else None)
        ask = new_ask if new_ask is not None else (existing.ask if existing else None)
        entry = PriceEntry(bid=bid, ask=ask, timestamp=now)
        if not self._validate_spread(entry):
            logger.warning(f"Price change has bid > ask for {aid}: bid={bid} ask={ask}")
            return
        self._prices[aid] = entry

    @staticmethod
    def _validate_spread(entry: PriceEntry) -> bool:
        """Return False if bid > ask (crossed book)."""
        if entry.bid is not None and entry.ask is not None:
            return entry.bid <= entry.ask
        return True
