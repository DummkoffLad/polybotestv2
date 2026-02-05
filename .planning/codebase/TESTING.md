# Testing Patterns

**Analysis Date:** 2026-02-05

## Test Framework

**Runner:**
- pytest >= 8.0
- Config: No explicit pytest.ini or setup.cfg; default discovery used
- Async support: pytest-asyncio >= 0.23 (installed but not heavily used)

**Assertion Library:**
- Standard pytest assertions with no custom assertion library

**Run Commands:**
```bash
pytest                              # Run all tests
pytest tests/unit                   # Run unit tests only
pytest tests/integration            # Run integration tests only
pytest -xvs tests/unit/test_capital_manager.py  # Single file with verbose output
pytest -k "test_soft_floor"         # Run tests matching pattern
pytest --tb=short                   # Use shorter traceback format
```

## Test File Organization

**Location:**
- Co-located with source: Tests live in `tests/` directory mirroring `src/` structure
- Unit tests: `tests/unit/test_<module_name>.py`
- Integration tests: `tests/integration/test_<scenario>.py`
- Tools/utilities: `tests/tools/verify_trades.py`

**Naming:**
- Test file: `test_<module_name>.py` where module is source module being tested
- Test function: `test_<scenario_or_condition>()` - descriptive name, no number suffixes
- Test class: `Test<Feature>` for grouping related tests (e.g., `TestConvictionScorer`)

**Structure:**
```
tests/
├── conftest.py              # Root fixtures (sys.path setup)
├── unit/
│   ├── conftest.py          # Shared unit test fixtures (make_trade, make_event, etc.)
│   ├── test_capital_manager.py
│   ├── test_conviction.py
│   ├── test_comparison_metrics.py
│   └── ...
├── integration/
│   ├── test_session_replay.py
│   ├── test_validation_pipeline.py
│   └── ...
└── tools/
    └── verify_trades.py
```

## Test Structure

**Suite Organization:**

From `tests/unit/test_capital_manager.py`:
```python
"""Comprehensive TDD tests for CapitalManager two-tier floor system.

Tests cover:
- TradingMode transitions (NORMAL -> SOFT_FLOOR -> HARD_FLOOR)
- High water mark tracking
- Entry gating based on mode and quality score
- Position management gating based on mode
- Recovery back to NORMAL
- Edge cases (zero equity, zero HWM)
"""

import pytest
from decimal import Decimal
from src.core.capital_manager import TradingMode, CapitalManager


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def make_capital_manager(
    soft_floor_pct: Decimal = Decimal("90.0"),
    hard_floor_pct: Decimal = Decimal("70.0"),
    exceptional_quality_threshold: Decimal = Decimal("0.85")
) -> CapitalManager:
    """Create CapitalManager with specified thresholds."""
    return CapitalManager(...)


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

def test_initialize_sets_hwm_and_normal_mode():
    """Initialize with $100 should set HWM=$100 and mode=NORMAL."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    assert cm.high_water_mark == Decimal("100.00")
    assert cm.mode == TradingMode.NORMAL
```

**Patterns:**
- Module docstring describing what's tested and key scenarios
- Section headers with `# ============================================================================` dividing test groups
- Helper function section: Create factories, builders for test data
- Test grouping: Related tests grouped in sections (INITIALIZATION, MODE DETECTION, GATING, etc.)
- One assertion focus per test (or related assertions on same concept)

## Mocking

**Framework:** None explicitly used; tests use real objects and factories instead

**Patterns:**
Tests avoid mocks by using:
- Factories (`make_capital_manager()`, `make_trade()`, `make_event()`)
- Real dataclass instances created in fixtures
- Direct instantiation of classes being tested
- No monkeypatching or Mock objects observed

**What to Mock:**
- Nothing - codebase prefers real objects with controlled initialization

**What NOT to Mock:**
- Everything - the pattern is to use factories and real instances
- Network calls: Not tested (live mode integration not in test suite)
- File I/O: Tested with real files in `data/sessions/` directory

**Example: Factories instead of mocks**

From `tests/unit/conftest.py`:
```python
def make_trade(**overrides) -> LeaderTrade:
    """Create a LeaderTrade with sensible defaults.

    Defaults:
        timestamp=now(UTC), market_id="test_market", token_id="test_token"
        side=UP, action=BUY, dollars=10, price=0.50, shares=20, source="test"

    Usage:
        trade = make_trade(price=Decimal("0.70"), shares=Decimal("15"))
    """
    defaults = {
        "timestamp": datetime.now(timezone.utc),
        "market_id": "test_market",
        "token_id": "test_token",
        "side": TradeSide.UP,
        "action": TradeAction.BUY,
        "dollars": Decimal("10"),
        "price": Decimal("0.50"),
        "shares": Decimal("20"),
        "source": "test",
        "tx_hash": None,
        "latency_ms": 0,
    }
    defaults.update(overrides)
    return LeaderTrade(**defaults)
```

## Fixtures and Factories

**Test Data:**

From `tests/unit/conftest.py`:
```python
@pytest.fixture
def base_config() -> StrategyConfig:
    """Return StrategyConfig with $100 starting capital defaults matching production.

    Matches the production config:
    - starting_capital=$100, hourly_budget=$100
    - cash_reserve_pct=10%, leader_capital=$900, k_factor=0.85
    - per_market_cap_pct=30%, per_side_pct=26%, global_exposure_pct=100%
    """
    return StrategyConfig(
        starting_capital=Decimal("100"),
        hourly_budget=Decimal("100"),
        cash_reserve_pct=Decimal("10"),
        leader_capital=Decimal("900"),
        k_factor=Decimal("0.85"),
        per_market_cap_pct=Decimal("30"),
        per_side_pct=Decimal("26"),
        global_exposure_pct=Decimal("100"),
    )


@pytest.fixture
def fresh_portfolio() -> Portfolio:
    """Return a new Portfolio instance with empty state.

    Use this for tests that need a clean portfolio state.
    """
    return Portfolio()
```

