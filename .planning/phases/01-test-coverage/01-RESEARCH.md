# Phase 1: Test Coverage - Research

**Researched:** 2026-01-30
**Domain:** Python testing for trading strategies, risk caps, and financial calculations
**Confidence:** HIGH

## Summary

This phase requires testing 8 trading strategy implementations, risk cap enforcement logic, and portfolio math (cost basis, PnL) using real recorded session data as input fixtures. The codebase is Python-based with existing Decimal-based financial calculations and a session recording/replay framework already in place.

The standard approach is pytest with parameterized tests, using recorded session files (JSONL format, ~234 events per 40-minute session) as fixtures. Expected outputs should be derived by tracing code logic with known inputs and locked as regression baselines. Python's Decimal module provides the precision needed for financial calculations, and pytest.approx or Decimal quantization handles comparison tolerances.

Key findings:
- All 8 strategies inherit from Strategy base class with consistent interface (on_event, on_fill, get_state)
- Portfolio math is centralized in Portfolio class with safety invariant checks
- Risk caps are percentage-based and enforced during buy/sell decision logic in each strategy
- Session replay framework already exists, integration test demonstrates the pattern
- Pytest ecosystem provides excellent regression testing tools (pytest-regressions, snapshot testing)

**Primary recommendation:** Use pytest with parameterized fixtures loading recorded sessions, test each strategy's outputs against code-derived expected values, use pytest.mark for organizing tests (per-strategy, portfolio, risk), and leverage pytest-regressions for maintaining expected output baselines.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 8.0+ | Test framework | Python standard, powerful parametrization and fixtures |
| pytest-asyncio | 0.23+ | Async test support | Already in requirements.txt, needed if testing async code |
| Decimal | stdlib | Financial precision | Python standard library, required for money calculations |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest-regressions | 2.5+ | Regression testing with file-based baselines | For maintaining known-good output files (optional) |
| pytest-parametrize-cases | latest | Enhanced parametrization | If test cases grow complex (optional) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pytest | unittest | pytest has superior fixtures, parametrization, and discovery |
| pytest-regressions | pytest-snapshot/syrupy | All viable; regressions better for structured data like dicts |
| File-based baselines | Inline expected values | Files better for large outputs; inline better for simple values |

**Installation:**
```bash
pip install pytest>=8.0 pytest-asyncio>=0.23
# Optional regression testing
pip install pytest-regressions>=2.5
```

## Architecture Patterns

### Recommended Test Structure
```
tests/
├── unit/
│   ├── test_strategies.py        # All 8 strategy tests
│   ├── test_portfolio.py          # Cost basis, PnL calculations
│   ├── test_risk_caps.py          # Risk enforcement logic
│   └── conftest.py                # Shared fixtures
├── integration/
│   ├── test_session_replay.py     # Full session replays (already exists)
│   └── conftest.py                # Session file fixtures
└── fixtures/
    └── sessions/                  # Symlink or copy of data/sessions/
```

### Pattern 1: Parameterized Strategy Tests
**What:** Single test function that runs against all 8 strategies with known inputs
**When to use:** Testing common behavior across all strategies (e.g., extreme price handling)
**Example:**
```python
# Source: pytest official docs + codebase analysis
import pytest
from decimal import Decimal
from src.strategies.base import get_strategy

STRATEGIES = ["mirror", "momentum_mirror", "conservative_mirror",
              "aggressive_mirror", "spread_aware_mirror", "velocity_mirror",
              "price_level_mirror", "hybrid_conservative_mirror"]

@pytest.mark.parametrize("strategy_name", STRATEGIES)
def test_strategy_skips_extreme_low_price(strategy_name, base_config):
    """All strategies should skip buys when ask <= 0.01"""
    strategy = get_strategy(strategy_name)
    strategy.initialize(base_config)

    event = create_event(action="BUY", ask=Decimal("0.01"))
    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "price_extreme_low"
```

### Pattern 2: Session Fixture Loading
**What:** Load recorded session files as pytest fixtures for replay testing
**When to use:** Integration tests that replay full sessions
**Example:**
```python
# Source: codebase test_dry_run_smoke.py + pytest docs
import pytest
from pathlib import Path

@pytest.fixture(scope="module")
def session_files():
    """Return list of all session files for testing"""
    session_dir = Path("data/sessions")
    return sorted(session_dir.glob("session_*.jsonl"))

@pytest.fixture
def session_events(session_files):
    """Load events from a session file"""
    # Load and parse JSONL
    events = []
    with open(session_files[0]) as f:
        for line in f:
            events.append(json.loads(line))
    return events
```

