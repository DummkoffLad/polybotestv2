"""Execution adapters with STRICT mode separation.

CRITICAL SAFETY DESIGN:
- NullExecutionAdapter: Pure no-op, cannot place orders
- LiveExecutionAdapter: Real API calls, isolated module

These adapters are selected at startup based on mode and CANNOT be switched
without restarting the process.
"""

from .base import ExecutionAdapter
from .null_adapter import NullExecutionAdapter
# NOTE: LiveExecutionAdapter is imported separately to avoid loading real API code
# in DRY_RUN mode

__all__ = [
    "ExecutionAdapter",
    "NullExecutionAdapter",
]
