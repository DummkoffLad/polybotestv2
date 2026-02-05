"""SessionReplayer orchestrator - coordinates loading, processing, and analysis."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from ...data.models import PriceSnapshot
from ...strategies.base import Strategy, StrategyConfig
from .models import ReplayResult
from .loader import SessionLoader
from .processor import EventProcessor

logger = logging.getLogger(__name__)


class SessionReplayer:
    """Replays a recorded session through any strategy.

    Now supports price_snapshot events for accurate historical pricing.
    During replay, prices are looked up from the most recent snapshot
    before each trade, providing more realistic simulation.
    """

    def __init__(self, session_path: Path, strategy: Strategy, config_overrides: Dict = None):
        self.session_path = Path(session_path)
        self.strategy = strategy
        self.config_overrides = config_overrides or {}
        self.loader = SessionLoader(session_path)

    def load(self) -> int:
        """Load session file. Returns event count."""
        return self.loader.load()

    def run(self, simulate_resolution: bool = False, collect_trades: bool = False,
            track_follow_metrics: bool = True, track_analysis: bool = False) -> ReplayResult:
        """Run the replay.

        Args:
            simulate_resolution: If True, calculate PnL assuming markets resolve
                                 at extreme prices (UP→0.99 win, DOWN→0 lose).
            collect_trades: If True, record each executed trade for analysis.
            track_follow_metrics: If True, track follow quality metrics.
            track_analysis: If True, run full performance analysis (attribution, equity, drawdown, slippage).
        """
        config = self._merge_config()
        strategy_config = StrategyConfig.from_dict(config)

        self.strategy.initialize(strategy_config)
        self.strategy.on_session_start()

        # Calculate session duration
        duration_minutes = 0.0
        if self.loader._session_start and self.loader._session_end:
            duration_minutes = (self.loader._session_end - self.loader._session_start).total_seconds() / 60.0

        result = ReplayResult(
            session_id=self.loader.session_id,
            strategy_name=self.strategy.name,
            events_dropped_no_prices=self.loader._dropped_no_prices,
            events_dropped_duplicates=self.loader._dropped_duplicates,
            session_start=self.loader._session_start,
            session_end=self.loader._session_end,
            session_duration_minutes=duration_minutes,
        )

        # Create event processor
        processor = EventProcessor(
            strategy=self.strategy,
            result=result,
            final_prices=self.loader.final_prices,
            collect_trades=collect_trades,
            track_follow_metrics=track_follow_metrics,
            track_analysis=track_analysis,
            config=config,
        )

        # Process all events
        for i, event in enumerate(self.loader.events):
            all_prices_at_time = self.loader.get_all_prices_at_time(event.trade.timestamp)
            processor.process_event(i, event, all_prices_at_time)

        # Compute PnL using final recorded prices
        realized, unrealized = self.strategy.calculate_pnl(self.loader.final_prices)
        result.realized_pnl = realized
        result.unrealized_pnl = unrealized
        result.total_pnl = realized + unrealized

        # Count open positions
        state = self.strategy.get_state()
        positions = state.get("positions", {})
        result.open_positions = len(positions)
        result.open_cost_basis = Decimal(str(state.get("total_deployed", "0")))

        # Calculate market resolution PnL if requested
        if simulate_resolution:
            resolved, wins, losses = self._calculate_resolved_pnl()
            result.resolved_pnl = realized + resolved
            result.win_count = wins
            result.loss_count = losses

        # Finalize processing (metrics, analysis)
        processor.finalize()

        self.strategy.on_session_end()
        return result

    def _calculate_resolved_pnl(self) -> tuple:
        """Calculate PnL assuming markets resolve at extremes.

        For hourly prediction markets:
        - If we hold UP and market resolves UP → shares worth $0.99 each
        - If we hold UP and market resolves DOWN → shares worth $0 each
        - If we hold DOWN and market resolves DOWN → shares worth $0.99 each
        - If we hold DOWN and market resolves UP → shares worth $0 each

        For Polymarket hourly markets:
        - Each position has a side (UP or DOWN)
        - The final bid/ask for that side's token indicates likelihood of winning
        - If bid for our token > 0.5, our side is likely to win
        - Resolution: winning side tokens → $0.99, losing side → $0

        Returns: (unrealized_pnl, win_count, loss_count)
        """
        unrealized = Decimal("0")
        wins = 0
        losses = 0

        # Get positions from strategy portfolio
        if hasattr(self.strategy, 'portfolio'):
            positions = self.strategy.portfolio.get_positions()
        else:
            return Decimal("0"), 0, 0

        for token_id, pos in positions.items():
            if pos.shares <= 0:
                continue

            # Get final price for this token
            final_price = self.loader.final_prices.get(token_id)
            if not final_price or not final_price.bid:
                continue

            # For the token we hold, bid > 0.5 means our side is likely to win
            our_side_wins = final_price.bid >= Decimal("0.5")

            # Calculate resolution value
            if our_side_wins:
                resolution_value = pos.shares * Decimal("0.99")  # We win
                wins += 1
            else:
                resolution_value = Decimal("0")  # We lose
                losses += 1

            # PnL = resolution value - cost basis
            pnl = resolution_value - pos.cost_basis
            unrealized += pnl

        return unrealized, wins, losses

    def _merge_config(self) -> Dict[str, Any]:
        config = dict(self.loader.original_config)
        for key, value in self.config_overrides.items():
            parts = key.split(".")
            target = config
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return config


def run_session_replay(session_path: str, strategy: Strategy) -> ReplayResult:
    """Convenience function to run a session replay.

    Args:
        session_path: Path to the session JSONL file
        strategy: The strategy instance to replay through

    Returns:
        ReplayResult with stats
    """
    replayer = SessionReplayer(Path(session_path), strategy)
    count = replayer.load()
    print(f"\nLoaded {count} events from {session_path}")
    if replayer.loader._dropped_no_prices:
        print(f"  WARNING: {replayer.loader._dropped_no_prices} events dropped (no real bid/ask prices)")

    result = replayer.run()

    # Print summary
    print(f"\n{'='*50}")
    print(f"  REPLAY SUMMARY: {result.strategy_name}")
    print(f"{'='*50}")
    print(f"  Events: {result.events_processed}")
    if result.events_dropped_no_prices:
        print(f"  Dropped (no prices): {result.events_dropped_no_prices}")
    print(f"  Buys: {result.buys_executed} (${result.buy_dollars:.2f})")
    print(f"  Sells: {result.sells_executed} (${result.sell_dollars:.2f})")
    print(f"  Skips: {result.skips}")
    if result.skip_reasons:
        print(f"  Skip reasons:")
        for reason, count in sorted(result.skip_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
    print(f"  ---")
    print(f"  Realized PnL:   ${result.realized_pnl:+.2f}")
    print(f"  Unrealized PnL: ${result.unrealized_pnl:+.2f}")
    print(f"  Total PnL:      ${result.total_pnl:+.2f}")
    if result.open_positions:
        print(f"  Open positions: {result.open_positions} (${result.open_cost_basis:.2f} deployed)")
    print(f"{'='*50}\n")

    return result


def run_session_replay_with_analysis(
    session_path: str,
    strategy: Strategy,
    output_dir: Path = Path("data/reports")
) -> ReplayResult:
    """Run session replay with full performance analysis.

    Args:
        session_path: Path to the session JSONL file
        strategy: The strategy instance to replay through
        output_dir: Directory for saving reports and charts

    Returns:
        ReplayResult with analysis field populated
    """
    from ...analysis.reports import ReportGenerator

    # Load and run replay with analysis
    replayer = SessionReplayer(Path(session_path), strategy)
    count = replayer.load()
    print(f"\nLoaded {count} events from {session_path}")
    if replayer.loader._dropped_no_prices:
        print(f"  WARNING: {replayer.loader._dropped_no_prices} events dropped (no real bid/ask prices)")

    result = replayer.run(track_analysis=True)

    if not result.analysis:
        print("ERROR: Analysis not generated")
        return result

    # Generate reports
    generator = ReportGenerator(output_dir)

    # Print console summary
    generator.print_console_summary(
        result,
        result.analysis['trade_summary'],
        result.analysis['drawdown'],
        result.analysis['slippage'],
        result.analysis['sizing'],
        result.analysis['selection']
    )

    # Generate charts
    session_id = result.session_id or "unknown"
    if len(result.analysis['equity_df']) > 0:
        equity_chart = generator.generate_equity_chart(result.analysis['equity_df'], session_id)
        print(f"Equity chart saved: {equity_chart}")

        trade_scatter = generator.generate_trade_scatter(result.analysis['attributed_trades'], session_id)
        print(f"Trade scatter saved: {trade_scatter}")
    else:
        print("No equity data to chart")

    # Save JSON report
    json_report = generator.save_json_report(
        result,
        result.analysis['trade_summary'],
        result.analysis['drawdown'],
        result.analysis['slippage'],
        result.analysis['sizing'],
        result.analysis['selection'],
        session_id
    )
    print(f"JSON report saved: {json_report}\n")

    return result
