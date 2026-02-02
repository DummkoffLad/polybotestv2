"""
Validation tools for testing strategy robustness.

This module provides tools for out-of-sample testing, parameter sensitivity analysis,
latency simulation, and validation reporting.
"""


def __getattr__(name):
    """Lazy import for validation module exports."""
    if name == "DataSplitManager":
        from .data_split import DataSplitManager
        return DataSplitManager
    elif name in ("SensitivitySweeper", "SensitivityResult", "ParamSweepResult"):
        from .sensitivity import SensitivitySweeper, SensitivityResult, ParamSweepResult
        return locals()[name]
    elif name in ("LatencySimulator", "LatencyConfig", "LatencyImpactResult"):
        from .latency_sim import LatencySimulator, LatencyConfig, LatencyImpactResult
        return locals()[name]
    elif name in ("ValidationReportGenerator", "GoNoGoDecision", "ValidationSummary"):
        from .report import ValidationReportGenerator, GoNoGoDecision, ValidationSummary
        return locals()[name]
    elif name in ("ValidationPipeline", "run_validation", "BASELINE_PARAMS"):
        from .pipeline import ValidationPipeline, run_validation, BASELINE_PARAMS
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DataSplitManager",
    "SensitivitySweeper",
    "SensitivityResult",
    "ParamSweepResult",
    "LatencySimulator",
    "LatencyConfig",
    "LatencyImpactResult",
    "ValidationReportGenerator",
    "GoNoGoDecision",
    "ValidationSummary",
    "ValidationPipeline",
    "run_validation",
    "BASELINE_PARAMS",
]
