"""Test improvement ideas from analysis:

1. Raise SKIP_PRICE_LOW (0.25 -> 0.35/0.45/0.50) — 0.25-0.45 has 0% WR
2. Stop-loss: sell position if bid drops below entry by X% (cut losers early)
3. Combo: higher skip + stop-loss
"""
import sys
import json
import statistics
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

TRAIN_SESSIONS = {"2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"}
TEST_SESSIONS = {"2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"}


def set_params(overrides):
    pt_module.SKIP_PRICE_LOW = overrides.get("skip_low", Decimal("0.25"))
    pt_module.SKIP_PRICE_HIGH = overrides.get("skip_high", Decimal("0.97"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("2.0"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = Decimal("15")
    pt_module.DRAWDOWN_STOP_THRESHOLD = Decimal("25")
    pt_module.LATE_HOUR_REDUCE_MIN = 59
    pt_module.LATE_HOUR_STOP_MIN = 60
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))
    pt_module.PROFIT_TARGET_LOW = Decimal("100")
    pt_module.PROFIT_TARGET_MID = Decimal("100")
    pt_module.PROFIT_TARGET_HIGH = Decimal("50")


def check_stop_losses(strategy, all_prices, stop_loss_pct):
    """Sell positions that have dropped below entry by stop_loss_pct."""
    exits = 0
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        entry = strategy.our_entries.get(token_id)
        if not entry or entry <= 0:
            continue

        ps = all_prices.get(token_id)
        if not ps or not ps.bid or ps.bid <= 0:
            continue

        # Check if price dropped below stop
        drop_pct = (entry - ps.bid) / entry * 100
        if drop_pct >= stop_loss_pct:
            sell_price = ps.bid
            dollars = pos.shares * sell_price
            strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, sell_price)
            strategy.cash += dollars
            strategy.sells += 1
            if token_id in strategy.our_entries:
                del strategy.our_entries[token_id]
            if token_id in strategy.high_water_marks:
                del strategy.high_water_marks[token_id]
            strategy.hourly_budget_used = max(Decimal("0"), strategy.hourly_budget_used - dollars)
            exits += 1

    return exits


def run_hour(hour_file, overrides, stop_loss_pct=None):
    set_params(overrides)
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

    last_hour = None
    stop_exits = 0

    if stop_loss_pct is not None:
        # Interleave snapshots to check stop-losses frequently
        snapshot_times = [(s.timestamp, s.prices) for s in replayer.loader._price_snapshots]
        event_times = [(e.trade.timestamp, e) for e in replayer.loader.events]

        timeline = []
        for ts, prices in snapshot_times:
            timeline.append(("snapshot", ts, prices))
        for ts, event in event_times:
            timeline.append(("event", ts, event))
        timeline.sort(key=lambda x: x[1])

        running_prices = {}
        for item_type, ts, data in timeline:
            if item_type == "snapshot":
                for tid, ps in data.items():
                    running_prices[tid] = ps
                # Check stop-losses on every snapshot
                stop_exits += check_stop_losses(strategy, running_prices, stop_loss_pct)
            elif item_type == "event":
                event = data
                all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
                running_prices.update(all_prices)
                event.context['all_prices'] = all_prices
                last_hour = event.trade.timestamp.hour
                decision = strategy.on_event(event)
                if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                    strategy.on_fill(event, decision)
    else:
        for event in replayer.loader.events:
            all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
            event.context['all_prices'] = all_prices
            last_hour = event.trade.timestamp.hour
            decision = strategy.on_event(event)
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)

    # Resolution with end-of-hour prices
    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            last_bid = ps.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    pnl = float(strategy.cash) - 100.0
    return {"pnl": round(pnl, 2), "stop_exits": stop_exits}


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


def discover_hours():
    base = Path("data/sessions")
    train_hours = []
    test_hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                if session in TRAIN_SESSIONS:
                    train_hours.append((session, utc_h, hf))
                elif session in TEST_SESSIONS:
                    test_hours.append((session, utc_h, hf))
    return train_hours, test_hours


BEST = {
    "boost": Decimal("8"),
    "min_pct": Decimal("2.0"),
    "skip_low": Decimal("0.25"),
    "skip_high": Decimal("0.97"),
    "mkt_cap": Decimal("50"),
}

