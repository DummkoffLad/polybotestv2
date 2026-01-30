# Coding Conventions

**Analysis Date:** 2026-01-30

## Naming Patterns

**Files:**
- Snake case: `mirror_strategy.py`, `blockchain_detector.py`
- Package modules use single descriptors: `models.py`, `base.py`, `runner.py`
- Test files: `test_dry_run_smoke.py` (test prefix + underscore-separated description)
- Tools: `verify_trades.py` (action verb + noun)

**Functions:**
- Snake case throughout
- Private functions prefixed with single underscore: `_check_hourly_reset()`, `_buy()`, `_skip()`
- Public methods: no prefix (`initialize()`, `on_event()`, `place_order()`)
- Helper utilities: descriptive verb-noun pattern: `fetch_api_trades()`, `scan_blockchain()`, `correlate()`

**Variables:**
- Snake case for all variables: `hourly_budget_used`, `scale_ratio`, `order_log`, `match_found`
- Type aliases in UPPER_CASE: `MarketId = str`
- Constants in UPPER_CASE with description: `MIN_MARKET_ORDER_DOLLARS = Decimal("1.00")`, `PRICE_EXTREME_HIGH = Decimal("0.99")`

**Types:**
- Enum names PascalCase: `ExecutionMode`, `OrderStatus`, `TradeAction`, `TradeSide`, `DecisionAction`
- Dataclass names PascalCase: `BotConfig`, `StrategyConfig`, `TradeDecision`, `OrderRequest`, `OrderResponse`
- Type annotations use full qualified names from `typing`: `Optional`, `Dict`, `List`, `Tuple`, `Any`

## Code Style

**Formatting:**
- No explicit formatter configured (no `.black`, `.flake8`, `.pylintrc`)
- Implicit conventions from codebase:
  - 4-space indentation (standard Python)
  - Line length: varies, examples show 80-100 character preference for readability
  - Dataclass fields on single lines when brief, multi-line for complex types
  - Long import statements organized on separate lines

**Linting:**
- No linter configuration found (linting not enforced)
- Code follows standard PEP 8 conventions implicitly

## Import Organization

**Order:**
1. `__future__` imports (for Python 3.7+ compatibility): `from __future__ import annotations`
2. Standard library: `import sys`, `from pathlib import Path`, `import logging`
3. Third-party: `import yaml`, `from dotenv import load_dotenv`, `import pytest`
4. Local project imports: `from ..core.config import load_config`, `from .base import Strategy`

**Path Aliases:**
- Relative imports use `..` for parent package traversal: `from ..core.config import BotConfig`
- No `@` path aliases (Django/TypeScript style)
- Type checking imports guarded: `from typing import TYPE_CHECKING` followed by conditional `if TYPE_CHECKING: from ..core.config import BotConfig`

**Pattern Example from `src/framework/runner.py`:**
```python
from __future__ import annotations
import json
import os
import signal
import time, logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Set, Dict, Any, TYPE_CHECKING
if TYPE_CHECKING:
    from ..core.config import BotConfig
    from .recorder import SessionRecorder
from ..strategies.base import Strategy, StrategyConfig, DecisionAction
```

## Error Handling

**Patterns:**
- Explicit exception catching with specific exception types (not bare `except:`)
- Try-except-finally blocks for resource cleanup (`self.price_service.stop()` in finally)
- Logging errors with `logger.error()`, `logger.warning()`, `logger.info()` contextually
- Validation with early returns: check preconditions and return error decisions early
- Exception propagation for critical failures, recovery logging for non-critical

**Example from `src/strategies/mirror/strategy.py`:**
```python
def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
    trade, prices, cfg = event.trade, event.prices, self.config
    ask = prices.ask
    if not ask or ask <= 0:
        return self._skip("no_price")

    if ask >= Decimal("1"):
        logger.warning(f"Invalid ask price >= 1: {ask}")
        return self._skip("invalid_price")
```

**Example from `src/framework/runner.py` (resource cleanup):**
```python
try:
    while self._running:
        # main loop
        self._cycle()
except Exception as e:
    logger.error(f"Fatal error in run loop: {e}")
finally:
    self._shutdown()
```

## Logging

**Framework:** Python's `logging` module (built-in)

**Patterns:**
- Logger initialized per module: `logger = logging.getLogger(__name__)`
- Log levels used:
  - `logger.debug()`: Detailed state tracking (`"Loaded {len(self._seen)} seen hashes from disk"`)
  - `logger.info()`: Important events (`"Hourly budget reset"`, `"Startup catchup"`)
  - `logger.warning()`: Unusual but recoverable conditions (`"Scale ratio out of typical bounds"`)
  - `logger.error()`: Errors that prevent operation (`"Fatal error in run loop"`, `"Blockchain init failed"`)

