"""Statistical validation module for trading bot.

Provides confidence interval calculations and sample size adequacy checks.
"""

# Import modules as they become available
__all__ = []

try:
    from .confidence import ConfidenceIntervalCalculator
    __all__.extend(["ConfidenceIntervalCalculator"])
except ImportError:
    pass

try:
    from .sample_size import SampleSizeChecker, SampleSizeWarning
    __all__.extend(["SampleSizeChecker", "SampleSizeWarning"])
except ImportError:
    pass

try:
    from .hypothesis import StrategyComparator, ComparisonResult
    __all__.extend(["StrategyComparator", "ComparisonResult"])
except ImportError:
    pass
