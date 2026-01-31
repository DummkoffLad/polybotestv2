"""Replay test - verifies session replay works correctly."""

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
    
    # Expected values (current baselines after recent strategy changes)
    # Note: These match the baselines in test_session_replay.py for mirror strategy
    expected_buys = 25
    expected_sells = 24
    
    # Verify exact match
    assert result.buys_executed == expected_buys, f"Expected {expected_buys} buys, got {result.buys_executed}"
    assert result.sells_executed == expected_sells, f"Expected {expected_sells} sells, got {result.sells_executed}"
    
    # Calculate diff
    buy_diff = abs(result.buys_executed - expected_buys)
    sell_diff = abs(result.sells_executed - expected_sells)
    total_diff = buy_diff + sell_diff
    
    assert total_diff == 0, f"Total diff should be 0, got {total_diff}"
    
    print(f"\nOK Replay: {result.buys_executed} buys, {result.sells_executed} sells, 0 diff")