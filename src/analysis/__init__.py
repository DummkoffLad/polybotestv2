"""Performance analysis modules.

Provides equity tracking, drawdown analysis, slippage measurement,
and trade attribution for evaluating trading strategy performance.
"""

def __getattr__(name):
    """Lazy imports to avoid circular dependencies."""
    if name == "DrawdownAnalyzer":
        from .drawdown import DrawdownAnalyzer
        return DrawdownAnalyzer
    elif name == "SlippageAnalyzer":
        from .slippage import SlippageAnalyzer
        return SlippageAnalyzer
    elif name == "SlippageMeasurement":
        from .slippage import SlippageMeasurement
        return SlippageMeasurement
    elif name == "SizingGap":
        from .slippage import SizingGap
        return SizingGap
    elif name == "SelectionGap":
        from .slippage import SelectionGap
        return SelectionGap
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
