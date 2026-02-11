"""Grid search: scale_boost x MIN_LEADER_TRADE_PCT across all hourly sessions.

Tests 4 combos per hour, reports per-session and aggregate stats.
"""
import sys
import json
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

# Parameter grid
BOOST_VALUES = [Decimal("6"), Decimal("8"), Decimal("10"), Decimal("12")]
MIN_TRADE_PCT_VALUES = [Decimal("0.6"), Decimal("0.8"), Decimal("1.0"), Decimal("1.2")]


def liquidate_at_resolution(strategy, all_prices):
    """Force-liquidate all positions at resolution prices."""
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


def run_hour(hour_file: Path, boost: Decimal, min_pct: Decimal) -> dict:
    """Run a single hour with specific parameters. Returns result dict."""
    # Monkey-patch the module constant
    pt_module.MIN_LEADER_TRADE_PCT = min_pct

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)

    try:
        count = replayer.load()
    except Exception as e:
        return {"error": str(e), "events": 0, "pnl": 0, "buys": 0, "sells": 0}
    if count == 0:
        return {"events": 0, "pnl": 0, "buys": 0, "sells": 0}

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    # Override the dynamic boost
    strategy.scale_boost = boost
    strategy.on_session_start()

    buys = sells = 0
    last_all_prices = {}

    for i, event in enumerate(replayer.loader.events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)
            if decision.action == DecisionAction.BUY:
                buys += 1
            else:
                sells += 1

    # Liquidate at resolution prices
    liquidate_at_resolution(strategy, last_all_prices)
    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial

    return {
        "events": count, "buys": buys, "sells": sells,
        "pnl": round(pnl, 2), "final_cash": round(float(strategy.cash), 2),
    }


def get_utc_hour_from_file(path: Path) -> int:
    """Extract UTC hour from first trade event in file."""
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


def discover_sessions():
    """Find all session hour files grouped by session."""
    base = Path("data/sessions")
    sessions = {}

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        # Find hour files
        hour_files = sorted(date_dir.glob("*_hour_*.jsonl"))
        if not hour_files:
            continue

        # Group by session prefix
        for hf in hour_files:
            prefix = hf.stem.split("_hour_")[0]
            key = f"{date_dir.name}/{prefix}"
            if key not in sessions:
                sessions[key] = []
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                sessions[key].append((utc_h, hf))

    return sessions


def main():
    sessions = discover_sessions()
    total_hours = sum(len(hours) for hours in sessions.values())
    combos = [(b, p) for b in BOOST_VALUES for p in MIN_TRADE_PCT_VALUES]

    print(f"Grid search: {len(combos)} combos x {total_hours} hours across {len(sessions)} sessions")
    print(f"Parameters: boost={[str(b) for b in BOOST_VALUES]}, min_pct={[str(p) for p in MIN_TRADE_PCT_VALUES]}")
    print()

    # Results: combo -> list of per-hour PnL
    results = {(str(b), str(p)): [] for b, p in combos}
    session_results = {(str(b), str(p)): defaultdict(list) for b, p in combos}

    done = 0
    for session_name, hours in sorted(sessions.items()):
        for utc_h, hf in sorted(hours):
            done += 1
            for boost, min_pct in combos:
                r = run_hour(hf, boost, min_pct)
                key = (str(boost), str(min_pct))
                pnl = r["pnl"]
                results[key].append(pnl)
                session_results[key][session_name].append(pnl)

            if done % 10 == 0:
                print(f"  {done}/{total_hours} hours done...")

    # Print results
    print(f"\n{'='*120}")
    print(f"  GRID SEARCH RESULTS ({total_hours} hourly trials)")
    print(f"{'='*120}")

    header = f"{'Boost':>6} {'MinPct':>6} | {'TotalPnL':>9} {'AvgPnL':>8} {'WinRate':>7} {'Wins':>5} {'Loss':>5} | {'StdDev':>7} {'MaxWin':>7} {'MaxLoss':>8} {'Sharpe':>7}"
    print(header)
    print("-" * 120)

    ranked = []
    for (boost, min_pct), pnl_list in sorted(results.items()):
        total = sum(pnl_list)
        avg = total / len(pnl_list) if pnl_list else 0
        wins = sum(1 for p in pnl_list if p > 0)
        losses = sum(1 for p in pnl_list if p < 0)
        breakeven = sum(1 for p in pnl_list if p == 0)
        win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        max_win = max(pnl_list) if pnl_list else 0
        max_loss = min(pnl_list) if pnl_list else 0

        import statistics
        std = statistics.stdev(pnl_list) if len(pnl_list) > 1 else 0
        sharpe = avg / std if std > 0 else 0

        ranked.append((total, boost, min_pct, avg, win_rate, wins, losses, std, max_win, max_loss, sharpe))

    # Sort by total PnL descending
    ranked.sort(key=lambda x: x[0], reverse=True)

    for total, boost, min_pct, avg, wr, wins, losses, std, mw, ml, sharpe in ranked:
        marker = " <-- BEST" if ranked[0][0] == total else ""
        print(f"  {boost:>5} {min_pct:>6} | ${total:>+7.2f} ${avg:>+6.2f} {wr:>5.1f}% {wins:>5} {losses:>5} | ${std:>5.2f} ${mw:>+5.2f} ${ml:>+6.2f} {sharpe:>+6.3f}{marker}")

    # Per-session breakdown for top 3
    print(f"\n{'='*120}")
    print(f"  PER-SESSION BREAKDOWN (Top 4 combos)")
    print(f"{'='*120}")

    for idx, (total, boost, min_pct, *_) in enumerate(ranked[:4]):
        key = (boost, min_pct)
        print(f"\n  #{idx+1}: boost={boost}, min_pct={min_pct} (Total: ${total:+.2f})")
        for sname in sorted(sessions.keys()):
            pnl_list = session_results[key].get(sname, [])
            if not pnl_list:
                continue
            s_total = sum(pnl_list)
            s_wins = sum(1 for p in pnl_list if p > 0)
            s_losses = sum(1 for p in pnl_list if p < 0)
            s_wr = s_wins / (s_wins + s_losses) * 100 if (s_wins + s_losses) > 0 else 0
            print(f"    {sname:>25}: ${s_total:>+7.2f}  W{s_wins}/L{s_losses} ({s_wr:.0f}%)")

    # Save raw data
    output = {
        "params": {
            "boost_values": [str(b) for b in BOOST_VALUES],
            "min_pct_values": [str(p) for p in MIN_TRADE_PCT_VALUES],
        },
        "summary": [],
    }
    for total, boost, min_pct, avg, wr, wins, losses, std, mw, ml, sharpe in ranked:
        output["summary"].append({
            "boost": boost, "min_pct": min_pct,
            "total_pnl": total, "avg_pnl": avg, "win_rate": wr,
            "wins": wins, "losses": losses, "std_dev": std,
            "max_win": mw, "max_loss": ml, "sharpe": sharpe,
        })

    with open("grid_search_params_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nRaw data saved to grid_search_params_results.json")


if __name__ == "__main__":
    main()