### Pattern 3: Decimal Precision Testing
**What:** Use Decimal quantization for exact financial comparisons
**When to use:** Testing portfolio math, PnL calculations
**Example:**
```python
# Source: Python Decimal docs + financial testing best practices
from decimal import Decimal

def test_portfolio_cost_basis_precision():
    """Cost basis calculation maintains USDC precision (4 decimals)"""
    portfolio = Portfolio()
    portfolio.apply_buy(
        token_id="test", market_id="test", side=Side.UP,
        shares=Decimal("10"), price=Decimal("0.5551")
    )

    expected_cost = Decimal("5.5510").quantize(Decimal("0.0001"))
    actual_cost = portfolio.get_total_deployed().quantize(Decimal("0.0001"))

    assert actual_cost == expected_cost
```

### Pattern 4: Risk Cap Scenario Testing
**What:** Test risk cap enforcement with sequential events that trigger caps
**When to use:** Testing per-market, per-side, global exposure caps
**Example:**
```python
# Source: Codebase analysis + test design patterns
def test_per_market_cap_enforcement(mirror_strategy, config):
    """Strategy respects per-market cap (30% = $30 for $100 capital)"""
    strategy = mirror_strategy
    market_id = "test_market"

    # First buy: $15 (under cap)
    event1 = create_buy_event(market_id=market_id, dollars=15, ask=0.5)
    decision1 = strategy.on_event(event1)
    assert decision1.action == DecisionAction.BUY
    strategy.on_fill(event1, decision1)

    # Second buy: $15 more (reaches cap)
    event2 = create_buy_event(market_id=market_id, dollars=15, ask=0.5)
    decision2 = strategy.on_event(event2)
    assert decision2.action == DecisionAction.BUY
    strategy.on_fill(event2, decision2)

    # Third buy: $5 more (EXCEEDS cap, should skip)
    event3 = create_buy_event(market_id=market_id, dollars=5, ask=0.5)
    decision3 = strategy.on_event(event3)
    assert decision3.action == DecisionAction.SKIP
    assert decision3.skip_reason == "market_cap"
```

### Anti-Patterns to Avoid
- **Testing with float:** Always use Decimal for financial calculations; float has precision issues
- **Hardcoded expected values:** Derive expected values from code logic, document assumptions
- **Coupling tests to implementation details:** Test public interface (on_event, on_fill), not private methods
- **Single large test function:** Break into focused tests per requirement (one test = one assertion about behavior)

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Float comparison with tolerance | Manual epsilon checks | pytest.approx() or Decimal.quantize() | Handles relative/absolute tolerance, edge cases (NaN, inf) |
| Test parametrization | Loop in test function | @pytest.mark.parametrize | Better test isolation, clearer failure reports, parallel execution |
| Regression baseline management | Manual JSON files + custom compare | pytest-regressions (data_regression fixture) | Handles update workflow, diff reporting, version control |
| Session file loading | Custom file parsing in each test | pytest fixture with scope | Reusable, cached, proper setup/teardown |
| Test discovery | Custom test runners | pytest's automatic discovery | Finds test_*.py files, supports marks, plugins, coverage |

**Key insight:** Pytest's fixture and parametrization system eliminates 90% of test boilerplate. The ecosystem has mature solutions for regression testing, data comparison, and test organization. Don't reinvent these wheels.

## Common Pitfalls

### Pitfall 1: Float Precision in Financial Calculations
**What goes wrong:** Using float for money amounts leads to rounding errors (0.1 + 0.2 != 0.3)
**Why it happens:** Binary floating-point cannot represent decimal fractions exactly
**How to avoid:** Always use Decimal for financial calculations and comparisons
**Warning signs:** Test failures with tiny differences (1e-15), inconsistent PnL calculations
```python
# WRONG
total = 0.1 + 0.2  # 0.30000000000000004

# CORRECT
from decimal import Decimal
total = Decimal("0.1") + Decimal("0.2")  # Decimal("0.3")
```

### Pitfall 2: Testing Expected Values Instead of Code Logic
**What goes wrong:** Expected outputs don't match what code actually produces; tests fail immediately
**Why it happens:** Manual verification is error-prone; assumptions about what "should" happen differ from code
**How to avoid:** Run code with known inputs, lock output as expected, verify logic makes sense
**Warning signs:** Tests fail on first run, expected values have arbitrary precision, "this should be X but code says Y"

### Pitfall 3: Tightly Coupled Tests Fail Together
**What goes wrong:** One strategy change breaks 50 tests; hard to isolate failures
**Why it happens:** Tests share state through class-level fixtures or global variables
**How to avoid:** Use function-scoped fixtures by default; strategies should initialize fresh per test
**Warning signs:** Test order matters, failures cascade, "works in isolation but fails in suite"

