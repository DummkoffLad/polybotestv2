"""Tests for StrategyComparator - TDD RED phase.

Tests define expected behavior for the comparison infrastructure:
- StrategyComparator runs multiple strategies on same session with isolated state
- Each strategy receives identical market events in same order
- ComparisonResult aggregates all strategy results for downstream analysis
"""

import json
import pytest
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.comparison.comparator import (
    StrategyComparator,
    ComparisonResult,
    StrategyResult,
)
from src.strategies.base import (
    Strategy,
    StrategyConfig,
    TradeDecision,
    DecisionAction,
)
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from src.framework.replay import ReplayResult


# =============================================================================
# TEST FIXTURES
# =============================================================================

def create_test_session_file(events: list) -> Path:
    """Create a temporary session file with given events.

    Returns path to the temp file (caller must clean up).
    """
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)

    # Write session start
    session_start = {
        "type": "session_start",
        "session_id": "test_session_001",
        "timestamp": "2026-02-04T10:00:00+00:00",
        "config": {
            "scaling": {
                "our_capital": "100",
                "leader_estimated_capital": "900",
                "k_factor": "0.85"
            },
            "mirror_strategy": {
                "cash_reserve_pct": "10",
                "per_market_cap_pct": "30"
            }
        }
    }
    tmp.write(json.dumps(session_start) + '\n')

    # Write events
    for i, event in enumerate(events):
        tmp.write(json.dumps(event) + '\n')

    tmp.close()
    return Path(tmp.name)


def make_test_event(
    timestamp: str,
    token_id: str,
    action: str,
    dollars: float,
    price: float,
    bid: float,
    ask: float
) -> dict:
    """Create a test market event dict."""
    return {
        "type": "market_event",
        "timestamp": timestamp,
        "leader_trade": {
            "timestamp": timestamp,
            "market_id": f"market_{token_id[:8]}",
            "token_id": token_id,
            "side": "UP",
            "action": action,
            "leader_dollars": dollars,
            "leader_price": price,
            "leader_shares": dollars / price if price > 0 else 0,
            "source": "test"
        },
        "price_context": {
            "token_id": token_id,
            "bid": str(bid),
            "ask": str(ask),
            "spread_pct": str((ask - bid) / ask * 100) if ask > 0 else "0"
        }
    }


class AlwaysBuyStrategy(Strategy):
    """Test strategy that always buys (with tracking)."""

    def __init__(self):
        self._events_received = []
        self._fills = []
        self._initialized = False
        self._session_started = False

    @property
    def name(self) -> str:
        return "always_buy"

    def initialize(self, config: StrategyConfig) -> None:
        self._events_received = []
        self._fills = []
        self._initialized = True
        self._config = config

    def on_session_start(self) -> None:
        self._session_started = True

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._events_received.append(event)
        # Always buy $5
        return TradeDecision.buy(
            dollars=Decimal("5.00"),
            shares=Decimal("5.00") / event.prices.ask if event.prices.ask else Decimal("5"),
            price=event.prices.ask
        )

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        self._fills.append((event, decision))

    def get_state(self) -> Dict[str, Any]:
        return {
            "events_received": len(self._events_received),
            "fills": len(self._fills),
            "positions": {},
            "total_deployed": "0"
        }

    def calculate_pnl(self, final_prices) -> Tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")

    def on_session_end(self) -> Dict[str, Any]:
        return {}


class AlwaysSkipStrategy(Strategy):
    """Test strategy that always skips (with tracking)."""

    def __init__(self):
        self._events_received = []
        self._initialized = False

    @property
    def name(self) -> str:
        return "always_skip"

    def initialize(self, config: StrategyConfig) -> None:
        self._events_received = []
        self._initialized = True

    def on_session_start(self) -> None:
        pass

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._events_received.append(event)
        return TradeDecision.skip(reason="test_skip")

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        pass  # Never called for skips

    def get_state(self) -> Dict[str, Any]:
        return {
            "events_received": len(self._events_received),
            "positions": {},
            "total_deployed": "0"
        }

    def calculate_pnl(self, final_prices) -> Tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")


