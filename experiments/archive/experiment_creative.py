"""Creative strategy experiments at $50 capital.

New ideas beyond parameter tweaking:
1. CASH RECYCLING: Sell winning positions to fund new entries
2. RE-ENTRY CONVICTION: Size up when leader re-buys same token
3. PRE-RESOLUTION EXIT: Sell losers at minute 50+ before $0.01 resolution
4. MOMENTUM FILTER: Only enter when price is rising (ask > leader's price)
5. SELECTIVE HOLD: Only hold to resolution if price > threshold (e.g. 0.60)
6. COMBINATION: Recycle + selective hold + re-entry
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
from src.data.models import PriceSnapshot, TradeAction
import src.strategies.profit_taker.strategy as pt_mod

TRAIN_SESSIONS = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST_SESSIONS = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT_SESSIONS = ["2026-02-08/06-39"]

CONFIG = {
    "scaling.our_capital": 50, "scaling.hourly_budget": 45,
    "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
}


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


def run_creative(hour_file, variant_config):
    """Run a creative strategy variant.

    variant_config keys:
    - recycle: bool — sell winners when we'd otherwise skip a buy
    - recycle_min_profit_pct: float — minimum profit % to trigger recycle sell
    - recycle_sell_pct: float — % of winning position to sell for recycling
    - pre_exit_minute: int — sell losers after this minute (0=disabled)
    - pre_exit_winners_too: bool — also sell winners before resolution
    - pre_exit_min_bid: float — only pre-exit if bid > this (avoid selling at 0.01)
    - momentum: bool — only buy when ask >= leader price (no slippage)
    - reentry_boost: float — extra multiplier for leader's 2nd+ buy of same token
    - hold_threshold: float — at resolution, sell if bid < this (instead of normal 0.50)
    - boost_override: int — override SCALE_BOOST
    """
    recycle = variant_config.get("recycle", False)
    recycle_min_profit = Decimal(str(variant_config.get("recycle_min_profit_pct", 10)))
    recycle_sell_pct = Decimal(str(variant_config.get("recycle_sell_pct", 50)))
    pre_exit_minute = variant_config.get("pre_exit_minute", 0)
    pre_exit_winners_too = variant_config.get("pre_exit_winners_too", False)
    pre_exit_min_bid = Decimal(str(variant_config.get("pre_exit_min_bid", 0.05)))
    momentum = variant_config.get("momentum", False)
    reentry_boost = Decimal(str(variant_config.get("reentry_boost", 1)))
    hold_threshold = Decimal(str(variant_config.get("hold_threshold", 0.50)))
    boost_override = variant_config.get("boost_override", None)

    # Patch boost if needed
    orig_boost = pt_mod.SCALE_BOOST
    if boost_override:
        pt_mod.SCALE_BOOST = Decimal(str(boost_override))

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG)
    try:
        count = replayer.load()
    except Exception:
        pt_mod.SCALE_BOOST = orig_boost
        return None
    if count == 0:
        pt_mod.SCALE_BOOST = orig_boost
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    # Track leader's per-token buy count for re-entry detection
    leader_token_buys = defaultdict(int)
    last_hour = None
    recycle_sells = 0
    recycle_cash_freed = Decimal("0")
    pre_exit_count = 0
    reentry_count = 0

    events = replayer.loader.events

    for idx, event in enumerate(events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        minute = event.trade.timestamp.minute

        # Track leader token buys
        if event.trade.action == TradeAction.BUY:
            leader_token_buys[event.trade.token_id] += 1

        # === PRE-RESOLUTION EXIT ===
        # At specified minute, sell positions that are underwater (or all)
        if pre_exit_minute > 0 and minute >= pre_exit_minute:
            for token_id, pos in list(strategy.portfolio.get_positions().items()):
                if pos.shares <= 0:
                    continue
                ps = all_prices.get(token_id)
                if not ps or ps.bid is None:
                    continue
                current_bid = ps.bid
                if current_bid < pre_exit_min_bid:
                    continue
                entry = strategy.our_entries.get(token_id, Decimal("0"))
                is_losing = current_bid < entry if entry > 0 else False
                is_winning = current_bid > entry if entry > 0 else False

                should_exit = False
                if is_losing:
                    should_exit = True
                elif is_winning and pre_exit_winners_too:
                    should_exit = True

                if should_exit:
                    sell_price = max(current_bid, Decimal("0.01"))
                    dollars = pos.shares * sell_price
                    strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, sell_price)
                    strategy.cash += dollars
                    strategy.sells += 1
                    pre_exit_count += 1
                    if token_id in strategy.our_entries:
                        del strategy.our_entries[token_id]
                    if token_id in strategy.high_water_marks:
                        del strategy.high_water_marks[token_id]

        # === MOMENTUM FILTER ===
        if momentum and event.trade.action == TradeAction.BUY:
            if event.prices.ask and event.trade.price > 0:
                if event.prices.ask > event.trade.price + Decimal("0.02"):
                    # Price moved against us — skip
                    continue

        # === RE-ENTRY BOOST ===
        # Temporarily boost scale if leader is re-entering same token
        applied_reentry = False
        if reentry_boost > 1 and event.trade.action == TradeAction.BUY:
            if leader_token_buys[event.trade.token_id] >= 2:
                # Leader's 2nd+ buy — higher conviction
                orig_boost_inner = pt_mod.SCALE_BOOST
                pt_mod.SCALE_BOOST = pt_mod.SCALE_BOOST * reentry_boost
                applied_reentry = True
                reentry_count += 1

        # === MAIN STRATEGY DECISION ===
        decision = strategy.on_event(event)

        # === CASH RECYCLING ===
        # If strategy wants to skip a BUY due to capacity, check for recyclable positions
        if recycle and decision.action == DecisionAction.SKIP and event.trade.action == TradeAction.BUY:
            skip_reason = decision.skip_reason if hasattr(decision, 'skip_reason') else ""
            recyclable_reasons = {"reserve", "budget", "market_cap", "side_cap",
                                  "global_cap", "no_cash", "min_order"}
            if skip_reason in recyclable_reasons:
                # Find the most profitable position to recycle
                best_recycle = None
                best_profit_pct = Decimal("0")
                for token_id, pos in strategy.portfolio.get_positions().items():
                    if pos.shares <= 0 or pos.avg_price <= 0:
                        continue
                    ps = all_prices.get(token_id)
                    if not ps or ps.bid is None or ps.bid <= 0:
                        continue
                    profit_pct = (ps.bid - pos.avg_price) / pos.avg_price * 100
                    if profit_pct >= recycle_min_profit and profit_pct > best_profit_pct:
                        best_recycle = (token_id, pos, ps.bid, profit_pct)
                        best_profit_pct = profit_pct

                if best_recycle:
                    token_id, pos, bid, profit_pct = best_recycle
                    # Sell portion of winning position
                    sell_shares = (pos.shares * recycle_sell_pct / 100).quantize(Decimal("0.01"))
                    if sell_shares > 0:
                        sell_price = max(bid, Decimal("0.01"))
                        dollars = sell_shares * sell_price
                        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side,
                                                     sell_shares, sell_price)
                        strategy.cash += dollars
                        strategy.sells += 1
                        recycle_sells += 1
                        recycle_cash_freed += dollars

                        # Update budget to reflect freed capacity
                        strategy.hourly_budget_used = max(Decimal("0"),
                            strategy.hourly_budget_used - dollars)

                        # Re-try the buy decision
                        decision = strategy.on_event(event)

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

        # Restore boost if we applied reentry
        if applied_reentry:
            pt_mod.SCALE_BOOST = orig_boost_inner

    # === RESOLUTION with hold_threshold ===
    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            last_bid = ps.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))

        # Use configurable threshold instead of fixed 0.50
        if last_bid >= hold_threshold:
            res_price = Decimal("0.99")
        else:
            res_price = Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    pnl = float(strategy.cash) - 100.0  # Pool is 2x capital = $100

    # Restore boost
    pt_mod.SCALE_BOOST = orig_boost

    return {
        "pnl": round(pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "recycle_sells": recycle_sells,
        "recycle_cash_freed": float(recycle_cash_freed),
        "pre_exit_count": pre_exit_count,
        "reentry_count": reentry_count,
    }


def sharpe(hourly_pnls):
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

    print(f"Creative strategy experiments across {len(all_hours)} hours...\n")

    variants = {
        # === BASELINE ===
        "baseline": {},

        # === CASH RECYCLING ===
        "recycle_10%_50%": {"recycle": True, "recycle_min_profit_pct": 10, "recycle_sell_pct": 50},
        "recycle_5%_50%": {"recycle": True, "recycle_min_profit_pct": 5, "recycle_sell_pct": 50},
        "recycle_5%_100%": {"recycle": True, "recycle_min_profit_pct": 5, "recycle_sell_pct": 100},
        "recycle_15%_50%": {"recycle": True, "recycle_min_profit_pct": 15, "recycle_sell_pct": 50},
        "recycle_0%_50%": {"recycle": True, "recycle_min_profit_pct": 0, "recycle_sell_pct": 50},
        "recycle_10%_100%": {"recycle": True, "recycle_min_profit_pct": 10, "recycle_sell_pct": 100},
        "recycle_10%_30%": {"recycle": True, "recycle_min_profit_pct": 10, "recycle_sell_pct": 30},

        # === PRE-RESOLUTION EXIT ===
        "pre_exit_50_losers": {"pre_exit_minute": 50},
        "pre_exit_45_losers": {"pre_exit_minute": 45},
        "pre_exit_55_losers": {"pre_exit_minute": 55},
        "pre_exit_50_all": {"pre_exit_minute": 50, "pre_exit_winners_too": True},
        "pre_exit_45_all": {"pre_exit_minute": 45, "pre_exit_winners_too": True},

        # === RE-ENTRY CONVICTION ===
        "reentry_1.5x": {"reentry_boost": 1.5},
        "reentry_2x": {"reentry_boost": 2},
        "reentry_3x": {"reentry_boost": 3},

        # === HOLD THRESHOLD (resolution) ===
        "hold_0.55": {"hold_threshold": 0.55},
        "hold_0.60": {"hold_threshold": 0.60},
        "hold_0.45": {"hold_threshold": 0.45},

        # === COMBINED STRATEGIES ===
        "recycle+pre_exit": {"recycle": True, "recycle_min_profit_pct": 10,
                             "recycle_sell_pct": 50, "pre_exit_minute": 50},
        "recycle+reentry": {"recycle": True, "recycle_min_profit_pct": 10,
                            "recycle_sell_pct": 50, "reentry_boost": 2},
        "recycle+hold55": {"recycle": True, "recycle_min_profit_pct": 10,
                           "recycle_sell_pct": 50, "hold_threshold": 0.55},
        "full_creative_v1": {"recycle": True, "recycle_min_profit_pct": 10,
                             "recycle_sell_pct": 50, "pre_exit_minute": 50,
                             "reentry_boost": 2, "hold_threshold": 0.55},
        "full_creative_v2": {"recycle": True, "recycle_min_profit_pct": 5,
                             "recycle_sell_pct": 100, "pre_exit_minute": 50,
                             "reentry_boost": 1.5},
        "recycle_aggressive": {"recycle": True, "recycle_min_profit_pct": 0,
                               "recycle_sell_pct": 100, "pre_exit_minute": 50,
                               "reentry_boost": 2},

        # === RECYCLING + HIGHER BOOST ===
        "recycle+b10": {"recycle": True, "recycle_min_profit_pct": 10,
                        "recycle_sell_pct": 50, "boost_override": 10},
        "recycle+b12": {"recycle": True, "recycle_min_profit_pct": 10,
                        "recycle_sell_pct": 50, "boost_override": 12},
        "recycle_agr+b12": {"recycle": True, "recycle_min_profit_pct": 5,
                            "recycle_sell_pct": 100, "boost_override": 12},

        # === MOMENTUM ===
        "momentum_only": {"momentum": True},
        "momentum+recycle": {"momentum": True, "recycle": True,
                             "recycle_min_profit_pct": 10, "recycle_sell_pct": 50},
    }

    results = []
    for i, (name, vc) in enumerate(variants.items()):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        total_recycles = 0
        total_cash_freed = 0
        total_pre_exits = 0
        total_reentries = 0

        for session, utc_h, hf, split in all_hours:
            r = run_creative(hf, vc)
            if r:
                split_pnl[split].append(r["pnl"])
                total_recycles += r["recycle_sells"]
                total_cash_freed += r["recycle_cash_freed"]
                total_pre_exits += r["pre_exit_count"]
                total_reentries += r["reentry_count"]

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
            "recycles": total_recycles, "cash_freed": total_cash_freed,
            "pre_exits": total_pre_exits, "reentries": total_reentries,
        })

        rob = "YES" if robust else "no"
        extra = ""
        if total_recycles > 0:
            extra += f" rec={total_recycles}"
        if total_pre_exits > 0:
            extra += f" preEx={total_pre_exits}"
        if total_reentries > 0:
            extra += f" reEnt={total_reentries}"
        print(f"  [{i+1:>2}/{len(variants)}] {name:<24} Tr=${train_sum:>+8.2f} Te=${test_sum:>+8.2f} Ho=${hold_sum:>+8.2f} => ${combined:>+9.2f} S={sr:+.3f} [{rob}]{extra}")

    # Ranking
    print(f"\n{'='*130}")
    print(f"  RANKING (sorted by combined PnL)")
    print(f"{'='*130}")
    print(f"  {'#':>2} {'Variant':<26} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} | {'Total$':>8} {'Sharpe':>7} {'$/hr':>6} | {'Rec':>4} {'PreEx':>5} | {'Rob':>3}")
    print(f"  {'-'*110}")

    for rank, r in enumerate(sorted(results, key=lambda x: -x["combined"]), 1):
        rob = "Y" if r["robust"] else "n"
        per_hr = r["combined"] / max(1, r["hours"])
        print(f"  {rank:>2} {r['name']:<26} | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} | ${r['combined']:>+7.2f} {r['sharpe']:>+6.3f} ${per_hr:>+5.2f} | {r['recycles']:>4} {r['pre_exits']:>5} | {rob:>3}")

    # Sharpe ranking (robust only)
    print(f"\n  SHARPE RANKING (robust only)")
    print(f"  {'#':>2} {'Variant':<26} | {'Total$':>8} {'Sharpe':>7} | Key feature")
    print(f"  {'-'*70}")
    robust_sorted = sorted([r for r in results if r["robust"]], key=lambda x: -x["sharpe"])
    for rank, r in enumerate(robust_sorted[:15], 1):
        feature = ""
        if "recycle" in r["name"]:
            feature = f"recycled ${r['cash_freed']:.0f}"
        if "pre_exit" in r["name"]:
            feature = f"{r['pre_exits']} pre-exits"
        if "reentry" in r["name"]:
            feature += f" {r['reentries']} re-entries"
        print(f"  {rank:>2} {r['name']:<26} | ${r['combined']:>+7.2f} {r['sharpe']:>+6.3f} | {feature}")


if __name__ == "__main__":
    main()
