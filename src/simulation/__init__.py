"""Simulation and optimization module."""
# Lazy imports to avoid circular dependency with framework.replay
# (optimizer imports SessionReplayer, replay imports FollowMetricsTracker)

def __getattr__(name):
    """Lazy import for simulation module exports."""
    if name in ("SimpleOptimizer", "OptimizationResult", "run_optimization"):
        from .optimizer import SimpleOptimizer, OptimizationResult, run_optimization
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")