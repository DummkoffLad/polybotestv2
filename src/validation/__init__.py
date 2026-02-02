"""
Validation tools for testing strategy robustness.

This module provides tools for out-of-sample testing, parameter sensitivity analysis,
latency simulation, and validation reporting.
"""

from .data_split import DataSplitManager
from .latency_sim import (
    LatencyConfig,
    LatencySimulator,
    LatencyImpactResult,
)
from .sensitivity import (
    SensitivitySweeper,
    SensitivityResult,
    ParamSweepResult,
)
from .report import (
    ValidationReportGenerator,
    GoNoGoDecision,
    ValidationSummary,
)

__all__ = [
    "DataSplitManager",
    "LatencyConfig",
    "LatencySimulator",
    "LatencyImpactResult",
    "SensitivitySweeper",
    "SensitivityResult",
    "ParamSweepResult",
    "ValidationReportGenerator",
    "GoNoGoDecision",
    "ValidationSummary",
]
