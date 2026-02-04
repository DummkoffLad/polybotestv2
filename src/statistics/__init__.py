"""Statistical validation module for trading bot.

Provides confidence interval calculations, sample size adequacy checks,
and hypothesis testing for strategy comparison.
"""

from .confidence import ConfidenceIntervalCalculator
from .sample_size import SampleSizeChecker, SampleSizeWarning
from .hypothesis import StrategyComparator, ComparisonResult

__all__ = [
    "ConfidenceIntervalCalculator",
    "SampleSizeChecker",
    "SampleSizeWarning",
    "StrategyComparator",
    "ComparisonResult",
]
