# Coding Conventions

**Analysis Date:** 2026-02-05

## Naming Patterns

**Files:**
- `snake_case.py` for modules: `capital_manager.py`, `kelly_engine.py`, `conviction.py`
- Co-located tests use `test_<module_name>.py`: `test_capital_manager.py`, `test_conviction.py`
- Polymarket-specific modules grouped by domain: `src/core/`, `src/strategies/`, `src/framework/`, `src/comparison/`, `src/analysis/`, `src/execution/`, `src/data/`

**Functions & Methods:**
- `snake_case` for all functions and methods
- Test functions use descriptive names: `test_normal_mode_above_soft_floor()`, `test_can_enter_soft_floor_exceptional_quality()`
- Helper functions in tests prefixed with `make_` or similar: `make_capital_manager()`, `make_trade()`, `make_prices()`, `make_event()`
- Private methods/attributes use leading underscore: `_high_water_mark`, `_load_seen_hashes()`, `_mode`

**Variables:**
- `snake_case` for all variables
- Decimal-based values explicitly named to indicate units: `soft_floor_pct`, `equity`, `current_equity`, `starting_capital`
- Collection names pluralized: `positions`, `trades`, `events`, `equities`, `strategies`
- Boolean flags use `is_` or `has_` prefix: `is_scale_in`, `has_position`, `negative_risk`
- Enum members use `UPPER_CASE`: `TradingMode.NORMAL`, `TradingMode.SOFT_FLOOR`, `DecisionAction.BUY`

**Types & Classes:**
- `PascalCase` for all classes: `CapitalManager`, `Portfolio`, `KellyCalculator`, `ConvictionScorer`
- Enums use `PascalCase`: `TradingMode`, `DecisionAction`, `TradeAction`, `TradeSide`, `OrderType`
- Exception classes end with `Error` or `Exception`: `PortfolioInvariantError`
- Dataclasses use `PascalCase`: `LeaderTrade`, `PriceSnapshot`, `MarketEvent`, `PortfolioPosition`

## Code Style

**Formatting:**
- No explicit linter configuration found in repo, but code follows PEP 8
- Line length appears flexible (no strict 80/100 char limit observed in practice)
- 4-space indentation consistently used
- Use `from __future__ import annotations` at top of modules for type hints

**Linting:**
- `ruff>=0.2` in requirements.txt indicates linting capability
- Type hints used throughout: `Optional[Decimal]`, `Dict[str, Any]`, `Tuple[bool, Optional[str]]`
- Type checking with `mypy>=1.8` available but not strictly enforced

## Import Organization

**Order:**
1. `__future__` imports: `from __future__ import annotations`
2. Standard library: `import json`, `from datetime import datetime`, `from decimal import Decimal`
3. Third-party: `import pandas as pd`, `import pytest`
4. Relative imports: `from ..core.portfolio import Portfolio`, `from .base import Strategy`
5. Conditional imports with `TYPE_CHECKING` for avoiding circular imports

**Path Aliases:**
- Imports relative to project root via sys.path manipulation in conftest: `from src.core.portfolio import Portfolio`
- Relative module imports using dots: `from ..data.models import LeaderTrade`
- TYPE_CHECKING block for circular dependency prevention: `if TYPE_CHECKING: from ..data.models import PriceSnapshot as PriceSnapshotType`

**Examples:**
```python
# From src/strategies/base.py
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..data.models import PriceSnapshot as PriceSnapshotType

from ..data.models import TradeAction, TradeSide, LeaderTrade, PriceSnapshot, MarketEvent

logger = logging.getLogger(__name__)
```

```python
# From tests/unit/conftest.py
import pytest
from datetime import datetime, timezone
from decimal import Decimal

from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from src.strategies.base import StrategyConfig, DecisionAction, TradeDecision
```

## Error Handling

**Patterns:**
- Custom exceptions inherit from `Exception`: `class PortfolioInvariantError(Exception): pass`
- Raise exceptions with descriptive messages: `raise PortfolioInvariantError(f"BUY shares must be positive: {shares}")`
- Invariant violations checked with comments: `# SAFETY: Validate inputs` or `# SAFETY: Never sell more than we own`
- Use `logger.warning()` for clamping/defensive behavior instead of raising: `logger.warning(f"SELL clamped: requested {shares}, have {pos.shares}")`
- Use `logger.error()` for actual failures
- No broad `except Exception` patterns; catch specific exceptions when needed

**Example from `src/core/portfolio.py`:**
```python
def apply_buy(self, token_id: str, market_id: str, side: Side, shares: Decimal,
              price: Decimal, timestamp: Optional[datetime] = None) -> PortfolioPosition:
    # SAFETY: Validate inputs
    if shares <= 0:
        raise PortfolioInvariantError(f"BUY shares must be positive: {shares}")
    if price <= 0 or price >= 1:
        raise PortfolioInvariantError(f"BUY price must be in (0,1): {price}")
    # ... rest of implementation
```

## Logging

**Framework:** Python's built-in `logging` module with `logger = logging.getLogger(__name__)`

