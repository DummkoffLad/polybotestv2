"""
Unit tests for EdgeTracker - per-token edge tracking with rolling window.

Tests verify:
- Trade result storage and retrieval
- Cold start handling (insufficient data returns None)
- Rolling window eviction (deque maxlen=50)
- Win rate and avg win/loss calculations
- Edge case handling (all wins, all losses)
- Multi-token independence
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from src.core.edge_tracker import EdgeTracker, TradeResult


class TestEdgeTracker:
    """Test EdgeTracker per-token edge tracking."""

    def test_record_trade_stores_result(self):
        """Recording a trade should store it for later retrieval."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)
        result = TradeResult(
            token_id="SOL",
            entry_price=Decimal("100.00"),
            exit_price=Decimal("110.00"),
            pnl_pct=Decimal("0.10"),
            timestamp=datetime.now(timezone.utc)
        )

        tracker.record_trade(result)

        # Verify trade was stored (but not enough for edge stats yet)
        assert not tracker.has_sufficient_data("SOL")

    def test_get_edge_stats_returns_none_insufficient_data(self):
        """With < min_trades (20), get_edge_stats returns None."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # Record 19 trades (below minimum)
        for i in range(19):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                pnl_pct=Decimal("0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is None

    def test_get_edge_stats_returns_stats_at_minimum(self):
        """With exactly min_trades (20), returns (win_rate, avg_win, avg_loss, count)."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # Record 20 trades: 12 wins, 8 losses
        for i in range(12):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                pnl_pct=Decimal("0.10"),
                timestamp=datetime.now(timezone.utc)
            ))

        for i in range(8):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        win_rate, avg_win, avg_loss, count = stats
        assert count == 20

    def test_rolling_window_evicts_old_trades(self):
        """After 50+ trades, oldest are evicted (deque maxlen=50)."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # Record 60 trades (exceeds window)
        for i in range(60):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                pnl_pct=Decimal("0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        _, _, _, count = stats
        # Should only have 50 trades (rolling window maxlen)
        assert count == 50

    def test_has_sufficient_data_false_below_minimum(self):
        """Returns False when count < min_trades."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        for i in range(15):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                pnl_pct=Decimal("0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        assert not tracker.has_sufficient_data("SOL")

    def test_has_sufficient_data_true_at_minimum(self):
        """Returns True when count >= min_trades."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        for i in range(20):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("105.00"),
                pnl_pct=Decimal("0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        assert tracker.has_sufficient_data("SOL")

    def test_win_rate_calculation(self):
        """12 wins out of 20 trades -> win_rate = 0.60."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # 12 wins
        for i in range(12):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                pnl_pct=Decimal("0.10"),
                timestamp=datetime.now(timezone.utc)
            ))

        # 8 losses
        for i in range(8):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        win_rate, _, _, _ = stats
        assert win_rate == Decimal("0.60")

    def test_avg_win_loss_calculation(self):
        """Verify avg_win and avg_loss computed correctly."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # 15 wins: +10%, +15%, +20% pattern
        for i in range(5):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                pnl_pct=Decimal("0.10"),
                timestamp=datetime.now(timezone.utc)
            ))
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("115.00"),
                pnl_pct=Decimal("0.15"),
                timestamp=datetime.now(timezone.utc)
            ))
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("120.00"),
                pnl_pct=Decimal("0.20"),
                timestamp=datetime.now(timezone.utc)
            ))

        # 5 losses: -5%
        for i in range(5):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        _, avg_win, avg_loss, _ = stats

        # avg_win = (10+15+20) / 3 * 5 repeats = 15%
        expected_avg_win = Decimal("0.15")
        assert avg_win == expected_avg_win

        # avg_loss = 5%
        expected_avg_loss = Decimal("0.05")
        assert avg_loss == expected_avg_loss

    def test_all_wins_edge_case(self):
        """100% win rate, avg_loss defaults to 0.01."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # 20 wins, no losses
        for i in range(20):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                pnl_pct=Decimal("0.10"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        win_rate, avg_win, avg_loss, _ = stats

        assert win_rate == Decimal("1.00")
        assert avg_win == Decimal("0.10")
        # avg_loss should default to 0.01 (avoid division by zero in Kelly)
        assert avg_loss == Decimal("0.01")

    def test_all_losses_edge_case(self):
        """0% win rate, avg_win = 0."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # 20 losses, no wins
        for i in range(20):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        stats = tracker.get_edge_stats("SOL")
        assert stats is not None
        win_rate, avg_win, avg_loss, _ = stats

        assert win_rate == Decimal("0.00")
        # avg_win should be 0 (no wins)
        assert avg_win == Decimal("0.00")
        assert avg_loss == Decimal("0.05")

    def test_multiple_tokens_tracked_independently(self):
        """Token A and Token B have separate histories."""
        tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)

        # SOL: 15 wins, 5 losses (20 total)
        for i in range(15):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                pnl_pct=Decimal("0.10"),
                timestamp=datetime.now(timezone.utc)
            ))
        for i in range(5):
            tracker.record_trade(TradeResult(
                token_id="SOL",
                entry_price=Decimal("100.00"),
                exit_price=Decimal("95.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        # ETH: 5 wins, 15 losses (20 total)
        for i in range(5):
            tracker.record_trade(TradeResult(
                token_id="ETH",
                entry_price=Decimal("2000.00"),
                exit_price=Decimal("2100.00"),
                pnl_pct=Decimal("0.05"),
                timestamp=datetime.now(timezone.utc)
            ))
        for i in range(15):
            tracker.record_trade(TradeResult(
                token_id="ETH",
                entry_price=Decimal("2000.00"),
                exit_price=Decimal("1900.00"),
                pnl_pct=Decimal("-0.05"),
                timestamp=datetime.now(timezone.utc)
            ))

        # Verify independence
        sol_stats = tracker.get_edge_stats("SOL")
        eth_stats = tracker.get_edge_stats("ETH")

        assert sol_stats is not None
        assert eth_stats is not None

        sol_win_rate, _, _, _ = sol_stats
        eth_win_rate, _, _, _ = eth_stats

        # SOL: 75% win rate
        assert sol_win_rate == Decimal("0.75")
        # ETH: 25% win rate
        assert eth_win_rate == Decimal("0.25")
