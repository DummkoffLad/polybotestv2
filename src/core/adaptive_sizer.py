"""
AdaptiveSizer: Bridges Phase 3 DynamicSizer and Phase 4 Kelly sizing.

Solves the cold start problem by using DynamicSizer as fallback until per-token
edge data reaches minimum threshold, then seamlessly transitions to Kelly-based sizing.

Key features:
- Falls back to Phase 3 DynamicSizer when EdgeTracker has insufficient data
- Uses Kelly sizing when EdgeTracker has sufficient per-token data
- Applies conviction multiplier AFTER Kelly sizing
- Returns None with reason 'negative_edge' when Kelly computes negative edge
- Returns reason string explaining which sizing method was used

Example:
    config = SizingConfig(base_risk_pct=Decimal("1.0"))
    dynamic_sizer = DynamicSizer(config)
    dynamic_sizer.initialize(Decimal("100.00"))
    kelly_calc = KellyCalculator(kelly_fraction=Decimal("0.5"))
    edge_tracker = EdgeTracker(min_trades_for_kelly=20)

    sizer = AdaptiveSizer(dynamic_sizer, kelly_calc, edge_tracker)

    # Cold start - no data for token
    size, reason = sizer.calculate_position_size(
        token_id="SOL",
        current_equity=Decimal("100.00"),
        quality_score=Decimal("0.50")
    )
    # Returns (Decimal("1.00"), "phase3_fallback")

    # After 20 trades recorded
    size, reason = sizer.calculate_position_size(
        token_id="SOL",
        current_equity=Decimal("100.00"),
        quality_score=Decimal("0.50"),
        conviction_multiplier=Decimal("1.5")
    )
    # Returns (Decimal("..."), "kelly")

    # Negative edge token
    size, reason = sizer.calculate_position_size(
        token_id="SCAM",
        current_equity=Decimal("100.00"),
        quality_score=Decimal("0.50")
    )
    # Returns (None, "negative_edge")
"""

import logging
from decimal import Decimal
from typing import Optional

from src.core.edge_tracker import EdgeTracker
from src.core.kelly_engine import KellyCalculator
from src.core.sizing import DynamicSizer

logger = logging.getLogger(__name__)


class AdaptiveSizer:
    """
    Adaptive position sizer with Kelly + Phase 3 fallback.

    Bridges Phase 3 DynamicSizer and Phase 4 Kelly sizing with graceful cold start handling.
    No trades are ever skipped due to lack of data - falls back to Phase 3 until sufficient
    per-token edge data is available.
    """

    def __init__(
        self,
        dynamic_sizer: DynamicSizer,
        kelly_calculator: KellyCalculator,
        edge_tracker: EdgeTracker
    ):
        """
        Initialize AdaptiveSizer.

        Args:
            dynamic_sizer: Phase 3 DynamicSizer for fallback
            kelly_calculator: KellyCalculator for Kelly sizing
            edge_tracker: EdgeTracker for per-token edge statistics
        """
        self.dynamic_sizer = dynamic_sizer
        self.kelly_calculator = kelly_calculator
        self.edge_tracker = edge_tracker

        logger.info(
            "AdaptiveSizer initialized: "
            f"kelly_fraction={kelly_calculator.kelly_fraction}, "
            f"min_trades={edge_tracker.min_trades_for_kelly}"
        )

    def calculate_position_size(
        self,
        token_id: str,
        current_equity: Decimal,
        quality_score: Decimal,
        conviction_multiplier: Decimal = Decimal("1.0")
    ) -> tuple[Optional[Decimal], str]:
        """
        Calculate position size for a token.

        Logic:
        1. Check EdgeTracker for per-token edge statistics
        2. If insufficient data (cold start) -> use DynamicSizer fallback
        3. If sufficient data -> calculate Kelly size
        4. If Kelly returns None (negative edge) -> return (None, "negative_edge")
        5. Apply conviction_multiplier AFTER Kelly sizing
        6. Quantize to Decimal("0.01")

        Args:
            token_id: Token identifier
            current_equity: Current portfolio equity
            quality_score: Trade quality score (used in fallback mode)
            conviction_multiplier: Conviction multiplier to apply after Kelly (default: 1.0)

        Returns:
            Tuple of (position_size_or_None, reason_string) where reason is one of:
            - "phase3_fallback": Used DynamicSizer (insufficient data)
            - "kelly": Used Kelly sizing (sufficient data)
            - "negative_edge": Kelly computed negative edge (don't trade)
        """
        # Check for per-token edge statistics
        edge_stats = self.edge_tracker.get_edge_stats(token_id)

        # Cold start: No edge data available, fall back to Phase 3
        if edge_stats is None:
            logger.debug(
                f"Cold start for {token_id}: using DynamicSizer fallback"
            )
            size = self.dynamic_sizer.calculate_position_size(
                current_equity=current_equity,
                quality_score=quality_score
            )
            return (size, "phase3_fallback")

        # Unpack edge statistics
        win_rate, avg_win_pct, avg_loss_pct, count = edge_stats

        logger.debug(
            f"Kelly calculation for {token_id}: "
            f"win_rate={win_rate}, avg_win={avg_win_pct}, "
            f"avg_loss={avg_loss_pct}, count={count}"
        )

        # Calculate Kelly size
        kelly_size = self.kelly_calculator.calculate_kelly_size(
            win_rate=win_rate,
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            current_equity=current_equity
        )

        # Negative edge: Don't trade this token
        if kelly_size is None:
            logger.info(
                f"Negative edge for {token_id}: skipping trade"
            )
            return (None, "negative_edge")

        # Apply conviction multiplier AFTER Kelly sizing
        final_size = kelly_size * conviction_multiplier

        # Quantize to cents
        final_size = final_size.quantize(Decimal("0.01"))

        logger.debug(
            f"Kelly size for {token_id}: "
            f"kelly_base={kelly_size}, conviction={conviction_multiplier}, "
            f"final={final_size}"
        )

        return (final_size, "kelly")