### Pitfall 4: Ignoring Risk Cap Boundary Conditions
**What goes wrong:** Caps work for normal cases but fail at boundaries (exactly at cap, $0.01 under cap)
**Why it happens:** Only testing happy path; boundary conditions have off-by-one errors
**How to avoid:** Test: (1) under cap, (2) exactly at cap, (3) just over cap for each cap type
**Warning signs:** Live trading hits unexpected cap rejection, caps trigger too early/late

### Pitfall 5: Not Testing Cascade Scenarios
**What goes wrong:** Individual risk checks pass but combined effect of multiple caps is untested
**Why it happens:** Tests focus on one cap at a time; real trading involves multiple constraints
**How to avoid:** Test scenarios where multiple caps interact (e.g., market cap + side cap + hourly budget)
**Warning signs:** Strategy behavior differs between single-trade tests and session replay

### Pitfall 6: Decimal Context Not Set Consistently
**What goes wrong:** Decimal precision varies between tests, causing flaky failures
**Why it happens:** Decimal uses global context; tests may set different precision
**How to avoid:** Set Decimal context explicitly in conftest.py or use quantize() on all comparisons
**Warning signs:** Tests pass/fail inconsistently, precision errors appear randomly

## Code Examples

Verified patterns from codebase and official sources:

### Loading Session Files as Fixtures
```python
# Source: tests/integration/test_dry_run_smoke.py
import pytest
from pathlib import Path

@pytest.fixture
def session_path():
    """Fixture providing path to test session file"""
    path = Path("data/sessions/session_20260129_191609.jsonl")
    if not path.exists():
        pytest.skip(f"Session file not found: {path}")
    return path

def test_replay_session(session_path):
    """Test replaying a recorded session produces expected results"""
    from src.framework.replay import SessionReplayer
    from src.strategies.mirror.strategy import MirrorStrategy

    strategy = MirrorStrategy()
    replayer = SessionReplayer(session_path=session_path, strategy=strategy)

    event_count = replayer.load()
    assert event_count > 0, "Should load events from session file"

    result = replayer.run()

    # Expected values derived from code analysis
    expected_buys = 36
    expected_sells = 43

    assert result.buys_executed == expected_buys
    assert result.sells_executed == expected_sells
```

### Testing Portfolio Math with Decimal Precision
```python
# Source: src/core/portfolio.py analysis + Decimal best practices
from decimal import Decimal
from src.core.portfolio import Portfolio
from src.core.types import Side

def test_portfolio_cost_basis_calculation():
    """Cost basis accumulates correctly for multiple buys"""
    portfolio = Portfolio()
    token_id = "test_token"

    # First buy: 10 shares @ 0.50
    portfolio.apply_buy(token_id, "market1", Side.UP,
                       Decimal("10"), Decimal("0.50"))

    # Second buy: 20 shares @ 0.60
    portfolio.apply_buy(token_id, "market1", Side.UP,
                       Decimal("20"), Decimal("0.60"))

    pos = portfolio.get(token_id)

    # Total cost: (10 * 0.50) + (20 * 0.60) = 17.00
    assert pos.cost_basis == Decimal("17.00")

    # Average price: 17.00 / 30 = 0.5667 (rounded to 4 decimals)
    expected_avg = Decimal("0.5667")
    assert pos.avg_price == expected_avg
    assert pos.shares == Decimal("30")

def test_portfolio_pnl_calculation():
    """Realized and unrealized PnL calculated correctly"""
    portfolio = Portfolio()
    token_id = "test_token"

    # Buy 10 shares @ 0.50
    portfolio.apply_buy(token_id, "market1", Side.UP,
                       Decimal("10"), Decimal("0.50"))

    # Sell 5 shares @ 0.60 (profit: 5 * (0.60 - 0.50) = 0.50)
    portfolio.apply_sell(token_id, "market1", Side.UP,
                        Decimal("5"), Decimal("0.60"))

    assert portfolio.realized_pnl == Decimal("0.50")

    # Unrealized PnL: 5 shares remaining, current price 0.70
    realized, unrealized = portfolio.calculate_pnl({
        token_id: Decimal("0.70")
    })

    # Unrealized: 5 * (0.70 - 0.50) = 1.00
    assert realized == Decimal("0.50")
    assert unrealized == Decimal("1.00")
```

