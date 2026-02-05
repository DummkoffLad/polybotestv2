"""Event processing logic for session replay."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ...data.models import MarketEvent, TradeAction, PriceSnapshot
from ...strategies.base import Strategy, DecisionAction
from ...simulation.follow_metrics import FollowMetricsTracker
from .models import ReplayResult, ExecutedTrade

logger = logging.getLogger(__name__)


class EventProcessor:
    """Processes events through a strategy during replay."""

    def __init__(
        self,
        strategy: Strategy,
        result: ReplayResult,
        final_prices: Dict[str, PriceSnapshot],
        collect_trades: bool = False,
        track_follow_metrics: bool = True,
        track_analysis: bool = False,
        config: Dict[str, Any] = None,
    ):
        self.strategy = strategy
        self.result = result
        self.final_prices = final_prices
        self.collect_trades = collect_trades
        self.track_follow_metrics = track_follow_metrics
        self.track_analysis = track_analysis
        self.config = config or {}

        # Initialize follow metrics tracker
        self.follow_tracker = FollowMetricsTracker() if track_follow_metrics else None

        # Initialize analysis trackers if requested
        self.attributor = None
        self.equity_tracker = None
        self.slippage_analyzer = None
        self.slippage_measurements: List[Any] = []
        self.sizing_gaps: List[Any] = []
        self.selection_gaps: List[Any] = []

        if track_analysis:
            from ...analysis.attribution import TradeAttributor
            from ...analysis.equity_tracker import EquityTracker
            from ...analysis.slippage import SlippageAnalyzer

            self.attributor = TradeAttributor()

            # Get starting capital from config
            starting_capital = Decimal(str(config.get('scaling', {}).get('our_capital', '100')))
            self.equity_tracker = EquityTracker(starting_capital)

            self.slippage_analyzer = SlippageAnalyzer()

            # Get leader capital for sizing gap calculation
            self.leader_capital = Decimal(str(config.get('scaling', {}).get('leader_estimated_capital', '900')))
            self.starting_capital = starting_capital

    def process_event(self, i: int, event: MarketEvent, all_prices_at_time: Dict[str, PriceSnapshot]) -> None:
        """Process a single event through the strategy."""
        self.result.events_processed += 1

        # Add all prices to event context for strategies that need them
        event.context['all_prices'] = all_prices_at_time

        decision = self.strategy.on_event(event)
        trade = event.trade
        prices = event.prices

        # Track leader trade for follow metrics
        if self.follow_tracker:
            action_str = "BUY" if trade.action == TradeAction.BUY else "SELL"
            self.follow_tracker.record_leader_trade(
                timestamp=trade.timestamp,
                token_id=trade.token_id,
                action=action_str,
                shares=trade.shares,
                sequence=i
            )

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            self.strategy.on_fill(event, decision)

            # Track our response for follow metrics
            if self.follow_tracker:
                our_action = "BUY" if decision.action == DecisionAction.BUY else "SELL"
                self.follow_tracker.record_our_trade(
                    timestamp=trade.timestamp,  # Same time (instant response in sim)
                    token_id=trade.token_id,
                    action=our_action,
                    shares=decision.shares or Decimal("0"),
                    leader_sequence=i
                )

            # Record the trade if requested
            if self.collect_trades:
                exec_price = prices.ask if decision.action == DecisionAction.BUY else prices.bid
                self.result.trades.append(ExecutedTrade(
                    sequence=i + 1,
                    timestamp=trade.timestamp,
                    action=decision.action.value,
                    market_id=trade.market_id,
                    token_id=trade.token_id,
                    side=trade.side.value,
                    leader_dollars=trade.dollars,
                    leader_price=trade.price,
                    our_dollars=decision.dollars or Decimal("0"),
                    our_shares=decision.shares or Decimal("0"),
                    our_price=exec_price or Decimal("0"),
                ))

            # Track for analysis if requested
            if self.track_analysis and self.attributor:
                # Record entry for attribution
                self.attributor.record_entry(event, decision, self.strategy.name)

                # Measure slippage
                exec_price = prices.ask if decision.action == DecisionAction.BUY else prices.bid
                market_mid = (prices.bid + prices.ask) / Decimal("2") if prices.bid and prices.ask else exec_price
                action_str = "BUY" if decision.action == DecisionAction.BUY else "SELL"

                measurement = self.slippage_analyzer.measure_trade_slippage(
                    our_price=exec_price or Decimal("0"),
                    leader_price=trade.price,
                    market_mid=market_mid or Decimal("0"),
                    action=action_str,
                    trade_size=decision.dollars or Decimal("0"),
                    token_id=trade.token_id
                )
                self.slippage_measurements.append(measurement)

                # Measure sizing gap
                gap = self.slippage_analyzer.measure_sizing_gap(
                    leader_dollars=trade.dollars,
                    our_dollars=decision.dollars or Decimal("0"),
                    leader_capital=self.leader_capital,
                    our_capital=self.starting_capital,
                    token_id=trade.token_id
                )
                self.sizing_gaps.append(gap)

                # For SELL, record exit
                if decision.action == DecisionAction.SELL:
                    self.attributor.record_exit(
                        token_id=trade.token_id,
                        timestamp=trade.timestamp,
                        shares=decision.shares or Decimal("0"),
                        price=exec_price or Decimal("0")
                    )

            if decision.action == DecisionAction.BUY:
                self.result.buys_executed += 1
                self.result.buy_dollars += decision.dollars or Decimal("0")
            else:
                self.result.sells_executed += 1
                self.result.sell_dollars += decision.dollars or Decimal("0")
        else:
            self.result.skips += 1
            reason = decision.skip_reason or "unknown"
            self.result.skip_reasons[reason] = self.result.skip_reasons.get(reason, 0) + 1

            # Track skip for follow metrics
            if self.follow_tracker:
                self.follow_tracker.record_skip(i, trade.token_id, reason)

            # Track selection gap if analysis enabled
            if self.track_analysis and self.slippage_analyzer:
                action_str = "BUY" if trade.action == TradeAction.BUY else "SELL"
                sel_gap = self.slippage_analyzer.record_skipped_trade(
                    token_id=trade.token_id,
                    leader_action=action_str,
                    leader_dollars=trade.dollars,
                    leader_price=trade.price,
                    skip_reason=reason
                )
                self.selection_gaps.append(sel_gap)

        # Record equity snapshot after each event if tracking analysis
        if self.track_analysis and self.equity_tracker:
            # Get current portfolio state from strategy
            realized, unrealized = self.strategy.calculate_pnl(self.final_prices)
            state = self.strategy.get_state()
            positions = state.get("positions", {})
            deployed = Decimal(str(state.get("total_deployed", "0")))

            self.equity_tracker.record_manual_snapshot(
                timestamp=trade.timestamp,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                open_positions=len(positions),
                deployed_capital=deployed,
                trade_count=self.result.buys_executed + self.result.sells_executed
            )

    def finalize(self) -> None:
        """Finalize processing and compute metrics."""
        # Calculate follow metrics
        if self.follow_tracker:
            self.result.follow_metrics = self.follow_tracker.calculate_metrics()

        # Build analysis results if tracking enabled
        if self.track_analysis and self.attributor and self.equity_tracker:
            from ...analysis.drawdown import DrawdownAnalyzer

            # Update all open trades with current prices
            current_prices_dict = {token_id: price.bid if price.bid else Decimal("0")
                                   for token_id, price in self.final_prices.items()}
            self.attributor.update_all_unrealized(current_prices_dict)

            # Get trade summary
            trade_summary = self.attributor.get_summary()

            # Build equity DataFrame and analyze drawdown
            equity_df = self.equity_tracker.to_dataframe()
            drawdown_analyzer = DrawdownAnalyzer()

            if len(equity_df) > 0:
                drawdown_metrics = drawdown_analyzer.analyze(equity_df)
            else:
                # Empty equity curve
                drawdown_metrics = {
                    'max_drawdown_pct': 0.0,
                    'max_drawdown_value': 0.0,
                    'drawdown_start': None,
                    'drawdown_bottom': None,
                    'drawdown_duration_min': 0.0,
                    'recovery_time_min': None,
                    'current_drawdown_pct': 0.0,
                }

            # Aggregate slippage, sizing, selection
            slippage_stats = self.slippage_analyzer.aggregate_slippage(self.slippage_measurements)
            sizing_stats = self.slippage_analyzer.aggregate_sizing(self.sizing_gaps)
            selection_stats = self.slippage_analyzer.aggregate_selection(self.selection_gaps)

            # Store in result
            self.result.analysis = {
                'trade_summary': trade_summary,
                'drawdown': drawdown_metrics,
                'slippage': slippage_stats,
                'sizing': sizing_stats,
                'selection': selection_stats,
                'equity_df': equity_df,
                'attributed_trades': self.attributor.get_all_trades(),
            }
