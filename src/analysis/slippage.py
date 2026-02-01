"""Slippage analysis for execution quality measurement.

Provides three gap metrics:
- Price gap (execution slippage): Our fill vs leader's fill
- Sizing gap: Our position sizing vs leader's proportional allocation
- Selection gap: Trades we skipped and their eventual outcomes
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional


@dataclass
class SlippageMeasurement:
    """Slippage breakdown for a single trade."""
    token_id: str
    action: str  # "BUY" or "SELL"

    # Prices
    leader_price: Decimal
    our_price: Decimal
    market_price_at_signal: Decimal

    # Slippage components (in basis points)
    execution_slippage_bps: Decimal
    delay_slippage_bps: Decimal
    total_slippage_bps: Decimal

    # Dollar impact
    trade_size_dollars: Decimal
    slippage_cost_dollars: Decimal


@dataclass
class SizingGap:
    """Sizing gap analysis for a trade."""
    token_id: str
    leader_dollars: Decimal
    our_dollars: Decimal
    leader_pct_of_capital: Decimal
    our_pct_of_capital: Decimal
    sizing_ratio: Decimal  # our_pct / leader_pct (1.0 = proportional)


@dataclass
class SelectionGap:
    """Record of a skipped trade."""
    token_id: str
    leader_action: str
    leader_dollars: Decimal
    leader_price: Decimal
    skip_reason: str
    eventual_pnl: Optional[Decimal] = None  # Filled later when outcome known


class SlippageAnalyzer:
    """Analyze execution slippage and delay costs."""

    def measure_trade_slippage(
        self,
        our_price: Decimal,
        leader_price: Decimal,
        market_mid: Decimal,
        action: str,
        trade_size: Decimal,
        token_id: str
    ) -> SlippageMeasurement:
        """Calculate slippage for a single trade.

        Args:
            our_price: Price we executed at
            leader_price: Price leader executed at
            market_mid: Mid price when we detected the signal
            action: "BUY" or "SELL"
            trade_size: Our trade size in dollars
            token_id: Token identifier

        Returns:
            SlippageMeasurement with slippage breakdown
        """
        # Execution slippage: our price vs leader price
        if action == "BUY":
            # Higher price = worse for buys
            exec_slip = ((our_price - leader_price) / leader_price) * Decimal("10000")
        else:
            # Lower price = worse for sells
            exec_slip = ((leader_price - our_price) / leader_price) * Decimal("10000")

        # Delay slippage: market moved between signal and execution
        if action == "BUY":
            delay_slip = ((our_price - market_mid) / market_mid) * Decimal("10000")
        else:
            delay_slip = ((market_mid - our_price) / market_mid) * Decimal("10000")

        total_slip = exec_slip + delay_slip

        # Dollar cost of slippage
        slippage_cost = (total_slip / Decimal("10000")) * trade_size

        # Quantize to 2 decimal places for basis points
        exec_slip = exec_slip.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        delay_slip = delay_slip.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_slip = total_slip.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        slippage_cost = slippage_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return SlippageMeasurement(
            token_id=token_id,
            action=action,
            leader_price=leader_price,
            our_price=our_price,
            market_price_at_signal=market_mid,
            execution_slippage_bps=exec_slip,
            delay_slippage_bps=delay_slip,
            total_slippage_bps=total_slip,
            trade_size_dollars=trade_size,
            slippage_cost_dollars=slippage_cost,
        )

    def measure_sizing_gap(
        self,
        leader_dollars: Decimal,
        our_dollars: Decimal,
        leader_capital: Decimal,
        our_capital: Decimal,
        token_id: str
    ) -> SizingGap:
        """Calculate sizing gap for a trade.

        Args:
            leader_dollars: Leader's trade size
            our_dollars: Our trade size
            leader_capital: Leader's total capital
            our_capital: Our total capital
            token_id: Token identifier

        Returns:
            SizingGap with proportional allocation analysis
        """
        # Calculate proportional allocation
        leader_pct = (leader_dollars / leader_capital) * Decimal("100") if leader_capital > 0 else Decimal("0")
        our_pct = (our_dollars / our_capital) * Decimal("100") if our_capital > 0 else Decimal("0")

        # Sizing ratio: our_pct / leader_pct
        if leader_pct > 0:
            sizing_ratio = our_pct / leader_pct
        else:
            sizing_ratio = Decimal("0")

        return SizingGap(
            token_id=token_id,
            leader_dollars=leader_dollars,
            our_dollars=our_dollars,
            leader_pct_of_capital=leader_pct,
            our_pct_of_capital=our_pct,
            sizing_ratio=sizing_ratio,
        )

    def record_skipped_trade(
        self,
        token_id: str,
        leader_action: str,
        leader_dollars: Decimal,
        leader_price: Decimal,
        skip_reason: str
    ) -> SelectionGap:
        """Record a trade we skipped.

        Args:
            token_id: Token identifier
            leader_action: "BUY" or "SELL"
            leader_dollars: Leader's trade size
            leader_price: Leader's execution price
            skip_reason: Why we skipped (e.g., "cap", "capital", "filter")

        Returns:
            SelectionGap record (eventual_pnl filled later)
        """
        return SelectionGap(
            token_id=token_id,
            leader_action=leader_action,
            leader_dollars=leader_dollars,
            leader_price=leader_price,
            skip_reason=skip_reason,
            eventual_pnl=None,  # Filled later when market resolves
        )

    def aggregate_slippage(self, measurements: List[SlippageMeasurement]) -> Dict:
        """Calculate aggregate slippage statistics.

        Volume-weighted average slippage (VWAP approach) to ensure
        larger trades have appropriate weight in the average.

        Args:
            measurements: List of SlippageMeasurement records

        Returns:
            Dict with aggregate statistics
        """
        if not measurements:
            return {
                'total_trades': 0,
                'total_volume': 0.0,
                'total_slippage_cost': 0.0,
                'avg_execution_slippage_bps': 0.0,
                'avg_delay_slippage_bps': 0.0,
                'total_slippage_bps': 0.0,
                'slippage_as_pct_of_volume': 0.0,
            }

        total_volume = sum(m.trade_size_dollars for m in measurements)
        total_cost = sum(m.slippage_cost_dollars for m in measurements)

        # Volume-weighted average slippage
        if total_volume > 0:
            vwap_exec = sum(m.execution_slippage_bps * m.trade_size_dollars for m in measurements) / total_volume
            vwap_delay = sum(m.delay_slippage_bps * m.trade_size_dollars for m in measurements) / total_volume
        else:
            vwap_exec = Decimal("0")
            vwap_delay = Decimal("0")

        return {
            'total_trades': len(measurements),
            'total_volume': float(total_volume),
            'total_slippage_cost': float(total_cost),
            'avg_execution_slippage_bps': float(vwap_exec),
            'avg_delay_slippage_bps': float(vwap_delay),
            'total_slippage_bps': float(vwap_exec + vwap_delay),
            'slippage_as_pct_of_volume': float((total_cost / total_volume) * 100) if total_volume > 0 else 0.0,
        }

    def aggregate_sizing(self, gaps: List[SizingGap]) -> Dict:
        """Calculate aggregate sizing gap statistics.

        Args:
            gaps: List of SizingGap records

        Returns:
            Dict with aggregate statistics
        """
        if not gaps:
            return {
                'total_trades': 0,
                'avg_sizing_ratio': 0.0,
                'undersized_count': 0,
                'proportional_count': 0,
                'oversized_count': 0,
                'total_dollar_difference': 0.0,
            }

        # Average sizing ratio
        avg_ratio = sum(g.sizing_ratio for g in gaps) / len(gaps)

        # Count sizing categories
        undersized = sum(1 for g in gaps if g.sizing_ratio < Decimal("0.8"))
        oversized = sum(1 for g in gaps if g.sizing_ratio > Decimal("1.2"))
        proportional = len(gaps) - undersized - oversized

        # Total dollar difference (our_dollars - leader_dollars scaled proportionally)
        total_diff = sum(g.our_dollars - g.leader_dollars for g in gaps)

        return {
            'total_trades': len(gaps),
            'avg_sizing_ratio': float(avg_ratio),
            'undersized_count': undersized,
            'proportional_count': proportional,
            'oversized_count': oversized,
            'total_dollar_difference': float(total_diff),
        }

    def aggregate_selection(self, gaps: List[SelectionGap]) -> Dict:
        """Calculate aggregate selection gap statistics.

        Args:
            gaps: List of SelectionGap records

        Returns:
            Dict with aggregate statistics
        """
        if not gaps:
            return {
                'total_skipped': 0,
                'skip_reasons': {},
                'total_missed_pnl': 0.0,
                'known_outcomes': 0,
            }

        # Count by skip reason
        skip_reasons = {}
        for gap in gaps:
            skip_reasons[gap.skip_reason] = skip_reasons.get(gap.skip_reason, 0) + 1

        # Total missed PnL (only from trades with known outcomes)
        known_outcomes = [g for g in gaps if g.eventual_pnl is not None]
        total_missed = sum(g.eventual_pnl for g in known_outcomes)

        return {
            'total_skipped': len(gaps),
            'skip_reasons': skip_reasons,
            'total_missed_pnl': float(total_missed),
            'known_outcomes': len(known_outcomes),
        }