### Parameterized Test for All Strategies
```python
# Source: pytest docs + codebase strategy registry
import pytest
from decimal import Decimal
from src.strategies.base import get_strategy, list_strategies, StrategyConfig
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide

@pytest.fixture
def base_config():
    """Standard config for testing"""
    return StrategyConfig(
        starting_capital=Decimal("100"),
        hourly_budget=Decimal("100"),
        leader_capital=Decimal("900"),
        k_factor=Decimal("0.85"),
        per_market_cap_pct=Decimal("30"),
        per_side_pct=Decimal("26"),
        global_exposure_pct=Decimal("100"),
    )

@pytest.mark.parametrize("strategy_name", list_strategies())
def test_all_strategies_skip_price_extreme_low(strategy_name, base_config):
    """All strategies should skip buys when ask <= 0.01 (treat as worthless)"""
    strategy = get_strategy(strategy_name)
    strategy.initialize(base_config)

    # Create event with extreme low price
    event = MarketEvent(
        trade=LeaderTrade(
            timestamp=datetime.now(timezone.utc),
            market_id="test_market",
            token_id="test_token",
            side=TradeSide.UP,
            action=TradeAction.BUY,
            dollars=Decimal("10"),
            shares=Decimal("100"),
            price=Decimal("0.10"),
        ),
        prices=PriceSnapshot(
            token_id="test_token",
            ask=Decimal("0.01"),  # Extreme low price
            bid=Decimal("0.009"),
        )
    )

    decision = strategy.on_event(event)

    assert decision.action == DecisionAction.SKIP
    assert decision.skip_reason == "price_extreme_low"
```

