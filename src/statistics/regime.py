"""Session regime classification and comparison.

Classifies trading sessions by market regime (time-of-day and volatility)
and compares strategy performance across different regimes using Welch's t-test.

Key concepts:
- Overnight regime: Sessions starting/ending during 10PM-6AM
- Daytime regime: All other sessions
- High volatility: Average price change > 5%
- Low volatility: Average price change <= 5%
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Dict
import numpy as np
from scipy.stats import ttest_ind


# Overnight hours: 10PM to 6AM
OVERNIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4, 5}

# Volatility threshold (from research)
HIGH_VOLATILITY_THRESHOLD = 5.0


@dataclass
class RegimeMetrics:
    """Metrics for a single session's market regime.

    Attributes:
        session_id: Unique session identifier
        start_time: Session start timestamp
        end_time: Session end timestamp
        duration_hours: Session duration in hours
        is_overnight: True if session overlaps overnight hours (10PM-6AM)
        is_daytime: True if session is during daytime hours
        avg_price_volatility: Average absolute price change (%)
        avg_spread_pct: Average bid-ask spread (%), 0.0 if not provided
        event_count: Number of market events in session
        events_per_hour: Event frequency (events/hour)
        regime_label: Combined regime classification (e.g., "overnight_low_vol")
    """
    session_id: str
    start_time: datetime
    end_time: datetime
    duration_hours: float
    is_overnight: bool
    is_daytime: bool
    avg_price_volatility: float
    avg_spread_pct: float
    event_count: int
    events_per_hour: float
    regime_label: str


@dataclass
class RegimeComparisonResult:
    """Result of comparing performance between two regimes.

    Uses Welch's t-test (independent samples, unequal variances) to determine
    if the performance difference between regimes is statistically significant.

    Attributes:
        regime_a_label: Label for first regime
        regime_b_label: Label for second regime
        regime_a_pnl_mean: Mean PnL for regime A
        regime_b_pnl_mean: Mean PnL for regime B
        difference: Difference in means (B - A)
        is_significant: True if p-value < 0.05
        p_value: Statistical significance (lower = more confident)
        interpretation: Human-readable explanation of results
    """
    regime_a_label: str
    regime_b_label: str
    regime_a_pnl_mean: float
    regime_b_pnl_mean: float
    difference: float
    is_significant: bool
    p_value: float
    interpretation: str


class RegimeAnalyzer:
    """Analyzer for session regime classification and comparison.

    Classifies sessions by:
    1. Time-of-day: overnight (10PM-6AM) vs daytime
    2. Volatility: high (>5% avg change) vs low (<=5% avg change)

    Compares regime performance using Welch's t-test for robustness
    to unequal variances.
    """

    def classify_session(
        self,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
        price_changes: List[float],
        spreads: List[float] = None,
        event_count: int = 0
    ) -> RegimeMetrics:
        """Classify a session by its market regime.

        Args:
            session_id: Unique session identifier
            start_time: Session start timestamp
            end_time: Session end timestamp
            price_changes: List of percentage price changes
            spreads: Optional list of percentage spreads
            event_count: Number of market events in session

        Returns:
            RegimeMetrics with classification results
        """
        # Calculate duration
        duration = end_time - start_time
        duration_hours = duration.total_seconds() / 3600.0

        # Classify time-of-day
        # Overnight if start OR end hour is in overnight hours
        is_overnight = (
            start_time.hour in OVERNIGHT_HOURS or
            end_time.hour in OVERNIGHT_HOURS
        )
        is_daytime = not is_overnight

        # Calculate volatility
        if len(price_changes) > 0:
            avg_price_volatility = np.mean([abs(p) for p in price_changes])
        else:
            avg_price_volatility = 0.0

        # Calculate average spread
        if spreads is not None and len(spreads) > 0:
            avg_spread_pct = np.mean(spreads)
        else:
            avg_spread_pct = 0.0

        # Calculate event frequency
        if duration_hours > 0:
            events_per_hour = event_count / duration_hours
        else:
            events_per_hour = 0.0

        # Determine volatility classification
        if avg_price_volatility > HIGH_VOLATILITY_THRESHOLD:
            vol_label = "high_vol"
        else:
            vol_label = "low_vol"

        # Combine into regime label
        time_label = "overnight" if is_overnight else "daytime"
        regime_label = f"{time_label}_{vol_label}"

        return RegimeMetrics(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            duration_hours=duration_hours,
            is_overnight=is_overnight,
            is_daytime=is_daytime,
            avg_price_volatility=avg_price_volatility,
            avg_spread_pct=avg_spread_pct,
            event_count=event_count,
            events_per_hour=events_per_hour,
            regime_label=regime_label
        )

    def compare_regimes(
        self,
        regime_a_sessions: List[Dict],
        regime_b_sessions: List[Dict],
        regime_a_label: str,
        regime_b_label: str
    ) -> RegimeComparisonResult:
        """Compare performance between two market regimes.

        Uses Welch's t-test (independent samples, unequal variances) because:
        - Regimes are independent groups (not paired)
        - Welch's test is robust to unequal variances

        Args:
            regime_a_sessions: List of dicts with 'session_id' and 'pnls' (List[Decimal])
            regime_b_sessions: List of dicts with 'session_id' and 'pnls' (List[Decimal])
            regime_a_label: Label for regime A
            regime_b_label: Label for regime B

        Returns:
            RegimeComparisonResult with statistical comparison

        Raises:
            ValueError: If either regime has no sessions
        """
        # Validate inputs
        if not regime_a_sessions:
            raise ValueError("regime_a_sessions cannot be empty")
        if not regime_b_sessions:
            raise ValueError("regime_b_sessions cannot be empty")

        # Aggregate all PnLs from each regime
        a_pnls = []
        for session in regime_a_sessions:
            a_pnls.extend([float(p) for p in session["pnls"]])

        b_pnls = []
        for session in regime_b_sessions:
            b_pnls.extend([float(p) for p in session["pnls"]])

        # Calculate means
        a_mean = float(np.mean(a_pnls))
        b_mean = float(np.mean(b_pnls))
        difference = b_mean - a_mean

        # Perform Welch's t-test (equal_var=False for robustness)
        statistic, p_value = ttest_ind(a_pnls, b_pnls, equal_var=False)

        # Determine significance (alpha = 0.05)
        # Convert to Python bool to avoid numpy bool type issues
        is_significant = bool(p_value < 0.05)
        p_value = float(p_value)

        # Generate interpretation
        if is_significant:
            if b_mean > a_mean:
                interpretation = (
                    f"{regime_b_label} regime significantly outperforms "
                    f"{regime_a_label} (p={p_value:.4f}, difference={difference:.2f})"
                )
            else:
                interpretation = (
                    f"{regime_a_label} regime significantly outperforms "
                    f"{regime_b_label} (p={p_value:.4f}, difference={difference:.2f})"
                )
        else:
            interpretation = (
                f"No significant difference between {regime_a_label} and "
                f"{regime_b_label} regimes (p={p_value:.4f})"
            )

        return RegimeComparisonResult(
            regime_a_label=regime_a_label,
            regime_b_label=regime_b_label,
            regime_a_pnl_mean=a_mean,
            regime_b_pnl_mean=b_mean,
            difference=difference,
            is_significant=is_significant,
            p_value=p_value,
            interpretation=interpretation
        )
