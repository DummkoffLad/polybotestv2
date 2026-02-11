"""Comprehensive unit tests for Portfolio cost basis, PnL, invariants, and exposure.

KNOWN BUGS DOCUMENTED IN THIS FILE:
- test_multiple_markets_same_token_id: Portfolio position keying bug
  Portfolio._positions keyed only by token_id, not (token_id, market_id, side).
  Same token_id in different markets incorrectly accumulates into single position.
  See inline "# BUG:" comments in test for details.
  Tracked for future fix, doesn't block testing.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from src.core.portfolio import Portfolio, PortfolioInvariantError, PortfolioPosition
from src.core.types import Side
from tests.unit.conftest import make_trade, make_prices


# ============================================================================
# COST BASIS TESTS
# ============================================================================

def test_single_buy_cost_basis(fresh_portfolio):
    """Buy 10 shares @ 0.50. Cost basis should be 5.00, avg_price 0.5000."""
    portfolio = fresh_portfolio

    pos = portfolio.apply_buy(
        token_id="token_a",
        market_id="market_1",
        side=Side.UP,
        shares=Decimal("10"),
        price=Decimal("0.50")
    )

    assert pos.shares == Decimal("10")
    assert pos.cost_basis == Decimal("5.00")
    assert pos.avg_price == Decimal("0.5000")


def test_multiple_buys_cost_basis(fresh_portfolio):
    """Buy 10 @ 0.50, then 20 @ 0.60. Cost basis accumulates correctly."""
    portfolio = fresh_portfolio

    # First buy: 10 @ 0.50 = $5.00
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Second buy: 20 @ 0.60 = $12.00
    pos = portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("20"), Decimal("0.60"))

    # Total: 30 shares, $17.00 cost, avg = 17/30 = 0.5667 (quantized)
    assert pos.shares == Decimal("30")
    assert pos.cost_basis == Decimal("17.00")
    # avg_price is quantized to 0.0001 in Portfolio.apply_buy
    expected_avg = (Decimal("17.00") / Decimal("30")).quantize(Decimal("0.0001"))
    assert pos.avg_price == expected_avg


def test_buy_different_tokens_independent(fresh_portfolio):
    """Buy token_a and token_b. Each position tracks independently."""
    portfolio = fresh_portfolio

    pos_a = portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    pos_b = portfolio.apply_buy("token_b", "market_2", Side.DOWN, Decimal("20"), Decimal("0.60"))

    assert pos_a.shares == Decimal("10")
    assert pos_a.cost_basis == Decimal("5.00")
    assert pos_b.shares == Decimal("20")
    assert pos_b.cost_basis == Decimal("12.00")

    # Verify they're separate positions
    assert portfolio.get_total_deployed() == Decimal("17.00")


# ============================================================================
# REALIZED PNL TESTS
# ============================================================================

def test_sell_at_profit_realized_pnl(fresh_portfolio):
    """Buy 10 @ 0.50, sell 5 @ 0.70. Realized PnL should be 5 * (0.70 - 0.50) = 1.00."""
    portfolio = fresh_portfolio

    # Buy 10 @ 0.50 = $5.00 cost
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Sell 5 @ 0.70 = $3.50 proceeds
    # Cost of sold = 5.00 * (5/10) = 2.50
    # Realized PnL = 3.50 - 2.50 = 1.00
    pos = portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("5"), Decimal("0.70"))

    assert portfolio.realized_pnl == Decimal("1.00")
    assert pos.shares == Decimal("5")
    assert pos.cost_basis == Decimal("2.50")  # Remaining cost basis


def test_sell_at_loss_realized_pnl(fresh_portfolio):
    """Buy 10 @ 0.60, sell 10 @ 0.40. Realized PnL should be 10 * (0.40 - 0.60) = -2.00."""
    portfolio = fresh_portfolio

    # Buy 10 @ 0.60 = $6.00 cost
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.60"))

    # Sell 10 @ 0.40 = $4.00 proceeds
    # Cost of sold = 6.00
    # Realized PnL = 4.00 - 6.00 = -2.00
    pos = portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.40"))

    assert portfolio.realized_pnl == Decimal("-2.00")
    # Position should be cleared (dust cleanup)
    assert pos.shares == Decimal("0")
    assert pos.cost_basis == Decimal("0")


def test_partial_sell_preserves_remaining_cost_basis(fresh_portfolio):
    """Buy 20 @ 0.50 (cost=10), sell 10 @ 0.60. Remaining cost_basis should be 5.00."""
    portfolio = fresh_portfolio

    # Buy 20 @ 0.50 = $10.00 cost
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("20"), Decimal("0.50"))

    # Sell half (10 shares) @ 0.60
    pos = portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.60"))

    # Remaining: 10 shares, cost_basis = 10 * (10/20) = 5.00
    assert pos.shares == Decimal("10")
    assert pos.cost_basis == Decimal("5.00")
    assert pos.avg_price == Decimal("0.5000")  # avg_price stays same


# ============================================================================
# UNREALIZED PNL TESTS
# ============================================================================

def test_unrealized_pnl_at_higher_price(fresh_portfolio):
    """Buy 10 @ 0.50 (cost=5). Unrealized PnL at 0.70 should be 10*0.70 - 5 = 2.00."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Calculate PnL with current price 0.70
    current_prices = {"token_a": Decimal("0.70")}
    realized, unrealized = portfolio.calculate_pnl(current_prices)

    assert realized == Decimal("0")  # No sells yet
    assert unrealized == Decimal("2.00")  # 10*0.70 - 5 = 7 - 5 = 2


