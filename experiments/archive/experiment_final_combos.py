"""Final combined experiments: test the winning ideas TOGETHER.

WINNING IDEAS (all triple-validated):
1. Tighter drawdown: dd_10/20 (Sharpe 0.085)
2. Late-entry bonus: 2x boost for min>=40 (Sharpe 0.074)
3. Selective exit at min 57: sell losers >5% underwater (Sharpe 0.067)
4. Selective exit at min 50: sell all losers (holdout $+24 — best holdout)

Test combinations to find the best overall strategy.
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
HOLDOUT_SESSIONS = {"2026-02-08/06-39"}


def set_base_params():
    pt_module.SKIP_PRICE_LOW = Decimal("0.45")
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


def run_hour(hour_file, dd_reduce=Decimal("15"), dd_stop=Decimal("25"),
             late_boost_mult=None, selective_exit_min=None, exit_loss_threshold=Decimal("0")):
    """Run one hour with combined parameters."""
    set_base_params()
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = dd_reduce
    pt_module.DRAWDOWN_STOP_THRESHOLD = dd_stop

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy.initialize(StrategyConfig.from_dict(config))
    strategy.on_session_start()

    last_hour = None
    selective_exits_done = False
    selective_exit_count = 0

    if selective_exit_min is not None:
        # Interleaved timeline for selective exit
        snapshot_times = [(s.timestamp, "snapshot", s) for s in replayer.loader._price_snapshots]
        event_times = [(e.trade.timestamp, "event", e) for e in replayer.loader.events]
        timeline = sorted(snapshot_times + event_times, key=lambda x: x[0])

        running_prices = {}
        for ts, item_type, data in timeline:
            minute = ts.minute
            if item_type == "snapshot":
                for tid, ps in data.prices.items():
                    running_prices[tid] = ps

                # Selective exit check
                if not selective_exits_done and minute >= selective_exit_min:
                    selective_exits_done = True
                    for token_id, pos in list(strategy.portfolio.get_positions().items()):
                        if pos.shares <= 0:
                            continue
                        entry = strategy.our_entries.get(token_id)
                        if not entry or entry <= 0:
                            continue
                        ps = running_prices.get(token_id)
                        if not ps or ps.bid is None or ps.bid <= 0:
                            continue
                        current_bid = ps.bid
                        loss_pct = (entry - current_bid) / entry * 100
                        if current_bid < entry and loss_pct >= exit_loss_threshold:
                            sell_price = max(current_bid, Decimal("0.01"))
                            dollars = pos.shares * sell_price
                            strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, sell_price)
                            strategy.cash += dollars
                            strategy.sells += 1
                            if token_id in strategy.our_entries:
                                del strategy.our_entries[token_id]
                            if token_id in strategy.high_water_marks:
                                del strategy.high_water_marks[token_id]
                            strategy.hourly_budget_used = max(Decimal("0"), strategy.hourly_budget_used - dollars)
                            selective_exit_count += 1

            elif item_type == "event":
                event = data
                all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
                for tid, ps_data in all_prices.items():
                    running_prices[tid] = ps_data
                event.context['all_prices'] = all_prices
                last_hour = event.trade.timestamp.hour

                # Apply late boost
                if late_boost_mult and event.trade.timestamp.minute >= 40:
                    strategy.scale_boost = pt_module.SCALE_BOOST * late_boost_mult
                else:
                    strategy.scale_boost = pt_module.SCALE_BOOST

                decision = strategy.on_event(event)
                if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                    strategy.on_fill(event, decision)
    else:
        for event in replayer.loader.events:
            all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
            event.context['all_prices'] = all_prices
            last_hour = event.trade.timestamp.hour

            # Apply late boost
            if late_boost_mult and event.trade.timestamp.minute >= 40:
                strategy.scale_boost = pt_module.SCALE_BOOST * late_boost_mult
            else:
                strategy.scale_boost = pt_module.SCALE_BOOST

            decision = strategy.on_event(event)
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)

    # Resolution
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
    return {"pnl": round(pnl, 2), "exits": selective_exit_count}


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


def discover_hours():
    base = Path("data/sessions")
    train, test, holdout = [], [], []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                if session in TRAIN_SESSIONS:
                    train.append((session, utc_h, hf))
                elif session in TEST_SESSIONS:
                    test.append((session, utc_h, hf))
                elif session in HOLDOUT_SESSIONS:
                    holdout.append((session, utc_h, hf))
    return train, test, holdout


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


# Variant definitions: (name, kwargs)
VARIANTS = [
    # Baselines
    ("BASELINE", {}),
    ("dd_10/20", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20")}),
    ("dd_8/15", {"dd_reduce": Decimal("8"), "dd_stop": Decimal("15")}),
    ("late_2x", {"late_boost_mult": Decimal("2")}),
    ("late_1.5x", {"late_boost_mult": Decimal("1.5")}),
    ("exit@57_>5%", {"selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("exit@50", {"selective_exit_min": 50}),

    # Combos of 2
    ("dd_10/20 + late_2x", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "late_boost_mult": Decimal("2")}),
    ("dd_10/20 + late_1.5x", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "late_boost_mult": Decimal("1.5")}),
    ("dd_10/20 + exit@57_>5%", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("dd_10/20 + exit@50", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "selective_exit_min": 50}),
    ("late_2x + exit@57_>5%", {"late_boost_mult": Decimal("2"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("late_2x + exit@50", {"late_boost_mult": Decimal("2"), "selective_exit_min": 50}),
    ("dd_8/15 + late_2x", {"dd_reduce": Decimal("8"), "dd_stop": Decimal("15"), "late_boost_mult": Decimal("2")}),
    ("dd_8/15 + exit@57_>5%", {"dd_reduce": Decimal("8"), "dd_stop": Decimal("15"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),

    # Combos of 3 (the dream)
    ("dd_10/20 + late_2x + exit@57", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "late_boost_mult": Decimal("2"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("dd_10/20 + late_2x + exit@50", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "late_boost_mult": Decimal("2"), "selective_exit_min": 50}),
    ("dd_10/20 + late_1.5x + exit@57", {"dd_reduce": Decimal("10"), "dd_stop": Decimal("20"), "late_boost_mult": Decimal("1.5"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("dd_8/15 + late_2x + exit@57", {"dd_reduce": Decimal("8"), "dd_stop": Decimal("15"), "late_boost_mult": Decimal("2"), "selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("dd_8/15 + late_2x + exit@50", {"dd_reduce": Decimal("8"), "dd_stop": Decimal("15"), "late_boost_mult": Decimal("2"), "selective_exit_min": 50}),
]


def main():
    train, test, holdout = discover_hours()
    print(f"TRAIN: {len(train)} | TEST: {len(test)} | HOLDOUT: {len(holdout)}")
    print(f"Variants: {len(VARIANTS)}\n")

    results = {name: {"train": [], "test": [], "holdout": []} for name, _ in VARIANTS}

    for idx, (session, utc_h, hf) in enumerate(train):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                results[vname]["train"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train)}...")

    for idx, (session, utc_h, hf) in enumerate(test):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                results[vname]["test"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test)}...")

    for idx, (session, utc_h, hf) in enumerate(holdout):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                results[vname]["holdout"].append(r["pnl"])

    # Display
    print(f"\n{'='*155}")
    print(f"  FINAL COMBINATION EXPERIMENTS — sorted by Combined Sharpe")
    print(f"{'='*155}")
    header = f"  {'Variant':<32} | {'TR $':>6} {'TShp':>6} {'TWR':>4} | {'TE $':>6} {'TShp':>6} {'TWR':>4} | {'COMB':>6} {'CShp':>6} | {'HOLD $':>6} {'HShp':>6} {'HWR':>4}"
    print(header)
    print("-" * 155)

    rows = []
    for vname, _ in VARIANTS:
        d = results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(d["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(d["test"])
        c_all = d["train"] + d["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        h_tot, _, h_wr, _, _, _, h_shp = compute_stats(d["holdout"])
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, h_wr))

    rows.sort(key=lambda x: x[0], reverse=True)
    best = rows[0][1] if rows else ""
    for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, h_wr in rows:
        robust = tr_shp > 0 and te_shp > 0
        triple = robust and h_tot > 0
        marker = " <-- BEST" if name == best else (" [3x]" if triple else (" ***" if robust else ""))
        print(f"  {name:<32} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>3.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>3.0f}% | ${c_tot:>+4.0f} {c_shp:>+5.3f} | ${h_tot:>+4.0f} {h_shp:>+5.3f} {h_wr:>3.0f}%{marker}")

    # Show triple-validated
    triple_rows = [r for r in rows if r[3] > 0 and r[6] > 0 and r[9] > 0]
    if triple_rows:
        print(f"\n  TRIPLE-VALIDATED (positive on train, test, AND holdout): {len(triple_rows)}")
        print(f"  {'Variant':<32} | {'Train':>6} | {'Test':>6} | {'Holdout':>7} | {'CombSharpe':>10} | {'HoldSharpe':>10}")
        for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, h_wr in triple_rows:
            print(f"  {name:<32} | ${tr_tot:>+4.0f} | ${te_tot:>+4.0f} | ${h_tot:>+5.0f} | {c_shp:>+9.3f} | {h_shp:>+9.3f}")

    # Save
    results_json = {}
    for vname, _ in VARIANTS:
        d = results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(d["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(d["test"])
        c_all = d["train"] + d["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        h_tot, _, h_wr, _, _, _, h_shp = compute_stats(d["holdout"])
        results_json[vname] = {
            "train_pnl": round(tr_tot, 2), "train_sharpe": round(tr_shp, 4), "train_wr": round(tr_wr, 1),
            "test_pnl": round(te_tot, 2), "test_sharpe": round(te_shp, 4), "test_wr": round(te_wr, 1),
            "combined_pnl": round(c_tot, 2), "combined_sharpe": round(c_shp, 4),
            "holdout_pnl": round(h_tot, 2), "holdout_sharpe": round(h_shp, 4), "holdout_wr": round(h_wr, 1),
        }
    with open("experiment_final_results.json", "w") as f:
        json.dump(results_json, f, indent=2)
    print(f"\nResults saved to experiment_final_results.json")


if __name__ == "__main__":
    main()
