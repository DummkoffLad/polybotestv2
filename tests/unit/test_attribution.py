"""Unit tests for trade attribution."""

import pytest
from datetime import datetime
from decimal import Decimal

from src.analysis.attribution import AttributedTrade, TradeAttributor
from src.data.models import MarketEvent, LeaderTrade, PriceSnapshot, TradeAction, TradeSide
from src.strategies.base import TradeDecision, DecisionAction


# ============================================================================
# TEST HELPERS
# ============================================================================

def make_event(
    token_id: str = "0xabc",
    market_id: str = "market1",
    side: TradeSide = TradeSide.UP,
    action: TradeAction = TradeAction.BUY,
    dollars: Decimal = Decimal("10"),
    price: Decimal = Decimal("0.50"),
    timestamp: datetime = None,
) -> MarketEvent:
    """Create a test MarketEvent."""
    if timestamp is None:
        timestamp = datetime(2024, 1, 1, 12, 0, 0)

    shares = dollars / price
    trade = LeaderTrade(
        timestamp=timestamp,
        market_id=market_id,
        token_id=token_id,
        side=side,
        action=action,
        dollars=dollars,
        price=price,
        shares=shares,
        source="test",
    )
    prices = PriceSnapshot(
        token_id=token_id,
        bid=price - Decimal("0.01"),
        ask=price + Decimal("0.01"),
    )
    return MarketEvent(trade=trade, prices=prices)


def make_decision(
    action: DecisionAction = DecisionAction.BUY,
    dollars: Decimal = Decimal("10"),
    shares: Decimal = Decimal("20"),
    price: Decimal = Decimal("0.50"),
) -> TradeDecision:
    """Create a test TradeDecision."""
    if action == DecisionAction.SKIP:
        return TradeDecision.skip("test skip")
    elif action == DecisionAction.BUY:
        return TradeDecision.buy(dollars, shares, price)
    else:
        return TradeDecision.sell(dollars, shares, price)


# ============================================================================
# ATTRIBUTED TRADE TESTS
# ============================================================================

def test_buy_trade_entry_records_correctly():
    """BUY entry should record all fields correctly."""
    event = make_event(
        token_id="0xabc",
        market_id="market1",
        side=TradeSide.UP,
        action=TradeAction.BUY,
        dollars=Decimal("10"),
        price=Decimal("0.50"),
    )
    decision = make_decision(
        action=DecisionAction.BUY,
        dollars=Decimal("10"),
        shares=Decimal("20"),
        price=Decimal("0.50"),
    )

    attributor = TradeAttributor()
    trade_id = attributor.record_entry(event, decision, "mirror")

    assert trade_id == "0xabc_0"
    trades = attributor.get_all_trades()
    assert len(trades) == 1

    trade = trades[0]
    assert trade.token_id == "0xabc"
    assert trade.market_id == "market1"
    assert trade.side == "UP"
    assert trade.action == "BUY"
    assert trade.entry_shares == Decimal("20")
    assert trade.entry_price == Decimal("0.5000")
    assert trade.entry_cost == Decimal("10.0000")
    assert trade.status == "open"
    assert trade.realized_pnl == Decimal("0")
    assert trade.unrealized_pnl == Decimal("0")
    assert trade.leader_dollars == Decimal("10")
    assert trade.leader_price == Decimal("0.5000")
    assert trade.strategy_name == "mirror"


def test_sell_trade_entry_records_correctly():
    """SELL entry should record correctly."""
    event = make_event(
        action=TradeAction.SELL,
        dollars=Decimal("15"),
        price=Decimal("0.60"),
    )
    decision = make_decision(
        action=DecisionAction.SELL,
        dollars=Decimal("15"),
        shares=Decimal("25"),
        price=Decimal("0.60"),
    )

    attributor = TradeAttributor()
    trade_id = attributor.record_entry(event, decision, "conservative")

    trades = attributor.get_all_trades()
    assert len(trades) == 1

    trade = trades[0]
    assert trade.action == "SELL"
    assert trade.entry_shares == Decimal("25")
    assert trade.entry_price == Decimal("0.6000")
    assert trade.entry_cost == Decimal("15.0000")
    assert trade.strategy_name == "conservative"