def test_unrealized_pnl_at_lower_price(fresh_portfolio):
    """Buy 10 @ 0.60 (cost=6). Unrealized PnL at 0.40 should be 10*0.40 - 6 = -2.00."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.60"))

    # Calculate PnL with current price 0.40
    current_prices = {"token_a": Decimal("0.40")}
    realized, unrealized = portfolio.calculate_pnl(current_prices)

    assert realized == Decimal("0")
    assert unrealized == Decimal("-2.00")  # 10*0.40 - 6 = 4 - 6 = -2


# ============================================================================
# COMBINED PNL TESTS
# ============================================================================

def test_realized_plus_unrealized_total(fresh_portfolio):
    """Buy 20 @ 0.50, sell 10 @ 0.70 (realized=2.00). Unrealized at 0.80 = 3.00. Total = 5.00."""
    portfolio = fresh_portfolio

    # Buy 20 @ 0.50 = $10.00 cost
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("20"), Decimal("0.50"))

    # Sell 10 @ 0.70 = $7.00 proceeds
    # Cost of sold = 10 * (10/20) = 5.00
    # Realized PnL = 7.00 - 5.00 = 2.00
    portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.70"))

    # Remaining: 10 shares @ 0.50 avg, cost_basis = 5.00
    # Current price: 0.80
    # Unrealized = 10*0.80 - 5 = 8 - 5 = 3.00
    current_prices = {"token_a": Decimal("0.80")}
    realized, unrealized = portfolio.calculate_pnl(current_prices)

    assert realized == Decimal("2.00")
    assert unrealized == Decimal("3.00")
    total_pnl = realized + unrealized
    assert total_pnl == Decimal("5.00")


# ============================================================================
# INVARIANT TESTS
# ============================================================================

def test_buy_negative_shares_raises(fresh_portfolio):
    """apply_buy with negative shares should raise PortfolioInvariantError."""
    portfolio = fresh_portfolio

    with pytest.raises(PortfolioInvariantError, match="BUY shares must be positive"):
        portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("-5"), Decimal("0.50"))


def test_buy_price_zero_raises(fresh_portfolio):
    """apply_buy with price=0 should raise PortfolioInvariantError."""
    portfolio = fresh_portfolio

    with pytest.raises(PortfolioInvariantError, match="BUY price must be in \\(0,1\\)"):
        portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0"))


def test_buy_price_one_raises(fresh_portfolio):
    """apply_buy with price=1.0 should raise PortfolioInvariantError."""
    portfolio = fresh_portfolio

    with pytest.raises(PortfolioInvariantError, match="BUY price must be in \\(0,1\\)"):
        portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("1.0"))


def test_sell_negative_shares_raises(fresh_portfolio):
    """apply_sell with negative shares should raise PortfolioInvariantError."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    with pytest.raises(PortfolioInvariantError, match="SELL shares must be positive"):
        portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("-5"), Decimal("0.50"))


def test_sell_more_than_owned_clamps(fresh_portfolio):
    """Buy 10, sell 15. Sell should clamp to 10 shares (warning logged, not error)."""
    portfolio = fresh_portfolio

    # Buy 10 shares
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Try to sell 15 (more than owned) - should clamp to 10
    pos = portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("15"), Decimal("0.60"))

    # Should have sold exactly 10 (all owned shares)
    assert pos.shares == Decimal("0")  # All sold (dust cleanup)
    assert pos.cost_basis == Decimal("0")

    # Realized PnL = 10 * (0.60 - 0.50) = 1.00
    assert portfolio.realized_pnl == Decimal("1.00")


# ============================================================================
# EXPOSURE TESTS
# ============================================================================

