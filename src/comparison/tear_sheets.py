"""QuantStats HTML tear sheet generation.

Per RESEARCH.md:
- QuantStats analyzes return series (daily, weekly, monthly), not discrete trades
- Convert equity curve to returns series
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, TYPE_CHECKING

import pandas as pd
import quantstats as qs

if TYPE_CHECKING:
    from .comparator import ComparisonResult, StrategyResult

logger = logging.getLogger(__name__)


def generate_single_tear_sheet(
    result: "StrategyResult",
    output_path: Path,
    session_id: str = ""
) -> Path:
    """Generate QuantStats HTML tear sheet for a single strategy.

    Per RESEARCH.md:
    - QuantStats analyzes return series, not discrete trades
    - Convert equity curve to returns series

    Args:
        result: StrategyResult from comparison
        output_path: Path to write the HTML file
        session_id: Optional session ID for the title

    Returns:
        Path to the generated HTML file

    Raises:
        ValueError: If equity data is empty or invalid
    """
    output_path = Path(output_path)

    # Get equity DataFrame
    equity_df = result.equity_df

    if equity_df is None or equity_df.empty:
        raise ValueError(f"Empty equity data for strategy {result.strategy_name}")

    # Extract equity series
    if 'equity' in equity_df.columns:
        equity_series = equity_df['equity']
    elif len(equity_df.columns) > 0:
        # Use first numeric column
        equity_series = equity_df.iloc[:, 0]
    else:
        raise ValueError(f"No equity column found for strategy {result.strategy_name}")

    # Convert to returns series: pct_change().dropna()
    returns = equity_series.pct_change().dropna()

    # Handle edge case of constant equity (all zeros)
    if returns.empty or returns.std() == 0:
        # Create a minimal returns series to avoid QuantStats errors
        logger.warning(f"Strategy {result.strategy_name} has constant/empty equity, using zeros")
        returns = pd.Series([0.0], index=equity_series.index[:1] if len(equity_series.index) > 0 else [0])

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build title
    title = f"{result.strategy_name} Performance Analysis"
    if session_id:
        title = f"{title} - Session {session_id}"

    # Generate tear sheet
    # QuantStats expects a pandas Series with datetime index
    try:
        qs.reports.html(
            returns,
            output=str(output_path),
            title=title,
            benchmark=None  # Could add SPY or other benchmark
        )
    except Exception as e:
        logger.error(f"Failed to generate tear sheet for {result.strategy_name}: {e}")
        raise

    return output_path


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
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result_paths: Dict[str, Path] = {}

    for strategy_result in comparison.strategy_results:
        strategy_name = strategy_result.strategy_name
        output_path = output_dir / f"{strategy_name}_tearsheet.html"

        logger.info(f"Generating tear sheet for {strategy_name}")

        try:
            tear_sheet_path = generate_single_tear_sheet(
                strategy_result,
                output_path,
                session_id=comparison.session_id
            )
            result_paths[strategy_name] = tear_sheet_path
        except ValueError as e:
            logger.warning(f"Skipping tear sheet for {strategy_name}: {e}")
            continue

    return result_paths
