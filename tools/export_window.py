#!/usr/bin/env python3
"""Export leader activity for a specified ET time window.

Extracts trades, positions, prices, and market metadata into a single
JSON file for offline replay and testing.

Usage:
    python -m tools.export_window \
        --leader 0xf247584e41117bbbe4cc06e4d2c95741792a5216 \
        --date 2026-01-26 \
        --start-et 17:00 \
        --end-et 18:00 \
        --out ./data/leader_window_2026-01-26_17-18ET.json \
        --sample-sec 5 \
        --mode hybrid
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
import statistics

# Try to import zoneinfo (Python 3.9+) or pytz
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as ZoneInfo  # type: ignore

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    print("Error: httpx required. Install with: pip install httpx")
    sys.exit(1)


# =============================================================================
# Constants
# =============================================================================

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class PriceSample:
    """Single price sample at a point in time."""
    t_utc: str  # ISO format
    t_et: str   # ISO format in ET
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_utc": self.t_utc,
            "t_et": self.t_et,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
        }


@dataclass
class TokenPriceSeries:
    """Time series of prices for a token."""
    token_id: str
    condition_id: str
    outcome: str  # "Yes" or "No" -> UP/DOWN
    samples: List[PriceSample] = field(default_factory=list)
    
    # Derived volatility stats
    mid_std_1m: Optional[float] = None
    mid_std_5m: Optional[float] = None
    avg_spread: Optional[float] = None
    max_spread: Optional[float] = None
    max_10s_jump: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "samples": [s.to_dict() for s in self.samples],
            "volatility": {
                "mid_std_1m": self.mid_std_1m,
                "mid_std_5m": self.mid_std_5m,
                "avg_spread": self.avg_spread,
                "max_spread": self.max_spread,
                "max_10s_jump": self.max_10s_jump,
            },
        }


@dataclass
class MarketInfo:
    """Enriched market information."""
    condition_id: str
    title: str
    slug: str
    hour_bucket_et: Optional[str] = None  # e.g., "2026-01-26T17:00"
    tokens: Dict[str, str] = field(default_factory=dict)  # {"UP": token_id, "DOWN": token_id}
    outcomes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "title": self.title,
            "slug": self.slug,
            "hour_bucket_et": self.hour_bucket_et,
            "tokens": self.tokens,
            "outcomes": self.outcomes,
        }


@dataclass
class TradeEvent:
    """A single trade event from the leader."""
    t_utc: str
    t_et: str
    market_id: str  # condition_id
    hour_bucket_et: str
    token_id: str
    outcome: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    shares: float  # Positive for buy, negative for sell
    notional_usd: Optional[float] = None
    trade_price: Optional[float] = None
    # Price context at event time
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    # Raw data reference
    raw_trade_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_utc": self.t_utc,
            "t_et": self.t_et,
            "market_id": self.market_id,
            "hour_bucket_et": self.hour_bucket_et,
            "token_id": self.token_id,
            "outcome": self.outcome,
            "action": self.action,
            "shares": self.shares,
            "notional_usd": self.notional_usd,
            "trade_price": self.trade_price,
            "price_context": {
                "bid": self.bid,
                "ask": self.ask,
                "mid": self.mid,
                "spread": self.spread,
            },
            "raw_trade_id": self.raw_trade_id,
        }


@dataclass
class PositionSnapshot:
    """Position snapshot at a point in time."""
    t_utc: str
    t_et: str
    positions: Dict[str, Dict[str, float]]  # {condition_id: {"UP": shares, "DOWN": shares}}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_utc": self.t_utc,
            "t_et": self.t_et,
            "positions": self.positions,
        }


@dataclass
class DerivedFeatures:
    """Derived features for the window."""
    total_trades: int = 0
    total_buy_trades: int = 0
    total_sell_trades: int = 0
    total_notional_usd: float = 0.0
    unique_markets: int = 0
    trades_per_minute: float = 0.0
    # Per-market burstiness
    market_trade_counts: Dict[str, int] = field(default_factory=dict)
    market_exposure_changes: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "total_buy_trades": self.total_buy_trades,
            "total_sell_trades": self.total_sell_trades,
            "total_notional_usd": self.total_notional_usd,
            "unique_markets": self.unique_markets,
            "trades_per_minute": self.trades_per_minute,
            "market_trade_counts": self.market_trade_counts,
            "market_exposure_changes": self.market_exposure_changes,
        }


@dataclass
class ExportMetadata:
    """Metadata for the export."""
    leader_address: str
    window_start_et: str
    window_end_et: str
    window_start_utc: str
    window_end_utc: str
    sample_interval_sec: int
    mode: str
    export_timestamp: str
    assumptions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# Historical Fetcher
# =============================================================================

class HistoricalFetcher:
    """Fetches historical data from Polymarket APIs."""
    
    def __init__(self, timeout: float = 30.0, rate_limit_delay: float = 0.5):
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self._client = httpx.Client(timeout=timeout)
        self._last_request_time = 0.0
        self._backoff_until = 0.0
    
    def _rate_limit(self) -> None:
        """Apply rate limiting with backoff support."""
        # Check if we're in backoff period
        now = time.time()
        if now < self._backoff_until:
            sleep_time = self._backoff_until - now
            print(f"      Rate limited, waiting {sleep_time:.1f}s...", flush=True)
            time.sleep(sleep_time)
        
        # Normal rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()
    
    def _handle_rate_limit(self, retry_after: int = 5) -> None:
        """Handle rate limit by setting backoff."""
        self._backoff_until = time.time() + retry_after
    
    def fetch_trades(
        self,
        address: str,
        start_ts: int,
        end_ts: int,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch trades for an address within a time window.
        
        Note: Polymarket DATA API returns trades newest-first.
        We fetch until we find trades older than start_ts.
        """
        all_trades = []
        offset = 0
        max_pages = 100  # Safety limit
        retries = 0
        max_retries = 3
        
        for page in range(max_pages):
            self._rate_limit()
            
            params = {
                "user": address,
                "limit": str(limit),
                "offset": str(offset),
            }
            
            try:
                response = self._client.get(
                    f"{DATA_API_BASE}/trades",
                    params=params,
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "10"))
                    print(f"      Rate limited on page {page}, waiting {retry_after}s...", flush=True)
                    self._handle_rate_limit(retry_after)
                    retries += 1
                    if retries <= max_retries:
                        continue  # Retry same page
                    else:
                        print("      Max retries reached, stopping", flush=True)
                        break
                
                response.raise_for_status()
                data = response.json()
                retries = 0  # Reset retry counter on success
                
                if not data:
                    break
                
                found_old = False
                
                # Filter by timestamp
                for trade in data:
                    ts = trade.get("timestamp", 0)
                    # Handle string timestamps (ISO format)
                    if isinstance(ts, str):
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            ts = int(dt.timestamp())
                        except:
                            ts = 0
                    
                    if start_ts <= ts <= end_ts:
                        all_trades.append(trade)
                    elif ts < start_ts:
                        # Trades are ordered newest first
                        found_old = True
                        break
                
                # Stop if we found trades older than our window
                if found_old:
                    break
                
                # Check if we got fewer than limit (last page)
                if len(data) < limit:
                    break
                
                # Progress indicator
                if (page + 1) % 10 == 0:
                    print(f"      ... fetched {offset + len(data)} trades (page {page + 1})", flush=True)
                
                # Next page
                offset += len(data)
                    
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    self._handle_rate_limit(10)
                    retries += 1
                    if retries <= max_retries:
                        continue
                print(f"Error fetching trades (page {page}): {e}", flush=True)
                break
            except Exception as e:
                print(f"Error fetching trades (page {page}): {e}", flush=True)
                break
        
        return all_trades
    
    def fetch_positions(self, address: str) -> List[Dict[str, Any]]:
        """Fetch current positions for an address."""
        self._rate_limit()
        
        try:
            response = self._client.get(
                f"{DATA_API_BASE}/positions",
                params={
                    "user": address,
                    "sizeThreshold": "0",
                    "limit": "500",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching positions: {e}")
            return []
    
    def fetch_market_info(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Fetch market metadata from GAMMA API."""
        self._rate_limit()
        
        try:
            response = self._client.get(
                f"{GAMMA_API_BASE}/markets/{condition_id}",
            )
            response.raise_for_status()
            info = response.json()
            if self._matches_condition_id(info, condition_id):
                return info
        except Exception as e:
            # Try alternate endpoint
            try:
                response = self._client.get(
                    f"{GAMMA_API_BASE}/markets",
                    params={"condition_id": condition_id},
                )
                response.raise_for_status()
                data = response.json()
                if data:
                    info = data[0] if isinstance(data, list) else data
                    if self._matches_condition_id(info, condition_id):
                        return info
            except:
                pass
            print(f"Warning: Could not fetch market info for {condition_id[:20]}...")
            return None

    @staticmethod
    def _matches_condition_id(info: Optional[Dict[str, Any]], condition_id: str) -> bool:
        if not info or not isinstance(info, dict):
            return False
        cid = condition_id.lower()
        for key in ("conditionId", "condition_id", "id"):
            val = info.get(key)
            if isinstance(val, str) and val.lower() == cid:
                return True
        return False
    
    def fetch_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a token (best bid/ask)."""
        self._rate_limit()
        
        try:
            response = self._client.get(
                f"{CLOB_API_BASE}/book",
                params={"token_id": token_id},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return None
    
    def fetch_midpoint(self, token_id: str) -> Optional[float]:
        """Fetch midpoint price for a token."""
        self._rate_limit()
        
        try:
            response = self._client.get(
                f"{CLOB_API_BASE}/midpoint",
                params={"token_id": token_id},
            )
            response.raise_for_status()
            data = response.json()
            mid = data.get("mid")
            return float(mid) if mid is not None else None
        except Exception as e:
            return None
    
    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()


# =============================================================================
# Window Exporter
# =============================================================================

class WindowExporter:
    """Exports leader activity for a time window."""
    
    def __init__(
        self,
        leader_address: str,
        start_et: datetime,
        end_et: datetime,
        sample_interval_sec: int = 5,
        mode: str = "hybrid",
    ):
        self.leader_address = leader_address
        self.start_et = start_et
        self.end_et = end_et
        self.sample_interval_sec = sample_interval_sec
        self.mode = mode
        
        self.fetcher = HistoricalFetcher()
        
        # Data storage
        self.markets: Dict[str, MarketInfo] = {}
        self.events: List[TradeEvent] = []
        self.prices: Dict[str, TokenPriceSeries] = {}
        self.position_snapshots: List[PositionSnapshot] = []
        self.derived: DerivedFeatures = DerivedFeatures()
        
        # Token tracking
        self._known_tokens: Set[str] = set()
        self._token_to_condition: Dict[str, str] = {}
        self._token_to_outcome: Dict[str, str] = {}
    
    def export(self) -> Dict[str, Any]:
        """Run the full export process."""
        print(f"\n{'='*60}", flush=True)
        print(f"HISTORICAL WINDOW EXPORT", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"Leader: {self.leader_address[:20]}...", flush=True)
        print(f"Window: {self.start_et.strftime('%Y-%m-%d %H:%M')} ET", flush=True)
        print(f"     to {self.end_et.strftime('%Y-%m-%d %H:%M')} ET", flush=True)
        print(f"Mode: {self.mode}", flush=True)
        print(f"Sample interval: {self.sample_interval_sec}s", flush=True)
        print(f"{'='*60}\n", flush=True)
        
        # Convert to UTC timestamps
        start_utc = self.start_et.astimezone(UTC)
        end_utc = self.end_et.astimezone(UTC)
        start_ts = int(start_utc.timestamp())
        end_ts = int(end_utc.timestamp())
        
        # Step 1: Fetch trades
        print("[1/6] Fetching trades...", flush=True)
        raw_trades = self.fetcher.fetch_trades(
            self.leader_address,
            start_ts,
            end_ts,
        )
        print(f"      Found {len(raw_trades)} trades in window", flush=True)
        
        # Step 2: Process trades and discover markets/tokens
        print("[2/6] Processing trades and discovering markets...", flush=True)
        self._process_trades(raw_trades)
        print(f"      Discovered {len(self.markets)} markets, {len(self._known_tokens)} tokens", flush=True)
        
        # Step 3: Enrich markets with metadata
        print("[3/6] Enriching market metadata...", flush=True)
        self._enrich_markets()
        
        # Step 4: Fetch position snapshots
        print("[4/6] Fetching position snapshots...", flush=True)
        self._fetch_position_snapshots()
        
        # Step 5: Sample prices (if we have tokens)
        if self._known_tokens and self.mode in ("hybrid", "prices"):
            print(f"[5/6] Sampling prices ({len(self._known_tokens)} tokens)...", flush=True)
            self._sample_prices()
        else:
            print("[5/6] Skipping price sampling (no tokens or mode=trades)", flush=True)
        
        # Step 6: Compute derived features
        print("[6/6] Computing derived features...", flush=True)
        self._compute_derived_features()
        
        # Build output
        print("\nBuilding output...", flush=True)
        return self._build_output(start_utc, end_utc)
    
    def _process_trades(self, raw_trades: List[Dict[str, Any]]) -> None:
        """Process raw trades into structured events."""
        for trade in raw_trades:
            try:
                # Extract fields
                condition_id = trade.get("conditionId") or trade.get("condition_id", "")
                token_id = trade.get("asset") or trade.get("token_id", "")
                outcome = trade.get("outcome", "")
                side = trade.get("side", "").upper()  # BUY or SELL
                size = float(trade.get("size", 0))
                price = trade.get("price")
                timestamp = trade.get("timestamp")
                trade_id = trade.get("id") or trade.get("trade_id")
                
                # Skip invalid
                if not condition_id or not token_id:
                    continue
                
                # Parse timestamp
                if isinstance(timestamp, str):
                    try:
                        dt_utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except:
                        continue
                else:
                    dt_utc = datetime.fromtimestamp(timestamp, tz=UTC)
                
                dt_et = dt_utc.astimezone(ET)
                
                # Determine action and outcome
                action = "BUY" if side == "BUY" else "SELL"
                outcome_normalized = "UP" if outcome.upper() in ("YES", "UP") else "DOWN"
                shares = size if action == "BUY" else -size
                
                # Calculate notional
                notional = None
                if price is not None:
                    notional = abs(size * float(price))
                
                # Hour bucket
                hour_bucket = dt_et.replace(minute=0, second=0, microsecond=0)
                
                # Track token
                self._known_tokens.add(token_id)
                self._token_to_condition[token_id] = condition_id
                self._token_to_outcome[token_id] = outcome_normalized
                
                # Create or update market
                if condition_id not in self.markets:
                    self.markets[condition_id] = MarketInfo(
                        condition_id=condition_id,
                        title=trade.get("title", "Unknown"),
                        slug=trade.get("slug", ""),
                        hour_bucket_et=hour_bucket.isoformat(),
                    )
                
                # Update market tokens
                market = self.markets[condition_id]
                market.tokens[outcome_normalized] = token_id
                if outcome not in market.outcomes:
                    market.outcomes.append(outcome)
                
                # Create event
                event = TradeEvent(
                    t_utc=dt_utc.isoformat(),
                    t_et=dt_et.isoformat(),
                    market_id=condition_id,
                    hour_bucket_et=hour_bucket.isoformat(),
                    token_id=token_id,
                    outcome=outcome_normalized,
                    action=action,
                    shares=shares,
                    notional_usd=notional,
                    trade_price=float(price) if price else None,
                    raw_trade_id=trade_id,
                )
                self.events.append(event)
                
            except Exception as e:
                print(f"Warning: Error processing trade: {e}")
                continue
        
        # Sort events by time
        self.events.sort(key=lambda e: e.t_utc)
    
    def _enrich_markets(self) -> None:
        """Enrich markets with metadata from GAMMA API."""
        for condition_id, market in self.markets.items():
            info = self.fetcher.fetch_market_info(condition_id)
            if info:
                market.title = info.get("question") or info.get("title") or market.title
                market.slug = info.get("slug") or market.slug
                
                # Extract token mappings if available
                tokens = info.get("tokens") or []
                for token in tokens:
                    tid = token.get("token_id")
                    outcome = token.get("outcome", "")
                    if tid and outcome:
                        outcome_norm = "UP" if outcome.upper() in ("YES", "UP") else "DOWN"
                        market.tokens[outcome_norm] = tid
                        self._known_tokens.add(tid)
                        self._token_to_condition[tid] = condition_id
                        self._token_to_outcome[tid] = outcome_norm
    
    def _fetch_position_snapshots(self) -> None:
        """Fetch position snapshots at window boundaries."""
        # For historical data, we can only get current positions
        # This is a limitation - for true historical positions,
        # we'd need to reconstruct from trades
        
        positions = self.fetcher.fetch_positions(self.leader_address)
        if positions:
            now = datetime.now(UTC)
            now_et = now.astimezone(ET)
            
            pos_dict: Dict[str, Dict[str, float]] = {}
            for pos in positions:
                cid = pos.get("conditionId") or pos.get("condition_id", "")
                outcome = pos.get("outcome", "")
                size = float(pos.get("size", 0))
                
                if cid:
                    if cid not in pos_dict:
                        pos_dict[cid] = {"UP": 0.0, "DOWN": 0.0}
                    
                    outcome_norm = "UP" if outcome.upper() in ("YES", "UP") else "DOWN"
                    pos_dict[cid][outcome_norm] = size
            
            self.position_snapshots.append(PositionSnapshot(
                t_utc=now.isoformat(),
                t_et=now_et.isoformat(),
                positions=pos_dict,
            ))
    
    def _sample_prices(self) -> None:
        """Sample prices for all tokens.
        
        Note: For historical windows, CLOB only provides current prices.
        This samples current state - for true historical prices,
        would need a historical price feed.
        """
        for token_id in self._known_tokens:
            condition_id = self._token_to_condition.get(token_id, "")
            outcome = self._token_to_outcome.get(token_id, "")
            
            series = TokenPriceSeries(
                token_id=token_id,
                condition_id=condition_id,
                outcome=outcome,
            )
            
            # Sample current price (historical would require different API)
            now = datetime.now(UTC)
            now_et = now.astimezone(ET)
            
            # Try orderbook first for bid/ask
            book = self.fetcher.fetch_orderbook(token_id)
            if book:
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                
                best_bid = float(bids[0]["price"]) if bids else None
                best_ask = float(asks[0]["price"]) if asks else None
                
                mid = None
                spread = None
                if best_bid and best_ask:
                    mid = (best_bid + best_ask) / 2
                    spread = best_ask - best_bid
                
                series.samples.append(PriceSample(
                    t_utc=now.isoformat(),
                    t_et=now_et.isoformat(),
                    bid=best_bid,
                    ask=best_ask,
                    mid=mid,
                    spread=spread,
                ))
            else:
                # Fallback to midpoint
                mid = self.fetcher.fetch_midpoint(token_id)
                if mid is not None:
                    series.samples.append(PriceSample(
                        t_utc=now.isoformat(),
                        t_et=now_et.isoformat(),
                        mid=mid,
                    ))
            
            # Compute volatility stats from samples
            self._compute_price_volatility(series)
            
            self.prices[token_id] = series
    
    def _compute_price_volatility(self, series: TokenPriceSeries) -> None:
        """Compute volatility statistics for a price series."""
        if not series.samples:
            return
        
        mids = [s.mid for s in series.samples if s.mid is not None]
        spreads = [s.spread for s in series.samples if s.spread is not None]
        
        if len(mids) >= 2:
            # Compute returns
            returns = [(mids[i] - mids[i-1]) / mids[i-1] if mids[i-1] != 0 else 0 
                       for i in range(1, len(mids))]
            
            if returns:
                series.mid_std_1m = statistics.stdev(returns) if len(returns) > 1 else 0
                series.mid_std_5m = series.mid_std_1m  # Same for single sample
        
        if spreads:
            series.avg_spread = statistics.mean(spreads)
            series.max_spread = max(spreads)
        
        # Max 10s jump (need multiple samples)
        if len(mids) >= 2:
            jumps = [abs(mids[i] - mids[i-1]) for i in range(1, len(mids))]
            series.max_10s_jump = max(jumps) if jumps else 0
    
    def _compute_derived_features(self) -> None:
        """Compute aggregate derived features."""
        self.derived.total_trades = len(self.events)
        self.derived.total_buy_trades = sum(1 for e in self.events if e.action == "BUY")
        self.derived.total_sell_trades = sum(1 for e in self.events if e.action == "SELL")
        self.derived.total_notional_usd = sum(
            e.notional_usd or 0 for e in self.events
        )
        self.derived.unique_markets = len(self.markets)
        
        # Trades per minute
        if self.events:
            window_minutes = (self.end_et - self.start_et).total_seconds() / 60
            if window_minutes > 0:
                self.derived.trades_per_minute = len(self.events) / window_minutes
        
        # Per-market stats
        for event in self.events:
            mid = event.market_id
            self.derived.market_trade_counts[mid] = (
                self.derived.market_trade_counts.get(mid, 0) + 1
            )
            self.derived.market_exposure_changes[mid] = (
                self.derived.market_exposure_changes.get(mid, 0) + abs(event.shares)
            )
    
    def _build_output(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Dict[str, Any]:
        """Build the final output JSON structure."""
        
        # Add price context to events
        for event in self.events:
            if event.token_id in self.prices:
                series = self.prices[event.token_id]
                if series.samples:
                    # Use closest sample (for now, just use first)
                    sample = series.samples[0]
                    event.bid = sample.bid
                    event.ask = sample.ask
                    event.mid = sample.mid
                    event.spread = sample.spread
        
        metadata = ExportMetadata(
            leader_address=self.leader_address,
            window_start_et=self.start_et.isoformat(),
            window_end_et=self.end_et.isoformat(),
            window_start_utc=start_utc.isoformat(),
            window_end_utc=end_utc.isoformat(),
            sample_interval_sec=self.sample_interval_sec,
            mode=self.mode,
            export_timestamp=datetime.now(UTC).isoformat(),
            assumptions=[
                "Trades fetched from public DATA API",
                "Prices are current snapshots (not historical)",
                "Position snapshots are current state only",
                "Token mappings derived from trade data and GAMMA API",
            ],
        )
        
        return {
            "metadata": metadata.to_dict(),
            "markets": {k: v.to_dict() for k, v in self.markets.items()},
            "events": [e.to_dict() for e in self.events],
            "prices": {k: v.to_dict() for k, v in self.prices.items()},
            "position_snapshots": [p.to_dict() for p in self.position_snapshots],
            "derived_features": self.derived.to_dict(),
        }
    
    def close(self) -> None:
        """Cleanup resources."""
        self.fetcher.close()


# =============================================================================
# CLI
# =============================================================================

def parse_time_et(date_str: str, time_str: str) -> datetime:
    """Parse date and time string into ET datetime."""
    dt_str = f"{date_str} {time_str}"
    dt_naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    return dt_naive.replace(tzinfo=ET)


def main():
    parser = argparse.ArgumentParser(
        description="Export leader activity for a time window (ET)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tools.export_window --leader 0xf247...5216 --date 2026-01-26 --start-et 17:00 --end-et 18:00 --out data/window.json
  python -m tools.export_window --leader 0xf247...5216 --date 2026-01-26 --start-et 17:00 --end-et 18:00 --sample-sec 5 --mode hybrid
        """,
    )
    
    parser.add_argument(
        "--leader",
        required=True,
        help="Leader wallet address (0x...)",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--start-et",
        required=True,
        help="Start time in HH:MM format (ET)",
    )
    parser.add_argument(
        "--end-et",
        required=True,
        help="End time in HH:MM format (ET)",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="Output JSON file path (default: stdout)",
    )
    parser.add_argument(
        "--sample-sec",
        type=int,
        default=5,
        help="Price sampling interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--mode",
        choices=["trades", "prices", "hybrid"],
        default="hybrid",
        help="Export mode: trades (only trades), prices (only prices), hybrid (both)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    
    args = parser.parse_args()
    
    # Parse times
    try:
        start_et = parse_time_et(args.date, args.start_et)
        end_et = parse_time_et(args.date, args.end_et)
    except ValueError as e:
        print(f"Error parsing date/time: {e}")
        print("Use format: --date YYYY-MM-DD --start-et HH:MM --end-et HH:MM")
        return 1
    
    # Validate window
    if end_et <= start_et:
        print("Error: end time must be after start time")
        return 1
    
    # Create output directory if needed
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
    
    # Run export
    exporter = WindowExporter(
        leader_address=args.leader,
        start_et=start_et,
        end_et=end_et,
        sample_interval_sec=args.sample_sec,
        mode=args.mode,
    )
    
    try:
        result = exporter.export()
        
        # Output
        indent = 2 if args.pretty else None
        json_str = json.dumps(result, indent=indent, default=str)
        
        if args.out:
            args.out.write_text(json_str)
            print(f"\nExported to: {args.out}")
            print(f"  - {len(result['events'])} events")
            print(f"  - {len(result['markets'])} markets")
            print(f"  - {len(result['prices'])} price series")
        else:
            print(json_str)
        
        return 0
        
    except Exception as e:
        print(f"Error during export: {e}")
        import traceback
        traceback.print_exc()
        return 1
        
    finally:
        exporter.close()


if __name__ == "__main__":
    sys.exit(main())
