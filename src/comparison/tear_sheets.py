"""QuantStats HTML tear sheet generation.

Per RESEARCH.md:
- QuantStats analyzes return series (daily, weekly, monthly), not discrete trades
- Convert equity curve to returns series
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .comparator import ComparisonResult, StrategyResult


def generate_single_tear_sheet(
    result: "StrategyResult",
    output_path: Path,
    session_id: str = ""
) -> Path:
    """Generate QuantStats HTML tear sheet for a single strategy.

    Args:
        result: StrategyResult from comparison
        output_path: Path to write the HTML file
        session_id: Optional session ID for the title

    Returns:
        Path to the generated HTML file
    """
    raise NotImplementedError("TDD RED phase - implement in GREEN phase")


def generate_tear_sheets(
    comparison: "ComparisonResult",
    output_dir: Path
) -> Dict[str, Path]:
    """Generate tear sheets for all strategies in comparison.

    Creates output_dir if needed.

    Args:
        comparison: ComparisonResult from StrategyComparator
        output_dir: Directory to write tear sheet files

    Returns:
        Dict mapping strategy_name to tear sheet path
    """
    raise NotImplementedError("TDD RED phase - implement in GREEN phase")
