"""
Unit tests for TradeRanker - trade prioritization with soft diversification.

Tests verify:
- Ranking by Kelly edge score (highest first)
- Quality score fallback when no Kelly edge
- 15% correlation penalty for existing positions
- Rebalancing threshold (1.5x edge gap)
"""
import pytest
from decimal import Decimal
from datetime import datetime, UTC
from src.core.trade_ranker import TradeRanker, TradeOpportunity


class TestTradeRanker:
    """Test TradeRanker prioritization and rebalancing logic."""

    def test_rank_by_kelly_edge_descending(self):
        """Opportunities ranked by Kelly edge, highest first."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.80"),
                kelly_edge=Decimal("0.5"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="BTC",
                market_id="btc-perp",
                leader_dollars=Decimal("200"),
                quality_score=Decimal("0.85"),
                kelly_edge=Decimal("1.2"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="ETH",
                market_id="eth-perp",
                leader_dollars=Decimal("150"),
                quality_score=Decimal("0.75"),
                kelly_edge=Decimal("0.8"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        current_positions = {}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # Should be ordered: BTC (1.2), ETH (0.8), SOL (0.5)
        assert ranked[0].token_id == "BTC"
        assert ranked[1].token_id == "ETH"
        assert ranked[2].token_id == "SOL"

    def test_no_kelly_edge_uses_quality_score(self):
        """Opportunities without Kelly edge fall back to quality score."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.60"),
                kelly_edge=None,
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="BTC",
                market_id="btc-perp",
                leader_dollars=Decimal("200"),
                quality_score=Decimal("0.90"),
                kelly_edge=None,
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        current_positions = {}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # Should be ordered by quality: BTC (0.90), SOL (0.60)
        assert ranked[0].token_id == "BTC"
        assert ranked[1].token_id == "SOL"

    def test_correlation_penalty_applied(self):
        """Token already in positions gets 15% edge reduction."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.80"),
                kelly_edge=Decimal("1.0"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="BTC",
                market_id="btc-perp",
                leader_dollars=Decimal("200"),
                quality_score=Decimal("0.85"),
                kelly_edge=Decimal("1.0"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        # SOL already in positions -> gets penalty
        current_positions = {"SOL": Decimal("50")}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # BTC should rank higher (no penalty) even though same kelly_edge
        # SOL: 1.0 * 0.85 = 0.85, BTC: 1.0 * 1.0 = 1.0
        assert ranked[0].token_id == "BTC"
        assert ranked[1].token_id == "SOL"

    def test_no_penalty_for_new_token(self):
        """Token NOT in positions gets no penalty."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.80"),
                kelly_edge=Decimal("1.0"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        # No existing positions
        current_positions = {}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # Just verify it ranks (no penalty applied)
        assert len(ranked) == 1
        assert ranked[0].token_id == "SOL"

    def test_conviction_multiplier_applied(self):
        """Edge score multiplied by conviction multiplier."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.80"),
                kelly_edge=Decimal("1.0"),
                conviction_multiplier=Decimal("0.5"),  # Low conviction
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="BTC",
                market_id="btc-perp",
                leader_dollars=Decimal("200"),
                quality_score=Decimal("0.85"),
                kelly_edge=Decimal("0.8"),
                conviction_multiplier=Decimal("1.5"),  # High conviction
                timestamp=datetime.now(UTC)
            ),
        ]

        current_positions = {}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # BTC: 0.8 * 1.5 = 1.2, SOL: 1.0 * 0.5 = 0.5
        assert ranked[0].token_id == "BTC"
        assert ranked[1].token_id == "SOL"

    def test_mixed_kelly_and_quality(self):
        """Mix of Kelly and quality-based opportunities ranks correctly."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.95"),
                kelly_edge=None,  # No Kelly
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
            TradeOpportunity(
                token_id="BTC",
                market_id="btc-perp",
                leader_dollars=Decimal("200"),
                quality_score=Decimal("0.70"),
                kelly_edge=Decimal("1.0"),  # Has Kelly
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        current_positions = {}
        available_capital = Decimal("1000")

        ranked = ranker.rank_opportunities(opportunities, current_positions, available_capital)

        # BTC (Kelly=1.0) should beat SOL (quality=0.95)
        assert ranked[0].token_id == "BTC"
        assert ranked[1].token_id == "SOL"

    def test_should_rebalance_true_large_gap(self):
        """New edge 1.5x+ existing edge returns True."""
        ranker = TradeRanker()

        # New edge is 3x existing
        assert ranker.should_rebalance(
            existing_edge=Decimal("1.0"),
            new_edge=Decimal("3.0")
        ) is True

        # Exactly 1.5x
        assert ranker.should_rebalance(
            existing_edge=Decimal("1.0"),
            new_edge=Decimal("1.5")
        ) is True

    def test_should_rebalance_false_small_gap(self):
        """New edge < 1.5x existing edge returns False."""
        ranker = TradeRanker()

        # 1.2x is not enough
        assert ranker.should_rebalance(
            existing_edge=Decimal("1.0"),
            new_edge=Decimal("1.2")
        ) is False

        # 1.4x is not enough
        assert ranker.should_rebalance(
            existing_edge=Decimal("1.0"),
            new_edge=Decimal("1.4")
        ) is False

    def test_should_rebalance_zero_existing(self):
        """Zero existing edge always allows rebalance."""
        ranker = TradeRanker()

        assert ranker.should_rebalance(
            existing_edge=Decimal("0"),
            new_edge=Decimal("0.5")
        ) is True

        assert ranker.should_rebalance(
            existing_edge=Decimal("0"),
            new_edge=Decimal("0.1")
        ) is True

    def test_empty_opportunities_returns_empty(self):
        """Empty opportunities list returns empty."""
        ranker = TradeRanker()

        ranked = ranker.rank_opportunities([], {}, Decimal("1000"))

        assert ranked == []

    def test_single_opportunity_returns_it(self):
        """Single opportunity returns as-is."""
        ranker = TradeRanker()

        opportunities = [
            TradeOpportunity(
                token_id="SOL",
                market_id="sol-perp",
                leader_dollars=Decimal("100"),
                quality_score=Decimal("0.80"),
                kelly_edge=Decimal("1.0"),
                conviction_multiplier=Decimal("1.0"),
                timestamp=datetime.now(UTC)
            ),
        ]

        ranked = ranker.rank_opportunities(opportunities, {}, Decimal("1000"))

        assert len(ranked) == 1
        assert ranked[0].token_id == "SOL"
