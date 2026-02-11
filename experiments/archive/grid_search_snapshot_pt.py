"""Test profit-target exits checked on EVERY price snapshot (not just leader trades).

Current strategy only checks positions when a leader event arrives.
Price snapshots come every 2-5 seconds — 2-13x more often than leader trades.
Checking on every snapshot catches profit targets much faster.

Test: low-price buys (skip_low=0.03-0.10) with profit targets checked on snapshots.
"""
import sys
import json
import statistics
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

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


def liquidate_at_resolution(strategy, all_prices):
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        price_snap = all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars
        if token_id in strategy.our_entries:
            del strategy.our_entries[token_id]
        if token_id in strategy.high_water_marks:
            del strategy.high_water_marks[token_id]


def check_profit_targets_on_snapshot(strategy, all_prices, profit_target_pct):
    """Check all positions against current prices for profit target exit.

    This simulates checking on every price snapshot, not just leader trades.
    Returns list of (token_id, sell_price, pnl) for executed exits.
    """
    exits = []
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        entry_price = strategy.our_entries.get(token_id)
        if not entry_price or entry_price <= 0:
            continue

        price_snap = all_prices.get(token_id)
        if not price_snap or not price_snap.bid or price_snap.bid <= 0:
            continue

        current_bid = price_snap.bid

        # Only apply profit target to LOW entries (< 0.30)
        if entry_price >= Decimal("0.30"):
            continue

        # Check if profit target hit
        profit_pct = ((current_bid - entry_price) / entry_price) * 100
        if profit_pct >= profit_target_pct:
            # Sell at current bid
            sell_price = current_bid
            dollars = pos.shares * sell_price
            strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, sell_price)
            strategy.cash += dollars
            strategy.sells += 1
            if token_id in strategy.our_entries:
                del strategy.our_entries[token_id]
            if token_id in strategy.high_water_marks:
                del strategy.high_water_marks[token_id]
            # Credit back to hourly budget
            strategy.hourly_budget_used = max(Decimal("0"), strategy.hourly_budget_used - dollars)
            exits.append((token_id, float(sell_price), float(profit_pct)))

    return exits


def run_hour(hour_file, overrides, check_snapshots=False, snapshot_pt_pct=Decimal("30")):
    """Run one hour with optional snapshot-based profit target checking."""
    pt_module.SKIP_PRICE_LOW = overrides.get("skip_low", Decimal("0.25"))
    pt_module.SKIP_PRICE_HIGH = overrides.get("skip_high", Decimal("0.97"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("2.0"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = overrides.get("dd_reduce", Decimal("15"))
    pt_module.DRAWDOWN_STOP_THRESHOLD = overrides.get("dd_stop", Decimal("25"))
    pt_module.LATE_HOUR_REDUCE_MIN = 59
    pt_module.LATE_HOUR_STOP_MIN = 60
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))
    pt_module.PROFIT_TARGET_LOW = Decimal("100")   # Disable built-in (we do our own)
    pt_module.PROFIT_TARGET_MID = Decimal("100")
    pt_module.PROFIT_TARGET_HIGH = Decimal("50")

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return {"pnl": 0, "profit_exits": 0}
    if count == 0:
        return {"pnl": 0, "profit_exits": 0}

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.scale_boost = overrides.get("boost", Decimal("8"))
    strategy.on_session_start()

    profit_exits = 0
    last_hour = None

    if check_snapshots:
        # Interleave: process events AND check snapshots chronologically
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
                # Check profit targets on every snapshot
                exits = check_profit_targets_on_snapshot(strategy, running_prices, snapshot_pt_pct)
                profit_exits += len(exits)
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
        # Standard: only check on leader events (current behavior)
        for event in replayer.loader.events:
            all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
            event.context['all_prices'] = all_prices
            last_hour = event.trade.timestamp.hour
            decision = strategy.on_event(event)
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)

    # BOTH paths use end-of-hour prices for resolution (matches live runner)
    end_of_hour_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
    liquidate_at_resolution(strategy, end_of_hour_prices)
    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial
    return {"pnl": round(pnl, 2), "profit_exits": profit_exits}


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
    "dd_reduce": Decimal("15"),
    "dd_stop": Decimal("25"),
    "mkt_cap": Decimal("50"),
}

