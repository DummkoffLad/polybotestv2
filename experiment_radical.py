"""Radical strategy experiments — think completely differently about $50 capital.

Key insight: At $50, we can't do everything. We need to be EXTREMELY selective
and deploy capital where it matters most.

New ideas:
1. DELAYED ENTRY: Wait until minute 20-30, only enter confirmed winners
2. CONCENTRATED: Fewer positions, bigger sizes (1-2 tokens per hour max)
3. PRICE-ONLY: Ignore leader's trades entirely, just bet on price > 0.65
4. FULL ROTATION: Sell EVERYTHING when leader sells, rebuy on next opportunity
5. TURBO RECYCLE: Sell at ANY profit to free cash, buy next opportunity
6. HIGHER POOL: Pool = 3-4x capital, allowing more simultaneous positions
7. BUDGET UNLOCKED: Remove hourly budget cap entirely
8. ALL CAPS REMOVED: No market/side/global caps — pure aggressive
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
from src.data.models import PriceSnapshot, TradeAction
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


def run_radical(hour_file, variant):
    """Run strategy with radical modifications."""
    name = variant["name"]

    # Save ALL originals
    orig = {}
    for attr in ["SCALE_BOOST", "SKIP_PRICE_LOW", "PER_MARKET_CAP_PCT",
                 "PER_SIDE_PCT", "GLOBAL_EXPOSURE_PCT", "DRAWDOWN_REDUCE_THRESHOLD",
                 "DRAWDOWN_STOP_THRESHOLD", "LATE_ENTRY_BOOST_MIN",
                 "LATE_ENTRY_BOOST_MULT", "POOL_CAPITAL_MULTIPLIER",
                 "MIN_LEADER_TRADE_PCT", "CASH_RESERVE_PCT"]:
        orig[attr] = getattr(pt_mod, attr)

    # Apply variant overrides
    for k, v in variant.get("overrides", {}).items():
        # LATE_ENTRY_BOOST_MIN is int, everything else is Decimal
        if k == "LATE_ENTRY_BOOST_MIN":
            setattr(pt_mod, k, int(v))
        else:
            setattr(pt_mod, k, Decimal(str(v)))

    capital = variant.get("capital", 50)
    budget = variant.get("budget", 45)

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

    min_entry_minute = variant.get("min_entry_minute", 0)
    max_positions = variant.get("max_positions", 999)
    turbo_recycle = variant.get("turbo_recycle", False)
    turbo_min_profit_pct = Decimal(str(variant.get("turbo_min_profit_pct", 5)))
    sell_all_on_leader_sell = variant.get("sell_all_on_leader_sell", False)

    last_hour = None
    events = replayer.loader.events
    recycle_count = 0
    positions_entered = set()

    for idx, event in enumerate(events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        minute = event.trade.timestamp.minute

        # === TURBO RECYCLE: sell any profitable position before processing new event ===
        if turbo_recycle and event.trade.action == TradeAction.BUY:
            for token_id, pos in list(strategy.portfolio.get_positions().items()):
                if pos.shares <= 0 or pos.avg_price <= 0:
                    continue
                ps = all_prices.get(token_id)
                if not ps or ps.bid is None or ps.bid <= 0:
                    continue
                profit_pct = (ps.bid - pos.avg_price) / pos.avg_price * 100
                if profit_pct >= turbo_min_profit_pct:
                    sell_price = max(ps.bid, Decimal("0.01"))
                    dollars = pos.shares * sell_price
                    strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side,
                                                 pos.shares, sell_price)
                    strategy.cash += dollars
                    strategy.sells += 1
                    recycle_count += 1
                    strategy.hourly_budget_used = max(Decimal("0"),
                        strategy.hourly_budget_used - dollars)
                    if token_id in strategy.our_entries:
                        del strategy.our_entries[token_id]
                    if token_id in strategy.high_water_marks:
                        del strategy.high_water_marks[token_id]

        # === SELL ALL ON LEADER SELL ===
        if sell_all_on_leader_sell and event.trade.action == TradeAction.SELL:
            token_id = event.trade.token_id
            pos = strategy.portfolio.get(token_id, event.trade.market_id,
                                         strategy.portfolio.get_positions().get(token_id, None) and
                                         strategy.portfolio.get_positions()[token_id].side or None)
            # Try to sell our full position if we have one
            for tid, p in list(strategy.portfolio.get_positions().items()):
                if tid == token_id and p.shares > 0:
                    ps = all_prices.get(tid)
                    if ps and ps.bid is not None and ps.bid > 0:
                        sell_price = max(ps.bid, Decimal("0.01"))
                        dollars = p.shares * sell_price
                        strategy.portfolio.apply_sell(tid, p.market_id, p.side,
                                                     p.shares, sell_price)
                        strategy.cash += dollars
                        strategy.sells += 1
                        strategy.hourly_budget_used = max(Decimal("0"),
                            strategy.hourly_budget_used - dollars)
                        if tid in strategy.our_entries:
                            del strategy.our_entries[tid]

        # === DELAYED ENTRY ===
        if minute < min_entry_minute and event.trade.action == TradeAction.BUY:
            # Skip early entries — let strategy handle other events
            decision = strategy.on_event(event)
            # Don't execute buy decisions
            if decision.action == DecisionAction.SELL:
                strategy.on_fill(event, decision)
            continue

        # === MAX POSITIONS LIMIT ===
        active_positions = sum(1 for p in strategy.portfolio.get_positions().values() if p.shares > 0)
        if active_positions >= max_positions and event.trade.action == TradeAction.BUY:
            # At capacity — try turbo recycle first if enabled
            if turbo_recycle:
                # Already handled above
                active_positions = sum(1 for p in strategy.portfolio.get_positions().values() if p.shares > 0)
                if active_positions >= max_positions:
                    continue
            else:
                continue

        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)
            if decision.action == DecisionAction.BUY:
                positions_entered.add(event.trade.token_id)

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

    pool = Decimal(str(capital)) * pt_mod.POOL_CAPITAL_MULTIPLIER
    pnl = float(strategy.cash) - float(pool)

    # Restore
    for k, v in orig.items():
        setattr(pt_mod, k, v)

    return {
        "pnl": round(pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "recycles": recycle_count,
        "positions_entered": len(positions_entered),
    }


def sharpe(pnls):
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    var = sum((x - mean) ** 2 for x in pnls) / (len(pnls) - 1)
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

    print(f"Radical experiments across {len(all_hours)} hours...\n")

    variants = [
        # === BASELINE ===
        {"name": "baseline", "overrides": {}},

        # === DELAYED ENTRY: Only enter after minute X ===
        {"name": "delay_15min", "min_entry_minute": 15, "overrides": {}},
        {"name": "delay_20min", "min_entry_minute": 20, "overrides": {}},
        {"name": "delay_30min", "min_entry_minute": 30, "overrides": {}},

        # === CONCENTRATED: Max 1-2 positions per hour ===
        {"name": "max_1_pos", "max_positions": 1, "overrides": {}},
        {"name": "max_2_pos", "max_positions": 2, "overrides": {}},
        {"name": "max_3_pos", "max_positions": 3, "overrides": {}},

        # === REMOVE ALL CAPS ===
        {"name": "no_caps", "overrides": {
            "PER_MARKET_CAP_PCT": 100, "PER_SIDE_PCT": 100,
            "GLOBAL_EXPOSURE_PCT": 200, "CASH_RESERVE_PCT": 0,
        }},
        {"name": "no_caps_b10", "overrides": {
            "PER_MARKET_CAP_PCT": 100, "PER_SIDE_PCT": 100,
            "GLOBAL_EXPOSURE_PCT": 200, "CASH_RESERVE_PCT": 0,
            "SCALE_BOOST": 10,
        }},

        # === HIGHER POOL (more cash available) ===
        {"name": "pool_3x", "overrides": {"POOL_CAPITAL_MULTIPLIER": 3}},
        {"name": "pool_4x", "overrides": {"POOL_CAPITAL_MULTIPLIER": 4}},

        # === BIGGER BUDGET ===
        {"name": "budget_90", "budget": 90, "overrides": {}},
        {"name": "budget_200", "budget": 200, "overrides": {}},
        {"name": "budget_200_b12", "budget": 200, "overrides": {"SCALE_BOOST": 12}},
        {"name": "budget_200_nocap", "budget": 200, "overrides": {
            "PER_MARKET_CAP_PCT": 100, "PER_SIDE_PCT": 100,
            "GLOBAL_EXPOSURE_PCT": 200, "CASH_RESERVE_PCT": 0,
        }},

        # === TURBO RECYCLE: Sell profitable positions before every new buy ===
        {"name": "turbo_5%", "turbo_recycle": True, "turbo_min_profit_pct": 5, "overrides": {}},
        {"name": "turbo_10%", "turbo_recycle": True, "turbo_min_profit_pct": 10, "overrides": {}},
        {"name": "turbo_15%", "turbo_recycle": True, "turbo_min_profit_pct": 15, "overrides": {}},
        {"name": "turbo_0%", "turbo_recycle": True, "turbo_min_profit_pct": 0, "overrides": {}},

        # === TURBO + HIGHER BOOST (recycle enables higher deployment) ===
        {"name": "turbo10+b12", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "overrides": {"SCALE_BOOST": 12}},
        {"name": "turbo10+b16", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "overrides": {"SCALE_BOOST": 16}},
        {"name": "turbo10+budget90", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "budget": 90, "overrides": {}},

        # === SELL ALL ON LEADER SELL (full exit, free cash) ===
        {"name": "sell_all_leader", "sell_all_on_leader_sell": True, "overrides": {}},
        {"name": "sell_all+turbo", "sell_all_on_leader_sell": True,
         "turbo_recycle": True, "turbo_min_profit_pct": 10, "overrides": {}},

        # === DELAYED + CONCENTRATED (ultra-selective) ===
        {"name": "delay20+max2", "min_entry_minute": 20, "max_positions": 2, "overrides": {}},
        {"name": "delay20+max2+b16", "min_entry_minute": 20, "max_positions": 2,
         "overrides": {"SCALE_BOOST": 16}},
        {"name": "delay30+max1+b20", "min_entry_minute": 30, "max_positions": 1,
         "overrides": {"SCALE_BOOST": 20}},

        # === COMBINED BEST IDEAS ===
        {"name": "turbo+nocap+b10", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "overrides": {"SCALE_BOOST": 10, "PER_MARKET_CAP_PCT": 100, "PER_SIDE_PCT": 100,
                       "GLOBAL_EXPOSURE_PCT": 200}},
        {"name": "turbo+budget200", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "budget": 200, "overrides": {}},
        {"name": "turbo+b200+nocap", "turbo_recycle": True, "turbo_min_profit_pct": 10,
         "budget": 200, "overrides": {"PER_MARKET_CAP_PCT": 100, "PER_SIDE_PCT": 100,
                                       "GLOBAL_EXPOSURE_PCT": 200}},
        {"name": "ultra_aggr", "turbo_recycle": True, "turbo_min_profit_pct": 5,
         "budget": 200, "overrides": {"SCALE_BOOST": 12, "PER_MARKET_CAP_PCT": 100,
                                       "PER_SIDE_PCT": 100, "GLOBAL_EXPOSURE_PCT": 200}},
    ]

    results = []
    for i, v in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        total_buys = 0
        total_recycles = 0

        for session, utc_h, hf, split in all_hours:
            r = run_radical(hf, v)
            if r:
                split_pnl[split].append(r["pnl"])
                total_buys += r["buys"]
                total_recycles += r["recycles"]

        train = sum(split_pnl["TRAIN"])
        test = sum(split_pnl["TEST"])
        hold = sum(split_pnl["HOLDOUT"])
        combined = train + test + hold
        all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        sr = sharpe(all_pnls)
        robust = train > 0 and test > 0 and hold > 0

        results.append({
            "name": v["name"], "train": train, "test": test, "holdout": hold,
            "combined": combined, "sharpe": sr, "robust": robust,
            "hours": len(all_pnls), "buys": total_buys, "recycles": total_recycles,
        })

        rob = "YES" if robust else "no"
        extra = f" buys={total_buys}" + (f" rec={total_recycles}" if total_recycles > 0 else "")
        print(f"  [{i+1:>2}/{len(variants)}] {v['name']:<24} Tr=${train:>+8.2f} Te=${test:>+8.2f} Ho=${hold:>+8.2f} => ${combined:>+9.2f} S={sr:+.3f} [{rob}]{extra}")

    # Ranking
    print(f"\n{'='*130}")
    print(f"  FINAL RANKING (sorted by combined PnL)")
    print(f"{'='*130}")
    print(f"  {'#':>2} {'Variant':<26} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} | {'Total$':>8} {'Sharpe':>7} {'$/hr':>6} | {'Buys':>5} {'Rec':>4} | {'Rob':>3}")
    print(f"  {'-'*110}")

    for rank, r in enumerate(sorted(results, key=lambda x: -x["combined"]), 1):
        rob = "Y" if r["robust"] else "n"
        per_hr = r["combined"] / max(1, r["hours"])
        print(f"  {rank:>2} {r['name']:<26} | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} | ${r['combined']:>+7.2f} {r['sharpe']:>+6.3f} ${per_hr:>+5.2f} | {r['buys']:>5} {r['recycles']:>4} | {rob:>3}")

    # Sharpe ranking
    print(f"\n  SHARPE RANKING (robust only)")
    robust_sorted = sorted([r for r in results if r["robust"]], key=lambda x: -x["sharpe"])
    for rank, r in enumerate(robust_sorted[:15], 1):
        per_hr = r["combined"] / max(1, r["hours"])
        print(f"  {rank:>2} {r['name']:<26} | ${r['combined']:>+7.2f} S={r['sharpe']:>+6.3f} $/hr=${per_hr:>+5.2f} buys={r['buys']}")


if __name__ == "__main__":
    main()
