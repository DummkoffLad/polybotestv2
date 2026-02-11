"""Grid search cherry-picking filters on TRAIN/TEST split.

Filters to test based on analyze_winners.py findings:
1. SKIP_PRICE_LOW: 0.03 (current), 0.15, 0.20, 0.25, 0.30
2. Leader nth buy: skip 1st buys only? Require 2nd+ buy?
3. Leader trade size minimum (absolute $): require bigger trades

TRAIN: 2026-02-03/05-56, 2026-02-04/02-35, 2026-02-06/05-30
TEST:  2026-02-05/03-58, 2026-02-05/22-15, 2026-02-07/05-52
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


def run_hour(hour_file, overrides):
    # Set module-level constants
    pt_module.SKIP_PRICE_LOW = overrides.get("skip_low", Decimal("0.03"))
    pt_module.SKIP_PRICE_HIGH = overrides.get("skip_high", Decimal("0.97"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("1.2"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = overrides.get("dd_reduce", Decimal("15"))
    pt_module.DRAWDOWN_STOP_THRESHOLD = overrides.get("dd_stop", Decimal("25"))
    pt_module.LATE_HOUR_REDUCE_MIN = 59  # Disabled
    pt_module.LATE_HOUR_STOP_MIN = 60    # Disabled
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return {"pnl": 0, "buys": 0, "sells": 0}
    if count == 0:
        return {"pnl": 0, "buys": 0, "sells": 0}

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.scale_boost = overrides.get("boost", Decimal("8"))
    strategy.on_session_start()

    last_all_prices = {}
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    liquidate_at_resolution(strategy, last_all_prices)
    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial
    return {
        "pnl": round(pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "skips": dict(strategy.skip_reasons),
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


# ---- VARIANTS ----
# Baseline: current settings
BASE = {
    "boost": Decimal("8"),
    "min_pct": Decimal("1.2"),
    "dd_reduce": Decimal("15"),
    "dd_stop": Decimal("25"),
    "mkt_cap": Decimal("50"),
    "skip_high": Decimal("0.97"),
}

VARIANTS = {
    # Baseline
    "Baseline (low=0.03)": {**BASE, "skip_low": Decimal("0.03")},

    # SKIP_PRICE_LOW variants
    "skip_low=0.10": {**BASE, "skip_low": Decimal("0.10")},
    "skip_low=0.15": {**BASE, "skip_low": Decimal("0.15")},
    "skip_low=0.20": {**BASE, "skip_low": Decimal("0.20")},
    "skip_low=0.25": {**BASE, "skip_low": Decimal("0.25")},
    "skip_low=0.30": {**BASE, "skip_low": Decimal("0.30")},
    "skip_low=0.35": {**BASE, "skip_low": Decimal("0.35")},

    # Also test skip_high (high-price trades have high WR but tiny profit)
    "low=0.25+high=0.90": {**BASE, "skip_low": Decimal("0.25"), "skip_high": Decimal("0.90")},
    "low=0.25+high=0.85": {**BASE, "skip_low": Decimal("0.25"), "skip_high": Decimal("0.85")},
    "low=0.30+high=0.90": {**BASE, "skip_low": Decimal("0.30"), "skip_high": Decimal("0.90")},

    # Higher min_pct (skip even more small trades) + price filter
    "low=0.25+pct=1.5": {**BASE, "skip_low": Decimal("0.25"), "min_pct": Decimal("1.5")},
    "low=0.25+pct=2.0": {**BASE, "skip_low": Decimal("0.25"), "min_pct": Decimal("2.0")},

    # More aggressive boost to compensate for filtering
    "low=0.25+boost=10": {**BASE, "skip_low": Decimal("0.25"), "boost": Decimal("10")},
    "low=0.25+boost=12": {**BASE, "skip_low": Decimal("0.25"), "boost": Decimal("12")},

    # Combination: filter more aggressively + boost harder
    "low=0.30+boost=10": {**BASE, "skip_low": Decimal("0.30"), "boost": Decimal("10")},
    "low=0.25+high=0.90+b10": {**BASE, "skip_low": Decimal("0.25"), "skip_high": Decimal("0.90"), "boost": Decimal("10")},
}


def print_results(name, hours, results_by_variant, session_results):
    print(f"\n{'='*130}")
    print(f"  {name} SET ({len(hours)} hours) -- sorted by Sharpe")
    print(f"{'='*130}")
    print(f"\n  {'Variant':<26} | {'Total':>7} {'Avg':>6} {'WR':>5} {'W':>3} {'L':>3} | {'Std':>6} {'Sharpe':>7} | {'MaxW':>6} {'MaxL':>7} | Buys")
    print("-" * 130)

    rows = []
    for vname, pnl_list in results_by_variant.items():
        total = sum(pnl_list)
        avg = total / len(pnl_list) if pnl_list else 0
        wins = sum(1 for p in pnl_list if p > 0)
        losses = sum(1 for p in pnl_list if p < 0)
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        std = statistics.stdev(pnl_list) if len(pnl_list) > 1 else 0
        sharpe = avg / std if std > 0 else 0
        maxw = max(pnl_list) if pnl_list else 0
        maxl = min(pnl_list) if pnl_list else 0
        rows.append((sharpe, vname, total, avg, wr, wins, losses, std, maxw, maxl))

    rows.sort(key=lambda x: x[0], reverse=True)
    best_name = rows[0][1] if rows else ""
    for sharpe, vname, total, avg, wr, wins, losses, std, maxw, maxl in rows:
        marker = " <-- BEST" if vname == best_name else ""
        print(f"  {vname:<26} | ${total:>+5.0f} ${avg:>+4.2f} {wr:>4.1f}% {wins:>3} {losses:>3} | ${std:>4.1f} {sharpe:>+6.3f} | ${maxw:>+4.0f} ${maxl:>+5.0f}{marker}")

    # Per-session for top 3
    top3 = [r[1] for r in rows[:3]]
    show = top3 + ["Baseline (low=0.03)"] if "Baseline (low=0.03)" not in top3 else top3
    sessions = sorted(set(s for s, _, _ in hours))

    print(f"\n  Per-session breakdown (top 3 + baseline):")
    header = f"  {'Session':<26}"
    for vname in show:
        short = vname[:16]
        header += f" | {short:>16}"
    print(header)
    print("-" * (30 + 19 * len(show)))

    for session in sessions:
        row = f"  {session:<26}"
        for vname in show:
            pnl_list = session_results[vname].get(session, [])
            s_total = sum(pnl_list)
            s_wins = sum(1 for p in pnl_list if p > 0)
            s_losses = sum(1 for p in pnl_list if p < 0)
            row += f" | ${s_total:>+6.0f} {s_wins}W/{s_losses}L"
        print(row)


def main():
    train_hours, test_hours = discover_hours()
    print(f"TRAIN: {len(train_hours)} hours from {len(TRAIN_SESSIONS)} sessions")
    print(f"TEST:  {len(test_hours)} hours from {len(TEST_SESSIONS)} sessions")
    print(f"Testing {len(VARIANTS)} variants\n")

    # Run on TRAIN
    train_results = {name: [] for name in VARIANTS}
    train_session_results = {name: defaultdict(list) for name in VARIANTS}

    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            train_results[vname].append(r["pnl"])
            train_session_results[vname][session].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)} hours done...")

    print_results("TRAIN", train_hours, train_results, train_session_results)

    # Run on TEST
    test_results = {name: [] for name in VARIANTS}
    test_session_results = {name: defaultdict(list) for name in VARIANTS}

    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            test_results[vname].append(r["pnl"])
            test_session_results[vname][session].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)} hours done...")

    print_results("TEST", test_hours, test_results, test_session_results)

    # Summary: which variants are good on BOTH?
    print(f"\n{'='*130}")
    print(f"  TRAIN vs TEST COMPARISON -- sorted by TEST Sharpe")
    print(f"{'='*130}")
    print(f"\n  {'Variant':<26} | {'TRAIN Total':>11} {'TRAIN Sharpe':>12} | {'TEST Total':>10} {'TEST Sharpe':>11} | {'Consistent':>10}")
    print("-" * 100)

    compare = []
    for vname in VARIANTS:
        t_pnl = train_results[vname]
        t_total = sum(t_pnl)
        t_avg = t_total / len(t_pnl) if t_pnl else 0
        t_std = statistics.stdev(t_pnl) if len(t_pnl) > 1 else 0
        t_sharpe = t_avg / t_std if t_std > 0 else 0

        te_pnl = test_results[vname]
        te_total = sum(te_pnl)
        te_avg = te_total / len(te_pnl) if te_pnl else 0
        te_std = statistics.stdev(te_pnl) if len(te_pnl) > 1 else 0
        te_sharpe = te_avg / te_std if te_std > 0 else 0

        consistent = "YES" if t_sharpe > 0 and te_sharpe > 0 else "no"
        compare.append((te_sharpe, vname, t_total, t_sharpe, te_total, te_sharpe, consistent))

    compare.sort(key=lambda x: x[0], reverse=True)
    for te_sharpe, vname, t_total, t_sharpe, te_total, te_sharpe2, consistent in compare:
        marker = " ***" if consistent == "YES" and te_sharpe > 0.03 else ""
        print(f"  {vname:<26} | ${t_total:>+9.2f} {t_sharpe:>+10.3f} | ${te_total:>+8.2f} {te_sharpe2:>+9.3f} | {consistent:>10}{marker}")


if __name__ == "__main__":
    main()
