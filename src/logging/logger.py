"""Structured JSON logging with mode prefix.

Every log line includes:
- MODE=XXX prefix (for immediate visibility)
- Structured JSON with correlation IDs and context
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from logging.handlers import RotatingFileHandler

from ..core.types import ExecutionMode, MarketPhase

# Eastern Time offset (EST = UTC-5, EDT = UTC-4)
# Using EST for simplicity; for accurate DST handling use pytz or zoneinfo
ET_OFFSET = timedelta(hours=-5)


def get_et_timestamp() -> str:
    """Get current timestamp in Eastern Time as ISO format string."""
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc + ET_OFFSET
    # Format as ISO but mark as ET
    return now_et.strftime("%Y-%m-%dT%H:%M:%S") + " ET"


class ModeFormatter(logging.Formatter):
    """Custom formatter that prepends MODE= to every log line."""
    
    def __init__(self, mode: ExecutionMode):
        super().__init__()
        self.mode = mode
        self.mode_prefix = f"[MODE={mode}]"
    
    def format(self, record: logging.LogRecord) -> str:
        # Build structured log entry
        log_entry = {
            "timestamp": get_et_timestamp(),
            "level": record.levelname,
            "mode": str(self.mode),
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, "correlation_id"):
            log_entry["correlation_id"] = record.correlation_id
        if hasattr(record, "market_id"):
            log_entry["market_id"] = record.market_id
        if hasattr(record, "phase"):
            log_entry["phase"] = str(record.phase)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Format as MODE prefix + JSON
        json_str = json.dumps(log_entry, default=str)
        return f"{self.mode_prefix} {json_str}"


class BotLogger:
    """Main bot logger with structured logging support.
    
    Features:
    - MODE= prefix on every line
    - Correlation ID tracking
    - Market and phase context
    - JSON structured output
    """
    
    def __init__(
        self,
        mode: ExecutionMode,
        name: str = "polybot",
        level: str = "INFO",
        log_file: Optional[Path] = None,
        console: bool = True,
        max_bytes: Optional[int] = None,
        backup_count: int = 5,
    ):
        self.mode = mode
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))
        self.logger.handlers.clear()
        
        formatter = ModeFormatter(mode)
        
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
        
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            if max_bytes and max_bytes > 0:
                file_handler = RotatingFileHandler(
                    log_file,
                    maxBytes=max_bytes,
                    backupCount=max(0, backup_count),
                    encoding="utf-8",
                )
            else:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        
        # Current context
        self._correlation_id: Optional[str] = None
        self._market_id: Optional[str] = None
        self._phase: Optional[MarketPhase] = None
    
    def set_context(
        self,
        correlation_id: Optional[str] = None,
        market_id: Optional[str] = None,
        phase: Optional[MarketPhase] = None,
    ) -> None:
        """Set logging context for subsequent calls."""
        if correlation_id is not None:
            self._correlation_id = correlation_id
        if market_id is not None:
            self._market_id = market_id
        if phase is not None:
            self._phase = phase
    
    def clear_context(self) -> None:
        """Clear logging context."""
        self._correlation_id = None
        self._market_id = None
        self._phase = None
    
    def new_correlation_id(self) -> str:
        """Generate and set a new correlation ID."""
        self._correlation_id = uuid.uuid4().hex[:12]
        return self._correlation_id
    
    def _log(
        self,
        level: int,
        msg: str,
        extra_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """Internal log method with context injection."""
        extra = {
            "correlation_id": kwargs.get("correlation_id", self._correlation_id),
            "market_id": kwargs.get("market_id", self._market_id),
            "phase": kwargs.get("phase", self._phase),
            "extra_data": extra_data,
        }
        self.logger.log(level, msg, extra=extra)
    
    def debug(self, msg: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._log(logging.DEBUG, msg, data, **kwargs)
    
    def info(self, msg: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._log(logging.INFO, msg, data, **kwargs)
    
    def warning(self, msg: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._log(logging.WARNING, msg, data, **kwargs)
    
    def error(self, msg: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._log(logging.ERROR, msg, data, **kwargs)
    
    def critical(self, msg: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._log(logging.CRITICAL, msg, data, **kwargs)
    
    # Specialized logging methods for common events
    
    def log_startup_banner(self, config_summary: Dict[str, Any]) -> None:
        """Log startup banner with mode and config."""
        banner = f"""
================================================================================
                    POLYMARKET COPY-TRADING BOT V2
                    MODE: {self.mode}
================================================================================
"""
        print(banner)
        self.info("Bot starting", data={
            "version": "2.0.0",
            "mode": str(self.mode),
            "config": config_summary,
        })
    
    def log_cycle_start(
        self,
        market_id: str,
        phase: MarketPhase,
        leader_snapshot: Dict[str, Any],
        my_snapshot: Dict[str, Any],
    ) -> str:
        """Log start of a decision cycle. Returns correlation ID."""
        corr_id = self.new_correlation_id()
        self.set_context(correlation_id=corr_id, market_id=market_id, phase=phase)
        self.debug("Cycle start", data={
            "leader_snapshot": leader_snapshot,
            "my_snapshot": my_snapshot,
        })
        return corr_id
    
    def log_decision(
        self,
        target: Dict[str, Any],
        caps_applied: Dict[str, Any],
        min_checks: Dict[str, Any],
        action: Optional[Dict[str, Any]],
        action_reason: str,
    ) -> None:
        """Log a decision computation."""
        self.info("Decision computed", data={
            "target": target,
            "caps_applied": caps_applied,
            "min_checks": min_checks,
            "action": action,
            "action_reason": action_reason,
        })
    
    def log_order_request(self, request: Dict[str, Any]) -> None:
        """Log an order request."""
        self.info("Order request", data={"request": request})
    
    def log_order_response(self, response: Dict[str, Any]) -> None:
        """Log an order response."""
        level = logging.INFO if response.get("success") else logging.ERROR
        self._log(level, "Order response", extra_data={"response": response})
    
    def log_circuit_breaker(self, reason: str, details: Dict[str, Any]) -> None:
        """Log circuit breaker activation."""
        self.warning(f"Circuit breaker triggered: {reason}", data=details)
    
    def log_state_change(self, from_phase: MarketPhase, to_phase: MarketPhase, reason: str) -> None:
        """Log state machine transition."""
        self.info(f"Phase transition: {from_phase} -> {to_phase}", data={"reason": reason})


# Global logger instance
_logger: Optional[BotLogger] = None


def get_logger() -> BotLogger:
    """Get the global logger instance."""
    if _logger is None:
        raise RuntimeError("Logger not initialized. Call init_logger() first.")
    return _logger


def init_logger(
    mode: ExecutionMode,
    level: str = "INFO",
    log_file: Optional[Path] = None,
    console: bool = True,
    max_bytes: Optional[int] = None,
    backup_count: int = 5,
) -> BotLogger:
    """Initialize the global logger."""
    global _logger
    # In dry-run, default to file-only logging unless explicitly forced
    if mode == ExecutionMode.DRY_RUN and console and not os.getenv("FORCE_DRY_RUN_CONSOLE"):
        console = False
    _logger = BotLogger(
        mode=mode,
        level=level,
        log_file=log_file,
        console=console,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    return _logger
