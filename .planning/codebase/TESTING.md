# Testing Patterns

**Analysis Date:** 2026-01-30

## Test Framework

**Runner:**
- `pytest` (standard Python testing framework)
- No explicit pytest.ini found; uses default discovery (`test_*.py`, `*_test.py`)

**Assertion Library:**
- Python's built-in `assert` statement

**Run Commands:**
```bash
pytest tests/ -v                    # Run all tests with verbose output
pytest tests/unit/test_scaling.py -v  # Run specific test file
pytest tests/ --cov=src             # Run with coverage report
pytest tests/tools/verify_trades.py -v -s  # Run tool test with stdout capture disabled
```

## Test File Organization

**Location:**
- Mirrors source structure: `src/X/module.py` → `tests/X/test_module.py`
- Integration tests: `tests/integration/`
- Unit tests: `tests/unit/`
- Tools/utilities: `tests/tools/`

**Naming:**
- Test functions: `test_<function_or_feature>_<scenario>()`
- Test classes: Not used in this codebase (functional style preferred)
- Test files: `test_<module_under_test>.py`

**Structure:**
```
tests/
├── __init__.py
├── unit/
│   ├── __init__.py
│   ├── test_caps.py (deleted in refactor)
│   ├── test_decision.py (deleted in refactor)
│   └── test_scaling.py (deleted in refactor)
├── integration/
│   ├── __init__.py
│   └── test_dry_run_smoke.py
└── tools/
    ├── __init__.py
    ├── verify_trades.py (pytest-compatible + CLI tool)
    └── __main__.py (for `python -m tests.tools.verify_trades`)
```

## Test Structure

**Suite Organization:**
```python
# From tests/integration/test_dry_run_smoke.py

import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.strategies.mirror.strategy import MirrorStrategy
from src.framework.replay import SessionReplayer


def test_replay_session_file():
    """Test replaying a recorded session produces expected results."""
    session_path = Path("data/sessions/session_20260129_191609.jsonl")

    if not session_path.exists():
        pytest.skip(f"Session file not found: {session_path}")

    # Create strategy
    strategy = MirrorStrategy()

    # Create replayer
    replayer = SessionReplayer(
        session_path=session_path,
        strategy=strategy,
    )

    # Load events
    event_count = replayer.load()
    assert event_count > 0, "Should load events from session file"

    # Run replay
    result = replayer.run()

    # Assertions
    expected_buys = 36
    expected_sells = 43
    assert result.buys_executed == expected_buys, f"Expected {expected_buys} buys, got {result.buys_executed}"
    assert result.sells_executed == expected_sells, f"Expected {expected_sells} sells, got {result.sells_executed}"
```

**Patterns:**
- **Setup:** Direct instantiation of objects under test (no fixtures yet)
- **Skip conditions:** `pytest.skip()` for missing data files or config
- **Assertions:** Simple `assert` with descriptive messages
- **Teardown:** Implicit (no state cleanup needed for these tests)

## Mocking

**Framework:** Manual mocking (no external framework like `unittest.mock` used)

**Approach:**
Tests use real objects rather than mocks. Example from `tests/tools/verify_trades.py`:
```python
def run_verification(
    leader_address: str,
    lookback_blocks: int = 500,
    api_trade_limit: int = 100,
) -> VerificationReport:
    # Real instantiation of data sources
    data_source = LiveDataSource(leader_address=leader_address)
    api_trades = fetch_api_trades(data_source, leader_address, limit=api_trade_limit)

    # Real blockchain detector
    detector = BlockchainDetector(leader_address=leader_address)
    chain_trades = scan_blockchain(detector, data_source._token_to_market, lookback_blocks=lookback_blocks)

    # Correlate real data
    report = correlate(api_trades, chain_trades)
```

**What to Mock:**
- Network calls when testing offline
- Time (using `SimulatedClock` for deterministic behavior)
- File I/O for data loading (use test data files)

**What NOT to Mock:**
- Core business logic (strategies, portfolio management)
- Data models and transformations
- Decision logic

**Rationale:** The system is designed for verification; testing with real data structures ensures correctness of decision-making.

## Fixtures and Factories

**Test Data:**
No explicit pytest fixtures (`@pytest.fixture`) in current codebase.

**Data Loading Pattern:**
Tests load real session files from disk:
```python
session_path = Path("data/sessions/session_20260129_191609.jsonl")
if not session_path.exists():
    pytest.skip(f"Session file not found: {session_path}")
```

**Factory Pattern for Config:**
```python
# From tests/tools/verify_trades.py
from src.core.config import load_config

try:
    config = load_config()  # Loads from config/config.yaml
except FileNotFoundError:
    import pytest
    pytest.skip("Config file not found")
```

**Strategy Creation Pattern:**
```python
# Direct instantiation
strategy = MirrorStrategy()

# Or via registry
from src.strategies import get_strategy
strategy = get_strategy("mirror")  # Creates fresh instance
```