From `tests/unit/test_comparison_metrics.py`:
```python
@pytest.fixture
def sample_equity_df_profitable():
    """Create equity DataFrame for a profitable strategy."""
    timestamps = pd.date_range(
        start="2026-02-04 10:00:00",
        periods=20,
        freq="5min",
        tz=timezone.utc
    )
    # Profitable with some drawdowns
    # Total return: (120 - 100) / 100 = 20%
    equities = [100, 102, 104, 103, 106, 108, 105, 110, 112, 109,
                113, 115, 112, 116, 118, 115, 119, 121, 118, 120]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "equity": equities
    })
    df.set_index("timestamp", inplace=True)
    return df
```

**Location:**
- Shared unit fixtures: `tests/unit/conftest.py`
- Root fixtures (sys.path setup): `tests/conftest.py`
- Test-specific fixtures: Defined in test file with `@pytest.fixture`
- Helper functions (not fixtures): Factory functions like `make_trade()` that accept kwargs

## Coverage

**Requirements:** No explicit coverage target found

**View Coverage:**
```bash
pytest --cov=src --cov-report=html tests/
# Then open htmlcov/index.html
```

## Test Types

**Unit Tests:**
- Scope: Single class/function in isolation
- Approach: Use factories for dependencies, test one behavior per function
- Examples: `test_capital_manager.py`, `test_conviction.py`, `test_comparison_metrics.py`
- Coverage: Core business logic (capital management, sizing, conviction, metrics)
- Location: `tests/unit/`

**Integration Tests:**
- Scope: Full strategy replay against recorded session data
- Approach: Use real SessionReplayer, compare output against regression baselines
- Examples: `test_session_replay.py` (8 strategies replayed on same session), `test_validation_pipeline.py`
- Location: `tests/integration/`
- Markers: `@pytest.mark.parametrize("strategy_name", list_strategies())` to run all strategies
- Fixtures: Loaded from `data/sessions/` directory (real session JSONL files)

**E2E Tests:**
- Not used in current test suite
- Integration tests serve as primary validation

## Common Patterns

**Test Function Pattern:**

Descriptive one-liner name + docstring explaining assertion:
```python
def test_soft_floor_at_threshold():
    """Equity at exactly 90% of HWM (10% DD) should trigger SOFT_FLOOR."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("90.00"))
    assert mode == TradingMode.SOFT_FLOOR
    assert cm.mode == TradingMode.SOFT_FLOOR
```

**Async Testing:**
Not heavily used; one test uses pytest-asyncio but async is minimal:
```python
# Pattern when needed (not common in this codebase):
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_func()
    assert result == expected
```

**Error Testing:**

Pattern: Expect specific exception type with message validation
```python
def test_invalid_input_raises_error():
    """Invalid input should raise PortfolioInvariantError."""
    portfolio = Portfolio()

    with pytest.raises(PortfolioInvariantError) as exc_info:
        portfolio.apply_buy("token", "market", Side.UP, Decimal("-10"), Decimal("0.50"))

    assert "must be positive" in str(exc_info.value)
```

**Parametrized Testing:**

Pattern: Test same logic across multiple scenarios
```python
@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_completes_without_error(strategy_name):
    """Test that each strategy can replay the full session without crashing."""
    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(SESSION_FILE, strategy)
    event_count = replayer.load()
    assert event_count > 0
    result = replayer.run()
    assert result.events_processed > 0
```

**Regression Testing Pattern:**

From `tests/integration/test_session_replay.py`:
```python
# Expected baselines for regression testing
# Values captured from session_20260129_191609.jsonl (232 events after dedup)
# These lock down exact behavior for regression detection
EXPECTED_BASELINES = {
    "aggressive_mirror": {"buys": 24, "sells": 26, "skips": 182},
    "conservative_mirror": {"buys": 26, "sells": 25, "skips": 181},
    # ...
}

@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_matches_baseline(strategy_name):
    """Verify strategy produces exact baseline trade counts."""
    # ... setup ...
    result = replayer.run()

    baseline = EXPECTED_BASELINES[strategy_name]
    assert result.buys == baseline["buys"], f"{strategy_name}: buy count mismatch"
    assert result.sells == baseline["sells"], f"{strategy_name}: sell count mismatch"
```

**Assertion Style:**

Use pytest's standard assertions with descriptive messages:
```python
assert mode == TradingMode.SOFT_FLOOR
assert cm.mode == TradingMode.SOFT_FLOOR
assert event_count > 0, f"{strategy_name}: Should load events from session file"
assert result.strategy_name == strategy_name, f"Result should have correct strategy name"
```

## Test Discovery

**Automatic:** pytest discovers:
- `test_*.py` files in `tests/` and subdirectories
- Functions named `test_*()` and `Test*` classes
- Fixtures from `conftest.py` files at multiple levels

**Root conftest setup:**
```python
# tests/conftest.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
```

This enables `from src.xxx import` in all tests without path manipulation.

---

*Testing analysis: 2026-02-05*
