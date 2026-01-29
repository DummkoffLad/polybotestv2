"""Data collector for leader activity and market data.

Collects:
- Leader activity timeline (trades with timestamps)
- Leader position snapshots (periodic)
- Market metadata
- Price snapshots (for realistic simulation)

Storage: JSONL with file rotation and active-market filtering.
Designed for ~2-3 MB/hour to avoid the 23 GB problem.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import time

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

if TYPE_CHECKING:
    from ..config import BotConfig
    from ..data.live_source import LiveDataSource


class DataCollector:
    """Collects data for replay simulation.

    Records leader positions, trades, and market prices in JSONL format
    compatible with the Replayer (src/simulator/replayer.py).

    Storage safeguards:
    - Only records markets with meaningful exposure (configurable threshold)
    - Deduplicates trades (only writes NEW trades each cycle)
    - Rotates files at configurable size limit
    - Prices fetched only for active market tokens
    """

    def __init__(self, config: "BotConfig", data_source: "LiveDataSource"):
        self.config = config
        self.data_source = data_source
        self._running = False

        # Storage path
        self.storage_path = config.data_dir / config.collector.storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Output files (one per type)
        self._leader_file: Optional[Any] = None
        self._price_file: Optional[Any] = None
        self._market_file: Optional[Any] = None

        # File size tracking for rotation
        self._leader_file_bytes: int = 0
        self._price_file_bytes: int = 0
        self._max_file_bytes: int = config.collector.max_file_size_mb * 1024 * 1024

        # Dedicated HTTP client for parallel price fetching
        self._price_client = httpx.Client(timeout=5.0) if HAS_HTTPX else None
        self._price_executor = ThreadPoolExecutor(max_workers=20)

        # Timing
        self._last_position_snapshot = 0.0
        self._last_price_snapshot = 0.0
        self._last_trade_fetch = 0.0
        self._trade_fetch_interval = 3.0  # trades only need fetching every 3s
        self._last_dedup_prune = 0.0

        # Trade deduplication (same pattern as runner.py _check_trade_trigger)
        self._seen_trade_keys: set = set()

        # Market metadata tracking
        self._known_market_keys: set = set()

        # Active markets: condition_id -> last trade unix timestamp
        # Markets are "active" if they had a trade in the last hour
        self._market_last_trade_ts: Dict[str, int] = {}
        self._active_condition_ids: set = set()

        # Session state for file naming
        self._session_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._file_sequence = 0

        # Stats
        self._total_position_records = 0
        self._total_trade_records = 0
        self._total_price_records = 0
        self._total_new_trades = 0

    def run(self) -> None:
        """Run the collector loop."""
        self._running = True
        self._open_files()

        coll = self.config.collector
        print(f"[MODE=COLLECTOR] Data collector started")
        print(f"[MODE=COLLECTOR] Output: {self.storage_path}")
        print(f"[MODE=COLLECTOR] Max file size: {coll.max_file_size_mb} MB")
        print(f"[MODE=COLLECTOR] Min exposure filter: ${coll.min_exposure_dollars}")
        print(f"[MODE=COLLECTOR] Position interval: {coll.position_snapshot_interval_sec}s")
        print(f"[MODE=COLLECTOR] Price interval: {coll.price_snapshot_interval_sec}s")
        print(f"[MODE=COLLECTOR] Poll interval: {self.config.leader.poll_interval_sec}s")

        # Initial market discovery via first snapshot
        self._collect_leader_positions()
        self._record_discovered_metadata()

        try:
            while self._running:
                self._collect_cycle()
                time.sleep(self.config.leader.poll_interval_sec)
        except KeyboardInterrupt:
            print("\n[MODE=COLLECTOR] Interrupted by user")
        finally:
            self._close_files()
            self._print_summary()

    def stop(self) -> None:
        """Stop the collector."""
        self._running = False

    def _open_files(self) -> None:
        """Open output files for current sequence."""
        suffix = f"_{self._file_sequence}" if self._file_sequence > 0 else ""
        ts = self._session_ts

        self._leader_file = open(
            self.storage_path / f"leader_{ts}{suffix}.jsonl", "a"
        )
        self._price_file = open(
            self.storage_path / f"prices_{ts}{suffix}.jsonl", "a"
        )
        self._market_file = open(
            self.storage_path / f"markets_{ts}{suffix}.jsonl", "a"
        )
        self._leader_file_bytes = 0
        self._price_file_bytes = 0

    def _collect_cycle(self) -> None:
        """Run a single collection cycle.

        Priorities: prices first (every 1s), trades (every 3s), positions (every 5s).
        Prices are fetched in parallel to keep cycle time under 1 second.
        """
        now = time.time()

        # Price snapshots — highest priority, every cycle if interval elapsed
        if self.config.collector.record_prices:
            if now - self._last_price_snapshot >= self.config.collector.price_snapshot_interval_sec:
                self._collect_prices()
                self._last_price_snapshot = now

        # Leader trades — every 3 seconds (deduplicated, no need for every cycle)
        if now - self._last_trade_fetch >= self._trade_fetch_interval:
            self._collect_leader_trades()
            self._last_trade_fetch = now

        # Position snapshots at interval
        if now - self._last_position_snapshot >= self.config.collector.position_snapshot_interval_sec:
            self._collect_leader_positions()
            self._last_position_snapshot = now

        # Prune dedup set every 10 minutes to prevent unbounded growth
        if now - self._last_dedup_prune >= 600:
            self._prune_seen_trades()
            self._last_dedup_prune = now

    def _collect_leader_trades(self) -> None:
        """Collect NEW leader trades only (deduplicated).

        Also tracks which markets have recent trade activity
        to determine the active market set for position/price recording.
        """
        try:
            trades = self.data_source.fetch_trades(
                self.data_source.leader_address,
                limit=self.config.collector.trade_lookback_limit,
            )
        except Exception as e:
            print(f"[MODE=COLLECTOR] Error fetching trades: {e}")
            return

        # Track ALL fetched trades for active-market detection
        # (even already-seen trades tell us the market is active)
        for trade in trades:
            cid = trade.condition_id
            ts = trade.timestamp
            if cid and ts:
                prev = self._market_last_trade_ts.get(cid, 0)
                if ts > prev:
                    self._market_last_trade_ts[cid] = ts

        # Update active condition IDs (traded in last hour)
        cutoff = int(time.time()) - 3600
        self._active_condition_ids = {
            cid for cid, ts in self._market_last_trade_ts.items() if ts >= cutoff
        }

        # Deduplicate and write only new trades
        new_trades = []
        for trade in trades:
            tx_hash = trade.transaction_hash or ""
            trade_ts = trade.timestamp

            if tx_hash:
                key = f"{tx_hash}_{trade_ts}"
            else:
                key = f"{trade.condition_id}_{trade_ts}_{trade.side}_{trade.size}_{trade.price}"

            if key not in self._seen_trade_keys:
                self._seen_trade_keys.add(key)
                new_trades.append(trade.to_dict())

        if new_trades:
            record = {
                "type": "leader_trades",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trades": new_trades,
            }
            self._write_record(self._leader_file, record, "leader")
            self._total_trade_records += 1
            self._total_new_trades += len(new_trades)

            # Check for new market metadata from trades
            self._check_new_markets()

    def _collect_leader_positions(self) -> None:
        """Collect leader position snapshot.

        Filters to markets that had trades in the last hour.
        If no trade data yet (first cycle), falls back to exposure threshold.
        """
        now = datetime.now(timezone.utc)
        try:
            snapshot = self.data_source.build_leader_snapshot(now)
        except Exception as e:
            print(f"[MODE=COLLECTOR] Error fetching positions: {e}")
            return

        filtered_positions: Dict[str, Dict[str, str]] = {}

        if self._active_condition_ids:
            # Primary filter: markets with trades in last hour
            for market_id, exposure in snapshot.exposures.items():
                if market_id in self._active_condition_ids:
                    filtered_positions[market_id] = {
                        "up_shares": str(exposure.up_shares),
                        "up_dollars": str(exposure.up_dollars),
                        "down_shares": str(exposure.down_shares),
                        "down_dollars": str(exposure.down_dollars),
                    }
        else:
            # Fallback for first cycle before trade data is available:
            # use exposure threshold
            min_exposure = Decimal(str(self.config.collector.min_exposure_dollars))
            for market_id, exposure in snapshot.exposures.items():
                if exposure.gross_dollars >= min_exposure:
                    filtered_positions[market_id] = {
                        "up_shares": str(exposure.up_shares),
                        "up_dollars": str(exposure.up_dollars),
                        "down_shares": str(exposure.down_shares),
                        "down_dollars": str(exposure.down_dollars),
                    }

        record = {
            "type": "leader_positions",
            "timestamp": now.isoformat(),
            "positions": filtered_positions,
            "total_assets": str(snapshot.total_assets),
        }
        self._write_record(self._leader_file, record, "leader")
        self._total_position_records += 1

        # Check for new market metadata
        self._check_new_markets()

    def _collect_prices(self) -> None:
        """Collect bid/ask prices for tokens in active markets only.

        Uses parallel HTTP requests to fetch all token prices concurrently,
        keeping cycle time well under 1 second regardless of token count.
        """
        if not self._active_condition_ids or not self._price_client:
            return

        discovered = self.data_source.get_discovered_markets()

        # Build list of tokens to fetch
        tokens_to_fetch: List[str] = []
        for key, market in discovered.items():
            if market.condition_id in self._active_condition_ids:
                tokens_to_fetch.append(market.token_id)

        if not tokens_to_fetch:
            return

        # Fetch all prices in parallel
        markets_data: Dict[str, Dict[str, str]] = {}
        futures = {
            self._price_executor.submit(self._fetch_book, token_id): token_id
            for token_id in tokens_to_fetch
        }

        for future in as_completed(futures):
            token_id = futures[future]
            try:
                bid, ask = future.result()
                price_entry: Dict[str, str] = {}
                if bid is not None:
                    price_entry["bid"] = str(bid)
                if ask is not None:
                    price_entry["ask"] = str(ask)
                if bid is not None and ask is not None:
                    midpoint = (bid + ask) / Decimal("2")
                    price_entry["midpoint"] = str(midpoint)
                if price_entry:
                    markets_data[token_id] = price_entry
            except Exception:
                continue

        if markets_data:
            record = {
                "type": "prices",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "markets": markets_data,
            }
            self._write_record(self._price_file, record, "price")
            self._total_price_records += 1

    def _fetch_book(self, token_id: str) -> tuple:
        """Fetch best bid/ask for a single token (thread-safe, no rate limiter)."""
        try:
            url = "https://clob.polymarket.com/book"
            response = self._price_client.get(url, params={"token_id": token_id})
            response.raise_for_status()
            data = response.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            best_bid = Decimal(bids[0]["price"]) if bids else None
            best_ask = Decimal(asks[0]["price"]) if asks else None

            return best_bid, best_ask
        except Exception:
            return None, None

    def _record_discovered_metadata(self) -> None:
        """Record metadata for all currently discovered markets."""
        discovered = self.data_source.get_discovered_markets()
        for key, market in discovered.items():
            if key not in self._known_market_keys:
                self._record_market_metadata(key, market)

    def _check_new_markets(self) -> None:
        """Check if any new markets have been discovered and record metadata."""
        discovered = self.data_source.get_discovered_markets()
        for key, market in discovered.items():
            if key not in self._known_market_keys:
                self._record_market_metadata(key, market)

    def _record_market_metadata(self, key: str, market: Any) -> None:
        """Record a single market's metadata."""
        self._known_market_keys.add(key)
        record = {
            "type": "market_metadata",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market": market.to_dict(),
        }
        self._write_record(self._market_file, record, "market")

    def _prune_seen_trades(self) -> None:
        """Remove old entries from dedup set to prevent memory growth."""
        cutoff = int(time.time()) - 3600  # 1 hour ago
        to_remove = set()
        for key in self._seen_trade_keys:
            # Keys contain timestamps - try to extract and check age
            parts = key.split("_")
            for part in parts:
                try:
                    ts = int(part)
                    # Unix timestamps are 10 digits (since ~2001)
                    if 1_000_000_000 < ts < 2_000_000_000 and ts < cutoff:
                        to_remove.add(key)
                        break
                except ValueError:
                    continue
        if to_remove:
            self._seen_trade_keys -= to_remove

    def _write_record(self, file: Any, record: Dict, file_type: str) -> None:
        """Write a record to a file, rotating if size limit exceeded."""
        if file is None:
            return

        json_str = json.dumps(record, default=str)
        byte_len = len(json_str.encode("utf-8")) + 1  # +1 for newline

        # Check rotation for leader/price files
        if file_type == "leader":
            self._leader_file_bytes += byte_len
            if self._leader_file_bytes > self._max_file_bytes:
                self._rotate_files()
                file = self._leader_file
        elif file_type == "price":
            self._price_file_bytes += byte_len
            if self._price_file_bytes > self._max_file_bytes:
                self._rotate_files()
                file = self._price_file

        file.write(json_str + "\n")
        file.flush()

    def _rotate_files(self) -> None:
        """Close current files and open new ones with incremented suffix."""
        self._close_files()
        self._file_sequence += 1
        self._open_files()
        print(f"[MODE=COLLECTOR] Rotated files (sequence {self._file_sequence})")

    def _close_files(self) -> None:
        """Close all output files and clean up resources."""
        for f in [self._leader_file, self._price_file, self._market_file]:
            if f:
                try:
                    f.close()
                except Exception:
                    pass

        self._leader_file = None
        self._price_file = None
        self._market_file = None

        # Clean up price fetching resources
        if self._price_client:
            try:
                self._price_client.close()
            except Exception:
                pass
        self._price_executor.shutdown(wait=False)

    def _print_summary(self) -> None:
        """Print collection summary."""
        print(f"\n[MODE=COLLECTOR] Collection Summary:")
        print(f"  Position snapshots: {self._total_position_records}")
        print(f"  Trade records: {self._total_trade_records} ({self._total_new_trades} unique trades)")
        print(f"  Price snapshots: {self._total_price_records}")
        print(f"  Markets discovered: {len(self._known_market_keys)}")
        print(f"  Active markets: {len(self._active_condition_ids)}")
        print(f"  Trade dedup keys: {len(self._seen_trade_keys)}")
        print(f"  File sequence: {self._file_sequence}")
        print(f"  Output: {self.storage_path}")
        print(f"[MODE=COLLECTOR] Data collector stopped")
