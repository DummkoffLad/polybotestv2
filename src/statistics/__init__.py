"""Statistical validation module for trading bot.

Provides confidence interval calculations and sample size adequacy checks.
"""

from .confidence import ConfidenceIntervalCalculator
from .sample_size import SampleSizeChecker, SampleSizeWarning

__all__ = [
    "ConfidenceIntervalCalculator",
    "SampleSizeChecker",
    "SampleSizeWarning",
]