def test_get_total_deployed(fresh_portfolio):
    """Buy in two different tokens, verify total_deployed == sum of cost bases."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))  # $5
    portfolio.apply_buy("token_b", "market_2", Side.DOWN, Decimal("20"), Decimal("0.60"))  # $12

    assert portfolio.get_total_deployed() == Decimal("17.00")


def test_get_market_exposure(fresh_portfolio):
    """Buy two tokens in market_a, one in market_b. Verify get_market_exposure returns only market_a."""
    portfolio = fresh_portfolio

    # Market A: two positions
    portfolio.apply_buy("token_a1", "market_a", Side.UP, Decimal("10"), Decimal("0.50"))    # $5
    portfolio.apply_buy("token_a2", "market_a", Side.DOWN, Decimal("10"), Decimal("0.60"))  # $6

    # Market B: one position
    portfolio.apply_buy("token_b", "market_b", Side.UP, Decimal("10"), Decimal("0.40"))     # $4

    assert portfolio.get_market_exposure("market_a") == Decimal("11.00")  # 5 + 6
    assert portfolio.get_market_exposure("market_b") == Decimal("4.00")


def test_get_side_exposure(fresh_portfolio):
    """Buy UP and DOWN in same market. Verify get_side_exposure returns only queried side."""
    portfolio = fresh_portfolio

    # Same market, different sides
    portfolio.apply_buy("token_up", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))    # $5
    portfolio.apply_buy("token_down", "market_1", Side.DOWN, Decimal("20"), Decimal("0.60"))  # $12

    assert portfolio.get_side_exposure("market_1", Side.UP) == Decimal("5.00")
    assert portfolio.get_side_exposure("market_1", Side.DOWN) == Decimal("12.00")


# ============================================================================
# EDGE CASES
# ============================================================================

def test_dust_cleanup_after_full_sell(fresh_portfolio):
    """Buy 10 shares, sell exactly 10. Verify shares, cost_basis, avg_price all become 0."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    pos = portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.60"))

    # Dust cleanup: shares < 0.001 triggers zeroing
    assert pos.shares == Decimal("0")
    assert pos.cost_basis == Decimal("0")
    assert pos.avg_price == Decimal("0")


