"""Unit tests for SlippageAnalyzer."""

from decimal import Decimal
import pytest
from src.analysis.slippage import (
    SlippageAnalyzer,
    SlippageMeasurement,
    SizingGap,
    SelectionGap,
)


class TestSlippageAnalyzer:
    """Tests for SlippageAnalyzer."""

    def test_buy_execution_slippage_positive(self):
        """BUY: We paid more than leader → positive (bad) slippage."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.55"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.52"),
            action="BUY",
            trade_size=Decimal("10"),
            token_id="token123"
        )

        # Execution slippage: (0.55 - 0.50) / 0.50 * 10000 = 1000 bps (10%)
        assert result.execution_slippage_bps == Decimal("1000.00")
        # Delay slippage: (0.55 - 0.52) / 0.52 * 10000 = 576.92 bps
        assert result.delay_slippage_bps == Decimal("576.92")
        # Total: 1576.92 bps
        assert result.total_slippage_bps == Decimal("1576.92")

    def test_buy_execution_slippage_negative(self):
        """BUY: We paid less than leader → negative (good) slippage."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.45"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.47"),
            action="BUY",
            trade_size=Decimal("10"),
            token_id="token123"
        )

        # Execution slippage: (0.45 - 0.50) / 0.50 * 10000 = -1000 bps
        assert result.execution_slippage_bps == Decimal("-1000.00")

    def test_sell_execution_slippage(self):
        """SELL: We received less than leader → positive (bad) slippage."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.45"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.48"),
            action="SELL",
            trade_size=Decimal("10"),
            token_id="token123"
        )

        # Execution slippage: (0.50 - 0.45) / 0.50 * 10000 = 1000 bps
        assert result.execution_slippage_bps == Decimal("1000.00")
        # Delay slippage: (0.48 - 0.45) / 0.48 * 10000 = 625 bps
        assert result.delay_slippage_bps == Decimal("625.00")

    def test_delay_slippage_buy(self):
        """Market moved up between signal and execution for BUY."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.53"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.50"),  # Market was at 0.50 when we detected signal
            action="BUY",
            trade_size=Decimal("10"),
            token_id="token123"
        )

        # Delay slippage: (0.53 - 0.50) / 0.50 * 10000 = 600 bps
        assert result.delay_slippage_bps == Decimal("600.00")

    def test_zero_slippage(self):
        """Same prices everywhere → zero slippage."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.50"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.50"),
            action="BUY",
            trade_size=Decimal("10"),
            token_id="token123"
        )

        assert result.execution_slippage_bps == Decimal("0.00")
        assert result.delay_slippage_bps == Decimal("0.00")
        assert result.total_slippage_bps == Decimal("0.00")
        assert result.slippage_cost_dollars == Decimal("0.00")

    def test_slippage_cost_calculation(self):
        """Verify dollar cost matches bps * trade size."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_trade_slippage(
            our_price=Decimal("0.55"),
            leader_price=Decimal("0.50"),
            market_mid=Decimal("0.50"),
            action="BUY",
            trade_size=Decimal("100"),
            token_id="token123"
        )

        # Execution slippage: (0.55 - 0.50) / 0.50 * 10000 = 1000 bps
        # Delay slippage: (0.55 - 0.50) / 0.50 * 10000 = 1000 bps
        # Total slippage: 2000 bps (20%)
        # Cost: 0.20 * 100 = $20
        assert result.total_slippage_bps == Decimal("2000.00")
        assert result.slippage_cost_dollars == Decimal("20.00")

    def test_sizing_gap_proportional(self):
        """Both allocate 5% of capital → ratio 1.0."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_sizing_gap(
            leader_dollars=Decimal("50"),
            our_dollars=Decimal("5"),
            leader_capital=Decimal("1000"),
            our_capital=Decimal("100"),
            token_id="token123"
        )

        # Leader: 50 / 1000 = 5%
        assert result.leader_pct_of_capital == Decimal("5")
        # Us: 5 / 100 = 5%
        assert result.our_pct_of_capital == Decimal("5")
        # Ratio: 5 / 5 = 1.0
        assert result.sizing_ratio == Decimal("1")

    def test_sizing_gap_undersized(self):
        """Leader 5%, we 2% → ratio 0.4."""
        analyzer = SlippageAnalyzer()

        result = analyzer.measure_sizing_gap(
            leader_dollars=Decimal("50"),
            our_dollars=Decimal("2"),
            leader_capital=Decimal("1000"),
            our_capital=Decimal("100"),
            token_id="token123"
        )

        # Leader: 50 / 1000 = 5%
        assert result.leader_pct_of_capital == Decimal("5")
        # Us: 2 / 100 = 2%
        assert result.our_pct_of_capital == Decimal("2")
        # Ratio: 2 / 5 = 0.4
        assert result.sizing_ratio == Decimal("0.4")

    def test_aggregate_slippage_volume_weighted(self):
        """Two trades, different sizes, verify volume weighting."""
        analyzer = SlippageAnalyzer()

        # Trade 1: $10, 100 bps slippage
        m1 = SlippageMeasurement(
            token_id="token1",
            action="BUY",
            leader_price=Decimal("0.50"),
            our_price=Decimal("0.51"),
            market_price_at_signal=Decimal("0.50"),
            execution_slippage_bps=Decimal("100"),
            delay_slippage_bps=Decimal("0"),
            total_slippage_bps=Decimal("100"),
            trade_size_dollars=Decimal("10"),
            slippage_cost_dollars=Decimal("0.10"),
        )

        # Trade 2: $90, 200 bps slippage
        m2 = SlippageMeasurement(
            token_id="token2",
            action="BUY",
            leader_price=Decimal("0.50"),
            our_price=Decimal("0.52"),
            market_price_at_signal=Decimal("0.50"),
            execution_slippage_bps=Decimal("200"),
            delay_slippage_bps=Decimal("0"),
            total_slippage_bps=Decimal("200"),
            trade_size_dollars=Decimal("90"),
            slippage_cost_dollars=Decimal("1.80"),
        )

        result = analyzer.aggregate_slippage([m1, m2])

        # Total volume: 100
        assert result['total_volume'] == 100.0
        # Total cost: 0.10 + 1.80 = 1.90
        assert result['total_slippage_cost'] == 1.90
        # Volume-weighted avg: (100*10 + 200*90) / 100 = 190 bps
        assert result['avg_execution_slippage_bps'] == 190.0

    def test_aggregate_slippage_empty_list(self):
        """Empty list → all zeros."""
        analyzer = SlippageAnalyzer()

        result = analyzer.aggregate_slippage([])

        assert result['total_trades'] == 0
        assert result['total_volume'] == 0.0
        assert result['total_slippage_cost'] == 0.0
        assert result['avg_execution_slippage_bps'] == 0.0

    def test_selection_gap_recording(self):
        """Record skip, verify fields."""
        analyzer = SlippageAnalyzer()

        gap = analyzer.record_skipped_trade(
            token_id="token123",
            leader_action="BUY",
            leader_dollars=Decimal("50"),
            leader_price=Decimal("0.55"),
            skip_reason="cap"
        )

        assert gap.token_id == "token123"
        assert gap.leader_action == "BUY"
        assert gap.leader_dollars == Decimal("50")
        assert gap.skip_reason == "cap"
        assert gap.eventual_pnl is None

    def test_aggregate_selection_by_reason(self):
        """Multiple skips, grouped by reason."""
        analyzer = SlippageAnalyzer()

        gap1 = SelectionGap(
            token_id="token1",
            leader_action="BUY",
            leader_dollars=Decimal("50"),
            leader_price=Decimal("0.55"),
            skip_reason="cap",
            eventual_pnl=Decimal("5")
        )

        gap2 = SelectionGap(
            token_id="token2",
            leader_action="BUY",
            leader_dollars=Decimal("30"),
            leader_price=Decimal("0.60"),
            skip_reason="cap",
            eventual_pnl=Decimal("3")
        )

        gap3 = SelectionGap(
            token_id="token3",
            leader_action="BUY",
            leader_dollars=Decimal("20"),
            leader_price=Decimal("0.50"),
            skip_reason="capital",
            eventual_pnl=None  # Outcome unknown
        )

        result = analyzer.aggregate_selection([gap1, gap2, gap3])

        assert result['total_skipped'] == 3
        assert result['skip_reasons'] == {'cap': 2, 'capital': 1}
        # Total missed PnL: 5 + 3 = 8 (gap3 excluded, outcome unknown)
        assert result['total_missed_pnl'] == 8.0
        assert result['known_outcomes'] == 2
