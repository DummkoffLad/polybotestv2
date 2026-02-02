"""
Validation tools for testing strategy robustness.

This module provides tools for out-of-sample testing, parameter sensitivity analysis,
latency simulation, and validation reporting.
"""

from .data_split import DataSplitManager

__all__ = [
    "DataSplitManager",
]
