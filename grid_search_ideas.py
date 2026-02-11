"""Test two ideas on train/test split:

1. Low-price buys with profit-target exits (scalp the low entries instead of skipping)
2. Late-hour high-price buys (safer near resolution — outcome nearly decided)

Current best: skip_low=0.25, min_pct=2.0, all profit targets disabled
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
    # Set ALL module-level constants
    pt_module.SKIP_PRICE_LOW = overrides.get("skip_low", Decimal("0.25"))
    pt_module.SKIP_PRICE_HIGH = overrides.get("skip_high", Decimal("0.97"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("2.0"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = overrides.get("dd_reduce", Decimal("15"))
    pt_module.DRAWDOWN_STOP_THRESHOLD = overrides.get("dd_stop", Decimal("25"))
    pt_module.LATE_HOUR_REDUCE_MIN = overrides.get("late_reduce", 59)
    pt_module.LATE_HOUR_STOP_MIN = overrides.get("late_stop", 60)
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))

    # Profit targets — the key test
    pt_module.PROFIT_TARGET_LOW = overrides.get("pt_low", Decimal("100"))    # default: disabled
    pt_module.PROFIT_TARGET_MID = overrides.get("pt_mid", Decimal("100"))    # default: disabled
    pt_module.PROFIT_TARGET_HIGH = overrides.get("pt_high", Decimal("50"))   # default: 50%

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return {"pnl": 0, "buys": 0, "sells": 0, "profit_exits": 0}
    if count == 0:
        return {"pnl": 0, "buys": 0, "sells": 0, "profit_exits": 0}

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
        "profit_exits": strategy.profit_exits,
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


# Current best params as base
BEST = {
    "boost": Decimal("8"),
    "min_pct": Decimal("2.0"),
    "skip_low": Decimal("0.25"),
    "skip_high": Decimal("0.97"),
    "dd_reduce": Decimal("15"),
    "dd_stop": Decimal("25"),
    "mkt_cap": Decimal("50"),
    "late_reduce": 59,
    "late_stop": 60,
    "pt_low": Decimal("100"),    # disabled
    "pt_mid": Decimal("100"),    # disabled
    "pt_high": Decimal("50"),    # 50% (rarely fires)
}

VARIANTS = {
    # ---- BASELINE ----
    "CURRENT BEST": {**BEST},

    # ---- IDEA 1: Low buys + profit target ----
    # Allow buys down to 0.10, take profit at various %
    "low=0.10+PT20%": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("20")},
    "low=0.10+PT30%": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("30")},
    "low=0.10+PT50%": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("50")},

    # Allow buys down to 0.03 (all), take profit at various %
    "low=0.03+PT20%": {**BEST, "skip_low": Decimal("0.03"), "pt_low": Decimal("20")},
    "low=0.03+PT30%": {**BEST, "skip_low": Decimal("0.03"), "pt_low": Decimal("30")},
    "low=0.03+PT50%": {**BEST, "skip_low": Decimal("0.03"), "pt_low": Decimal("50")},

    # Also set profit targets for mid-range entries
    "low=0.10+PTall30%": {**BEST, "skip_low": Decimal("0.10"),
                          "pt_low": Decimal("30"), "pt_mid": Decimal("30")},
    "low=0.10+PTall50%": {**BEST, "skip_low": Decimal("0.10"),
                          "pt_low": Decimal("50"), "pt_mid": Decimal("50")},

    # Allow low buys but with higher min_pct (only large leader trades)
    "low=0.10+PT30+pct3": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("30"),
                           "min_pct": Decimal("3.0")},

    # ---- IDEA 2: Late-hour high-price buys ----
    # Allow buys up to 0.99 after minute 45 (outcome nearly decided)
    "high=0.99+late45": {**BEST, "skip_high": Decimal("0.99"), "late_reduce": 59, "late_stop": 60},
    "high=0.98": {**BEST, "skip_high": Decimal("0.98")},

    # Also try being more aggressive late-hour (lower min_pct for late entries)
    # This tests: near hour end, even small leader trades are signal
    "high=0.99+pct1.0": {**BEST, "skip_high": Decimal("0.99"), "min_pct": Decimal("1.0")},

    # ---- COMBO: both ideas ----
    "low=0.10+PT30+high=0.99": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("30"),
                                 "skip_high": Decimal("0.99")},
    "low=0.10+PT50+high=0.98": {**BEST, "skip_low": Decimal("0.10"), "pt_low": Decimal("50"),
                                 "skip_high": Decimal("0.98")},
}


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
    for vname in VARIANTS:
        all_results[vname] = {"train": [], "test": [], "profit_exits": 0}

    # TRAIN
    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            all_results[vname]["train"].append(r["pnl"])
            all_results[vname]["profit_exits"] += r.get("profit_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)}...")

    # TEST
    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            all_results[vname]["test"].append(r["pnl"])
            all_results[vname]["profit_exits"] += r.get("profit_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)}...")

    # Results
    print(f"\n{'='*150}")
    print(f"  TRAIN vs TEST -- sorted by COMBINED Sharpe")
    print(f"{'='*150}")
    print(f"\n  {'Variant':<26} | {'TR $':>6} {'TRShp':>6} {'TRWR':>5} | {'TE $':>6} {'TEShp':>6} {'TEWR':>5} | {'COMB $':>7} {'CShp':>6} | ProfExits")
    print("-" * 150)

    rows = []
    for vname, data in all_results.items():
        tr_tot, tr_avg, tr_wr, _, _, tr_std, tr_shp = compute_stats(data["train"])
        te_tot, te_avg, te_wr, _, _, te_std, te_shp = compute_stats(data["test"])
        c_all = data["train"] + data["test"]
        c_tot, c_avg, _, _, _, c_std, c_shp = compute_stats(c_all)
        pe = data["profit_exits"]
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_shp, pe))

    rows.sort(key=lambda x: x[0], reverse=True)
    best = rows[0][1] if rows else ""
    for c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_shp2, pe in rows:
        consistent = te_shp > 0 and tr_shp > 0
        marker = " <-- BEST" if vname == best else (" ***" if consistent and te_shp > 0.05 else "")
        print(f"  {vname:<26} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>4.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>4.0f}% | ${c_tot:>+5.0f} {c_shp2:>+5.3f} | {pe:>5}{marker}")

    # Show the current best for easy comparison
    print(f"\n  Note: CURRENT BEST = skip_low=0.25, min_pct=2.0, all profit targets disabled")
    print(f"  Profit exits column shows how many times the profit target triggered a sell")


if __name__ == "__main__":
    main()
