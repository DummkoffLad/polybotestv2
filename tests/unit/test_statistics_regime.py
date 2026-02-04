"""Tests for session regime classification and comparison.

Tests RegimeAnalyzer which classifies trading sessions by time-of-day
(overnight vs daytime) and volatility (high vs low), then compares performance
across different regime combinations.
"""

import pytest
from datetime import datetime, time
from decimal import Decimal
from src.statistics.regime import (
    RegimeAnalyzer,
    RegimeMetrics,
    RegimeComparisonResult,
)


class TestClassifySession:
    """Tests for RegimeAnalyzer.classify_session()."""

    def test_classify_overnight_early_morning(self):
        """Session starting at 2AM should be classified as overnight."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 2, 0, 0)  # 2:00 AM
        end = datetime(2026, 2, 4, 8, 0, 0)    # 8:00 AM

        result = analyzer.classify_session(
            session_id="session_1",
            start_time=start,
            end_time=end,
            price_changes=[2.0, -1.5, 1.0],
            event_count=10
        )

        assert result.is_overnight is True
        assert result.is_daytime is False
        assert "overnight" in result.regime_label

    def test_classify_overnight_late_night(self):
        """Session starting at 10PM should be classified as overnight."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 22, 0, 0)  # 10:00 PM
        end = datetime(2026, 2, 5, 2, 0, 0)     # 2:00 AM next day

        result = analyzer.classify_session(
            session_id="session_2",
            start_time=start,
            end_time=end,
            price_changes=[1.0, -0.5, 0.8],
            event_count=15
        )

        assert result.is_overnight is True
        assert result.is_daytime is False
        assert "overnight" in result.regime_label

    def test_classify_daytime(self):
        """Session during business hours should be classified as daytime."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)  # 10:00 AM
        end = datetime(2026, 2, 4, 16, 0, 0)    # 4:00 PM

        result = analyzer.classify_session(
            session_id="session_3",
            start_time=start,
            end_time=end,
            price_changes=[3.0, -2.0, 2.5],
            event_count=20
        )

        assert result.is_overnight is False
        assert result.is_daytime is True
        assert "daytime" in result.regime_label

    def test_classify_high_volatility(self):
        """Price changes averaging >5% should be classified as high volatility."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)

        # Average of abs values: (8.0 + 6.0 + 7.0) / 3 = 7.0 > 5.0
        result = analyzer.classify_session(
            session_id="session_4",
            start_time=start,
            end_time=end,
            price_changes=[8.0, -6.0, 7.0],
            event_count=10
        )

        assert result.avg_price_volatility > 5.0
        assert "high_vol" in result.regime_label

    def test_classify_low_volatility(self):
        """Price changes averaging <=5% should be classified as low volatility."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)

        # Average of abs values: (2.0 + 1.5 + 1.0) / 3 = 1.5 <= 5.0
        result = analyzer.classify_session(
            session_id="session_5",
            start_time=start,
            end_time=end,
            price_changes=[2.0, -1.5, 1.0],
            event_count=10
        )

        assert result.avg_price_volatility <= 5.0
        assert "low_vol" in result.regime_label

    def test_regime_label_format_overnight_low_vol(self):
        """Overnight + low volatility should produce 'overnight_low_vol' label."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 2, 0, 0)  # 2:00 AM
        end = datetime(2026, 2, 4, 8, 0, 0)

        result = analyzer.classify_session(
            session_id="session_6",
            start_time=start,
            end_time=end,
            price_changes=[2.0, -1.5, 1.0],  # Low vol
            event_count=10
        )

        assert result.regime_label == "overnight_low_vol"

    def test_regime_label_format_daytime_high_vol(self):
        """Daytime + high volatility should produce 'daytime_high_vol' label."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)  # 10:00 AM
        end = datetime(2026, 2, 4, 16, 0, 0)

        result = analyzer.classify_session(
            session_id="session_7",
            start_time=start,
            end_time=end,
            price_changes=[8.0, -6.0, 7.0],  # High vol
            event_count=10
        )

        assert result.regime_label == "daytime_high_vol"

    def test_duration_calculation(self):
        """Duration should be calculated correctly in hours."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)  # 4 hours later

        result = analyzer.classify_session(
            session_id="session_8",
            start_time=start,
            end_time=end,
            price_changes=[2.0],
            event_count=20
        )

        assert result.duration_hours == 4.0

    def test_events_per_hour(self):
        """Events per hour should be calculated correctly."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)  # 4 hours

        result = analyzer.classify_session(
            session_id="session_9",
            start_time=start,
            end_time=end,
            price_changes=[2.0],
            event_count=20  # 20 events / 4 hours = 5 events/hour
        )

        assert result.events_per_hour == 5.0

    def test_empty_price_changes(self):
        """Empty price changes should default to 0.0 volatility and low_vol."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)

        result = analyzer.classify_session(
            session_id="session_10",
            start_time=start,
            end_time=end,
            price_changes=[],  # Empty
            event_count=0
        )

        assert result.avg_price_volatility == 0.0
        assert "low_vol" in result.regime_label

    def test_optional_spreads(self):
        """When spreads are None, avg_spread_pct should default to 0.0."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)

        result = analyzer.classify_session(
            session_id="session_11",
            start_time=start,
            end_time=end,
            price_changes=[2.0],
            spreads=None,  # Not provided
            event_count=10
        )

        assert result.avg_spread_pct == 0.0

    def test_spreads_provided(self):
        """When spreads are provided, avg_spread_pct should be calculated."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 10, 0, 0)
        end = datetime(2026, 2, 4, 14, 0, 0)

        result = analyzer.classify_session(
            session_id="session_12",
            start_time=start,
            end_time=end,
            price_changes=[2.0],
            spreads=[0.5, 1.0, 1.5],  # Avg = 1.0
            event_count=10
        )

        assert result.avg_spread_pct == 1.0

    def test_boundary_overnight_hour(self):
        """Session ending at 5AM (boundary hour) should be overnight."""
        analyzer = RegimeAnalyzer()
        start = datetime(2026, 2, 4, 3, 0, 0)
        end = datetime(2026, 2, 4, 5, 0, 0)  # 5:00 AM (last overnight hour)

        result = analyzer.classify_session(
            session_id="session_13",
            start_time=start,
            end_time=end,
            price_changes=[2.0],
            event_count=10
        )

        assert result.is_overnight is True


