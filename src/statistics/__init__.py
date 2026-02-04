"""Statistical validation module for trading bot.

Provides confidence interval calculations, sample size adequacy checks,
hypothesis testing for strategy comparison, and regime-based analysis.
"""

from .confidence import ConfidenceIntervalCalculator
from .sample_size import SampleSizeChecker, SampleSizeWarning
from .hypothesis import StrategyComparator, ComparisonResult
from .regime import RegimeAnalyzer, RegimeMetrics, RegimeComparisonResult

__all__ = [
    "ConfidenceIntervalCalculator",
    "SampleSizeChecker",
    "SampleSizeWarning",
    "StrategyComparator",
    "ComparisonResult",
    "RegimeAnalyzer",
    "RegimeMetrics",
    "RegimeComparisonResult",
]
