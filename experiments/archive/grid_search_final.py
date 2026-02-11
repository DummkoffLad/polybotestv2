"""Final grid search: squeeze every dollar from $50 and find optimal $200 config.

Focus areas:
1. $50 combos that haven't been tested
2. $200 fine-tuning (boost 10-14 x dd x late)
3. Sharpe ratio calculation for risk-adjusted comparison
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
import src.strategies.profit_taker.strategy as pt_mod

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
                late_min, late_mult, side_pct=55, global_pct=100):
    """Run strategy variant and return PnL."""
    orig = {
        "boost": pt_mod.SCALE_BOOST, "skip_low": pt_mod.SKIP_PRICE_LOW,
        "mkt_cap": pt_mod.PER_MARKET_CAP_PCT, "dd_reduce": pt_mod.DRAWDOWN_REDUCE_THRESHOLD,
        "dd_stop": pt_mod.DRAWDOWN_STOP_THRESHOLD, "late_min": pt_mod.LATE_ENTRY_BOOST_MIN,
        "late_mult": pt_mod.LATE_ENTRY_BOOST_MULT, "side_pct": pt_mod.PER_SIDE_PCT,
        "global_pct": pt_mod.GLOBAL_EXPOSURE_PCT,
    }
    pt_mod.SCALE_BOOST = Decimal(str(boost))
    pt_mod.SKIP_PRICE_LOW = Decimal(str(skip_low))
    pt_mod.PER_MARKET_CAP_PCT = Decimal(str(mkt_cap))
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal(str(dd_reduce))
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal(str(dd_stop))
    pt_mod.LATE_ENTRY_BOOST_MIN = late_min
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal(str(late_mult))
    pt_mod.PER_SIDE_PCT = Decimal(str(side_pct))
    pt_mod.GLOBAL_EXPOSURE_PCT = Decimal(str(global_pct))

    config_overrides = {
        "scaling.our_capital": capital, "scaling.hourly_budget": budget,
        "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
    }

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=config_overrides)
    try:
        count = replayer.load()
    except Exception:
        for k, v in orig.items():
            setattr(pt_mod, {"boost": "SCALE_BOOST", "skip_low": "SKIP_PRICE_LOW",
                             "mkt_cap": "PER_MARKET_CAP_PCT", "dd_reduce": "DRAWDOWN_REDUCE_THRESHOLD",
                             "dd_stop": "DRAWDOWN_STOP_THRESHOLD", "late_min": "LATE_ENTRY_BOOST_MIN",
                             "late_mult": "LATE_ENTRY_BOOST_MULT", "side_pct": "PER_SIDE_PCT",
                             "global_pct": "GLOBAL_EXPOSURE_PCT"}[k], v)
        return None
    if count == 0:
        for k, v in orig.items():
            setattr(pt_mod, {"boost": "SCALE_BOOST", "skip_low": "SKIP_PRICE_LOW",
                             "mkt_cap": "PER_MARKET_CAP_PCT", "dd_reduce": "DRAWDOWN_REDUCE_THRESHOLD",
                             "dd_stop": "DRAWDOWN_STOP_THRESHOLD", "late_min": "LATE_ENTRY_BOOST_MIN",
                             "late_mult": "LATE_ENTRY_BOOST_MULT", "side_pct": "PER_SIDE_PCT",
                             "global_pct": "GLOBAL_EXPOSURE_PCT"}[k], v)
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

    # Restore all
    pt_mod.SCALE_BOOST = orig["boost"]
    pt_mod.SKIP_PRICE_LOW = orig["skip_low"]
    pt_mod.PER_MARKET_CAP_PCT = orig["mkt_cap"]
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = orig["dd_reduce"]
    pt_mod.DRAWDOWN_STOP_THRESHOLD = orig["dd_stop"]
    pt_mod.LATE_ENTRY_BOOST_MIN = orig["late_min"]
    pt_mod.LATE_ENTRY_BOOST_MULT = orig["late_mult"]
    pt_mod.PER_SIDE_PCT = orig["side_pct"]
    pt_mod.GLOBAL_EXPOSURE_PCT = orig["global_pct"]

    return round(pnl, 2)


def sharpe(hourly_pnls):
    """Calculate Sharpe ratio from hourly PnL list."""
    if len(hourly_pnls) < 2:
        return 0.0
    mean = sum(hourly_pnls) / len(hourly_pnls)
    var = sum((x - mean) ** 2 for x in hourly_pnls) / (len(hourly_pnls) - 1)
    std = math.sqrt(var) if var > 0 else 0.001
    return mean / std


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

    print(f"Final grid search across {len(all_hours)} hours...\n")

    # (name, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s, late_min, late_mult)
    variants = [
        # === $50 COMBOS (squeeze current capital) ===
        ("$50_base", 50, 45, 8, 0.45, 50, 10, 20, 40, 2),
        ("$50_skip35_dd15", 50, 45, 8, 0.35, 50, 15, 25, 40, 2),
        ("$50_skip35", 50, 45, 8, 0.35, 50, 10, 20, 40, 2),
        ("$50_b10_dd12", 50, 45, 10, 0.45, 50, 12, 22, 40, 2),
        ("$50_late3x", 50, 45, 8, 0.45, 50, 10, 20, 35, 3),
        ("$50_skip35_lat3", 50, 45, 8, 0.35, 50, 10, 20, 35, 3),
        ("$50_mkt60_dd12", 50, 45, 8, 0.45, 60, 12, 22, 40, 2),
        ("$50_b10_skip35", 50, 45, 10, 0.35, 50, 10, 20, 40, 2),

        # === $200 FINE TUNING (find the absolute best) ===
        ("$200_b12_base", 200, 180, 12, 0.45, 50, 10, 20, 40, 2),
        ("$200_b12_dd15/30", 200, 180, 12, 0.45, 50, 15, 30, 40, 2),
        ("$200_b12_dd20/40", 200, 180, 12, 0.45, 50, 20, 40, 40, 2),
        ("$200_b12_mkt60", 200, 180, 12, 0.45, 60, 10, 20, 40, 2),
        ("$200_b12_mkt70", 200, 180, 12, 0.45, 70, 10, 20, 40, 2),
        ("$200_b12_skip35", 200, 180, 12, 0.35, 50, 10, 20, 40, 2),
        ("$200_b12_late3x", 200, 180, 12, 0.45, 50, 10, 20, 40, 3),
        ("$200_b11", 200, 180, 11, 0.45, 50, 10, 20, 40, 2),
        ("$200_b13", 200, 180, 13, 0.45, 50, 10, 20, 40, 2),
        ("$200_b14", 200, 180, 14, 0.45, 50, 10, 20, 40, 2),
        ("$200_b12_dd15_m60", 200, 180, 12, 0.45, 60, 15, 30, 40, 2),
        ("$200_b12_late3_d15", 200, 180, 12, 0.45, 50, 15, 30, 40, 3),
        ("$200_b12_allIn", 200, 180, 12, 0.45, 70, 15, 30, 40, 3),
    ]

    results = []
    for i, (name, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s, late_min, late_mult) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}

        for session, utc_h, hf, split in all_hours:
            pnl = run_variant(hf, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s,
                              late_min, late_mult)
            if pnl is not None:
                split_pnl[split].append(pnl)

        train_sum = sum(split_pnl["TRAIN"])
        test_sum = sum(split_pnl["TEST"])
        hold_sum = sum(split_pnl["HOLDOUT"])
        combined = train_sum + test_sum + hold_sum
        all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        sr = sharpe(all_pnls)
        robust = train_sum > 0 and test_sum > 0 and hold_sum > 0
        total_hrs = len(all_pnls)

        results.append({
            "name": name, "train": train_sum, "test": test_sum, "holdout": hold_sum,
            "combined": combined, "sharpe": sr, "hours": total_hrs, "robust": robust,
            "capital": cap,
        })

        rob = "YES" if robust else "no"
        per_hr = combined / max(1, total_hrs)
        print(f"  [{i+1:>2}/{len(variants)}] {name:<22} Tr=${train_sum:>+8.2f} Te=${test_sum:>+8.2f} Ho=${hold_sum:>+8.2f} => ${combined:>+9.2f} Sharpe={sr:+.3f} [{rob}]")

    # Print rankings by capital level
    for cap_level in [50, 200]:
        cap_results = [r for r in results if r["capital"] == cap_level]
        print(f"\n{'='*120}")
        print(f"  RANKING: ${cap_level} CAPITAL (sorted by Sharpe, robust only)")
        print(f"{'='*120}")
        print(f"  {'#':>2} {'Variant':<24} | {'Train$':>9} {'Test$':>9} {'Hold$':>9} | {'Total$':>9} {'Sharpe':>7} {'$/hr':>6} | {'Robust':>6}")
        print(f"  {'-'*105}")

        robust_only = sorted([r for r in cap_results if r["robust"]], key=lambda x: -x["sharpe"])
        non_robust = sorted([r for r in cap_results if not r["robust"]], key=lambda x: -x["combined"])

        for rank, r in enumerate(robust_only + non_robust, 1):
            rob = "YES" if r["robust"] else "no"
            per_hr = r["combined"] / max(1, r["hours"])
            print(f"  {rank:>2} {r['name']:<24} | ${r['train']:>+8.2f} ${r['test']:>+8.2f} ${r['holdout']:>+8.2f} | ${r['combined']:>+8.2f} {r['sharpe']:>+6.3f} ${per_hr:>+5.2f} | {rob:>6}")


if __name__ == "__main__":
    main()
