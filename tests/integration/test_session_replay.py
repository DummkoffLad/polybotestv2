"""Session replay integration tests - validates strategies against real session data.

Tests all 8 strategies replaying the same session to:
- Verify strategies complete without errors
- Lock down regression baselines (exact buy/sell/skip counts)
- Verify PnL accounting consistency (realized + unrealized = total)
- Confirm deterministic behavior (same inputs = same outputs)
"""

import pytest
from pathlib import Path
from decimal import Decimal

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.strategies import list_strategies, get_strategy
# Import directly from module to avoid circular import through __init__.py
from src.framework.replay import SessionReplayer


# Session file to use for integration testing
# Using session_20260129_191609.jsonl (234 events, already proven in test_dry_run_smoke.py)
SESSION_FILE = Path("data/sessions/session_20260129_191609.jsonl")


# Expected baselines for regression testing
# Values captured from session_20260129_191609.jsonl (232 events after dedup)
# These lock down exact behavior for regression detection
# Format: {"strategy_name": {"buys": N, "sells": N, "skips": N}}
EXPECTED_BASELINES = {
    "aggressive_mirror": {"buys": 24, "sells": 26, "skips": 182},
    "conservative_mirror": {"buys": 26, "sells": 25, "skips": 181},
    "hybrid_conservative": {"buys": 27, "sells": 26, "skips": 179},
    "mirror": {"buys": 23, "sells": 22, "skips": 187},  # Phase 4 Kelly integration: conviction multiplier adjusts sizes, baselines unchanged from Phase 3
    "momentum_mirror": {"buys": 25, "sells": 24, "skips": 183},
    "price_level": {"buys": 28, "sells": 26, "skips": 178},
    "spread_aware": {"buys": 23, "sells": 24, "skips": 185},
    "velocity": {"buys": 26, "sells": 27, "skips": 179},
}


def test_session_file_exists():
    """Verify the session file exists before running replay tests."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    assert SESSION_FILE.exists(), f"Session file should exist: {SESSION_FILE}"


@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_completes_without_error(strategy_name):
    """Test that each strategy can replay the full session without crashing."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    # Create strategy instance
    strategy = get_strategy(strategy_name)

    # Create replayer
    replayer = SessionReplayer(
        session_path=SESSION_FILE,
        strategy=strategy,
    )

    # Load events
    event_count = replayer.load()
    assert event_count > 0, f"{strategy_name}: Should load events from session file"

    # Run replay - should not raise any exception
    result = replayer.run()

    # Verify result has processed events
    assert result.events_processed > 0, f"{strategy_name}: Should process events"
    assert result.strategy_name == strategy_name, f"Result should have correct strategy name"


@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_pnl_consistency(strategy_name):
    """Verify PnL accounting identity: total_pnl = realized_pnl + unrealized_pnl."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(session_path=SESSION_FILE, strategy=strategy)
    replayer.load()
    result = replayer.run()

    # PnL accounting identity
    expected_total = result.realized_pnl + result.unrealized_pnl

    assert result.total_pnl == expected_total, (
        f"{strategy_name}: PnL accounting broken - "
        f"total={result.total_pnl}, realized={result.realized_pnl}, "
        f"unrealized={result.unrealized_pnl}, expected_total={expected_total}"
    )


@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_nonnegative_counts(strategy_name):
    """Verify decision counts are non-negative and sum to events_processed."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(session_path=SESSION_FILE, strategy=strategy)
    replayer.load()
    result = replayer.run()

    # All counts should be non-negative
    assert result.buys_executed >= 0, f"{strategy_name}: buys_executed should be >= 0"
    assert result.sells_executed >= 0, f"{strategy_name}: sells_executed should be >= 0"
    assert result.skips >= 0, f"{strategy_name}: skips should be >= 0"

    # Counts should sum to total events
    total_decisions = result.buys_executed + result.sells_executed + result.skips
    assert total_decisions == result.events_processed, (
        f"{strategy_name}: Decision counts don't sum to events_processed - "
        f"buys={result.buys_executed}, sells={result.sells_executed}, "
        f"skips={result.skips}, total={total_decisions}, "
        f"events_processed={result.events_processed}"
    )