class CountingStrategy(Strategy):
    """Test strategy that tracks how many times each method is called.

    Useful for verifying isolation between strategy instances.
    """

    _instance_counter = 0  # Class variable to track instances

    def __init__(self):
        CountingStrategy._instance_counter += 1
        self._instance_id = CountingStrategy._instance_counter
        self._event_count = 0
        self._initialize_count = 0
        self._session_start_count = 0
        self._session_end_count = 0

    @property
    def name(self) -> str:
        return f"counting_{self._instance_id}"

    def initialize(self, config: StrategyConfig) -> None:
        self._initialize_count += 1
        self._event_count = 0  # Reset on initialize

    def on_session_start(self) -> None:
        self._session_start_count += 1

    def on_event(self, event: MarketEvent) -> TradeDecision:
        self._event_count += 1
        return TradeDecision.skip(reason="counting")

    def on_fill(self, event: MarketEvent, decision: TradeDecision) -> None:
        pass

    def get_state(self) -> Dict[str, Any]:
        return {
            "instance_id": self._instance_id,
            "event_count": self._event_count,
            "initialize_count": self._initialize_count,
            "session_start_count": self._session_start_count,
            "positions": {},
            "total_deployed": "0"
        }

    def calculate_pnl(self, final_prices) -> Tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")

    def on_session_end(self) -> Dict[str, Any]:
        self._session_end_count += 1
        return {}


@pytest.fixture
def sample_events():
    """Create a list of test events."""
    return [
        make_test_event("2026-02-04T10:01:00+00:00", "token_aaa111", "BUY", 100.0, 0.60, 0.59, 0.61),
        make_test_event("2026-02-04T10:02:00+00:00", "token_bbb222", "BUY", 50.0, 0.45, 0.44, 0.46),
        make_test_event("2026-02-04T10:03:00+00:00", "token_aaa111", "SELL", 80.0, 0.65, 0.64, 0.66),
    ]


@pytest.fixture
def session_file(sample_events):
    """Create a temporary session file."""
    path = create_test_session_file(sample_events)
    yield path
    # Cleanup
    try:
        path.unlink()
    except:
        pass


@pytest.fixture
def reset_counting_strategy():
    """Reset CountingStrategy instance counter before each test."""
    CountingStrategy._instance_counter = 0
    yield
    CountingStrategy._instance_counter = 0


# =============================================================================
# DATACLASS TESTS
# =============================================================================

class TestStrategyResult:
    """Tests for StrategyResult dataclass."""

    def test_strategy_result_has_required_fields(self):
        """StrategyResult should have strategy_name, replay_result, equity_df fields."""
        # Create minimal ReplayResult
        replay_result = ReplayResult(
            session_id="test_session",
            strategy_name="test_strategy"
        )

        # Create minimal equity DataFrame
        equity_df = pd.DataFrame({
            'timestamp': [datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone.utc)],
            'equity': [100.0]
        })

        # Create StrategyResult
        result = StrategyResult(
            strategy_name="test_strategy",
            replay_result=replay_result,
            equity_df=equity_df
        )

        assert result.strategy_name == "test_strategy"
        assert result.replay_result == replay_result
        assert len(result.equity_df) == 1
        assert result.equity_df['equity'].iloc[0] == 100.0


class TestComparisonResult:
    """Tests for ComparisonResult dataclass."""

    def test_comparison_result_has_required_fields(self):
        """ComparisonResult should have session_id, session_path, strategy_results, comparison_time."""
        # Create a comparison result
        result = ComparisonResult(
            session_id="test_session",
            session_path=Path("/tmp/test.jsonl"),
            strategy_results=[],
            comparison_time=datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone.utc)
        )

        assert result.session_id == "test_session"
        assert result.session_path == Path("/tmp/test.jsonl")
        assert result.strategy_results == []
        assert result.comparison_time == datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone.utc)

    def test_comparison_result_stores_multiple_strategy_results(self):
        """ComparisonResult should store a list of StrategyResult objects."""
        replay_result1 = ReplayResult(session_id="test", strategy_name="strategy1")
        replay_result2 = ReplayResult(session_id="test", strategy_name="strategy2")

        equity_df = pd.DataFrame({'timestamp': [], 'equity': []})

        sr1 = StrategyResult(
            strategy_name="strategy1",
            replay_result=replay_result1,
            equity_df=equity_df
        )
        sr2 = StrategyResult(
            strategy_name="strategy2",
            replay_result=replay_result2,
            equity_df=equity_df
        )

        result = ComparisonResult(
            session_id="test",
            session_path=Path("/tmp/test.jsonl"),
            strategy_results=[sr1, sr2],
            comparison_time=datetime.now(timezone.utc)
        )

        assert len(result.strategy_results) == 2
        assert result.strategy_results[0].strategy_name == "strategy1"
        assert result.strategy_results[1].strategy_name == "strategy2"


# =============================================================================
# STRATEGYCOMPARATOR INITIALIZATION TESTS
# =============================================================================