def test_portfolio_clear(fresh_portfolio):
    """Buy some positions, call clear(). Verify everything is reset."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("5"), Decimal("0.60"))

    # Before clear
    assert portfolio.get_total_deployed() > 0
    assert portfolio.realized_pnl != 0
    assert portfolio.total_bought > 0
    assert portfolio.total_sold > 0

    # Clear
    portfolio.clear()

    # After clear
    assert portfolio.get_total_deployed() == Decimal("0")
    assert portfolio.realized_pnl == Decimal("0")
    assert portfolio.total_bought == Decimal("0")
    assert portfolio.total_sold == Decimal("0")
    assert len(portfolio.get_positions()) == 0


def test_total_bought_total_sold_tracking(fresh_portfolio):
    """Make several buys and sells. Verify total_bought and total_sold accumulators match."""
    portfolio = fresh_portfolio

    # Buy #1: 10 @ 0.50 = $5
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Buy #2: 20 @ 0.60 = $12
    portfolio.apply_buy("token_b", "market_2", Side.DOWN, Decimal("20"), Decimal("0.60"))

    # Sell #1: 5 @ 0.70 = $3.50
    portfolio.apply_sell("token_a", "market_1", Side.UP, Decimal("5"), Decimal("0.70"))

    # Sell #2: 10 @ 0.55 = $5.50
    portfolio.apply_sell("token_b", "market_2", Side.DOWN, Decimal("10"), Decimal("0.55"))

    assert portfolio.total_bought == Decimal("17.00")  # 5 + 12
    assert portfolio.total_sold == Decimal("9.00")     # 3.50 + 5.50


# ============================================================================
# ADDITIONAL EDGE CASES
# ============================================================================

def test_buy_price_edge_high(fresh_portfolio):
    """apply_buy with price=0.9999 should work (just under 1.0)."""
    portfolio = fresh_portfolio

    # Should work - price is in (0, 1)
    pos = portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.9999"))
    assert pos.shares == Decimal("10")
    assert pos.cost_basis == Decimal("9.999")


def test_buy_price_edge_low(fresh_portfolio):
    """apply_buy with price=0.0001 should work (just above 0)."""
    portfolio = fresh_portfolio

    # Should work - price is in (0, 1)
    pos = portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.0001"))
    assert pos.shares == Decimal("10")
    assert pos.cost_basis == Decimal("0.0010")


def test_position_isolation_by_token(fresh_portfolio):
    """Verify positions are isolated by token_id, not by market_id or side."""
    portfolio = fresh_portfolio

    # Same market, same side, different tokens
    portfolio.apply_buy("token_1", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    portfolio.apply_buy("token_2", "market_1", Side.UP, Decimal("20"), Decimal("0.60"))

    pos1 = portfolio.get("token_1", "market_1", Side.UP)
    pos2 = portfolio.get("token_2", "market_1", Side.UP)

    assert pos1.shares == Decimal("10")
    assert pos2.shares == Decimal("20")
    assert pos1.cost_basis == Decimal("5.00")
    assert pos2.cost_basis == Decimal("12.00")


def test_unrealized_pnl_missing_price_uses_avg_price(fresh_portfolio):
    """If current_price is missing from the dict, calculate_pnl uses avg_price (no change in value)."""
    portfolio = fresh_portfolio

    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Calculate PnL without providing current price for token_a
    current_prices = {}  # Empty - will fall back to avg_price
    realized, unrealized = portfolio.calculate_pnl(current_prices)

    # With avg_price fallback: 10*0.50 - 5 = 5 - 5 = 0
    assert realized == Decimal("0")
    assert unrealized == Decimal("0")


def test_multiple_markets_same_token_id(fresh_portfolio):
    """
    FIX IMPLEMENTED: Portfolio positions are now keyed by (token_id, market_id, side).

    Same token_id in different markets creates separate, independent positions.
    """
    portfolio = fresh_portfolio

    # Buy same token_id in two different markets
    pos1 = portfolio.apply_buy("token_same", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    pos2 = portfolio.apply_buy("token_same", "market_2", Side.DOWN, Decimal("20"), Decimal("0.60"))

    # CORRECT BEHAVIOR: Two independent positions
    assert pos1.shares == Decimal("10")
    assert pos1.cost_basis == Decimal("5.00")
    assert pos1.market_id == "market_1"
    assert pos1.side == Side.UP

    assert pos2.shares == Decimal("20")
    assert pos2.cost_basis == Decimal("12.00")
    assert pos2.market_id == "market_2"
    assert pos2.side == Side.DOWN

    # The portfolio now tracks TWO separate positions for this token_id
    assert len([p for p in portfolio._positions.values() if p.token_id == "token_same"]) == 2


def test_same_token_different_sides_independent(fresh_portfolio):
    """Same token_id in same market but different sides creates independent positions."""
    portfolio = fresh_portfolio

    # Buy same token_id, same market, but different sides
    pos_up = portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    pos_down = portfolio.apply_buy("token_a", "market_1", Side.DOWN, Decimal("15"), Decimal("0.60"))

    # Should be two separate positions
    assert pos_up.shares == Decimal("10")
    assert pos_up.cost_basis == Decimal("5.00")
    assert pos_up.side == Side.UP

    assert pos_down.shares == Decimal("15")
    assert pos_down.cost_basis == Decimal("9.00")
    assert pos_down.side == Side.DOWN

    # Both in same market
    assert pos_up.market_id == "market_1"
    assert pos_down.market_id == "market_1"


def test_has_position_composite_key(fresh_portfolio):
    """has_position checks composite key (token_id, market_id, side)."""
    portfolio = fresh_portfolio

    # Buy in market_1, UP
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))

    # Should return True only for exact combo
    assert portfolio.has_position("token_a", "market_1", Side.UP) == True

    # Different market: False
    assert portfolio.has_position("token_a", "market_2", Side.UP) == False

    # Different side: False
    assert portfolio.has_position("token_a", "market_1", Side.DOWN) == False

    # Different token: False
    assert portfolio.has_position("token_b", "market_1", Side.UP) == False


def test_get_positions_composite_keys(fresh_portfolio):
    """get_positions returns dict keyed by composite key strings."""
    portfolio = fresh_portfolio

    # Create several positions with different combinations
    portfolio.apply_buy("token_a", "market_1", Side.UP, Decimal("10"), Decimal("0.50"))
    portfolio.apply_buy("token_a", "market_2", Side.UP, Decimal("20"), Decimal("0.60"))
    portfolio.apply_buy("token_b", "market_1", Side.DOWN, Decimal("15"), Decimal("0.55"))

    positions = portfolio.get_positions()

    # Should have 3 positions with composite key format
    assert len(positions) == 3

    # Keys should be composite strings (format: "token_id|market_id|side")
    expected_keys = {
        "token_a|market_1|UP",
        "token_a|market_2|UP",
        "token_b|market_1|DOWN"
    }
    assert set(positions.keys()) == expected_keys

    # Verify position data is correct
    assert positions["token_a|market_1|UP"].shares == Decimal("10")
    assert positions["token_a|market_2|UP"].shares == Decimal("20")
    assert positions["token_b|market_1|DOWN"].shares == Decimal("15")
