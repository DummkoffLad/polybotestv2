"""Comparison infrastructure for multi-strategy analysis.

This module provides tools to run multiple strategies on identical session data,
collect results, and generate comparison reports.

Phase 7 of the trading bot development.
"""

from .comparator import StrategyComparator, ComparisonResult, StrategyResult
from .visualizer import create_equity_comparison, save_equity_html
from .metrics import calculate_strategy_metrics, create_metrics_table

__all__ = [
    "StrategyComparator",
    "ComparisonResult",
    "StrategyResult",
    "create_equity_comparison",
    "save_equity_html",
    "calculate_strategy_metrics",
    "create_metrics_table",
]
