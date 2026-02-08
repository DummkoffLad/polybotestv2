"""Session Recorder - records events for replay."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, TextIO, Callable, List, Set

from ..data.models import MarketEvent, LeaderTrade, PriceSnapshot
from ..strategies.base import TradeDecision, DecisionAction

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "value"):
            return obj.value
        return super().default(obj)


class SessionRecorder:
    """Records events for later replay.
    
    Supports recording:
    - Leader trade events with prices at trade time
    - Periodic price snapshots for all subscribed markets
    """
    
    def __init__(self, output_dir: Path = Path("data/sessions"),
                 price_snapshot_interval_sec: float = 2.0):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = ""
        self.file: Optional[TextIO] = None
        self.sequence = 0
        self.start_time: Optional[datetime] = None

        # Hour-splitting: per-hour files alongside continuous
        self._hour_file: Optional[TextIO] = None
        self._current_hour: Optional[int] = None
        self._session_time_name: str = ""
        self._day_path: Path = Path()

        # Price snapshot config
        self.price_snapshot_interval_sec = price_snapshot_interval_sec
        self._last_price_snapshot: float = 0.0
        self._subscribed_tokens: Set[str] = set()  # Tokens we're tracking
        self._price_service = None  # Will be set by runner
    
    def start_session(self, config: Dict[str, Any], strategy_name: str = "") -> str:
        self.start_time = datetime.now(timezone.utc)
        # New format: data/sessions/YYYY-MM-DD/HH-MM.jsonl
        day_dir = self.start_time.strftime("%Y-%m-%d")
        time_name = self.start_time.strftime("%H-%M")
        self.session_id = f"{day_dir}/{time_name}"
        self.sequence = 0

        # Create day directory if needed
        day_path = self.output_dir / day_dir
        day_path.mkdir(parents=True, exist_ok=True)
        filepath = day_path / f"{time_name}.jsonl"
        self.file = open(filepath, "w", encoding="utf-8")

        # Store for hour file naming
        self._session_time_name = time_name
        self._day_path = day_path
        self._current_hour = None
        self._hour_file = None

        self._write({"type": "session_start", "timestamp": self.start_time.isoformat(),
                     "session_id": self.session_id, "config": self._clean(config)})
        return self.session_id
    
    def record_event(self, event: MarketEvent, decision: Optional[TradeDecision] = None) -> None:
        if not self.file:
            return
        self.sequence += 1

        has_bid = event.prices.bid is not None and event.prices.bid > 0
        has_ask = event.prices.ask is not None and event.prices.ask > 0
        if not has_bid or not has_ask:
            logger.warning(
                f"Recording event seq={self.sequence} with missing prices "
                f"(bid={event.prices.bid}, ask={event.prices.ask}) — "
                f"this event will be unusable for accurate replay"
            )
        elif event.prices.bid > event.prices.ask:
            logger.warning(
                f"Recording event seq={self.sequence} with crossed book "
                f"(bid={event.prices.bid} > ask={event.prices.ask})"
            )

        record = {
            "type": "leader_trade",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self.sequence,
            "leader_trade": {
                "timestamp": event.trade.timestamp.isoformat(),
                "market_id": event.trade.market_id,
                "token_id": event.trade.token_id,
                "side": event.trade.side.value,
                "action": event.trade.action.value,
                "leader_dollars": str(event.trade.dollars),
                "leader_price": str(event.trade.price),
                "leader_shares": str(event.trade.shares),
                "source": event.trade.source,
                "tx_hash": event.trade.tx_hash,
            },
            "price_context": {
                "token_id": event.prices.token_id,
                "bid": str(event.prices.bid) if event.prices.bid else None,
                "ask": str(event.prices.ask) if event.prices.ask else None,
                "spread_pct": str(event.prices.spread_pct) if event.prices.spread_pct else None,
            },
            "has_real_prices": has_bid and has_ask,
        }
        if decision:
            record["decision"] = {
                "action": decision.action.value,
                "skip_reason": decision.skip_reason,
                "our_dollars": str(decision.dollars) if decision.dollars else None,
            }

        # Detect hour from trade timestamp and rotate hour file if needed
        event_hour = event.trade.timestamp.hour
        self._check_hour_rotation(event_hour)

        self._write_all(record)

        # Track this token for price snapshots
        self._subscribed_tokens.add(event.trade.token_id)
    
    def set_price_service(self, price_service) -> None:
        """Set the price service for capturing price snapshots."""
        self._price_service = price_service
    
    def add_token(self, token_id: str) -> None:
        """Add a token to track for price snapshots."""
        self._subscribed_tokens.add(token_id)
    
    def maybe_record_price_snapshot(self) -> None:
        """Record a price snapshot if enough time has passed.
        
        Call this from the main runner loop to capture periodic prices.
        """
        if not self.file or not self._price_service:
            return
        
        now = time.time()
        if now - self._last_price_snapshot < self.price_snapshot_interval_sec:
            return
        
        self._last_price_snapshot = now
        self._record_price_snapshot()
    
    def _record_price_snapshot(self) -> None:
        """Record current prices for all tracked tokens."""
        if not self._subscribed_tokens:
            return
        
        prices_data: Dict[str, Dict] = {}
        for token_id in self._subscribed_tokens:
            try:
                bid, ask = self._price_service.get_prices(token_id)
                if bid is not None and ask is not None:
                    spread_pct = ((ask - bid) / ask * 100) if ask > 0 else None
                    prices_data[token_id] = {
                        "bid": str(bid),
                        "ask": str(ask),
                        "spread_pct": str(spread_pct) if spread_pct else None,
                    }
            except Exception:
                pass  # Token might not have prices yet
        
        if prices_data:
            # Rotate hour file if needed (only after first event set the hour)
            if self._current_hour is not None:
                now_hour = datetime.now(timezone.utc).hour
                self._check_hour_rotation(now_hour)

            self._write_all({
                "type": "price_snapshot",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "prices": prices_data,
                "token_count": len(prices_data),
            })
            logger.debug(f"Price snapshot: {len(prices_data)} tokens")
    
    def end_session(self, summary: Optional[Dict] = None) -> Path:
        if not self.file:
            return Path()

        # Record final price snapshot before ending
        if self._price_service:
            self._record_price_snapshot()

        end_time = datetime.now(timezone.utc)
        end_record = {"type": "session_end", "timestamp": end_time.isoformat(),
                      "session_id": self.session_id, "summary": summary or {}}

        # Close continuous file
        self._write(end_record)
        filepath = Path(self.file.name)
        self.file.close()
        self.file = None

        # Close hour file
        if self._hour_file:
            self._write_hour(end_record)
            self._hour_file.close()
            self._hour_file = None
            self._current_hour = None

        return filepath
    
    def _check_hour_rotation(self, event_hour: int) -> None:
        """Rotate per-hour file when the UTC hour changes."""
        if self._current_hour == event_hour:
            return

        # Close old hour file with session_end
        if self._hour_file:
            self._write_hour({"type": "session_end",
                              "timestamp": datetime.now(timezone.utc).isoformat(),
                              "session_id": self.session_id, "hour": self._current_hour})
            self._hour_file.close()
            self._hour_file = None
            logger.info(f"Hour file closed for hour {self._current_hour}")

        # Open new hour file
        self._current_hour = event_hour
        filename = f"{self._session_time_name}_hour_{event_hour:02d}.jsonl"
        filepath = self._day_path / filename
        self._hour_file = open(filepath, "w", encoding="utf-8")

        # Write session_start to hour file
        self._write_hour({"type": "session_start",
                          "timestamp": datetime.now(timezone.utc).isoformat(),
                          "session_id": self.session_id, "hour": event_hour})
        logger.info(f"Hour file opened: {filepath}")

    def _write(self, data: Dict) -> None:
        """Write to continuous file only."""
        if self.file:
            self.file.write(json.dumps(data, cls=DecimalEncoder) + "\n")
            self.file.flush()

    def _write_all(self, data: Dict) -> None:
        """Write to both continuous and current hour file."""
        line = json.dumps(data, cls=DecimalEncoder) + "\n"
        if self.file:
            self.file.write(line)
            self.file.flush()
        if self._hour_file:
            self._hour_file.write(line)
            self._hour_file.flush()

    def _write_hour(self, data: Dict) -> None:
        """Write to hour file only."""
        if self._hour_file:
            self._hour_file.write(json.dumps(data, cls=DecimalEncoder) + "\n")
            self._hour_file.flush()
    
    def _clean(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._clean(v) for v in obj]
        return obj