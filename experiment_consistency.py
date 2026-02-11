"""Optimize for CONSISTENCY at $50 capital.

Goal: Prove $50 can make money reliably with minimal drawdowns.

Metrics:
- Sharpe ratio (higher = more consistent)
- Max hourly loss (lower = safer)
- Win rate (% of profitable hours)
- Max consecutive losing hours
- Tail risk: worst 5 hours
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
                late_min, late_mult, skip_high=0.97):
    """Run strategy variant."""
    orig = {}
    for attr in ["SCALE_BOOST", "SKIP_PRICE_LOW", "SKIP_PRICE_HIGH", "PER_MARKET_CAP_PCT",
                 "DRAWDOWN_REDUCE_THRESHOLD", "DRAWDOWN_STOP_THRESHOLD",
                 "LATE_ENTRY_BOOST_MIN", "LATE_ENTRY_BOOST_MULT"]:
        orig[attr] = getattr(pt_mod, attr)

    pt_mod.SCALE_BOOST = Decimal(str(boost))
    pt_mod.SKIP_PRICE_LOW = Decimal(str(skip_low))
    pt_mod.SKIP_PRICE_HIGH = Decimal(str(skip_high))
    pt_mod.PER_MARKET_CAP_PCT = Decimal(str(mkt_cap))
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal(str(dd_reduce))
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal(str(dd_stop))
    pt_mod.LATE_ENTRY_BOOST_MIN = late_min
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal(str(late_mult))

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
            setattr(pt_mod, k, v)
        return None
    if count == 0:
        for k, v in orig.items():
            setattr(pt_mod, k, v)
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

    for k, v in orig.items():
        setattr(pt_mod, k, v)

    return round(pnl, 2)


def analyze_consistency(hourly_pnls):
    """Compute consistency metrics."""
    if not hourly_pnls:
        return {}

    n = len(hourly_pnls)
    mean = sum(hourly_pnls) / n
    var = sum((x - mean) ** 2 for x in hourly_pnls) / max(1, n - 1)
    std = math.sqrt(var) if var > 0 else 0.001
    sharpe = mean / std

    sorted_pnl = sorted(hourly_pnls)
    winning = sum(1 for x in hourly_pnls if x > 0)
    losing = sum(1 for x in hourly_pnls if x < 0)
    zero = n - winning - losing

    # Max consecutive losses
    max_consec_loss = 0
    current_streak = 0
    for p in hourly_pnls:
        if p < 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    # Running PnL for max drawdown
    cum_pnl = 0
    peak_pnl = 0
    max_dd = 0
    for p in hourly_pnls:
        cum_pnl += p
        peak_pnl = max(peak_pnl, cum_pnl)
        dd = peak_pnl - cum_pnl
        max_dd = max(max_dd, dd)

    return {
        "sharpe": round(sharpe, 4),
        "total": round(sum(hourly_pnls), 2),
        "mean": round(mean, 2),
        "std": round(std, 2),
        "win_rate": round(winning / n * 100, 1) if n > 0 else 0,
        "max_loss": round(sorted_pnl[0], 2) if sorted_pnl else 0,
        "max_win": round(sorted_pnl[-1], 2) if sorted_pnl else 0,
        "worst_5": [round(x, 2) for x in sorted_pnl[:5]],
        "best_5": [round(x, 2) for x in sorted_pnl[-5:]],
        "max_consec_loss": max_consec_loss,
        "max_drawdown": round(max_dd, 2),
        "n_winning": winning,
        "n_losing": losing,
        "n_zero": zero,
    }


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

    print(f"Consistency optimization across {len(all_hours)} hours...\n")

    # (name, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s, late_min, late_mult, skip_high)
    variants = [
        # === BASELINE ===
        ("baseline", 50, 45, 8, 0.45, 50, 10, 20, 40, 2, 0.97),

        # === TIGHTER DRAWDOWN (catch losses earlier) ===
        ("dd_5/10", 50, 45, 8, 0.45, 50, 5, 10, 40, 2, 0.97),
        ("dd_7/14", 50, 45, 8, 0.45, 50, 7, 14, 40, 2, 0.97),
        ("dd_8/15", 50, 45, 8, 0.45, 50, 8, 15, 40, 2, 0.97),
        ("dd_5/15", 50, 45, 8, 0.45, 50, 5, 15, 40, 2, 0.97),

        # === LOWER BOOST (smaller positions = less variance) ===
        ("boost_4", 50, 45, 4, 0.45, 50, 10, 20, 40, 2, 0.97),
        ("boost_5", 50, 45, 5, 0.45, 50, 10, 20, 40, 2, 0.97),
        ("boost_6", 50, 45, 6, 0.45, 50, 10, 20, 40, 2, 0.97),

        # === HIGHER SKIP_LOW (only enter high-probability) ===
        ("skip_50", 50, 45, 8, 0.50, 50, 10, 20, 40, 2, 0.97),
        ("skip_55", 50, 45, 8, 0.55, 50, 10, 20, 40, 2, 0.97),
        ("skip_60", 50, 45, 8, 0.60, 50, 10, 20, 40, 2, 0.97),

        # === SKIP HIGH TOO (avoid extreme both sides) ===
        ("skip_hi90", 50, 45, 8, 0.45, 50, 10, 20, 40, 2, 0.90),
        ("skip_hi85", 50, 45, 8, 0.45, 50, 10, 20, 40, 2, 0.85),

        # === LOWER MKT CAP (spread risk) ===
        ("mkt_30%", 50, 45, 8, 0.45, 30, 10, 20, 40, 2, 0.97),
        ("mkt_40%", 50, 45, 8, 0.45, 40, 10, 20, 40, 2, 0.97),

        # === NO LATE BOOST (less risk on late entries) ===
        ("no_late", 50, 45, 8, 0.45, 50, 10, 20, 60, 1, 0.97),
        ("late_1.5x", 50, 45, 8, 0.45, 50, 10, 20, 40, 1.5, 0.97),

        # === COMBINED CONSISTENCY CONFIGS ===
        ("safe_v1", 50, 45, 6, 0.50, 40, 7, 14, 40, 2, 0.97),
        ("safe_v2", 50, 45, 6, 0.45, 40, 8, 15, 40, 1.5, 0.97),
        ("safe_v3", 50, 45, 5, 0.50, 40, 5, 10, 40, 2, 0.97),
        ("safe_v4", 50, 45, 7, 0.45, 45, 7, 14, 40, 2, 0.97),
        ("safe_v5", 50, 45, 8, 0.50, 40, 7, 14, 40, 2, 0.97),
        ("safe_v6", 50, 45, 8, 0.45, 40, 5, 10, 40, 2, 0.97),
        ("safe_v7", 50, 45, 6, 0.50, 50, 5, 10, 40, 2, 0.97),
        ("ultra_safe", 50, 45, 4, 0.55, 35, 5, 10, 40, 2, 0.90),
    ]

    results = []
    for i, (name, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s, late_min, late_mult, skip_hi) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}

        for session, utc_h, hf, split in all_hours:
            pnl = run_variant(hf, cap, budget, boost, skip_low, mkt_cap, dd_r, dd_s,
                              late_min, late_mult, skip_hi)
            if pnl is not None:
                split_pnl[split].append(pnl)

        all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        metrics = analyze_consistency(all_pnls)
        train_sum = sum(split_pnl["TRAIN"])
        test_sum = sum(split_pnl["TEST"])
        hold_sum = sum(split_pnl["HOLDOUT"])
        robust = train_sum > 0 and test_sum > 0 and hold_sum > 0

        results.append({
            "name": name, "train": train_sum, "test": test_sum, "holdout": hold_sum,
            "robust": robust, "metrics": metrics,
        })

        rob = "YES" if robust else "no"
        print(f"  [{i+1:>2}/{len(variants)}] {name:<16} "
              f"Sharpe={metrics.get('sharpe',0):+.3f} "
              f"WR={metrics.get('win_rate',0):>4.0f}% "
              f"MaxLoss=${metrics.get('max_loss',0):>+6.2f} "
              f"MaxDD=${metrics.get('max_drawdown',0):>5.2f} "
              f"PnL=${metrics.get('total',0):>+7.2f} [{rob}]")

    # Sort by Sharpe (robust only)
    print(f"\n{'='*140}")
    print(f"  CONSISTENCY RANKING (robust variants, sorted by Sharpe)")
    print(f"{'='*140}")
    print(f"  {'#':>2} {'Variant':<18} | {'Sharpe':>7} {'WR%':>5} | {'Total$':>8} {'$/hr':>6} | {'MaxLoss':>8} {'MaxDD':>7} {'ConsecL':>7} | {'Worst5':>40} | {'Rob':>3}")
    print(f"  {'-'*140}")

    robust_sorted = sorted([r for r in results if r["robust"]], key=lambda x: -x["metrics"]["sharpe"])
    for rank, r in enumerate(robust_sorted, 1):
        m = r["metrics"]
        worst5_str = ", ".join(f"${x:+.1f}" for x in m["worst_5"])
        per_hr = m["total"] / max(1, m["n_winning"] + m["n_losing"] + m["n_zero"])
        print(f"  {rank:>2} {r['name']:<18} | {m['sharpe']:>+6.3f} {m['win_rate']:>4.0f}% | ${m['total']:>+7.2f} ${per_hr:>+5.2f} | ${m['max_loss']:>+7.2f} ${m['max_drawdown']:>6.2f} {m['max_consec_loss']:>7} | {worst5_str:>40} | {'Y' if r['robust'] else 'n':>3}")

    # Also show non-robust for comparison
    non_robust = sorted([r for r in results if not r["robust"]], key=lambda x: -x["metrics"]["sharpe"])
    if non_robust:
        print(f"\n  NON-ROBUST (for reference):")
        for r in non_robust[:5]:
            m = r["metrics"]
            worst5_str = ", ".join(f"${x:+.1f}" for x in m["worst_5"])
            print(f"     {r['name']:<18} | {m['sharpe']:>+6.3f} {m['win_rate']:>4.0f}% | ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_drawdown']:>6.2f} | {worst5_str}")

    # Best of each metric
    print(f"\n  BEST-IN-CLASS (robust only):")
    if robust_sorted:
        best_sharpe = max(robust_sorted, key=lambda x: x["metrics"]["sharpe"])
        best_wr = max(robust_sorted, key=lambda x: x["metrics"]["win_rate"])
        least_loss = max(robust_sorted, key=lambda x: x["metrics"]["max_loss"])
        least_dd = min(robust_sorted, key=lambda x: x["metrics"]["max_drawdown"])
        print(f"    Best Sharpe:     {best_sharpe['name']:<18} Sharpe={best_sharpe['metrics']['sharpe']:+.3f}")
        print(f"    Best Win Rate:   {best_wr['name']:<18} WR={best_wr['metrics']['win_rate']:.0f}%")
        print(f"    Smallest Loss:   {least_loss['name']:<18} MaxLoss=${least_loss['metrics']['max_loss']:+.2f}")
        print(f"    Smallest DD:     {least_dd['name']:<18} MaxDD=${least_dd['metrics']['max_drawdown']:.2f}")


if __name__ == "__main__":
    main()
