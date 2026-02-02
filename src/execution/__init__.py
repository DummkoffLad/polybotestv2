"""Execution adapters."""
from .base import ExecutionAdapter
from .dry_run import NullExecutionAdapter
DryRunAdapter = NullExecutionAdapter