def test_close_winning_trade():
    """BUY at 0.40, close at 0.60 -> win status, positive PnL."""
    trade = AttributedTrade(
        entry_timestamp=datetime(2024, 1, 1, 12, 0, 0),
        market_id="market1",
        token_id="0xabc",
        side="UP",
        action="BUY",
        entry_shares=Decimal("25"),
        entry_price=Decimal("0.40"),
        entry_cost=Decimal("10"),
        strategy_name="mirror",
    )

    # Close at higher price
    trade.close_trade(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        shares=Decimal("25"),
        price=Decimal("0.60"),
    )

    assert trade.status == "win"
    assert trade.exit_price == Decimal("0.6000")
    assert trade.exit_proceeds == Decimal("15.0000")
    # PnL = proceeds - cost = 15 - 10 = 5
    assert trade.realized_pnl == Decimal("5.0000")
    assert trade.unrealized_pnl == Decimal("0")


def test_close_losing_trade():
    """BUY at 0.60, close at 0.40 -> loss status, negative PnL."""
    trade = AttributedTrade(
        entry_timestamp=datetime(2024, 1, 1, 12, 0, 0),
        market_id="market1",
        token_id="0xabc",
        side="UP",
        action="BUY",
        entry_shares=Decimal("25"),
        entry_price=Decimal("0.60"),
        entry_cost=Decimal("15"),
        strategy_name="mirror",
    )

    # Close at lower price
    trade.close_trade(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        shares=Decimal("25"),
        price=Decimal("0.40"),
    )

    assert trade.status == "loss"
    assert trade.exit_price == Decimal("0.4000")
    assert trade.exit_proceeds == Decimal("10.0000")
    # PnL = proceeds - cost = 10 - 15 = -5
    assert trade.realized_pnl == Decimal("-5.0000")
    assert trade.unrealized_pnl == Decimal("0")


def test_close_breakeven_trade():
    """BUY and close at same price -> breakeven."""
    trade = AttributedTrade(
        entry_timestamp=datetime(2024, 1, 1, 12, 0, 0),
        market_id="market1",
        token_id="0xabc",
        side="UP",
        action="BUY",
        entry_shares=Decimal("20"),
        entry_price=Decimal("0.50"),
        entry_cost=Decimal("10"),
        strategy_name="mirror",
    )

    # Close at same price
    trade.close_trade(
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        shares=Decimal("20"),
        price=Decimal("0.50"),
    )

    assert trade.status == "breakeven"
    assert trade.realized_pnl == Decimal("0.0000")
    assert trade.unrealized_pnl == Decimal("0")


def test_update_unrealized_open_position():
    """Open trade, update with higher price -> positive unrealized."""
    trade = AttributedTrade(
        entry_timestamp=datetime(2024, 1, 1, 12, 0, 0),
        market_id="market1",
        token_id="0xabc",
        side="UP",
        action="BUY",
        entry_shares=Decimal("25"),
        entry_price=Decimal("0.40"),
        entry_cost=Decimal("10"),
        strategy_name="mirror",
    )

    # Update with higher price
    trade.update_unrealized(Decimal("0.60"))

    assert trade.status == "open"
    # unrealized = (25 * 0.60) - 10 = 15 - 10 = 5
    assert trade.unrealized_pnl == Decimal("5.0000")
    assert trade.realized_pnl == Decimal("0")


# ============================================================================
# TRADE ATTRIBUTOR TESTS
# ============================================================================