# (overrides, stop_loss_pct, name)
VARIANTS = [
    # Baseline
    ({**BEST}, None, "CURRENT BEST (low=0.25)"),

    # Raise skip_low
    ({**BEST, "skip_low": Decimal("0.35")}, None, "skip_low=0.35"),
    ({**BEST, "skip_low": Decimal("0.45")}, None, "skip_low=0.45"),
    ({**BEST, "skip_low": Decimal("0.50")}, None, "skip_low=0.50"),

    # Stop-losses (on snapshots) with current skip
    ({**BEST}, Decimal("30"), "SL=30% (current skip)"),
    ({**BEST}, Decimal("40"), "SL=40% (current skip)"),
    ({**BEST}, Decimal("50"), "SL=50% (current skip)"),

    # Higher skip + stop-loss combos
    ({**BEST, "skip_low": Decimal("0.35")}, Decimal("30"), "low=0.35+SL=30%"),
    ({**BEST, "skip_low": Decimal("0.35")}, Decimal("40"), "low=0.35+SL=40%"),
    ({**BEST, "skip_low": Decimal("0.45")}, Decimal("30"), "low=0.45+SL=30%"),
    ({**BEST, "skip_low": Decimal("0.45")}, Decimal("40"), "low=0.45+SL=40%"),

    # More aggressive: higher skip + bigger min trades
    ({**BEST, "skip_low": Decimal("0.45"), "min_pct": Decimal("2.5")}, None, "low=0.45+pct=2.5"),
    ({**BEST, "skip_low": Decimal("0.45"), "min_pct": Decimal("3.0")}, None, "low=0.45+pct=3.0"),

    # Higher boost with higher skip (compensate for fewer trades)
    ({**BEST, "skip_low": Decimal("0.45"), "boost": Decimal("10")}, None, "low=0.45+boost=10"),
    ({**BEST, "skip_low": Decimal("0.45"), "boost": Decimal("12")}, None, "low=0.45+boost=12"),
]


def compute_stats(pnl_list):
    if not pnl_list:
        return 0, 0, 0, 0, 0, 0, 0
    total = sum(pnl_list)
    avg = total / len(pnl_list)
    wins = sum(1 for p in pnl_list if p > 0)
    losses = sum(1 for p in pnl_list if p < 0)
    wr = wins / max(1, wins + losses) * 100
    std = statistics.stdev(pnl_list) if len(pnl_list) > 1 else 0
    sharpe = avg / std if std > 0 else 0
    return total, avg, wr, wins, losses, std, sharpe


def main():
    train_hours, test_hours = discover_hours()
    print(f"TRAIN: {len(train_hours)} hours | TEST: {len(test_hours)} hours | Variants: {len(VARIANTS)}\n")

    all_results = {}
    for _, _, vname in VARIANTS:
        all_results[vname] = {"train": [], "test": [], "stop_exits": 0}

    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for overrides, sl_pct, vname in VARIANTS:
            r = run_hour(hf, overrides, stop_loss_pct=sl_pct)
            if r:
                all_results[vname]["train"].append(r["pnl"])
                all_results[vname]["stop_exits"] += r.get("stop_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)}...")

    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for overrides, sl_pct, vname in VARIANTS:
            r = run_hour(hf, overrides, stop_loss_pct=sl_pct)
            if r:
                all_results[vname]["test"].append(r["pnl"])
                all_results[vname]["stop_exits"] += r.get("stop_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)}...")

    print(f"\n{'='*140}")
    print(f"  IMPROVEMENT IDEAS -- sorted by Combined Sharpe")
    print(f"{'='*140}")
    print(f"\n  {'Variant':<28} | {'TR $':>6} {'TRShp':>6} {'TRWR':>5} | {'TE $':>6} {'TEShp':>6} {'TEWR':>5} | {'COMB $':>7} {'CShp':>6} | SL-Exits")
    print("-" * 140)

    rows = []
    for _, _, vname in VARIANTS:
        data = all_results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(data["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(data["test"])
        c_all = data["train"] + data["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        se = data["stop_exits"]
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, se))

    rows.sort(key=lambda x: x[0], reverse=True)
    best = rows[0][1] if rows else ""
    for c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, se in rows:
        consistent = te_shp > 0 and tr_shp > 0
        marker = " <-- BEST" if vname == best else (" ***" if consistent and te_shp > 0.05 else "")
        se_str = f"{se:>5}" if se > 0 else "    -"
        print(f"  {vname:<28} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>4.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>4.0f}% | ${c_tot:>+5.0f} {c_shp:>+5.3f} | {se_str}{marker}")


if __name__ == "__main__":
    main()