## Coverage

**Requirements:** Not enforced (no `.coverage` config, no CI pipeline)

**View Coverage:**
```bash
pytest tests/ --cov=src
pytest tests/ --cov=src --cov-report=html
```

**Current State:**
- Core strategy logic has implicit coverage (tests/integration/test_dry_run_smoke.py exercises replay)
- Tool tests cover blockchain/API correlation (`tests/tools/verify_trades.py`)
- Many unit tests deleted in refactor (test_caps.py, test_decision.py, test_scaling.py, test_state_machine.py removed)

## Test Types

**Unit Tests:**
- **Scope:** Individual functions or classes in isolation
- **Approach:** Direct assertions on return values
- **Examples (deleted in refactor):** test_caps.py, test_decision.py, test_scaling.py
- **Current Status:** Minimal unit test coverage (strategy tests through replay framework instead)

**Integration Tests:**
- **Scope:** Full strategy execution against recorded market data
- **Approach:** Load session file → run strategy → verify trade counts and decisions
- **Examples:** `tests/integration/test_dry_run_smoke.py` - exercises UniversalRunner, strategy, execution adapter
- **Data:** Real recorded sessions in `data/sessions/*.jsonl` files

**E2E Tests:**
- **Framework:** Not formal E2E tests; verification tools serve this purpose
- **Approach:** `tests/tools/verify_trades.py` - Polymarket API correlation test
  - Fetches real API trades
  - Scans blockchain for leader trades
  - Correlates by transaction hash and fuzzy matching
  - Verifies no trades were missed and no duplicates exist

**Tool Tests (Pytest-Compatible):**
```python
# From tests/tools/verify_trades.py

def test_verify_trades():
    """Pytest-compatible: verifies blockchain detection matches API trades."""
    from src.core.config import load_config

    try:
        config = load_config()
    except FileNotFoundError:
        import pytest
        pytest.skip("Config file not found")

    if not config.leader.address:
        import pytest
        pytest.skip("No leader address configured")

    report = run_verification(
        leader_address=config.leader.address,
        lookback_blocks=300,
        api_trade_limit=50,
    )

    # Assertions
    assert report.api_trades_fetched > 0
    # ... more assertions
    assert report.duplicate_count == 0
```

Can be run as:
```bash
pytest tests/tools/verify_trades.py -v -s
python -m tests.tools.verify_trades --address 0x... --lookback 500 --trades 100
```

## Common Patterns

**Async Testing:**
Not used (no async/await in codebase; uses synchronous polling with time.sleep()).

**Error Testing:**
Explicit error path testing through skip reasons in strategy decisions.

**Example from strategy testing:**
```python
def _skip(self, reason: str) -> TradeDecision:
    self.skips += 1
    self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
    return TradeDecision.skip(reason)
```

Tests verify skip reasons are counted:
```python
result = replayer.run()
assert result.buys_executed == expected_buys
assert result.sells_executed == expected_sells
# skip_reasons dict available for analysis
```

## Test-Specific Clock Pattern

**Deterministic Time Handling:**
`src/core/clock.py` provides injectable clock for testing:

```python
from src.core.clock import SimulatedClock
from datetime import datetime

# In tests or replay:
clock = SimulatedClock(start=datetime(2026, 1, 29, 12, 0, 0))
clock.advance(300)  # Move forward 5 minutes
clock.set_time(some_datetime)  # Jump to specific time

# Strategy receives events with deterministic timestamps
event = MarketEvent(
    trade=LeaderTrade(..., timestamp=clock.now()),
    prices=...
)
```

This enables:
- Deterministic replay of recorded sessions
- Hourly budget reset testing (checks `_current_hour` changes)
- Consistent test results regardless of wall-clock time

## Testing Philosophy

**Emphasis on Replay & Verification:**
Rather than unit test every component, the codebase emphasizes:

1. **Session Recording** (`src/framework/recorder.py`): Capture real market events
2. **Deterministic Replay** (`src/framework/replay.py`): Re-run strategy against same events
3. **Exact Match Verification**: Compare buy/sell counts, skip reasons, portfolio state
4. **Tool-Based Integration**: `verify_trades.py` correlates blockchain/API data

**Benefits:**
- Tests run on real market data (discovered in refactor: session_20260129_191609.jsonl)
- Each strategy can be backtested without mocking
- Regression detection: if replay counts change, strategy broke
- Audit trail: recorded sessions can be replayed anytime

## Pytest Configuration

**pytest.ini:** Not found (uses defaults)

**Default Discovery:**
- Test files: `test_*.py` or `*_test.py`
- Test functions: `test_*`
- Test classes: `Test*`

**No Configuration:**
- No custom markers
- No test collection filters
- No parallel execution configured

---

*Testing analysis: 2026-01-30*
