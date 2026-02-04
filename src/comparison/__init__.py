"""Comparison infrastructure for multi-strategy analysis.

This module provides tools to run multiple strategies on identical session data,
collect results, and generate comparison reports.

Phase 7 of the trading bot development.
"""

from .comparator import StrategyComparator, ComparisonResult, StrategyResult
from .visualizer import create_equity_comparison, save_equity_html
from .metrics import calculate_strategy_metrics, create_metrics_table
from .decision_matrix import create_decision_matrix, get_trade_listing
from .tear_sheets import generate_tear_sheets, generate_single_tear_sheet

__all__ = [
    # Comparator
    "StrategyComparator",
    "ComparisonResult",
    "StrategyResult",
    # Visualization
    "create_equity_comparison",
    "save_equity_html",
    # Metrics
    "calculate_strategy_metrics",
    "create_metrics_table",
    # Decision Matrix
    "create_decision_matrix",
    "get_trade_listing",
    # Tear Sheets
    "generate_tear_sheets",
    "generate_single_tear_sheet",
]
