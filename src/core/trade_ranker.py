"""
TradeRanker - Trade prioritization with soft diversification.

Ranks opportunities by Kelly edge score (highest first).
Applies 15% soft penalty for correlated positions (same token).
Provides rebalancing logic with 1.5x edge gap threshold.
"""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional


@dataclass
class TradeOpportunity:
    """Trade opportunity with scoring metrics."""
    token_id: str
    market_id: str
    leader_dollars: Decimal
    quality_score: Decimal
    kelly_edge: Optional[Decimal]
    conviction_multiplier: Decimal
    timestamp: datetime


class TradeRanker:
    """
    Ranks trade opportunities with soft diversification.

    Ranking:
    - Primary: Kelly edge (if available)
    - Fallback: Quality score
    - Modifier: Conviction multiplier
    - Penalty: 15% for correlated positions

    Rebalancing:
    - Only when new_edge >= 1.5x existing_edge
    """

    def __init__(
        self,
        correlation_penalty_pct: Decimal = Decimal("0.15"),
        rebalance_edge_gap: Decimal = Decimal("1.5")
    ):
        """
        Initialize TradeRanker.

        Args:
            correlation_penalty_pct: Percentage penalty for correlated positions (default 15%)
            rebalance_edge_gap: Minimum edge ratio to trigger rebalancing (default 1.5x)
        """
        self.correlation_penalty_pct = correlation_penalty_pct
        self.rebalance_edge_gap = rebalance_edge_gap

    def rank_opportunities(
        self,
        opportunities: list[TradeOpportunity],
        current_positions: dict[str, Decimal],
        available_capital: Decimal
    ) -> list[TradeOpportunity]:
        """
        Rank opportunities by adjusted edge score.

        Args:
            opportunities: List of trade opportunities
            current_positions: Dict of token_id -> position size
            available_capital: Available capital for new trades

        Returns:
            Sorted list of opportunities (highest score first)
        """
        if not opportunities:
            return []

        # Calculate adjusted score for each opportunity
        scored_opportunities = []
        for opp in opportunities:
            # Start with Kelly edge or quality score fallback
            base_score = opp.kelly_edge if opp.kelly_edge is not None else opp.quality_score

            # Apply conviction multiplier
            score = base_score * opp.conviction_multiplier

            # Apply correlation penalty if token already in positions
            if opp.token_id in current_positions:
                penalty_multiplier = Decimal("1.0") - self.correlation_penalty_pct
                score = score * penalty_multiplier

            scored_opportunities.append((score, opp))

        # Sort by score descending
        scored_opportunities.sort(key=lambda x: x[0], reverse=True)

        # Return just the opportunities
        return [opp for score, opp in scored_opportunities]

    def should_rebalance(self, existing_edge: Decimal, new_edge: Decimal) -> bool:
        """
        Determine if position should be rebalanced.

        Args:
            existing_edge: Edge of existing position
            new_edge: Edge of potential new position

        Returns:
            True if new_edge >= rebalance_edge_gap * existing_edge
        """
        # Always rebalance if existing edge is zero or negative
        if existing_edge <= Decimal("0"):
            return True

        # Check if new edge meets threshold
        return new_edge >= (self.rebalance_edge_gap * existing_edge)