### Testing Risk Cap Enforcement
```python
# Source: Strategy implementation analysis
import pytest
from decimal import Decimal
from src.strategies.mirror.strategy import MirrorStrategy
from src.strategies.base import StrategyConfig, DecisionAction

def test_per_market_cap_enforced():
    """Mirror strategy enforces per-market cap (30% of capital)"""
    config = StrategyConfig(
        starting_capital=Decimal("100"),
        per_market_cap_pct=Decimal("30"),  # $30 max per market
        cash_reserve_pct=Decimal("10"),
        hourly_budget=Decimal("100"),
    )

    strategy = MirrorStrategy()
    strategy.initialize(config)

    market_id = "test_market"
    token_id = "test_token"

    # First buy: $20 (under cap)
    event1 = create_buy_event(
        market_id=market_id, token_id=token_id + "_1",
        leader_dollars=Decimal("200"), ask=Decimal("0.50")
    )
    decision1 = strategy.on_event(event1)
    # Should buy but capped by per-market limit
    assert decision1.action == DecisionAction.BUY
    assert decision1.dollars <= Decimal("30")
    strategy.on_fill(event1, decision1)

    # Check deployed capital in this market
    deployed = strategy.portfolio.get_market_exposure(market_id)
    assert deployed <= Decimal("30")

    # Second buy in same market: should hit cap
    event2 = create_buy_event(
        market_id=market_id, token_id=token_id + "_2",
        leader_dollars=Decimal("200"), ask=Decimal("0.50")
    )
    decision2 = strategy.on_event(event2)

    # Should either skip (if already at cap) or buy small amount (if under by pennies)
    if decision2.action == DecisionAction.BUY:
        assert decision2.dollars + deployed <= Decimal("30")
    else:
        assert decision2.skip_reason == "market_cap"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| unittest.TestCase | pytest with fixtures | ~2015 | Less boilerplate, better parametrization |
| Manual baseline files | pytest-regressions | ~2018 | Automated update workflow (--regtest-reset) |
| float for money | Decimal module | Always (stdlib) | Exact financial calculations, no rounding errors |
| Inline test data | Parametrized fixtures | pytest 2.9+ (2016) | Reusable test data, clearer separation |
| Custom JSON comparison | pytest.approx + Decimal.quantize | pytest 3.0+ (2016) | Handles tolerance elegantly |

**Deprecated/outdated:**
- `pytest.approx` for Decimal: Doesn't work with Decimal type; use Decimal.quantize() or exact equality
- `setup/teardown` methods: Use yield fixtures instead (more flexible, composable)
- `pytest.mark.skipif` without reason: Now requires reason parameter (pytest 6.0+)

## Open Questions

Things that couldn't be fully resolved:

1. **PnL Precision Tolerance**
   - What we know: USDC uses 6 decimals on-chain, code uses Decimal with 4-decimal quantization
   - What's unclear: Whether tests should enforce exact equality or allow tolerance for accumulated rounding
   - Recommendation: Start with exact equality (quantize to 4 decimals); relax if legitimate rounding issues appear

2. **Session File Selection**
   - What we know: 6 session files exist, ~40 minutes each, varying market conditions
   - What's unclear: Whether to use all sessions or hold one back for Phase 5 out-of-sample validation
   - Recommendation: Use 2 sessions for unit tests (per context doc), reserve others for integration/validation

3. **Synthetic Edge Cases**
   - What we know: Real session data covers normal trading scenarios
   - What's unclear: Whether to add synthetic events for edge cases (exactly at cap, rapid bursts, extreme prices)
   - Recommendation: Add synthetic unit tests for edge cases; use real sessions for integration

4. **Strategy Isolation vs. Composition**
   - What we know: 8 strategies exist, some are compositional (hybrid_conservative)
   - What's unclear: Whether composition strategies need separate tests or are covered by component tests
   - Recommendation: Test all 8 strategies individually; compositional behavior is part of strategy logic

5. **Bug Fix Policy**
   - What we know: Context says document bugs, don't fix; exception for risk cap money-losing bugs
   - What's unclear: How to distinguish "money-losing risk cap bug" from "intended behavior we disagree with"
   - Recommendation: Document all failures; flag risk cap failures that allow over-limit exposure as critical

## Sources

### Primary (HIGH confidence)
- **Codebase analysis** (local files)
  - `src/strategies/base.py` - Strategy interface, constants, validation
  - `src/core/portfolio.py` - Portfolio math implementation
  - `src/strategies/mirror/strategy.py` - Reference strategy implementation
  - `tests/integration/test_dry_run_smoke.py` - Existing replay test pattern
  - `data/sessions/session_20260129_191609.jsonl` - Session file format (234 events)
- **Python official docs** - https://docs.python.org/3/library/decimal.html (Decimal precision)
- **pytest official docs** - https://docs.pytest.org/en/stable/
  - Good practices: https://docs.pytest.org/en/stable/explanation/goodpractices.html
  - Parametrization: https://docs.pytest.org/en/stable/how-to/parametrize.html
  - Fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html

### Secondary (MEDIUM confidence)
- **pytest.approx for numeric testing** - [Pytest with Eric guide](https://pytest-with-eric.com/pytest-advanced/pytest-approx/)
  - Verified: approx() handles relative/absolute tolerance for float comparisons
  - Note: Does NOT work with Decimal type; use quantize() instead
- **pytest-regressions plugin** - [GitHub](https://github.com/ESSS/pytest-regressions), [PyPI](https://pypi.org/project/pytest-regressions/)
  - Verified: Provides data_regression fixture for dict/list baseline comparison
  - Workflow: --regtest-reset flag to update baselines
- **Decimal for financial calculations** - [Python Decimal guide (LabEx)](https://labex.io/tutorials/python-how-to-use-the-decimal-class-for-financial-calculations-in-python-398093)
  - Verified: Decimal module is standard for financial calculations, quantize() controls precision
- **Test organization best practices** - [Pytest with Eric (Nov 2024)](https://pytest-with-eric.com/pytest-best-practices/pytest-organize-tests/)
  - Verified: Structure tests/ to mirror src/, use conftest.py for shared fixtures

### Tertiary (LOW confidence - for awareness)
- **Trading strategy testing patterns** - [Backtesting.py docs](https://kernc.github.io/backtesting.py/)
  - General backtesting framework patterns; not directly applicable (we have replay framework)
- **Snapshot testing plugins** - [pytest-snapshot PyPI](https://pypi.org/project/pytest-snapshot/), [Syrupy](https://til.simonwillison.net/pytest/syrupy)
  - Alternative to pytest-regressions; similar functionality

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pytest is Python standard, Decimal is stdlib, codebase already uses both
- Architecture: HIGH - Patterns derived from existing codebase test + pytest official docs
- Pitfalls: HIGH - Based on codebase analysis (Decimal usage, risk cap logic) + common Python testing mistakes
- Code examples: HIGH - Adapted from actual codebase files + pytest official documentation
- Edge cases/open questions: MEDIUM - Require judgment calls based on context and goals

**Research date:** 2026-01-30
**Valid until:** 2026-03-30 (60 days - pytest stable, codebase structure unlikely to change)

**Notes for planner:**
- SessionReplayer framework already exists and works (test_dry_run_smoke.py proves it)
- Two session files recommended per context; suggest session_20260129_191609.jsonl (234 events) and one other
- All 8 strategies follow same interface; tests can be highly parameterized
- Risk cap testing is critical - user context emphasizes this is primary operating regime (<$100 budget)
- Decimal precision is non-negotiable for financial calculations; avoid pytest.approx, use quantize()
- Expected outputs should be CODE-DERIVED (trace through logic), not manually verified (per context)
