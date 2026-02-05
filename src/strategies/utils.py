"""Shared utility functions for all strategies.

This module contains helper functions used across multiple strategy implementations
to avoid code duplication and ensure consistent behavior.
"""
from ..core.types import Side
from ..data.models import TradeSide


def to_side(trade_side: TradeSide) -> Side:
    """Convert TradeSide enum to Side enum.

    Args:
        trade_side: TradeSide.UP or TradeSide.DOWN from data models

    Returns:
        Side.UP or Side.DOWN from core types

    Note: Centralized conversion used by all strategies.
          Previously duplicated in 10 strategy files.
    """
    return Side.UP if trade_side == TradeSide.UP else Side.DOWN
