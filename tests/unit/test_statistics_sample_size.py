"""Tests for SampleSizeChecker using TDD approach."""

import pytest
from decimal import Decimal
from src.statistics.sample_size import SampleSizeChecker, SampleSizeWarning


class TestSampleSizeCheckerMinimumThreshold:
    """Tests for minimum floor threshold (30 trades - CLT baseline)."""

    def test_below_minimum_floor(self):
        """25 trades with target='minimum' should be inadequate."""
        checker = SampleSizeChecker(target_confidence="minimum")
        pnls = [Decimal("1.0")] * 25
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 25
        assert result.minimum_required == 30
        assert result.confidence_level == "minimum"
        assert result.is_adequate is False
        assert "30" in result.warning_message

    def test_meets_minimum_floor(self):
        """35 trades with target='minimum' should be adequate."""
        checker = SampleSizeChecker(target_confidence="minimum")
        pnls = [Decimal("1.0")] * 35
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 35
        assert result.minimum_required == 30
        assert result.confidence_level == "minimum"
        assert result.is_adequate is True


class TestSampleSizeCheckerBasicThreshold:
    """Tests for basic reliability threshold (100 trades)."""

    def test_below_basic_threshold(self):
        """80 trades with target='basic' should be inadequate."""
        checker = SampleSizeChecker(target_confidence="basic")
        pnls = [Decimal("1.0")] * 80
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 80
        assert result.minimum_required == 100
        assert result.confidence_level == "basic"
        assert result.is_adequate is False
        assert "100" in result.warning_message

    def test_meets_basic_threshold(self):
        """150 trades with target='basic' should be adequate."""
        checker = SampleSizeChecker(target_confidence="basic")
        pnls = [Decimal("1.0")] * 150
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 150
        assert result.minimum_required == 100
        assert result.confidence_level == "basic"
        assert result.is_adequate is True


class TestSampleSizeCheckerHighThreshold:
    """Tests for institutional grade threshold (200 trades)."""

    def test_below_high_threshold(self):
        """150 trades with target='high' should be inadequate."""
        checker = SampleSizeChecker(target_confidence="high")
        pnls = [Decimal("1.0")] * 150
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 150
        assert result.minimum_required == 200
        assert result.confidence_level == "high"
        assert result.is_adequate is False
        assert "200" in result.warning_message

    def test_meets_high_threshold(self):
        """250 trades with target='high' should be adequate."""
        checker = SampleSizeChecker(target_confidence="high")
        pnls = [Decimal("1.0")] * 250
        result = checker.check_adequacy(pnls)

        assert result.trade_count == 250
        assert result.minimum_required == 200
        assert result.confidence_level == "high"
        assert result.is_adequate is True


class TestSampleSizeCheckerWarningMessages:
    """Tests for warning message content."""

    def test_warning_message_contains_threshold(self):
        """Warning message should include the required threshold."""
        checker = SampleSizeChecker(target_confidence="basic")
        pnls = [Decimal("1.0")] * 50
        result = checker.check_adequacy(pnls)

        assert "50" in result.warning_message  # Current count
        assert "100" in result.warning_message  # Required count


class TestSampleSizeCheckerRecommendations:
    """Tests for data collection recommendations."""

    def test_recommend_more_data(self):
        """Should recommend additional trades and sessions needed."""
        checker = SampleSizeChecker(target_confidence="basic")
        recommendation = checker.recommend_more_data(45)

        # Should mention 55 more trades needed (100 - 45)
        assert "55" in recommendation
        # Should mention approximately 6 sessions (55 / 10, rounded up)
        assert "6" in recommendation or "sessions" in recommendation.lower()

    def test_recommend_adequate_data(self):
        """Should return positive message when data is adequate."""
        checker = SampleSizeChecker(target_confidence="basic")
        recommendation = checker.recommend_more_data(150)

        assert "adequate" in recommendation.lower() or "sufficient" in recommendation.lower()


class TestSampleSizeCheckerEdgeCases:
    """Tests for edge cases."""

    def test_empty_list(self):
        """Empty list should return inadequate with count=0."""
        checker = SampleSizeChecker(target_confidence="basic")
        result = checker.check_adequacy([])

        assert result.trade_count == 0
        assert result.is_adequate is False

    def test_default_target_is_basic(self):
        """Default target should be 'basic' (100 trades)."""
        checker = SampleSizeChecker()
        pnls = [Decimal("1.0")] * 80
        result = checker.check_adequacy(pnls)

        assert result.minimum_required == 100
        assert result.confidence_level == "basic"
