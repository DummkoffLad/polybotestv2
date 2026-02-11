"""Debug: WHY does snapshot-interleaved processing show +$431 vs standard +$209?

Hypothesis: snapshot path uses end-of-hour prices for resolution (minute 59),
while standard path uses prices from last leader trade (could be minute 45-55).
End-of-hour prices are closer to actual resolution, so PnL is more accurate.
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
from src.data.models import PriceSnapshot
import src.strategies.profit_taker.strategy as pt_module

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def set_best_params():
    pt_module.SKIP_PRICE_LOW = Decimal("0.25")
    pt_module.SKIP_PRICE_HIGH = Decimal("0.97")
    pt_module.SCALE_BOOST = Decimal("8")
    pt_module.MIN_LEADER_TRADE_PCT = Decimal("2.0")
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = Decimal("15")
    pt_module.DRAWDOWN_STOP_THRESHOLD = Decimal("25")
    pt_module.LATE_HOUR_REDUCE_MIN = 59
    pt_module.LATE_HOUR_STOP_MIN = 60
    pt_module.PER_MARKET_CAP_PCT = Decimal("50")
    pt_module.PROFIT_TARGET_LOW = Decimal("100")
    pt_module.PROFIT_TARGET_MID = Decimal("100")
    pt_module.PROFIT_TARGET_HIGH = Decimal("50")


def run_standard(hour_file):
    """Standard path: only process leader events."""
    set_best_params()
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    last_all_prices = {}
    last_event_minute = 0
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        last_event_minute = event.trade.timestamp.minute
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # What prices do we have at resolution time?
    positions = strategy.portfolio.get_positions()
    res_info = {}
    for token_id, pos in positions.items():
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        bid = float(price_snap.bid) if price_snap and price_snap.bid else None
        res_info[token_id[-8:]] = {"bid": bid, "shares": float(pos.shares)}

    # Liquidate
    for token_id, pos in list(positions.items()):
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    pnl = float(strategy.cash) - 100.0
    return {
        "pnl": round(pnl, 2),
        "last_event_minute": last_event_minute,
        "resolution_prices": res_info,
    }


def run_snapshot_interleaved(hour_file):
    """Snapshot path: interleave snapshots with events."""
    set_best_params()
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    snapshot_times = [(s.timestamp, s.prices) for s in replayer.loader._price_snapshots]
    event_times = [(e.trade.timestamp, e) for e in replayer.loader.events]

    timeline = []
    for ts, prices in snapshot_times:
        timeline.append(("snapshot", ts, prices))
    for ts, event in event_times:
        timeline.append(("event", ts, event))
    timeline.sort(key=lambda x: x[1])

    last_all_prices = {}
    last_snapshot_minute = 0
    last_event_minute = 0

    for item_type, ts, data in timeline:
        if item_type == "snapshot":
            for tid, ps in data.items():
                last_all_prices[tid] = ps
            last_snapshot_minute = ts.minute if hasattr(ts, 'minute') else 0
        elif item_type == "event":
            event = data
            all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
            last_all_prices.update(all_prices)  # Merge event prices into running prices
            event.context['all_prices'] = all_prices
            last_event_minute = event.trade.timestamp.minute
            decision = strategy.on_event(event)
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)

    # Resolution with latest snapshot prices
    positions = strategy.portfolio.get_positions()
    res_info = {}
    for token_id, pos in positions.items():
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        bid = float(price_snap.bid) if price_snap and price_snap.bid else None
        res_info[token_id[-8:]] = {"bid": bid, "shares": float(pos.shares)}

    # Liquidate
    for token_id, pos in list(positions.items()):
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    pnl = float(strategy.cash) - 100.0
    return {
        "pnl": round(pnl, 2),
        "last_event_minute": last_event_minute,
        "last_snapshot_minute": last_snapshot_minute,
        "resolution_prices": res_info,
    }


def get_utc_hour_from_file(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts:
                        return int(ts.split("T")[1][:2])
            except:
                continue
    return -1


def main():
    base = Path("data/sessions")
    total_std = total_snap = 0
    diffs = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue

            std = run_standard(hf)
            snap = run_snapshot_interleaved(hf)
            if not std or not snap:
                continue

            diff = snap["pnl"] - std["pnl"]
            total_std += std["pnl"]
            total_snap += snap["pnl"]
            diffs.append(diff)

            if abs(diff) > 2:  # Show hours where it matters
                session = f"{date_dir.name}/{hf.stem}"
                print(f"\n{session} H{utc_h:02d}: STD=${std['pnl']:+.2f}  SNAP=${snap['pnl']:+.2f}  DIFF=${diff:+.2f}")
                print(f"  Last event: min {std['last_event_minute']}  |  Last snapshot: min {snap.get('last_snapshot_minute', '?')}")
                # Compare resolution prices
                for tok in set(list(std["resolution_prices"].keys()) + list(snap["resolution_prices"].keys())):
                    sp = std["resolution_prices"].get(tok, {})
                    snp = snap["resolution_prices"].get(tok, {})
                    std_bid = sp.get("bid", "N/A")
                    snap_bid = snp.get("bid", "N/A")
                    shares = sp.get("shares", snp.get("shares", 0))
                    if std_bid != snap_bid and shares > 0:
                        std_res = "WIN" if (isinstance(std_bid, (int, float)) and std_bid >= 0.50) else "LOSE"
                        snap_res = "WIN" if (isinstance(snap_bid, (int, float)) and snap_bid >= 0.50) else "LOSE"
                        flip = " *** FLIP" if std_res != snap_res else ""
                        print(f"  ...{tok}: std_bid={std_bid} ({std_res}) vs snap_bid={snap_bid} ({snap_res}) [{shares:.0f}sh]{flip}")

    print(f"\n{'='*80}")
    print(f"  TOTAL: Standard=${total_std:+.2f}  Snapshot=${total_snap:+.2f}  Gap=${total_snap-total_std:+.2f}")
    print(f"  Hours where diff > $2: {sum(1 for d in diffs if abs(d) > 2)}/{len(diffs)}")
    print(f"  Hours where diff > $5: {sum(1 for d in diffs if abs(d) > 5)}/{len(diffs)}")
    flips = sum(1 for d in diffs if abs(d) > 10)
    print(f"  Hours where diff > $10: {flips}/{len(diffs)}")


if __name__ == "__main__":
    main()