# Test matrix: (check_snapshots, snapshot_pt_pct, overrides, name)
VARIANTS = [
    # Baseline: current best (no low buys, no snapshot checks)
    (False, Decimal("0"), {**BEST}, "CURRENT BEST"),

    # Low buys + snapshot PT at different %
    (True, Decimal("10"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10+snapPT10%"),
    (True, Decimal("15"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10+snapPT15%"),
    (True, Decimal("20"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10+snapPT20%"),
    (True, Decimal("30"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10+snapPT30%"),
    (True, Decimal("50"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10+snapPT50%"),

    # Even lower (all prices)
    (True, Decimal("15"), {**BEST, "skip_low": Decimal("0.03")}, "low=0.03+snapPT15%"),
    (True, Decimal("20"), {**BEST, "skip_low": Decimal("0.03")}, "low=0.03+snapPT20%"),
    (True, Decimal("30"), {**BEST, "skip_low": Decimal("0.03")}, "low=0.03+snapPT30%"),

    # Compare: same low buys but WITHOUT snapshot checking (only leader-event PT)
    # This shows the VALUE of snapshot checking specifically
    (False, Decimal("0"), {**BEST, "skip_low": Decimal("0.10")}, "low=0.10 NO PT"),

    # Low buys with higher min_pct (conviction only) + snapshot PT
    (True, Decimal("20"), {**BEST, "skip_low": Decimal("0.10"), "min_pct": Decimal("3.0")}, "low=0.10+snapPT20+pct3"),
    (True, Decimal("30"), {**BEST, "skip_low": Decimal("0.10"), "min_pct": Decimal("3.0")}, "low=0.10+snapPT30+pct3"),

    # Apply snapshot PT to ALL entries (not just low)
    (True, Decimal("30"), {**BEST}, "snapPT30% (all entries)"),
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
    for _, _, _, vname in VARIANTS:
        all_results[vname] = {"train": [], "test": [], "profit_exits": 0}

    # TRAIN
    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for check_snaps, pt_pct, overrides, vname in VARIANTS:
            r = run_hour(hf, overrides, check_snapshots=check_snaps, snapshot_pt_pct=pt_pct)
            all_results[vname]["train"].append(r["pnl"])
            all_results[vname]["profit_exits"] += r.get("profit_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)}...")

    # TEST
    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for check_snaps, pt_pct, overrides, vname in VARIANTS:
            r = run_hour(hf, overrides, check_snapshots=check_snaps, snapshot_pt_pct=pt_pct)
            all_results[vname]["test"].append(r["pnl"])
            all_results[vname]["profit_exits"] += r.get("profit_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)}...")

    # Results
    print(f"\n{'='*150}")
    print(f"  SNAPSHOT PROFIT-TARGET TEST -- sorted by Combined Sharpe")
    print(f"{'='*150}")
    print(f"\n  {'Variant':<28} | {'TR $':>6} {'TRShp':>6} {'TRWR':>5} | {'TE $':>6} {'TEShp':>6} {'TEWR':>5} | {'COMB $':>7} {'CShp':>6} | ProfExits")
    print("-" * 150)

    rows = []
    for _, _, _, vname in VARIANTS:
        data = all_results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(data["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(data["test"])
        c_all = data["train"] + data["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        pe = data["profit_exits"]
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, pe))

    rows.sort(key=lambda x: x[0], reverse=True)
    best = rows[0][1] if rows else ""
    for c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, pe in rows:
        consistent = te_shp > 0 and tr_shp > 0
        marker = " <-- BEST" if vname == best else (" ***" if consistent and te_shp > 0.05 else "")
        print(f"  {vname:<28} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>4.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>4.0f}% | ${c_tot:>+5.0f} {c_shp:>+5.3f} | {pe:>5}{marker}")


if __name__ == "__main__":
    main()
