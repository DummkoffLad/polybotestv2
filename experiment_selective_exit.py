"""Test SELECTIVE time-based exit: sell losers at minute 55, keep winners for resolution.

The core insight from deep_analysis:
- Mid-hour selling: +$323 (the money maker!)
- Resolution: -$135 (where we lose)
- But full time-exit at min 55 is WORSE (-$195 vs -$135) because winners lose $0.99 upside

SELECTIVE EXIT: At minute 55, sell ONLY positions that are LOSING (bid < entry).
- Winners keep $0.99 resolution upside
- Losers avoid catastrophic $0.01 resolution

Also test:
- Different exit minutes (50, 52, 55, 57)
- Different underwater thresholds (any loss, >5% loss, >10% loss)
- Late-entry bonus (correct implementation)
- Holdout validation on 2026-02-08 data
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


def run_hour(hour_file, overrides=None, selective_exit_min=None, exit_loss_threshold=Decimal("0"),
             late_boost=None):
    """Run one hour with optional selective time-exit for losers.

    Args:
        selective_exit_min: If set, at this minute sell underwater positions at market bid
        exit_loss_threshold: Only exit if loss % exceeds this (0 = any loss)
        late_boost: If set, multiply boost by this for trades after minute 40
    """
    set_base_params()
    if overrides:
        for k, v in overrides.items():
            setattr(pt_module, k, v)

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
    selective_exit_pnl = Decimal("0")

    # Build timeline: interleave snapshots and events for selective exit timing
    if selective_exit_min is not None:
        snapshot_times = [(s.timestamp, "snapshot", s) for s in replayer.loader._price_snapshots]
        event_times = [(e.trade.timestamp, "event", e) for e in replayer.loader.events]
        timeline = sorted(snapshot_times + event_times, key=lambda x: x[0])

        running_prices = {}
        for ts, item_type, data in timeline:
            minute = ts.minute
            if item_type == "snapshot":
                for tid, ps in data.prices.items():
                    running_prices[tid] = ps

                # Check if we should do selective exit
                if not selective_exits_done and minute >= selective_exit_min:
                    selective_exits_done = True
                    for token_id, pos in list(strategy.portfolio.get_positions().items()):
                        if pos.shares <= 0:
                            continue
                        entry = strategy.our_entries.get(token_id)
                        if not entry or entry <= 0:
                            continue
                        ps = running_prices.get(token_id)
                        if not ps or ps.bid is None:
                            continue
                        current_bid = ps.bid
                        if current_bid <= 0:
                            continue
                        loss_pct = (entry - current_bid) / entry * 100
                        # Only exit if underwater by at least threshold
                        # For sell_all variants (threshold < 0), also sell profitable positions
                        should_exit = (exit_loss_threshold < 0) or (current_bid < entry and loss_pct >= exit_loss_threshold)
                        if should_exit:
                            sell_price = max(current_bid, Decimal("0.01"))
                            dollars = pos.shares * sell_price
                            pnl_on_exit = dollars - pos.cost_basis
                            selective_exit_pnl += pnl_on_exit
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

                # Apply late boost if configured
                if late_boost and event.trade.timestamp.minute >= 40:
                    strategy.scale_boost = pt_module.SCALE_BOOST * late_boost
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

            # Apply late boost if configured
            if late_boost and event.trade.timestamp.minute >= 40:
                strategy.scale_boost = pt_module.SCALE_BOOST * late_boost
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
    return {"pnl": round(pnl, 2), "selective_exits": selective_exit_count}


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


# Define all variants as (name, kwargs_dict)
VARIANTS = [
    # Baselines
    ("BASELINE", {}),
    ("skip=0.50", {"overrides": {"SKIP_PRICE_LOW": Decimal("0.50")}}),
    ("skip=0.55", {"overrides": {"SKIP_PRICE_LOW": Decimal("0.55")}}),

    # Selective exits at different minutes (sell losers only)
    ("exit_losers@50", {"selective_exit_min": 50}),
    ("exit_losers@52", {"selective_exit_min": 52}),
    ("exit_losers@55", {"selective_exit_min": 55}),
    ("exit_losers@57", {"selective_exit_min": 57}),

    # Selective exits with loss threshold (only exit if >X% underwater)
    ("exit_losers@55_>5%", {"selective_exit_min": 55, "exit_loss_threshold": Decimal("5")}),
    ("exit_losers@55_>10%", {"selective_exit_min": 55, "exit_loss_threshold": Decimal("10")}),
    ("exit_losers@55_>15%", {"selective_exit_min": 55, "exit_loss_threshold": Decimal("15")}),
    ("exit_losers@55_>20%", {"selective_exit_min": 55, "exit_loss_threshold": Decimal("20")}),

    ("exit_losers@57_>5%", {"selective_exit_min": 57, "exit_loss_threshold": Decimal("5")}),
    ("exit_losers@57_>10%", {"selective_exit_min": 57, "exit_loss_threshold": Decimal("10")}),

    # Late-entry bonus (bigger positions after minute 40)
    ("late_1.5x", {"late_boost": Decimal("1.5")}),
    ("late_2.0x", {"late_boost": Decimal("2.0")}),
    ("late_1.5x+exit@55", {"late_boost": Decimal("1.5"), "selective_exit_min": 55}),
    ("late_2.0x+exit@55", {"late_boost": Decimal("2.0"), "selective_exit_min": 55}),

    # Combined: skip + selective exit
    ("skip=0.50+exit@55", {"overrides": {"SKIP_PRICE_LOW": Decimal("0.50")}, "selective_exit_min": 55}),
    ("skip=0.55+exit@55", {"overrides": {"SKIP_PRICE_LOW": Decimal("0.55")}, "selective_exit_min": 55}),

    # Sell ALL positions (not just losers) at minute 55 at market bid
    ("sell_all@55", {"selective_exit_min": 55, "exit_loss_threshold": Decimal("-100")}),
    ("sell_all@57", {"selective_exit_min": 57, "exit_loss_threshold": Decimal("-100")}),

    # Sell all + higher skip (avoid low entries + sell before resolution)
    ("skip=0.55+sell_all@55", {"overrides": {"SKIP_PRICE_LOW": Decimal("0.55")},
                               "selective_exit_min": 55, "exit_loss_threshold": Decimal("-100")}),

    # More aggressive drawdown limits
    ("dd_10/20", {"overrides": {"DRAWDOWN_REDUCE_THRESHOLD": Decimal("10"),
                                "DRAWDOWN_STOP_THRESHOLD": Decimal("20")}}),
    ("dd_8/15", {"overrides": {"DRAWDOWN_REDUCE_THRESHOLD": Decimal("8"),
                               "DRAWDOWN_STOP_THRESHOLD": Decimal("15")}}),

    # Tighter drawdown + selective exit
    ("dd_10/20+exit@55", {"overrides": {"DRAWDOWN_REDUCE_THRESHOLD": Decimal("10"),
                                        "DRAWDOWN_STOP_THRESHOLD": Decimal("20")},
                          "selective_exit_min": 55}),
]


def main():
    train, test, holdout = discover_hours()
    print(f"TRAIN: {len(train)} hours | TEST: {len(test)} hours | HOLDOUT: {len(holdout)} hours")
    print(f"Total variants: {len(VARIANTS)}\n")

    all_results = {name: {"train": [], "test": [], "holdout": [], "exits": 0}
                   for name, _ in VARIANTS}

    # Run train
    for idx, (session, utc_h, hf) in enumerate(train):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                all_results[vname]["train"].append(r["pnl"])
                all_results[vname]["exits"] += r.get("selective_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train)}...")

    # Run test
    for idx, (session, utc_h, hf) in enumerate(test):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                all_results[vname]["test"].append(r["pnl"])
                all_results[vname]["exits"] += r.get("selective_exits", 0)
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test)}...")

    # Run holdout
    for idx, (session, utc_h, hf) in enumerate(holdout):
        for vname, kwargs in VARIANTS:
            r = run_hour(hf, **kwargs)
            if r:
                all_results[vname]["holdout"].append(r["pnl"])
        if (idx + 1) % 5 == 0:
            print(f"  HOLDOUT: {idx+1}/{len(holdout)}...")

    # Display results
    print(f"\n{'='*150}")
    print(f"  SELECTIVE EXIT + LATE BONUS EXPERIMENTS — sorted by Combined Sharpe")
    print(f"{'='*150}")
    print(f"  {'Variant':<28} | {'TR $':>6} {'TShp':>6} {'TWR':>4} | {'TE $':>6} {'TShp':>6} {'TWR':>4} | {'COMB':>6} {'CShp':>6} | {'HOLD $':>6} {'HShp':>6} | Exits")
    print("-" * 150)

    rows = []
    for vname, _ in VARIANTS:
        d = all_results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(d["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(d["test"])
        c_all = d["train"] + d["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        h_tot, _, _, _, _, _, h_shp = compute_stats(d["holdout"])
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr,
                      c_tot, h_tot, h_shp, d["exits"]))

    rows.sort(key=lambda x: x[0], reverse=True)
    best_name = rows[0][1] if rows else ""
    for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, exits in rows:
        robust = tr_shp > 0 and te_shp > 0
        marker = " <-- BEST" if name == best_name else (" ***" if robust and te_shp > 0.01 else "")
        ex_str = f"{exits:>5}" if exits > 0 else "    -"
        h_str = f"${h_tot:>+4.0f} {h_shp:>+5.3f}" if h_tot != 0 else "   -     -"
        print(f"  {name:<28} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>3.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>3.0f}% | ${c_tot:>+4.0f} {c_shp:>+5.3f} | {h_str} | {ex_str}{marker}")

    # Robust summary
    robust_rows = [(c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, exits)
                   for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, exits in rows
                   if tr_tot > 0 and te_tot > 0]
    if robust_rows:
        print(f"\n  ROBUST (positive on BOTH train & test): {len(robust_rows)} variants")
        for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, exits in robust_rows:
            h_str = f"holdout=${h_tot:>+.0f}" if h_tot != 0 else "holdout=N/A"
            print(f"    {name:<28}: combined=${c_tot:>+.0f} Sharpe={c_shp:>+.3f}  {h_str}")

    # Triple-validated (positive on train, test, AND holdout)
    triple = [(c_shp, name, tr_tot, te_tot, h_tot)
              for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp, exits in rows
              if tr_tot > 0 and te_tot > 0 and h_tot > 0]
    if triple:
        print(f"\n  TRIPLE-VALIDATED (positive on train, test, AND holdout): {len(triple)}")
        for c_shp, name, tr_tot, te_tot, h_tot in triple:
            print(f"    {name:<28}: train=${tr_tot:>+.0f} test=${te_tot:>+.0f} holdout=${h_tot:>+.0f}")

    # Save
    results_json = {}
    for vname, _ in VARIANTS:
        d = all_results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(d["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(d["test"])
        c_all = d["train"] + d["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        h_tot, _, _, _, _, _, h_shp = compute_stats(d["holdout"])
        results_json[vname] = {
            "train_pnl": round(tr_tot, 2), "train_sharpe": round(tr_shp, 4), "train_wr": round(tr_wr, 1),
            "test_pnl": round(te_tot, 2), "test_sharpe": round(te_shp, 4), "test_wr": round(te_wr, 1),
            "combined_pnl": round(c_tot, 2), "combined_sharpe": round(c_shp, 4),
            "holdout_pnl": round(h_tot, 2), "holdout_sharpe": round(h_shp, 4),
        }
    with open("experiment_selective_exit_results.json", "w") as f:
        json.dump(results_json, f, indent=2)
    print(f"\nResults saved to experiment_selective_exit_results.json")


if __name__ == "__main__":
    main()