**Patterns:**
- Each module initializes: `logger = logging.getLogger(__name__)` at module level after imports
- `logger.info()` for normal operations: `logger.info(f"CapitalManager initialized: HWM=${starting_capital}, mode={self._mode.value}")`
- `logger.debug()` for detailed tracing (rarely used): `logger.debug(f"BUY applied: {token_id} +{shares} @{price} -> total {pos.shares}")`
- `logger.warning()` for defensive/fallback behavior: `logger.warning(f"SELL clamped: requested {shares}, have {pos.shares}")`
- `logger.error()` for actual failures
- Use f-strings in log messages with context
- Log function parameters and results for debugging

**Example:**
```python
from src.core.kelly_engine.py:
logger = logging.getLogger(__name__)

logger.info(
    f"KellyCalculator initialized: kelly_fraction={kelly_fraction}, "
    f"max_position_pct={self.MAX_POSITION_PCT}"
)
```

## Comments

**When to Comment:**
- Comment algorithmic complexity or non-obvious logic
- Use section headers with `# ============================================================================` for readability
- SAFETY comments on invariant checks or critical validations
- Constraint comments for Polymarket-specific limits: `# Market orders: minimum $1`, `# Price extremes: 0.99 = auto-sell`
- Comments on test helper functions explaining defaults and usage patterns

**Examples:**
```python
# Section header pattern
# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

# Inline constraint comment
# Polymarket order constraints
MIN_MARKET_ORDER_DOLLARS = Decimal("1.00")
MIN_LIMIT_ORDER_SHARES = Decimal("5.0")

# Safety comment
# SAFETY: Validate inputs
if shares <= 0:
    raise PortfolioInvariantError(f"BUY shares must be positive: {shares}")
```

**Docstrings:**
- Module docstrings at top of file explaining purpose: `"""Capital protection system with two-tier floor mechanism."""`
- Class docstrings with Examples section showing usage
- Function docstrings with Args, Returns, and Raises sections
- Attribute docstrings in dataclasses rare; context usually clear from names
- Test docstring style: One-liner describing the test scenario in imperative: `"""Initialize with $100 should set HWM=$100 and mode=NORMAL."""`

**Example from `src/core/capital_manager.py`:**
```python
class CapitalManager:
    """Manages capital protection via two-tier floor system.

    Tracks high water mark (HWM) and determines trading mode based on
    current equity as percentage of HWM:
    - Above soft_floor_pct: NORMAL mode (unrestricted)
    - Between soft_floor_pct and hard_floor_pct: SOFT_FLOOR (exceptional trades only)
    - Below hard_floor_pct: HARD_FLOOR (all trading stopped)

    Examples:
        >>> cm = CapitalManager()
        >>> cm.initialize(Decimal("100.00"))
        >>> cm.check_floor_status(Decimal("95.00"))  # 5% DD -> NORMAL
        TradingMode.NORMAL
    """

    def __init__(
        self,
        soft_floor_pct: Decimal = Decimal("90.0"),
        hard_floor_pct: Decimal = Decimal("70.0"),
        exceptional_quality_threshold: Decimal = Decimal("0.85")
    ):
        """Initialize capital manager with floor thresholds.

        Args:
            soft_floor_pct: Equity % of HWM below which soft floor activates (default 90% = 10% DD)
            hard_floor_pct: Equity % of HWM below which hard floor activates (default 70% = 30% DD)
            exceptional_quality_threshold: Minimum quality score for trades at soft floor (default 0.85)
        """
```

## Function Design

**Size:** Generally 20-50 lines, with helper methods breaking complex logic

**Parameters:**
- Use type hints on all parameters
- Keyword arguments for optional/configuration parameters
- Use dataclass instances for complex parameter groups: `strategy_config: StrategyConfig`
- Decimal type preferred for all financial calculations (never float)

**Return Values:**
- Explicit return type hints: `-> Decimal`, `-> TradingMode`, `-> Tuple[bool, Optional[str]]`
- Return tuples for multiple related values: `return (True, None)` for (can_trade, error_reason)
- Return None for no result rather than empty values
- Single responsibility: functions return one logical value/structure

**Example from `src/core/capital_manager.py`:**
```python
def can_enter_new_trade(self, quality_score: Decimal) -> Tuple[bool, Optional[str]]:
    """Check if current mode allows new trade entry.

    Args:
        quality_score: Trade quality score (0 to 1)

    Returns:
        Tuple of (can_enter: bool, reason: Optional[str])
        - (True, None) if trade allowed
        - (False, reason) if blocked by mode
    """
```

## Module Design

**Exports:**
- Classes and main functions defined in module are public by default
- Private implementation details use leading underscore: `_high_water_mark`, `_load_seen_hashes`
- Dataclass fields are always public
- No explicit `__all__` lists observed

**Barrel Files:**
- `src/strategies/__init__.py` provides convenience exports: `list_strategies()`, `get_strategy(name)`
- Minimalist approach to re-exports; mostly for strategy registration
- No deep nesting of imports; relative imports preferred

**Example from `src/strategies/__init__.py`:**
```python
def list_strategies():
    """Return list of available strategy names."""

def get_strategy(name: str) -> Strategy:
    """Get strategy instance by name."""
```

---

*Convention analysis: 2026-02-05*