class TestStrategyComparatorInit:
    """Tests for StrategyComparator initialization."""

    def test_accepts_session_path_and_strategies(self, session_file):
        """StrategyComparator should accept session_path and list of strategies."""
        strategies = [AlwaysBuyStrategy(), AlwaysSkipStrategy()]

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=strategies
        )

        assert comparator is not None

    def test_raises_on_empty_strategy_list(self, session_file):
        """StrategyComparator should raise ValueError for empty strategy list."""
        with pytest.raises(ValueError, match="empty|strategies"):
            StrategyComparator(
                session_path=session_file,
                strategies=[]
            )

    def test_raises_on_nonexistent_session_path(self):
        """StrategyComparator should raise FileNotFoundError for missing session file."""
        with pytest.raises(FileNotFoundError):
            StrategyComparator(
                session_path=Path("/nonexistent/session.jsonl"),
                strategies=[AlwaysBuyStrategy()]
            )

    def test_accepts_single_strategy(self, session_file):
        """StrategyComparator should work with a single strategy."""
        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[AlwaysBuyStrategy()]
        )

        assert comparator is not None


# =============================================================================
# RUN_COMPARISON TESTS
# =============================================================================

class TestRunComparison:
    """Tests for StrategyComparator.run_comparison()."""

    def test_returns_comparison_result(self, session_file):
        """run_comparison should return a ComparisonResult object."""
        strategies = [AlwaysBuyStrategy(), AlwaysSkipStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        assert isinstance(result, ComparisonResult)

    def test_result_contains_all_strategies(self, session_file):
        """ComparisonResult should contain results for all strategies."""
        strategies = [AlwaysBuyStrategy(), AlwaysSkipStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        assert len(result.strategy_results) == 2

        # Verify both strategies are represented
        strategy_names = [sr.strategy_name for sr in result.strategy_results]
        assert "always_buy" in strategy_names
        assert "always_skip" in strategy_names

    def test_result_has_session_metadata(self, session_file):
        """ComparisonResult should include session_id and comparison_time."""
        strategies = [AlwaysBuyStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        assert result.session_id == "test_session_001"
        assert result.session_path == session_file
        assert result.comparison_time is not None
        assert isinstance(result.comparison_time, datetime)

    def test_strategies_receive_identical_events(self, session_file, reset_counting_strategy):
        """All strategies should receive the same events in the same order."""
        strategy1 = AlwaysBuyStrategy()
        strategy2 = AlwaysSkipStrategy()

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[strategy1, strategy2]
        )

        comparator.run_comparison()

        # Both strategies should have received 3 events
        assert len(strategy1._events_received) == 3
        assert len(strategy2._events_received) == 3

        # Events should be in same order (same token_ids)
        for i in range(3):
            assert strategy1._events_received[i].trade.token_id == strategy2._events_received[i].trade.token_id

    def test_strategies_have_isolated_state(self, session_file, reset_counting_strategy):
        """Each strategy should have isolated state (no cross-contamination)."""
        strategy1 = CountingStrategy()
        strategy2 = CountingStrategy()

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[strategy1, strategy2]
        )

        comparator.run_comparison()

        # Each strategy should have been initialized once
        assert strategy1._initialize_count == 1
        assert strategy2._initialize_count == 1

        # Each strategy should have seen session start once
        assert strategy1._session_start_count == 1
        assert strategy2._session_start_count == 1

        # Each strategy should have seen 3 events independently
        assert strategy1._event_count == 3
        assert strategy2._event_count == 3

    def test_strategy_results_have_replay_results(self, session_file):
        """Each StrategyResult should contain a valid ReplayResult."""
        strategies = [AlwaysBuyStrategy(), AlwaysSkipStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        for sr in result.strategy_results:
            assert sr.replay_result is not None
            assert isinstance(sr.replay_result, ReplayResult)
            assert sr.replay_result.events_processed == 3

    def test_always_buy_strategy_has_buys(self, session_file):
        """AlwaysBuyStrategy should have buys_executed > 0 in replay result."""
        strategies = [AlwaysBuyStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        buy_result = result.strategy_results[0]
        assert buy_result.strategy_name == "always_buy"
        assert buy_result.replay_result.buys_executed == 3  # 3 events, all bought
        assert buy_result.replay_result.skips == 0

    def test_always_skip_strategy_has_skips(self, session_file):
        """AlwaysSkipStrategy should have skips == events_processed."""
        strategies = [AlwaysSkipStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        skip_result = result.strategy_results[0]
        assert skip_result.strategy_name == "always_skip"
        assert skip_result.replay_result.skips == 3  # 3 events, all skipped
        assert skip_result.replay_result.buys_executed == 0

    def test_strategy_results_have_equity_dataframe(self, session_file):
        """Each StrategyResult should have an equity_df (may be empty)."""
        strategies = [AlwaysBuyStrategy()]
        comparator = StrategyComparator(session_path=session_file, strategies=strategies)

        result = comparator.run_comparison()

        sr = result.strategy_results[0]
        assert sr.equity_df is not None
        assert isinstance(sr.equity_df, pd.DataFrame)


# =============================================================================
# SEQUENTIAL EXECUTION TESTS
# =============================================================================

class TestSequentialExecution:
    """Tests verifying strategies are run sequentially (not parallel)."""

    def test_strategies_run_in_order(self, session_file, reset_counting_strategy):
        """Strategies should be run sequentially in the order provided."""
        # Use instance IDs to verify order
        strategy1 = CountingStrategy()  # instance_id = 1
        strategy2 = CountingStrategy()  # instance_id = 2
        strategy3 = CountingStrategy()  # instance_id = 3

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[strategy1, strategy2, strategy3]
        )

        result = comparator.run_comparison()

        # Results should be in same order as input
        assert len(result.strategy_results) == 3
        assert result.strategy_results[0].strategy_name == "counting_1"
        assert result.strategy_results[1].strategy_name == "counting_2"
        assert result.strategy_results[2].strategy_name == "counting_3"

    def test_each_strategy_gets_fresh_replay(self, session_file):
        """Each strategy should get its own fresh SessionReplayer instance."""
        # This is verified by checking that both strategies process all events
        # (if they shared a replayer, the second would see no events)
        strategy1 = AlwaysBuyStrategy()
        strategy2 = AlwaysSkipStrategy()

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[strategy1, strategy2]
        )

        result = comparator.run_comparison()

        # Both should have processed all 3 events
        buy_result = next(sr for sr in result.strategy_results if sr.strategy_name == "always_buy")
        skip_result = next(sr for sr in result.strategy_results if sr.strategy_name == "always_skip")

        assert buy_result.replay_result.events_processed == 3
        assert skip_result.replay_result.events_processed == 3


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

class TestEdgeCases:
    """Edge case tests for StrategyComparator."""

    def test_single_strategy_comparison(self, session_file):
        """Comparison with single strategy should work correctly."""
        strategy = AlwaysBuyStrategy()
        comparator = StrategyComparator(session_path=session_file, strategies=[strategy])

        result = comparator.run_comparison()

        assert len(result.strategy_results) == 1
        assert result.strategy_results[0].strategy_name == "always_buy"

    def test_empty_session_file(self, sample_events):
        """Comparison with session file containing no events should work."""
        # Create session file with no events (just session_start)
        path = create_test_session_file([])

        try:
            strategy = AlwaysBuyStrategy()
            comparator = StrategyComparator(session_path=path, strategies=[strategy])

            result = comparator.run_comparison()

            assert len(result.strategy_results) == 1
            assert result.strategy_results[0].replay_result.events_processed == 0
        finally:
            try:
                path.unlink()
            except:
                pass

    def test_many_strategies(self, session_file, reset_counting_strategy):
        """Comparison with many strategies should work correctly."""
        # Create 5 strategies
        strategies = [CountingStrategy() for _ in range(5)]

        comparator = StrategyComparator(session_path=session_file, strategies=strategies)
        result = comparator.run_comparison()

        assert len(result.strategy_results) == 5

        # All should have processed all events
        for sr in result.strategy_results:
            assert sr.replay_result.events_processed == 3

    def test_strategy_names_preserved(self, session_file):
        """Strategy names in results should match original strategy names."""
        buy_strategy = AlwaysBuyStrategy()
        skip_strategy = AlwaysSkipStrategy()

        comparator = StrategyComparator(
            session_path=session_file,
            strategies=[buy_strategy, skip_strategy]
        )

        result = comparator.run_comparison()

        # Verify names match
        assert result.strategy_results[0].strategy_name == buy_strategy.name
        assert result.strategy_results[1].strategy_name == skip_strategy.name


# =============================================================================
# INTEGRATION WITH TRACK_ANALYSIS
# =============================================================================

class TestTrackAnalysis:
    """Tests verifying integration with replay's track_analysis feature."""

    def test_equity_df_populated_when_available(self, session_file):
        """equity_df should be populated from replay analysis when available."""
        strategy = AlwaysBuyStrategy()
        comparator = StrategyComparator(session_path=session_file, strategies=[strategy])

        result = comparator.run_comparison()

        sr = result.strategy_results[0]
        # equity_df should exist (may be empty if no trades, but should be a DataFrame)
        assert isinstance(sr.equity_df, pd.DataFrame)