class TestCompareRegimes:
    """Tests for RegimeAnalyzer.compare_regimes()."""

    def test_compare_identical_regimes(self):
        """Identical PnLs in both regimes should have p close to 1.0, not significant."""
        analyzer = RegimeAnalyzer()

        # Same PnLs in both regimes
        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
            {"session_id": "s2", "pnls": [Decimal("1.5"), Decimal("2.5"), Decimal("3.5")]},
        ]
        regime_b = [
            {"session_id": "s3", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
            {"session_id": "s4", "pnls": [Decimal("1.5"), Decimal("2.5"), Decimal("3.5")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight_low_vol",
            regime_b_label="daytime_low_vol"
        )

        assert result.is_significant is False
        assert result.p_value > 0.05  # Not significant

    def test_compare_different_regimes_significant(self):
        """Very different PnLs should produce significant result (p < 0.05)."""
        analyzer = RegimeAnalyzer()

        # Regime A has low PnLs, Regime B has high PnLs
        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
            {"session_id": "s2", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
        ]
        regime_b = [
            {"session_id": "s3", "pnls": [Decimal("10.0"), Decimal("20.0"), Decimal("30.0")]},
            {"session_id": "s4", "pnls": [Decimal("10.0"), Decimal("20.0"), Decimal("30.0")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight_low_vol",
            regime_b_label="daytime_high_vol"
        )

        assert result.is_significant is True
        assert result.p_value < 0.05

    def test_compare_regimes_interpretation_b_wins(self):
        """When regime B mean > A mean and significant, interpretation should mention B."""
        analyzer = RegimeAnalyzer()

        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
        ]
        regime_b = [
            {"session_id": "s2", "pnls": [Decimal("10.0"), Decimal("20.0"), Decimal("30.0")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight",
            regime_b_label="daytime"
        )

        # Regime B should have higher mean
        assert result.regime_b_pnl_mean > result.regime_a_pnl_mean
        assert "daytime" in result.interpretation.lower()

    def test_compare_regimes_interpretation_a_wins(self):
        """When regime A mean > B mean and significant, interpretation should mention A."""
        analyzer = RegimeAnalyzer()

        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("10.0"), Decimal("20.0"), Decimal("30.0")]},
        ]
        regime_b = [
            {"session_id": "s2", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight",
            regime_b_label="daytime"
        )

        # Regime A should have higher mean
        assert result.regime_a_pnl_mean > result.regime_b_pnl_mean
        assert "overnight" in result.interpretation.lower()

    def test_compare_regimes_not_significant_interpretation(self):
        """When not significant, interpretation should say so."""
        analyzer = RegimeAnalyzer()

        # Similar PnLs
        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("5.0"), Decimal("6.0"), Decimal("7.0")]},
        ]
        regime_b = [
            {"session_id": "s2", "pnls": [Decimal("5.5"), Decimal("6.5"), Decimal("7.5")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight",
            regime_b_label="daytime"
        )

        if not result.is_significant:
            assert "no significant difference" in result.interpretation.lower()

    def test_welch_ttest_used(self):
        """Result should match scipy.stats.ttest_ind with equal_var=False."""
        from scipy.stats import ttest_ind

        analyzer = RegimeAnalyzer()

        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]},
        ]
        regime_b = [
            {"session_id": "s2", "pnls": [Decimal("10.0"), Decimal("20.0"), Decimal("30.0")]},
        ]

        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight",
            regime_b_label="daytime"
        )

        # Calculate expected result
        a_pnls = [float(p) for p in regime_a[0]["pnls"]]
        b_pnls = [float(p) for p in regime_b[0]["pnls"]]
        expected_statistic, expected_p = ttest_ind(a_pnls, b_pnls, equal_var=False)

        # Should match Welch's t-test result
        assert abs(result.p_value - expected_p) < 0.0001

    def test_empty_regime_a_raises_error(self):
        """Empty regime A sessions should raise ValueError."""
        analyzer = RegimeAnalyzer()

        with pytest.raises(ValueError, match="regime_a_sessions.*empty"):
            analyzer.compare_regimes(
                regime_a_sessions=[],  # Empty
                regime_b_sessions=[{"session_id": "s1", "pnls": [Decimal("1.0")]}],
                regime_a_label="overnight",
                regime_b_label="daytime"
            )

    def test_empty_regime_b_raises_error(self):
        """Empty regime B sessions should raise ValueError."""
        analyzer = RegimeAnalyzer()

        with pytest.raises(ValueError, match="regime_b_sessions.*empty"):
            analyzer.compare_regimes(
                regime_a_sessions=[{"session_id": "s1", "pnls": [Decimal("1.0")]}],
                regime_b_sessions=[],  # Empty
                regime_a_label="overnight",
                regime_b_label="daytime"
            )

    def test_single_session_per_regime(self):
        """Single session per regime should still work (low power but valid)."""
        analyzer = RegimeAnalyzer()

        regime_a = [
            {"session_id": "s1", "pnls": [Decimal("1.0"), Decimal("2.0")]},
        ]
        regime_b = [
            {"session_id": "s2", "pnls": [Decimal("10.0"), Decimal("20.0")]},
        ]

        # Should not raise, should return valid result
        result = analyzer.compare_regimes(
            regime_a_sessions=regime_a,
            regime_b_sessions=regime_b,
            regime_a_label="overnight",
            regime_b_label="daytime"
        )

        assert isinstance(result, RegimeComparisonResult)
        assert result.p_value >= 0.0
        assert result.p_value <= 1.0