@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_deterministic(strategy_name):
    """Verify replay is deterministic - same inputs produce same outputs."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    # Run replay TWICE with same inputs
    results = []
    for run in range(2):
        strategy = get_strategy(strategy_name)
        replayer = SessionReplayer(session_path=SESSION_FILE, strategy=strategy)
        replayer.load()
        result = replayer.run()
        results.append(result)

    # Both runs should produce identical decision counts
    assert results[0].buys_executed == results[1].buys_executed, (
        f"{strategy_name}: Non-deterministic buys - "
        f"run1={results[0].buys_executed}, run2={results[1].buys_executed}"
    )
    assert results[0].sells_executed == results[1].sells_executed, (
        f"{strategy_name}: Non-deterministic sells - "
        f"run1={results[0].sells_executed}, run2={results[1].sells_executed}"
    )
    assert results[0].skips == results[1].skips, (
        f"{strategy_name}: Non-deterministic skips - "
        f"run1={results[0].skips}, run2={results[1].skips}"
    )


@pytest.mark.skipif(not EXPECTED_BASELINES, reason="Baselines not yet captured")
@pytest.mark.parametrize("strategy_name", list_strategies())
def test_replay_baselines(strategy_name):
    """Test that replay matches expected regression baselines."""
    if not SESSION_FILE.exists():
        pytest.skip(f"Session file not found: {SESSION_FILE}")

    strategy = get_strategy(strategy_name)
    replayer = SessionReplayer(session_path=SESSION_FILE, strategy=strategy)
    replayer.load()
    result = replayer.run()

    # Get expected baseline
    expected = EXPECTED_BASELINES.get(strategy_name)
    if not expected:
        pytest.skip(f"No baseline for {strategy_name}")

    # Verify exact match
    assert result.buys_executed == expected["buys"], (
        f"{strategy_name}: Baseline mismatch for buys - "
        f"expected={expected['buys']}, got={result.buys_executed}"
    )
    assert result.sells_executed == expected["sells"], (
        f"{strategy_name}: Baseline mismatch for sells - "
        f"expected={expected['sells']}, got={result.sells_executed}"
    )
    assert result.skips == expected["skips"], (
        f"{strategy_name}: Baseline mismatch for skips - "
        f"expected={expected['skips']}, got={result.skips}"
    )


# ============================================================================
# BASELINE CAPTURE UTILITY
# ============================================================================

def capture_baselines():
    """Capture baseline values for all strategies.

    Run this manually once to populate EXPECTED_BASELINES dict:
        python -m pytest tests/integration/test_session_replay.py::capture_baselines -v -s
    """
    if not SESSION_FILE.exists():
        print(f"Session file not found: {SESSION_FILE}")
        return

    print("\n" + "="*60)
    print("CAPTURING REPLAY BASELINES")
    print("="*60)

    baselines = {}

    for strategy_name in list_strategies():
        strategy = get_strategy(strategy_name)
        replayer = SessionReplayer(session_path=SESSION_FILE, strategy=strategy)
        replayer.load()
        result = replayer.run()

        baselines[strategy_name] = {
            "buys": result.buys_executed,
            "sells": result.sells_executed,
            "skips": result.skips,
        }

        print(f"\n{strategy_name}:")
        print(f"  buys={result.buys_executed}, sells={result.sells_executed}, skips={result.skips}")
        print(f"  total_pnl={result.total_pnl:.2f}, events={result.events_processed}")

    print("\n" + "="*60)
    print("EXPECTED_BASELINES = {")
    for strategy_name in sorted(baselines.keys()):
        b = baselines[strategy_name]
        print(f'    "{strategy_name}": {{"buys": {b["buys"]}, "sells": {b["sells"]}, "skips": {b["skips"]}}},')
    print("}")
    print("="*60)


if __name__ == "__main__":
    # Run baseline capture when executed directly
    capture_baselines()
