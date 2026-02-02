"""
Unit tests for TradeQualityScorer and SelectiveFollower.

Tests use RED-GREEN-REFACTOR TDD cycle.
"""

from decimal import Decimal
import pytest

from src.core.trade_filter import TradeQualityScorer, SelectiveFollower


def make_scorer(**overrides):
    """Helper to create TradeQualityScorer with defaults."""
    defaults = {
        "leader_avg_size": Decimal("100"),
        "high_quality_threshold": Decimal("0.70"),
        "dca_quality_threshold": Decimal("0.75"),
    }
    defaults.update(overrides)
    return TradeQualityScorer(**defaults)


def make_follower(**overrides):
    """Helper to create SelectiveFollower with defaults."""
    defaults = {"max_positions": 5}
    defaults.update(overrides)
    return SelectiveFollower(**defaults)


class TestTradeQualityScorerSpread:
    """Test spread scoring component."""

    def test_excellent_spread_zero_bps(self):
        """0 bps spread gets 1.00 score."""
        scorer = make_scorer()
        score = scorer.score_spread(Decimal("0"))
        assert score == Decimal("1.00")

    def test_excellent_spread_at_threshold(self):
        """50 bps spread (at excellent threshold) gets 1.00 score."""
        scorer = make_scorer()
        score = scorer.score_spread(Decimal("50"))
        assert score == Decimal("1.00")

    def test_midpoint_spread(self):
        """175 bps spread (midpoint between 50-300) gets 0.50 score."""
        scorer = make_scorer()
        score = scorer.score_spread(Decimal("175"))
        assert score == Decimal("0.50")

    def test_poor_spread_at_threshold(self):
        """300 bps spread (at poor threshold) gets 0.00 score."""
        scorer = make_scorer()
        score = scorer.score_spread(Decimal("300"))
        assert score == Decimal("0.00")

    def test_extreme_spread_clamped(self):
        """500 bps spread (above poor threshold) gets 0.00 score (clamped)."""
        scorer = make_scorer()
        score = scorer.score_spread(Decimal("500"))
        assert score == Decimal("0.00")


class TestTradeQualityScorerConviction:
    """Test leader conviction scoring component."""

    def test_average_size_trade(self):
        """Leader trade at average size gets 0.50 score (1x = midpoint to 2x cap)."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_conviction(Decimal("100"))
        assert score == Decimal("0.50")

    def test_double_average_trade(self):
        """Leader trade at 2x average gets 1.00 score (max)."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_conviction(Decimal("200"))
        assert score == Decimal("1.00")

    def test_triple_average_trade_capped(self):
        """Leader trade at 3x average gets 1.00 score (capped at 2x)."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_conviction(Decimal("300"))
        assert score == Decimal("1.00")

    def test_half_average_trade(self):
        """Leader trade at 0.5x average gets 0.25 score."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_conviction(Decimal("50"))
        assert score == Decimal("0.25")

    def test_zero_size_trade(self):
        """Leader trade at zero size gets 0.00 score."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_conviction(Decimal("0"))
        assert score == Decimal("0.00")


class TestTradeQualityScorerComposite:
    """Test composite scoring (60% spread + 40% conviction)."""

    def test_perfect_trade(self):
        """Perfect spread (0 bps) + max conviction (2x avg) = 1.00."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_trade(Decimal("0"), Decimal("200"))
        # 0.60 * 1.0 + 0.40 * 1.0 = 1.00
        assert score == Decimal("1.00")

    def test_terrible_trade(self):
        """Poor spread (300 bps) + low conviction (0.5x avg) = 0.10."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_trade(Decimal("300"), Decimal("50"))
        # 0.60 * 0.0 + 0.40 * 0.25 = 0.10
        assert score == Decimal("0.10")

    def test_good_spread_average_conviction(self):
        """Excellent spread (50 bps) + average conviction (1x avg) = 0.80."""
        scorer = make_scorer(leader_avg_size=Decimal("100"))
        score = scorer.score_trade(Decimal("50"), Decimal("100"))
        # 0.60 * 1.0 + 0.40 * 0.50 = 0.80
        assert score == Decimal("0.80")


class TestTradeQualityScorerThresholds:
    """Test trade filtering thresholds."""

    def test_should_take_trade_at_threshold(self):
        """Quality score at threshold (0.70) should be taken."""
        scorer = make_scorer(high_quality_threshold=Decimal("0.70"))
        assert scorer.should_take_trade(Decimal("0.70")) is True

    def test_should_take_trade_below_threshold(self):
        """Quality score below threshold (0.69) should not be taken."""
        scorer = make_scorer(high_quality_threshold=Decimal("0.70"))
        assert scorer.should_take_trade(Decimal("0.69")) is False

    def test_should_take_trade_above_threshold(self):
        """Quality score above threshold (1.00) should be taken."""
        scorer = make_scorer(high_quality_threshold=Decimal("0.70"))
        assert scorer.should_take_trade(Decimal("1.00")) is True

    def test_should_follow_dca_both_high_quality(self):
        """DCA allowed when both original and new quality >= 0.75."""
        scorer = make_scorer(dca_quality_threshold=Decimal("0.75"))
        assert scorer.should_follow_dca(Decimal("0.80"), Decimal("0.80")) is True

    def test_should_follow_dca_new_below_threshold(self):
        """DCA rejected when new quality below threshold."""
        scorer = make_scorer(dca_quality_threshold=Decimal("0.75"))
        assert scorer.should_follow_dca(Decimal("0.80"), Decimal("0.70")) is False

    def test_should_follow_dca_original_below_threshold(self):
        """DCA rejected when original quality below threshold."""
        scorer = make_scorer(dca_quality_threshold=Decimal("0.75"))
        assert scorer.should_follow_dca(Decimal("0.70"), Decimal("0.80")) is False


class TestSelectiveFollower:
    """Test SelectiveFollower position count gating."""

    def test_can_open_position_none(self):
        """With 0 positions and max 5, can open position."""
        follower = make_follower(max_positions=5)
        can_open, reason = follower.can_open_position(0)
        assert can_open is True
        assert reason is None

    def test_can_open_position_below_max(self):
        """With 4 positions and max 5, can open position."""
        follower = make_follower(max_positions=5)
        can_open, reason = follower.can_open_position(4)
        assert can_open is True
        assert reason is None

    def test_cannot_open_position_at_max(self):
        """With 5 positions and max 5, cannot open position."""
        follower = make_follower(max_positions=5)
        can_open, reason = follower.can_open_position(5)
        assert can_open is False
        assert reason == "max_positions_reached"

    def test_cannot_open_position_above_max(self):
        """With 6 positions and max 5, cannot open position."""
        follower = make_follower(max_positions=5)
        can_open, reason = follower.can_open_position(6)
        assert can_open is False
        assert reason == "max_positions_reached"
