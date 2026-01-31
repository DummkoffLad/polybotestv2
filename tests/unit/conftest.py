"""Shared unit test fixtures and helpers."""
import pytest
from datetime import datetime, timezone
from decimal import Decimal

# Import data models
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide

# Import strategy types
from src.strategies.base import StrategyConfig, DecisionAction, TradeDecision

# Import portfolio
from src.core.portfolio import Portfolio, PortfolioInvariantError

# Import core types
from src.core.types import Side


# ============================================================================
# HELPER FUNCTIONS (not fixtures - callers can pass kwargs to override)
# ============================================================================

def make_trade(**overrides) -> LeaderTrade:
    """Create a LeaderTrade with sensible defaults.

    Defaults:
        timestamp=now(UTC), market_id="test_market", token_id="test_token"
        side=UP, action=BUY, dollars=10, price=0.50, shares=20, source="test"

    Usage:
        trade = make_trade(price=Decimal("0.70"), shares=Decimal("15"))
    """
    defaults = {
        "timestamp": datetime.now(timezone.utc),
        "market_id": "test_market",
        "token_id": "test_token",
        "side": TradeSide.UP,
        "action": TradeAction.BUY,
        "dollars": Decimal("10"),
        "price": Decimal("0.50"),
        "shares": Decimal("20"),
        "source": "test",
        "tx_hash": None,
        "latency_ms": 0,
    }
    defaults.update(overrides)
    return LeaderTrade(**defaults)


def make_prices(**overrides) -> PriceSnapshot:
    """Create a PriceSnapshot with sensible defaults.

    Defaults:
        token_id="test_token", bid=0.49, ask=0.51

    Usage:
        prices = make_prices(bid=Decimal("0.60"), ask=Decimal("0.62"))
    """
    defaults = {
        "token_id": "test_token",
        "bid": Decimal("0.49"),
        "ask": Decimal("0.51"),
        "mid": None,
        "spread_pct": None,
        "timestamp": None,
    }
    defaults.update(overrides)
    return PriceSnapshot(**defaults)


def make_event(**overrides) -> MarketEvent:
    """Create a MarketEvent using make_trade and make_prices.

    Usage:
        event = make_event(
            trade_overrides={"price": Decimal("0.70")},
            price_overrides={"ask": Decimal("0.72")}
        )

    Or for simple cases:
        event = make_event()  # uses all defaults
    """
    trade_overrides = overrides.pop("trade_overrides", {})
    price_overrides = overrides.pop("price_overrides", {})

    trade = make_trade(**trade_overrides)
    prices = make_prices(**price_overrides)

    return MarketEvent(
        trade=trade,
        prices=prices,
        context=overrides.get("context", {}),
        sequence=overrides.get("sequence", 0)
    )


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def base_config() -> StrategyConfig:
    """Return StrategyConfig with $100 starting capital defaults matching production.

    Matches the production config:
    - starting_capital=$100, hourly_budget=$100
    - cash_reserve_pct=10%, leader_capital=$900, k_factor=0.85
    - per_market_cap_pct=30%, per_side_pct=26%, global_exposure_pct=100%
    """
    return StrategyConfig(
        starting_capital=Decimal("100"),
        hourly_budget=Decimal("100"),
        cash_reserve_pct=Decimal("10"),
        leader_capital=Decimal("900"),
        k_factor=Decimal("0.85"),
        per_market_cap_pct=Decimal("30"),
        per_side_pct=Decimal("26"),
        global_exposure_pct=Decimal("100"),
    )


@pytest.fixture
def fresh_portfolio() -> Portfolio:
    """Return a new Portfolio instance with empty state.

    Use this for tests that need a clean portfolio state.
    """
    return Portfolio()
