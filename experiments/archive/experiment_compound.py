"""Test compounding: run hours SEQUENTIALLY with growing cash balance.

In real trading, profits from hour 1 increase capital for hour 2.
Our backtests reset to $50 each hour — this misses compound growth.

Also test: what if we reinvest more aggressively as capital grows?
Dynamic boost: as we profit, increase position sizes.
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

ALL_SESSION_ORDER = [
    "2026-02-03/05-56", "2026-02-04/02-35", "2026-02-05/03-58",
    "2026-02-05/22-15", "2026-02-06/05-30", "2026-02-07/05-52",
    "2026-02-08/06-39",
]


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


def run_hour_with_capital(hour_file, capital, boost=8):
    """Run one hour with specified capital, return PnL and ending cash."""
    orig_boost = pt_mod.SCALE_BOOST
    pt_mod.SCALE_BOOST = Decimal(str(boost))

    budget = max(float(capital) * 0.9, 10)  # 90% of capital as budget

    config_overrides = {
        "scaling.our_capital": float(capital),
        "scaling.hourly_budget": budget,
        "scaling.k_factor": 1,
        "scaling.leader_estimated_capital": 900,
    }

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=config_overrides)
    try:
        count = replayer.load()
    except Exception:
        pt_mod.SCALE_BOOST = orig_boost
        return {"pnl": 0, "buys": 0}
    if count == 0:
        pt_mod.SCALE_BOOST = orig_boost
        return {"pnl": 0, "buys": 0}

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

    pool = Decimal(str(float(capital))) * pt_mod.POOL_CAPITAL_MULTIPLIER
    pnl = float(strategy.cash) - float(pool)

    pt_mod.SCALE_BOOST = orig_boost

    return {"pnl": round(pnl, 2), "buys": strategy.buys}


def main():
    base = Path("data/sessions")

    # Collect all hours organized by session
    session_hours = defaultdict(list)
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                session_hours[session].append((utc_h, hf))

    # Sort sessions chronologically
    ordered_sessions = []
    for session_prefix in ALL_SESSION_ORDER:
        for session_name in sorted(session_hours.keys()):
            if session_prefix in session_name:
                hours = sorted(session_hours[session_name], key=lambda x: x[0])
                ordered_sessions.append((session_name, hours))
                break

    # Count total hours
    total_hours = sum(len(hours) for _, hours in ordered_sessions)
    print(f"Running sequential compounding across {total_hours} hours in {len(ordered_sessions)} sessions\n")

    # Test different starting capitals and compounding strategies
    scenarios = [
        ("$50 fixed (no compound)", 50, False, 8),
        ("$50 compound", 50, True, 8),
        ("$50 compound + dynamic boost", 50, True, "dynamic"),
        ("$50 compound + aggressive", 50, True, "aggressive"),
        ("$75 fixed", 75, False, 8),
        ("$75 compound", 75, True, 8),
        ("$100 fixed", 100, False, 8),
        ("$100 compound", 100, True, 8),
        ("$100 compound + dynamic", 100, True, "dynamic"),
    ]

    for scenario_name, starting_capital, compound, boost_mode in scenarios:
        print(f"\n{'='*100}")
        print(f"  {scenario_name} (starting ${starting_capital})")
        print(f"{'='*100}")

        capital = starting_capital
        total_pnl = 0
        hour_num = 0
        max_capital = starting_capital
        min_capital = starting_capital
        max_drawdown = 0
        peak_capital = starting_capital

        session_results = []

        for session_name, hours in ordered_sessions:
            split = get_split(session_name)
            session_pnl = 0

            for utc_h, hf in hours:
                hour_num += 1

                # Dynamic boost based on capital
                if boost_mode == "dynamic":
                    # Scale boost with capital: more capital = higher boost
                    ratio = capital / starting_capital
                    boost = max(6, min(16, int(8 * ratio)))
                elif boost_mode == "aggressive":
                    ratio = capital / starting_capital
                    boost = max(8, min(20, int(10 * ratio)))
                else:
                    boost = boost_mode

                result = run_hour_with_capital(hf, capital, boost=boost)
                hour_pnl = result["pnl"]
                total_pnl += hour_pnl
                session_pnl += hour_pnl

                if compound:
                    # Reinvest: capital grows (or shrinks) with PnL
                    # Capital = what we'd deposit. Pool = 2x capital.
                    # After a profitable hour, pool grows, so capital grows.
                    capital = max(10, starting_capital + total_pnl / 2)
                    # /2 because pool = 2x capital, so profit adds to pool

                max_capital = max(max_capital, capital)
                min_capital = min(min_capital, capital)
                peak_capital = max(peak_capital, starting_capital + total_pnl)
                current_dd = peak_capital - (starting_capital + total_pnl)
                max_drawdown = max(max_drawdown, current_dd)

            session_results.append({
                "session": session_name, "split": split,
                "pnl": session_pnl, "capital_at_end": capital,
            })

            print(f"  {split:<6} {session_name:<26} PnL=${session_pnl:>+8.2f} | Capital=${capital:>7.2f} | CumPnL=${total_pnl:>+8.2f}")

        print(f"\n  SUMMARY:")
        print(f"    Total PnL:       ${total_pnl:>+10.2f}")
        print(f"    Starting cap:    ${starting_capital:>10.2f}")
        print(f"    Ending cap:      ${capital:>10.2f}")
        print(f"    Peak cap:        ${max_capital:>10.2f}")
        print(f"    Min cap:         ${min_capital:>10.2f}")
        print(f"    Max drawdown:    ${max_drawdown:>10.2f}")
        print(f"    Total return:    {total_pnl/starting_capital*100:>+8.1f}%")
        print(f"    Hours traded:    {hour_num}")
        print(f"    $/hr average:    ${total_pnl/max(1,hour_num):>+8.2f}")

        # Split performance
        for split in ["TRAIN", "TEST", "HOLDOUT"]:
            split_pnl = sum(r["pnl"] for r in session_results if r["split"] == split)
            split_hrs = sum(1 for _ in [h for sn, hrs in ordered_sessions for h in hrs
                                        if get_split(sn) == split])
            print(f"    {split:>8}: ${split_pnl:>+8.2f}")


if __name__ == "__main__":
    main()