**Example from `src/strategies/mirror/strategy.py`:**
```python
logger = logging.getLogger(__name__)

def _check_hourly_reset(self, event_time: datetime) -> None:
    if self._current_hour is not None and current_hour != self._current_hour:
        logger.info(f"Hourly budget reset: ${self.hourly_budget_used:.2f} used last hour")
```

## Comments

**When to Comment:**
- Complex decision logic or non-obvious constraints (e.g., "SAFETY:" comments for security-critical code)
- Historical context explaining WHY something is done (not WHAT it does)
- Algorithm explanations (e.g., "Fuzzy match by token + amount + timestamp")
- Warnings about gotchas or fragile assumptions

**Pattern Examples:**
```python
# SAFETY: Check for hourly budget reset
self._check_hourly_reset(event.trade.timestamp)

# Use content-based dedup key to avoid duplicate OrderFilled events
# Same trade can emit 2 logs (leader as maker AND taker) with different log_index
h = f"{bt.tx_hash}_{bt.token_id}_{bt.action}_{bt.dollar_value}"

# SAFETY: Validate order meets Polymarket minimum constraints.
# Market orders: min $1
# Limit orders: min 5 shares
```

**Module-level Docstrings:**
- Present on all modules with triple-quoted description
- Example from `src/data/models.py`:
```python
"""Data models for Polymarket events."""
```

## Function Design

**Size:** Functions typically 20-60 lines; larger ones broken into private helpers
- `on_event()`: ~15 lines (delegates to `_buy()`, `_sell()`, `_check_extreme_prices()`)
- `_buy()`: ~60 lines (complex with multiple validation checks)
- `place_order()`: ~5 lines (simple delegation)

**Parameters:**
- Positional arguments for required inputs
- Type annotations required on all parameters: `def on_event(self, event: MarketEvent) -> TradeDecision:`
- Default values for optional parameters: `def create_clock(mode: str, start: Optional[datetime] = None) -> Clock:`
- No `**kwargs` for configuration (use dataclasses instead)

**Return Values:**
- Always annotated: `-> TradeDecision`, `-> bool`, `-> Dict[str, Any]`
- Early returns for guard clauses (fail fast)
- Consistent return types (no conditional None vs list)

## Module Design

**Exports:**
- `__all__` not used; rely on `from X import Y` clarity
- Public classes and functions placed at module level
- Private implementation classes prefixed with underscore: `class _STRATEGIES` (private registry)

**Barrel Files:**
- Central re-export pattern in `__init__.py` files
- Example from `src/execution/__init__.py`:
```python
from .base import ExecutionAdapter
from .dry_run import NullExecutionAdapter
DryRunAdapter = NullExecutionAdapter  # Alias for backward compat
```

**Pattern from `src/strategies/__init__.py`:**
```python
from .base import Strategy, StrategyConfig, TradeDecision, DecisionAction, register_strategy, get_strategy, list_strategies
from .mirror import MirrorStrategy
from .momentum import MomentumMirrorStrategy
# ... all strategies imported for registration
```

## Dataclass Usage

**Pattern:**
- Use `@dataclass` for immutable data models: `@dataclass(frozen=True)` for event-like structures
- Use `field(default_factory=...)` for mutable defaults: `pending_orders: List[str] = field(default_factory=list)`
- Implement `@classmethod` factory methods for API parsing: `LeaderTrade.from_api(data: Dict) -> LeaderTrade`

**Example from `src/core/types.py`:**
```python
@dataclass
class Exposure:
    """Exposure in a market (both sides)."""
    market_id: MarketId
    up_shares: Decimal = Decimal("0")
    up_dollars: Decimal = Decimal("0")

    @property
    def total_dollars(self) -> Decimal:
        return self.up_dollars + self.down_dollars
```

## Strategy Pattern (Register/Lookup)

**Registry Pattern:**
Used for dynamic strategy loading without hardcoded imports.

**Implementation in `src/strategies/base.py`:**
```python
_STRATEGIES: Dict[str, type] = {}

def register_strategy(cls: type) -> type:
    instance = cls()
    _STRATEGIES[instance.name] = cls
    return cls

def get_strategy(name: str) -> Strategy:
    if name not in _STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}")
    return _STRATEGIES[name]()

def list_strategies() -> List[str]:
    return list(_STRATEGIES.keys())

@register_strategy
class MirrorStrategy(Strategy):
    @property
    def name(self) -> str:
        return "mirror"
```

**Usage:**
```python
from src.strategies import get_strategy, list_strategies
strategy = get_strategy("mirror")  # Returns fresh instance
```

---

*Convention analysis: 2026-01-30*
