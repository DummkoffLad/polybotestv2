"""Narrow refinement around the winning variant: skip_low=0.25 + min_pct=2.0.

Test nearby values to find the exact optimum without overfitting.
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
    pt_module.SKIP_PRICE_LOW = overrides.get("skip_low", Decimal("0.03"))
    pt_module.SKIP_PRICE_HIGH = overrides.get("skip_high", Decimal("0.97"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("1.2"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = overrides.get("dd_reduce", Decimal("15"))
    pt_module.DRAWDOWN_STOP_THRESHOLD = overrides.get("dd_stop", Decimal("25"))
    pt_module.LATE_HOUR_REDUCE_MIN = 59
    pt_module.LATE_HOUR_STOP_MIN = 60
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return {"pnl": 0}
    if count == 0:
        return {"pnl": 0}

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
    return {"pnl": round(pnl, 2)}


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


BASE = {
    "boost": Decimal("8"),
    "dd_reduce": Decimal("15"),
    "dd_stop": Decimal("25"),
    "mkt_cap": Decimal("50"),
    "skip_high": Decimal("0.97"),
}

# Narrow grid around winner: skip_low in [0.20, 0.25, 0.30] x min_pct in [1.5, 1.8, 2.0, 2.2, 2.5]
VARIANTS = {}
for low in ["0.15", "0.20", "0.25", "0.30"]:
    for pct in ["1.5", "1.8", "2.0", "2.2", "2.5", "3.0"]:
        name = f"low={low}+pct={pct}"
        VARIANTS[name] = {**BASE, "skip_low": Decimal(low), "min_pct": Decimal(pct)}

# Also add baseline for reference
VARIANTS["Baseline"] = {**BASE, "skip_low": Decimal("0.03"), "min_pct": Decimal("1.2")}


def main():
    train_hours, test_hours = discover_hours()
    print(f"TRAIN: {len(train_hours)} hours | TEST: {len(test_hours)} hours | Variants: {len(VARIANTS)}\n")

    all_results = {}  # variant -> {"train": [...], "test": [...]}

    for vname in VARIANTS:
        all_results[vname] = {"train": [], "test": [], "train_sessions": defaultdict(list), "test_sessions": defaultdict(list)}

    # TRAIN
    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            all_results[vname]["train"].append(r["pnl"])
            all_results[vname]["train_sessions"][session].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)}...")

    # TEST
    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for vname, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            all_results[vname]["test"].append(r["pnl"])
            all_results[vname]["test_sessions"][session].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)}...")

    # Summary table
    print(f"\n{'='*140}")
    print(f"  TRAIN vs TEST -- {len(train_hours)} train hours, {len(test_hours)} test hours -- sorted by COMBINED Sharpe")
    print(f"{'='*140}")
    print(f"\n  {'Variant':<22} | {'TR Total':>8} {'TR Sharpe':>9} {'TR WR':>6} | {'TE Total':>8} {'TE Sharpe':>9} {'TE WR':>6} | {'COMBINED':>8} {'CombSharpe':>10}")
    print("-" * 140)

    rows = []
    for vname, data in all_results.items():
        tr = data["train"]
        te = data["test"]
        combined = tr + te

        tr_total = sum(tr)
        tr_avg = tr_total / len(tr) if tr else 0
        tr_std = statistics.stdev(tr) if len(tr) > 1 else 0
        tr_sharpe = tr_avg / tr_std if tr_std > 0 else 0
        tr_wr = sum(1 for p in tr if p > 0) / max(1, sum(1 for p in tr if p != 0)) * 100

        te_total = sum(te)
        te_avg = te_total / len(te) if te else 0
        te_std = statistics.stdev(te) if len(te) > 1 else 0
        te_sharpe = te_avg / te_std if te_std > 0 else 0
        te_wr = sum(1 for p in te if p > 0) / max(1, sum(1 for p in te if p != 0)) * 100

        c_total = sum(combined)
        c_avg = c_total / len(combined) if combined else 0
        c_std = statistics.stdev(combined) if len(combined) > 1 else 0
        c_sharpe = c_avg / c_std if c_std > 0 else 0

        rows.append((c_sharpe, vname, tr_total, tr_sharpe, tr_wr, te_total, te_sharpe, te_wr, c_total, c_sharpe))

    rows.sort(key=lambda x: x[0], reverse=True)
    for i, (cs, vname, trt, trs, trw, tet, tes, tew, ct, cs2) in enumerate(rows):
        marker = " <-- BEST" if i == 0 else (" ***" if tes > 0.05 and trs > 0.05 else "")
        print(f"  {vname:<22} | ${trt:>+6.0f} {trs:>+8.3f} {trw:>4.0f}% | ${tet:>+6.0f} {tes:>+8.3f} {tew:>4.0f}% | ${ct:>+6.0f} {cs2:>+9.3f}{marker}")

    # Per-session for top 5
    top5 = [r[1] for r in rows[:5]]
    sessions_all = sorted(set(s for s, _, _ in train_hours + test_hours))
    print(f"\n  Per-session (top 5):")
    header = f"  {'Session':<26} {'Split':>5}"
    for vname in top5:
        header += f" | {vname[:14]:>14}"
    print(header)
    print("-" * (35 + 17 * len(top5)))

    for session in sessions_all:
        split = "TRAIN" if session in TRAIN_SESSIONS else "TEST"
        row = f"  {session:<26} {split:>5}"
        for vname in top5:
            key = "train_sessions" if session in TRAIN_SESSIONS else "test_sessions"
            pnl_list = all_results[vname][key].get(session, [])
            s_total = sum(pnl_list)
            row += f" | ${s_total:>+12.0f}"
        print(row)


if __name__ == "__main__":
    main()
