"""Human-readable colored display for dry-run mode.

Single-line, clear output with colors for easy reading.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

# Eastern Time offset (EST = UTC-5)
ET_OFFSET = timedelta(hours=-5)


def to_et(dt: datetime) -> datetime:
    """Convert a datetime to Eastern Time."""
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=timezone.utc)
    # Convert to UTC first, then apply ET offset
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt + ET_OFFSET


def et_time_str(dt: datetime) -> str:
    """Format datetime as HH:MM:SS in ET."""
    return to_et(dt).strftime("%H:%M:%S")


# ANSI Color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    
    # Background
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"

    @classmethod
    def disable(cls) -> None:
        """Disable ANSI colors by setting codes to empty strings."""
        cls.RESET = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.RED = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.MAGENTA = ""
        cls.CYAN = ""
        cls.WHITE = ""
        cls.GRAY = ""
        cls.BG_RED = ""
        cls.BG_GREEN = ""
        cls.BG_YELLOW = ""
        cls.BG_BLUE = ""


@dataclass
class TradeRecord:
    """Record of a simulated trade."""
    timestamp: datetime
    market_title: str
    side: str  # "UP" or "DOWN"
    action: str  # "BUY" or "SELL"
    dollars: Decimal
    price: Decimal
    reason: str


@dataclass
class RunStats:
    """Statistics for a dry-run session."""
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cycles: int = 0
    api_calls: int = 0
    decisions_computed: int = 0
    orders_would_place: int = 0
    orders_skipped_no_delta: int = 0
    orders_skipped_below_min: int = 0
    orders_skipped_caps: int = 0
    auto_sells_triggered: int = 0
    auto_sells: int = 0  # Actual auto-sell executions in shadow portfolio
    errors: int = 0
    markets_seen: set = field(default_factory=set)
    trades: List[TradeRecord] = field(default_factory=list)
    total_would_spend: Decimal = field(default_factory=lambda: Decimal("0"))
    total_would_receive: Decimal = field(default_factory=lambda: Decimal("0"))  # From sells
    
    @property
    def elapsed(self) -> timedelta:
        return datetime.now(timezone.utc) - self.start_time
    
    @property
    def elapsed_str(self) -> str:
        elapsed = self.elapsed
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        return f"{minutes:02d}:{seconds:02d}"


class HumanDisplay:
    """Human-readable display for dry-run mode."""
    
    # Price threshold for auto-sell
    AUTO_SELL_PRICE = Decimal("0.9999")
    
    def __init__(self, our_capital: Decimal, hourly_budget: Decimal):
        self.our_capital = our_capital
        self.hourly_budget = hourly_budget
        self.stats = RunStats()
        self._last_status_line = ""
        self._market_titles: Dict[str, str] = {}  # condition_id -> title
        self._market_prices: Dict[str, Decimal] = {}  # condition_id_side -> price

        self._use_color = self._supports_color()
        if not self._use_color:
            Colors.disable()
        self._use_overwrite = self._supports_overwrite()
        
        # Duration limit
        self._duration_limit: Optional[timedelta] = None
        self._should_stop = False
    
    def set_duration(self, minutes: int) -> None:
        """Set duration limit for the run."""
        self._duration_limit = timedelta(minutes=minutes)

    def _supports_color(self) -> bool:
        """Best-effort detection for ANSI color support."""
        if os.getenv("NO_COLOR") or os.getenv("DISABLE_COLOR") or os.getenv("TERM") == "dumb":
            return False
        if not sys.stdout.isatty():
            return False
        if os.name == "nt" and not os.getenv("FORCE_COLOR"):
            return False
        return True

    def _supports_overwrite(self) -> bool:
        """Use carriage-return status line only when safe."""
        if not sys.stdout.isatty():
            return False
        if os.getenv("NO_OVERWRITE"):
            return False
        if os.name == "nt" and not os.getenv("FORCE_OVERWRITE"):
            return False
        return True
    
    def should_stop(self) -> bool:
        """Check if we should stop due to duration limit."""
        if self._should_stop:
            return True
        if self._duration_limit and self.stats.elapsed >= self._duration_limit:
            self._should_stop = True
            return True
        return False
    
    def update_market_info(self, condition_id: str, title: str, side: str, price: Decimal) -> None:
        """Update cached market info."""
        self._market_titles[condition_id] = title
        self._market_prices[f"{condition_id}_{side}"] = price
    
    def get_price(self, condition_id: str, side: str) -> Optional[Decimal]:
        """Get cached price for market/side."""
        return self._market_prices.get(f"{condition_id}_{side}")
    
    def check_auto_sell(self, condition_id: str, side: str, price: Decimal) -> bool:
        """Check if price triggers auto-sell (>= 0.9999)."""
        if price >= self.AUTO_SELL_PRICE:
            self.stats.auto_sells_triggered += 1
            return True
        return False
    
    def print_startup(self) -> None:
        """Print startup banner."""
        print()
        print(f"{Colors.BG_BLUE}{Colors.WHITE}{Colors.BOLD}")
        print("=" * 70)
        print("        POLYMARKET COPY-TRADING BOT - DRY RUN MODE")
        print("=" * 70)
        print(f"{Colors.RESET}")
        print(f"{Colors.CYAN}Capital: ${self.our_capital}  |  Hourly Budget: ${self.hourly_budget}{Colors.RESET}")
        if self._duration_limit:
            mins = int(self._duration_limit.total_seconds() // 60)
            print(f"{Colors.YELLOW}Duration: {mins} minutes{Colors.RESET}")
        print(f"{Colors.DIM}Press Ctrl+C to stop{Colors.RESET}")
        print("-" * 70)
        print()
    
    def print_cycle_status(self, budget_remaining: Decimal, markets_active: int) -> None:
        """Print a brief status line (overwrites previous)."""
        self.stats.cycles += 1
        
        elapsed = self.stats.elapsed_str
        remaining = f"${budget_remaining:.2f}"
        
        # Build status line
        status = (
            f"{Colors.DIM}[{elapsed}]{Colors.RESET} "
            f"Cycle {self.stats.cycles} | "
            f"Markets: {Colors.CYAN}{markets_active}{Colors.RESET} | "
            f"Budget left: {Colors.GREEN}{remaining}{Colors.RESET} | "
            f"Would trade: {Colors.YELLOW}{self.stats.orders_would_place}{Colors.RESET}"
        )
        
        if self._use_overwrite:
            # Overwrite line
            sys.stdout.write(f"\r{status}    ")
            sys.stdout.flush()
            self._last_status_line = status
        else:
            print(status)
    
    def print_trade(
        self,
        market_id: str,
        title: str,
        side: str,
        action: str,  # "BUY" or "SELL"
        dollars: Decimal,
        price: Decimal,
        shares: Decimal,
        cash_left: Decimal,
        timestamp: datetime,
        reason: str,
        bid_price: Optional[Decimal] = None,
        ask_price: Optional[Decimal] = None,
    ) -> None:
        """Print a trade notification with LIVE bid/ask prices."""
        if self._use_overwrite:
            # Clear the status line
            sys.stdout.write("\r" + " " * 80 + "\r")
        
        # Color based on action (use ASCII symbols for Windows compatibility)
        if action == "BUY":
            action_color = Colors.GREEN
            symbol = "[+]"
            action_label = "BOUGHT"
        else:
            action_color = Colors.RED
            symbol = "[-]"
            action_label = "SOLD"
        
        # Color based on side
        side_color = Colors.CYAN if side == "UP" else Colors.MAGENTA
        
        # Format title (truncate if needed)
        short_title = title[:35] + "..." if len(title) > 38 else title
        
        # Build bid/ask string
        price_info = ""
        if bid_price is not None and ask_price is not None:
            price_info = f" (bid:{bid_price:.3f}/ask:{ask_price:.3f})"
        elif bid_price is not None:
            price_info = f" (bid:{bid_price:.3f})"
        elif ask_price is not None:
            price_info = f" (ask:{ask_price:.3f})"
        
        # Build the line (short format)
        # Convert to ET for display
        time_str = et_time_str(timestamp)
        line = (
            f"{Colors.BOLD}{symbol} {action_color}{action_label}{Colors.RESET} "
            f"{side_color}{side}{Colors.RESET} "
            f"{short_title} | "
            f"${dollars:.2f} @ {price:.4f}{Colors.DIM}{price_info}{Colors.RESET} | "
            f"{shares:.2f} shares | "
            f"cash ${cash_left:.2f} | "
            f"{Colors.DIM}{time_str}{Colors.RESET}"
        )
        
        print(line)
        
        # Record the trade
        self.stats.trades.append(TradeRecord(
            timestamp=timestamp,
            market_title=title,
            side=side,
            action=action,
            dollars=dollars,
            price=price,
            reason=reason,
        ))
        
        self.stats.orders_would_place += 1
        if action == "BUY":
            self.stats.total_would_spend += dollars
        else:
            self.stats.total_would_receive += dollars

    def print_status_summary(
        self,
        leader_trades_seen: int,
        filtered_count: int,
        portfolio_value: Decimal,
        pnl: Decimal,
        timestamp: datetime,
        api_age_sec: Optional[float] = None,
        missing_bids: int = 0,
        positions_with_prices: int = 0,
        total_positions: int = 0,
    ) -> None:
        """Print periodic status summary with LIVE API status."""
        # Convert to ET for display
        time_str = et_time_str(timestamp)
        pnl_color = Colors.GREEN if pnl >= 0 else Colors.RED
        
        # API age indicator
        api_str = ""
        if api_age_sec is not None:
            if api_age_sec < 3:
                api_str = f" | {Colors.GREEN}LIVE{Colors.RESET}"
            else:
                api_str = f" | API {api_age_sec:.1f}s ago"
        
        # Price status
        price_str = ""
        if total_positions > 0:
            if positions_with_prices == total_positions:
                price_str = f" | {Colors.GREEN}Prices: {positions_with_prices}/{total_positions}{Colors.RESET}"
            else:
                price_str = f" | {Colors.YELLOW}Prices: {positions_with_prices}/{total_positions}{Colors.RESET}"
        
        # Missing bids warning
        missing_str = ""
        if missing_bids > 0:
            missing_str = f" | {Colors.RED}No bid: {missing_bids}{Colors.RESET}"
        
        line = (
            f"{Colors.DIM}[{time_str}]{Colors.RESET} "
            f"Leader: {leader_trades_seen} trades | "
            f"Skip: {filtered_count} | "
            f"Value: ${portfolio_value:.2f} | "
            f"PnL: {pnl_color}{pnl:+.2f}{Colors.RESET}"
            f"{api_str}"
            f"{price_str}"
            f"{missing_str}"
        )
        print(line)
    
    def print_auto_sell(self, market_id: str, title: str, side: str, price: Decimal) -> None:
        """Print auto-sell trigger notification."""
        if self._use_overwrite:
            sys.stdout.write("\r" + " " * 80 + "\r")
        
        short_title = title[:35] + "..." if len(title) > 38 else title
        
        line = (
            f"{Colors.BG_YELLOW}{Colors.BOLD}[!] AUTO-SELL{Colors.RESET} "
            f"{Colors.YELLOW}{side} @ {price:.4f}{Colors.RESET} | "
            f"{short_title} | "
            f"{Colors.DIM}Price >= 0.9999, would exit{Colors.RESET}"
        )
        
        print(line)
    
    def print_error(self, message: str) -> None:
        """Print an error."""
        if self._use_overwrite:
            sys.stdout.write("\r" + " " * 80 + "\r")
        self.stats.errors += 1
        print(f"{Colors.RED}[X] ERROR: {message}{Colors.RESET}")
    
    def print_info(self, message: str) -> None:
        """Print info message."""
        if self._use_overwrite:
            sys.stdout.write("\r" + " " * 80 + "\r")
        print(f"{Colors.BLUE}[i] {message}{Colors.RESET}")
    
    def print_summary(self) -> None:
        """Print final summary."""
        print()
        print()
        print(f"{Colors.BG_BLUE}{Colors.WHITE}{Colors.BOLD}")
        print("=" * 70)
        print("                    DRY RUN SUMMARY")
        print("=" * 70)
        print(f"{Colors.RESET}")
        
        elapsed = self.stats.elapsed
        mins = int(elapsed.total_seconds() // 60)
        secs = int(elapsed.total_seconds() % 60)
        
        print(f"{Colors.BOLD}Duration:{Colors.RESET} {mins}m {secs}s")
        print(f"{Colors.BOLD}Cycles:{Colors.RESET} {self.stats.cycles}")
        print(f"{Colors.BOLD}Markets Seen:{Colors.RESET} {len(self.stats.markets_seen)}")
        print()
        
        print(f"{Colors.CYAN}=== DECISIONS ==={Colors.RESET}")
        print(f"  Computed: {self.stats.decisions_computed}")
        print(f"  {Colors.GREEN}Would Place Orders:{Colors.RESET} {self.stats.orders_would_place}")
        print(f"  {Colors.GRAY}Skipped (no delta):{Colors.RESET} {self.stats.orders_skipped_no_delta}")
        print(f"  {Colors.GRAY}Skipped (below min):{Colors.RESET} {self.stats.orders_skipped_below_min}")
        print(f"  {Colors.GRAY}Skipped (caps):{Colors.RESET} {self.stats.orders_skipped_caps}")
        print()
        
        print(f"{Colors.YELLOW}=== SIMULATED TRADING ==={Colors.RESET}")
        print(f"  Total Would Spend (BUY): {Colors.GREEN}${self.stats.total_would_spend:.2f}{Colors.RESET}")
        print(f"  Total Would Receive (SELL): {Colors.CYAN}${self.stats.total_would_receive:.2f}{Colors.RESET}")
        print(f"  Net: ${self.stats.total_would_spend - self.stats.total_would_receive:.2f}")
        print(f"  Auto-Sells Triggered: {Colors.YELLOW}{self.stats.auto_sells_triggered}{Colors.RESET}")
        print(f"  Auto-Sells Executed: {Colors.YELLOW}{self.stats.auto_sells}{Colors.RESET}")
        print()
        
        if self.stats.errors > 0:
            print(f"{Colors.RED}=== ERRORS ==={Colors.RESET}")
            print(f"  Count: {self.stats.errors}")
            print()
        
        # Recent trades
        if self.stats.trades:
            print(f"{Colors.MAGENTA}=== RECENT TRADES (last 10) ==={Colors.RESET}")
            for trade in self.stats.trades[-10:]:
                action_color = Colors.GREEN if trade.action == "BUY" else Colors.RED
                print(
                    f"  {action_color}{trade.action}{Colors.RESET} "
                    f"{trade.side} ${trade.dollars:.2f} @ {trade.price:.4f} - "
                    f"{trade.market_title[:40]}"
                )
            print()
        
        print("-" * 70)
        print(f"{Colors.DIM}End of dry run{Colors.RESET}")
        print()