def test_summary_with_mixed_outcomes():
    """Multiple trades with different outcomes -> verify summary counts and averages."""
    attributor = TradeAttributor()

    # Trade 1: Win (+5)
    event1 = make_event(token_id="0x111", price=Decimal("0.40"))
    decision1 = make_decision(dollars=Decimal("10"), shares=Decimal("25"), price=Decimal("0.40"))
    attributor.record_entry(event1, decision1, "mirror")
    attributor.record_exit("0x111", datetime(2024, 1, 1, 13, 0, 0), Decimal("25"), Decimal("0.60"))

    # Trade 2: Loss (-4)
    event2 = make_event(token_id="0x222", price=Decimal("0.60"))
    decision2 = make_decision(dollars=Decimal("12"), shares=Decimal("20"), price=Decimal("0.60"))
    attributor.record_entry(event2, decision2, "mirror")
    attributor.record_exit("0x222", datetime(2024, 1, 1, 13, 0, 0), Decimal("20"), Decimal("0.40"))

    # Trade 3: Open (unrealized +2)
    event3 = make_event(token_id="0x333", price=Decimal("0.50"))
    decision3 = make_decision(dollars=Decimal("10"), shares=Decimal("20"), price=Decimal("0.50"))
    attributor.record_entry(event3, decision3, "mirror")
    attributor.update_all_unrealized({"0x333": Decimal("0.60")})

    summary = attributor.get_summary()

    assert summary["total_trades"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["open"] == 1
    assert summary["win_rate"] == Decimal("50.00")  # 1 win, 1 loss
    assert summary["total_realized_pnl"] == Decimal("1.0000")  # 5 - 4
    assert summary["total_unrealized_pnl"] == Decimal("2.0000")
    assert summary["avg_win"] == Decimal("5.0000")
    assert summary["avg_loss"] == Decimal("-4.0000")
    assert summary["best_trade"] == Decimal("5.0000")
    assert summary["worst_trade"] == Decimal("-4.0000")


def test_trade_serialization():
    """to_dict() produces valid JSON-serializable dict with str Decimals."""
    trade = AttributedTrade(
        entry_timestamp=datetime(2024, 1, 1, 12, 30, 45),
        market_id="market1",
        token_id="0xabc",
        side="UP",
        action="BUY",
        entry_shares=Decimal("25"),
        entry_price=Decimal("0.40"),
        entry_cost=Decimal("10"),
        leader_dollars=Decimal("100"),
        leader_price=Decimal("0.40"),
        strategy_name="mirror",
    )

    trade_dict = trade.to_dict()

    assert trade_dict["entry_timestamp"] == "2024-01-01T12:30:45"
    assert trade_dict["token_id"] == "0xabc"
    assert trade_dict["entry_shares"] == "25"
    assert trade_dict["entry_price"] == "0.40"
    assert trade_dict["entry_cost"] == "10"
    assert trade_dict["status"] == "open"
    assert trade_dict["realized_pnl"] == "0"
    assert trade_dict["unrealized_pnl"] == "0"
    assert trade_dict["exit_timestamp"] is None
    assert trade_dict["exit_price"] is None


def test_multiple_trades_same_token():
    """Two BUY entries on same token_id -> verify both tracked."""
    attributor = TradeAttributor()

    # First trade
    event1 = make_event(
        token_id="0xabc",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        price=Decimal("0.40"),
    )
    decision1 = make_decision(dollars=Decimal("10"), shares=Decimal("25"), price=Decimal("0.40"))
    trade_id1 = attributor.record_entry(event1, decision1, "mirror")

    # Second trade (same token)
    event2 = make_event(
        token_id="0xabc",
        timestamp=datetime(2024, 1, 1, 13, 0, 0),
        price=Decimal("0.50"),
    )
    decision2 = make_decision(dollars=Decimal("12"), shares=Decimal("24"), price=Decimal("0.50"))
    trade_id2 = attributor.record_entry(event2, decision2, "mirror")

    assert trade_id1 == "0xabc_0"
    assert trade_id2 == "0xabc_1"

    all_trades = attributor.get_all_trades()
    assert len(all_trades) == 2

    # Verify both trades exist with different prices
    assert all_trades[0].entry_price == Decimal("0.4000")
    assert all_trades[1].entry_price == Decimal("0.5000")
