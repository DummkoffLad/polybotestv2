"""Grid search capital x strategy parameters to find optimal deployment.

Key question: What's the best way to deploy more capital?
- More capital = more $ per trade (higher budget)
- But also: should we change boost, skip_low, mkt_cap with more capital?
- And: what about time-of-day filtering?
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
import src.strategies.profit_taker.strategy as pt_mod

# Session split
TRAIN_SESSIONS = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST_SESSIONS = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT_SESSIONS = ["2026-02-08/06-39"]


def get_split(session_name):
    for s in TRAIN_SESSIONS:
        if s in session_name:
            return "TRAIN"
    for s in TEST_SESSIONS:
        if s in session_name:
            return "TEST"
    for s in HOLDOUT_SESSIONS:
        if s in session_name:
            return "HOLDOUT"
    return "UNKNOWN"


def get_utc_hour_from_file(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts:
                        return int(ts.split("T")[1][:2])
            except Exception:
                continue
    return -1


def run_variant(hour_file, capital, budget, boost, skip_low, mkt_cap, dd_reduce, dd_stop,
                late_boost_min, late_boost_mult):
    """Run strategy with variant parameters and return PnL."""
    # Patch module-level constants
    orig = {
        "boost": pt_mod.SCALE_BOOST,
        "skip_low": pt_mod.SKIP_PRICE_LOW,
        "mkt_cap": pt_mod.PER_MARKET_CAP_PCT,
        "dd_reduce": pt_mod.DRAWDOWN_REDUCE_THRESHOLD,
        "dd_stop": pt_mod.DRAWDOWN_STOP_THRESHOLD,
        "late_min": pt_mod.LATE_ENTRY_BOOST_MIN,
        "late_mult": pt_mod.LATE_ENTRY_BOOST_MULT,
    }
    pt_mod.SCALE_BOOST = Decimal(str(boost))
    pt_mod.SKIP_PRICE_LOW = Decimal(str(skip_low))
    pt_mod.PER_MARKET_CAP_PCT = Decimal(str(mkt_cap))
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal(str(dd_reduce))
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal(str(dd_stop))
    pt_mod.LATE_ENTRY_BOOST_MIN = late_boost_min
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal(str(late_boost_mult))

    config_overrides = {
        "scaling.our_capital": capital,
        "scaling.hourly_budget": budget,
        "scaling.k_factor": 1,
        "scaling.leader_estimated_capital": 900,
    }

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=config_overrides)
    try:
        count = replayer.load()
    except Exception:
        # Restore
        pt_mod.SCALE_BOOST = orig["boost"]
        pt_mod.SKIP_PRICE_LOW = orig["skip_low"]
        pt_mod.PER_MARKET_CAP_PCT = orig["mkt_cap"]
        pt_mod.DRAWDOWN_REDUCE_THRESHOLD = orig["dd_reduce"]
        pt_mod.DRAWDOWN_STOP_THRESHOLD = orig["dd_stop"]
        pt_mod.LATE_ENTRY_BOOST_MIN = orig["late_min"]
        pt_mod.LATE_ENTRY_BOOST_MULT = orig["late_mult"]
        return None
    if count == 0:
        pt_mod.SCALE_BOOST = orig["boost"]
        pt_mod.SKIP_PRICE_LOW = orig["skip_low"]
        pt_mod.PER_MARKET_CAP_PCT = orig["mkt_cap"]
        pt_mod.DRAWDOWN_REDUCE_THRESHOLD = orig["dd_reduce"]
        pt_mod.DRAWDOWN_STOP_THRESHOLD = orig["dd_stop"]
        pt_mod.LATE_ENTRY_BOOST_MIN = orig["late_min"]
        pt_mod.LATE_ENTRY_BOOST_MULT = orig["late_mult"]
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # Resolve
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

    pnl = float(strategy.cash) - float(Decimal(str(capital)) * Decimal("2"))

    # Restore
    pt_mod.SCALE_BOOST = orig["boost"]
    pt_mod.SKIP_PRICE_LOW = orig["skip_low"]
    pt_mod.PER_MARKET_CAP_PCT = orig["mkt_cap"]
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = orig["dd_reduce"]
    pt_mod.DRAWDOWN_STOP_THRESHOLD = orig["dd_stop"]
    pt_mod.LATE_ENTRY_BOOST_MIN = orig["late_min"]
    pt_mod.LATE_ENTRY_BOOST_MULT = orig["late_mult"]

    return round(pnl, 2)


def main():
    base = Path("data/sessions")
    all_hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                all_hours.append((session, utc_h, hf, get_split(session)))

    print(f"Grid searching across {len(all_hours)} hours...\n")

    # Define variants as (name, capital, budget, boost, skip_low, mkt_cap,
    #                      dd_reduce, dd_stop, late_min, late_mult)
    # Current baseline: cap=50, budget=45, boost=8, skip=0.45, mkt=50, dd=10/20, late=40/2x
    variants = [
        # === BASELINE ===
        ("BASE_$50", 50, 45, 8, 0.45, 50, 10, 20, 40, 2),

        # === CAPITAL SCALING (same strategy) ===
        ("CAP_$75", 75, 67, 8, 0.45, 50, 10, 20, 40, 2),
        ("CAP_$100", 100, 90, 8, 0.45, 50, 10, 20, 40, 2),
        ("CAP_$150", 150, 135, 8, 0.45, 50, 10, 20, 40, 2),
        ("CAP_$200", 200, 180, 8, 0.45, 50, 10, 20, 40, 2),
        ("CAP_$300", 300, 270, 8, 0.45, 50, 10, 20, 40, 2),
        ("CAP_$500", 500, 450, 8, 0.45, 50, 10, 20, 40, 2),

        # === CAPITAL + ADJUSTED BOOST (more cap needs less boost?) ===
        ("$100_b6", 100, 90, 6, 0.45, 50, 10, 20, 40, 2),
        ("$100_b10", 100, 90, 10, 0.45, 50, 10, 20, 40, 2),
        ("$100_b12", 100, 90, 12, 0.45, 50, 10, 20, 40, 2),
        ("$200_b4", 200, 180, 4, 0.45, 50, 10, 20, 40, 2),
        ("$200_b6", 200, 180, 6, 0.45, 50, 10, 20, 40, 2),
        ("$200_b10", 200, 180, 10, 0.45, 50, 10, 20, 40, 2),
        ("$200_b12", 200, 180, 12, 0.45, 50, 10, 20, 40, 2),

        # === CAPITAL + SKIP_LOW ===
        ("$100_skip35", 100, 90, 8, 0.35, 50, 10, 20, 40, 2),
        ("$100_skip40", 100, 90, 8, 0.40, 50, 10, 20, 40, 2),
        ("$100_skip50", 100, 90, 8, 0.50, 50, 10, 20, 40, 2),
        ("$200_skip35", 200, 180, 8, 0.35, 50, 10, 20, 40, 2),
        ("$200_skip40", 200, 180, 8, 0.40, 50, 10, 20, 40, 2),
        ("$200_skip50", 200, 180, 8, 0.50, 50, 10, 20, 40, 2),

        # === CAPITAL + MKT_CAP ===
        ("$100_mkt60", 100, 90, 8, 0.45, 60, 10, 20, 40, 2),
        ("$100_mkt70", 100, 90, 8, 0.45, 70, 10, 20, 40, 2),
        ("$200_mkt60", 200, 180, 8, 0.45, 60, 10, 20, 40, 2),
        ("$200_mkt70", 200, 180, 8, 0.45, 70, 10, 20, 40, 2),

        # === CAPITAL + DRAWDOWN (scale DD with capital?) ===
        ("$100_dd15/30", 100, 90, 8, 0.45, 50, 15, 30, 40, 2),
        ("$100_dd20/40", 100, 90, 8, 0.45, 50, 20, 40, 40, 2),
        ("$200_dd20/40", 200, 180, 8, 0.45, 50, 20, 40, 40, 2),
        ("$200_dd30/60", 200, 180, 8, 0.45, 50, 30, 60, 40, 2),

        # === CAPITAL + LATE BOOST ===
        ("$100_late3x", 100, 90, 8, 0.45, 50, 10, 20, 40, 3),
        ("$100_noLate", 100, 90, 8, 0.45, 50, 10, 20, 60, 1),
        ("$200_late3x", 200, 180, 8, 0.45, 50, 10, 20, 40, 3),
        ("$200_noLate", 200, 180, 8, 0.45, 50, 10, 20, 60, 1),

        # === COMBINED BEST IDEAS ===
        ("$100_best_v1", 100, 90, 8, 0.35, 60, 15, 30, 40, 2),
        ("$100_best_v2", 100, 90, 10, 0.40, 60, 10, 20, 40, 2),
        ("$200_best_v1", 200, 180, 8, 0.35, 60, 20, 40, 40, 2),
        ("$200_best_v2", 200, 180, 6, 0.45, 60, 20, 40, 40, 2),
        ("$200_best_v3", 200, 180, 8, 0.40, 60, 15, 30, 40, 3),
    ]

    results = []
    for i, (name, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s, late_min, late_mult) in enumerate(variants):
        split_pnl = {"TRAIN": 0.0, "TEST": 0.0, "HOLDOUT": 0.0}
        split_hours = {"TRAIN": 0, "TEST": 0, "HOLDOUT": 0}

        for session, utc_h, hf, split in all_hours:
            pnl = run_variant(hf, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s,
                              late_min, late_mult)
            if pnl is not None:
                split_pnl[split] += pnl
                split_hours[split] += 1

        combined = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        total_hrs = split_hours["TRAIN"] + split_hours["TEST"] + split_hours["HOLDOUT"]
        robust = split_pnl["TRAIN"] > 0 and split_pnl["TEST"] > 0 and split_pnl["HOLDOUT"] > 0

        results.append({
            "name": name, "train": split_pnl["TRAIN"], "test": split_pnl["TEST"],
            "holdout": split_pnl["HOLDOUT"], "combined": combined, "hours": total_hrs,
            "robust": robust, "capital": cap,
        })

        per_hr = combined / max(1, total_hrs)
        rob = "YES" if robust else "no"
        print(f"  [{i+1:>2}/{len(variants)}] {name:<20} Train=${split_pnl['TRAIN']:>+8.2f} Test=${split_pnl['TEST']:>+8.2f} Hold=${split_pnl['HOLDOUT']:>+8.2f} => ${combined:>+9.2f} ({per_hr:>+5.2f}/hr) [{rob}]")

    # Sort and print final ranking
    print(f"\n{'='*130}")
    print(f"  FINAL RANKING — sorted by combined PnL (robust only)")
    print(f"{'='*130}")
    print(f"  {'#':>2} {'Variant':<22} | {'Train$':>9} {'Test$':>9} {'Hold$':>9} | {'Combined$':>10} {'$/hr':>7} {'ROI%':>7} | {'Robust':>6}")
    print(f"  {'-'*105}")

    for rank, r in enumerate(sorted(results, key=lambda x: -x["combined"]), 1):
        rob = "YES" if r["robust"] else "no"
        per_hr = r["combined"] / max(1, r["hours"])
        roi = r["combined"] / r["capital"] * 100 if r["capital"] > 0 else 0
        print(f"  {rank:>2} {r['name']:<22} | ${r['train']:>+8.2f} ${r['test']:>+8.2f} ${r['holdout']:>+8.2f} | ${r['combined']:>+9.2f} ${per_hr:>+6.2f} {roi:>+6.1f}% | {rob:>6}")

    # ROI ranking (return on capital)
    print(f"\n{'='*130}")
    print(f"  ROI RANKING — sorted by return on capital (robust only)")
    print(f"{'='*130}")
    robust_results = [r for r in results if r["robust"]]
    print(f"  {'#':>2} {'Variant':<22} | {'Capital':>7} {'Combined$':>10} {'ROI%':>7} {'$/hr':>7}")
    print(f"  {'-'*70}")
    for rank, r in enumerate(sorted(robust_results, key=lambda x: -x["combined"]/max(1,x["capital"])), 1):
        per_hr = r["combined"] / max(1, r["hours"])
        roi = r["combined"] / r["capital"] * 100 if r["capital"] > 0 else 0
        print(f"  {rank:>2} {r['name']:<22} | ${r['capital']:>6} ${r['combined']:>+9.2f} {roi:>+6.1f}% ${per_hr:>+6.2f}")


if __name__ == "__main__":
    main()
