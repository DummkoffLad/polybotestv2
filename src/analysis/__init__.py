"""Performance analysis modules.

Provides trade attribution, equity tracking, drawdown analysis, and slippage
measurement for evaluating trading strategy performance.

Exports:
- AttributedTrade, TradeAttributor: Per-trade PnL attribution
- EquitySnapshot, EquityTracker: Timestamped equity curves
- DrawdownAnalyzer: Peak-to-trough drawdown calculation
- SlippageAnalyzer, SlippageMeasurement, SizingGap, SelectionGap: Slippage metrics
- ReportGenerator: Console summaries and chart generation
"""

def __getattr__(name):
    """Lazy imports to avoid circular dependencies."""
    if name == "AttributedTrade":
        from .attribution import AttributedTrade
        return AttributedTrade
    elif name == "TradeAttributor":
        from .attribution import TradeAttributor
        return TradeAttributor
    elif name == "EquitySnapshot":
        from .equity_tracker import EquitySnapshot
        return EquitySnapshot
    elif name == "EquityTracker":
        from .equity_tracker import EquityTracker
        return EquityTracker
    elif name == "DrawdownAnalyzer":
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
    elif name == "ReportGenerator":
        from .reports import ReportGenerator
        return ReportGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
