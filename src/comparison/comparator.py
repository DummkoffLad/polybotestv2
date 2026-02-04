"""Strategy comparison orchestrator.

Runs multiple strategies on identical session data and aggregates results for comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from ..framework.replay import SessionReplayer, ReplayResult
from ..strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result from running a single strategy on a session.

    Captures the replay result and equity curve for downstream comparison.
    """

    strategy_name: str
    replay_result: ReplayResult
    equity_df: pd.DataFrame


@dataclass
class ComparisonResult:
    """Aggregated comparison of multiple strategies on same session.

    Contains results for all strategies plus session metadata.
    """

    session_id: str
    session_path: Path
    strategy_results: List[StrategyResult]
    comparison_time: datetime


class StrategyComparator:
    """Runs multiple strategies on identical session data for comparison.

    Key behavior:
    - Sequential replay ensures data consistency (not parallel)
    - Each strategy gets isolated SessionReplayer instance
    - Strategies receive identical events in same order
    - Results aggregated into ComparisonResult for downstream analysis
    """

    def __init__(self, session_path: Path, strategies: List[Strategy]):
        """Initialize the comparator.

        Args:
            session_path: Path to session JSONL file
            strategies: List of strategy instances to compare

        Raises:
            ValueError: If strategies list is empty
            FileNotFoundError: If session_path doesn't exist
        """
        # Validate strategies list
        if not strategies:
            raise ValueError("Cannot compare with empty strategies list")

        # Validate session path exists
        self.session_path = Path(session_path)
        if not self.session_path.exists():
            raise FileNotFoundError(f"Session file not found: {self.session_path}")

        self.strategies = strategies

    def run_comparison(self) -> ComparisonResult:
        """Execute all strategies sequentially on same session.

        Each strategy:
        1. Gets a fresh SessionReplayer instance
        2. Loads the same session file (ensures identical events)
        3. Runs replay with track_analysis=True for equity tracking
        4. Results are collected into ComparisonResult

        Returns:
            ComparisonResult containing all strategy results
        """
        strategy_results: List[StrategyResult] = []
        session_id = ""

        for strategy in self.strategies:
            logger.info(f"Running comparison for strategy: {strategy.name}")

            # Create fresh replayer for this strategy (ensures isolation)
            replayer = SessionReplayer(
                session_path=self.session_path,
                strategy=strategy
            )

            # Load session (each strategy gets identical events)
            event_count = replayer.load()
            logger.info(f"  Loaded {event_count} events")

            # Capture session_id from first replayer
            if not session_id:
                session_id = replayer.session_id

            # Run replay with analysis tracking for equity curve
            replay_result = replayer.run(track_analysis=True)
            logger.info(f"  Processed {replay_result.events_processed} events, "
                       f"buys={replay_result.buys_executed}, "
                       f"sells={replay_result.sells_executed}, "
                       f"skips={replay_result.skips}")

            # Extract equity DataFrame from analysis (or create empty if not available)
            if replay_result.analysis and 'equity_df' in replay_result.analysis:
                equity_df = replay_result.analysis['equity_df']
            else:
                # Create empty DataFrame with expected columns
                equity_df = pd.DataFrame(columns=['timestamp', 'equity'])

            # Create StrategyResult
            strategy_result = StrategyResult(
                strategy_name=strategy.name,
                replay_result=replay_result,
                equity_df=equity_df
            )
            strategy_results.append(strategy_result)

        # Build ComparisonResult
        comparison_result = ComparisonResult(
            session_id=session_id,
            session_path=self.session_path,
            strategy_results=strategy_results,
            comparison_time=datetime.now(timezone.utc)
        )

        logger.info(f"Comparison complete: {len(strategy_results)} strategies compared")
        return comparison_result
