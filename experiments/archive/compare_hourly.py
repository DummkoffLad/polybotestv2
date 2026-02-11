"""Compare per-hour independent vs continuous replay for S1 and S2.

Fixed attribution: continuous tracks cash AFTER resolution of that hour's positions
(resolution happens at start of next hour, but we attribute it to the hour that owned them).
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

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def get_utc_hour_from_file(path: Path) -> int:
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


def liquidate_at_resolution(strategy, all_prices) -> None:
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


def run_continuous(session_path: Path) -> dict:
    """Run continuous replay.

    Attribution: after each hour's events + the resolution that fires at start of next hour,
    we record that hour's cash. This gives us the true hour PnL including resolution.
    """
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(session_path, strategy, config_overrides=CONFIG_OVERRIDES)
    replayer.load()

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    hour_data = {}
    current_hour = None
    hour_buys = hour_sells = hour_skips = 0
    hour_skip_reasons = defaultdict(int)
    events_in_hour = 0
    initial_cash = float(strategy.cash)
    prev_hour_info = None  # Store previous hour's counters until resolution

    for i, event in enumerate(replayer.loader.events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event_hour = event.trade.timestamp.hour

        if current_hour is not None and event_hour != current_hour:
            # Store the current hour's counters (before resolution)
            prev_hour_info = {
                "hour": current_hour,
                "events": events_in_hour,
                "buys": hour_buys, "sells": hour_sells, "skips": hour_skips,
                "skip_reasons": dict(hour_skip_reasons),
            }
            hour_buys = hour_sells = hour_skips = 0
            hour_skip_reasons = defaultdict(int)
            events_in_hour = 0

        current_hour = event_hour

        # Process event (on_event triggers _liquidate_hour_boundary if hour changed)
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)
        events_in_hour += 1

        # If this was the first event of a new hour, resolution just happened.
        # Now save the PREVIOUS hour with post-resolution cash.
        if prev_hour_info is not None:
            h = prev_hour_info["hour"]
            hour_data[h] = {
                **prev_hour_info,
                "cash_after_resolve": float(strategy.cash),  # After resolution
                "positions_after_resolve": len(strategy.portfolio.get_positions()),
            }
            prev_hour_info = None

        # Process fill
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)
            if decision.action == DecisionAction.BUY:
                hour_buys += 1
            else:
                hour_sells += 1
        else:
            hour_skips += 1
            reason = decision.skip_reason or "unknown"
            hour_skip_reasons[reason] += 1

    # Save final hour - manually resolve positions
    if current_hour is not None:
        last_all_prices = replayer.loader.get_all_prices_at_time(
            replayer.loader.events[-1].trade.timestamp
        )
        liquidate_at_resolution(strategy, last_all_prices)
        hour_data[current_hour] = {
            "hour": current_hour,
            "events": events_in_hour,
            "buys": hour_buys, "sells": hour_sells, "skips": hour_skips,
            "skip_reasons": dict(hour_skip_reasons),
            "cash_after_resolve": float(strategy.cash),
            "positions_after_resolve": 0,
        }

    # Calculate per-hour PnL from cash changes
    prev_cash = initial_cash
    for h in sorted(hour_data.keys()):
        cash = hour_data[h]["cash_after_resolve"]
        hour_data[h]["hour_pnl"] = cash - prev_cash
        hour_data[h]["start_cash"] = prev_cash
        prev_cash = cash

    return {"hours": hour_data, "final_cash": float(strategy.cash), "initial_cash": initial_cash}


def run_independent_hour(hour_file: Path) -> dict:
    """Run a single hour independently, then liquidate at resolution prices."""
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)

    try:
        count = replayer.load()
    except Exception as e:
        return {"error": str(e), "events": 0, "total_pnl": 0}
    if count == 0:
        return {"events": 0, "total_pnl": 0}

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    buys = sells = skips = 0
    skip_reasons = defaultdict(int)
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
        else:
            skips += 1
            reason = decision.skip_reason or "unknown"
            skip_reasons[reason] += 1

    # Force liquidate at resolution prices
    liquidate_at_resolution(strategy, last_all_prices)
    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial

    return {
        "events": count, "buys": buys, "sells": sells, "skips": skips,
        "skip_reasons": dict(skip_reasons), "total_pnl": pnl,
        "final_cash": float(strategy.cash),
    }


def compare_session(name: str, full_session: Path, hour_files: dict):
    print(f"\n{'='*100}")
    print(f"  SESSION: {name}")
    print(f"{'='*100}")

    hour_file_to_utc = {}
    for file_num, path in sorted(hour_files.items()):
        utc_h = get_utc_hour_from_file(path)
        if utc_h >= 0:
            hour_file_to_utc[file_num] = utc_h

    indep_by_utc = {}
    for file_num, path in sorted(hour_files.items()):
        utc_h = hour_file_to_utc.get(file_num, -1)
        if utc_h >= 0:
            indep_by_utc[utc_h] = run_independent_hour(path)

    cont = run_continuous(full_session)
    all_hours = sorted(set(list(cont["hours"].keys()) + list(indep_by_utc.keys())))

    print(f"\n{'Hour':>6} | {'C PnL':>9} {'I PnL':>9} {'Delta':>8} | {'C B/S':>7} {'I B/S':>7} | {'Start$':>7} {'C End$':>7} {'I End$':>7} | Notes")
    print("-" * 115)

    total_cont = 0
    total_indep = 0

    for h in all_hours:
        c = cont["hours"].get(h, {})
        ind = indep_by_utc.get(h, {})

        c_pnl = c.get("hour_pnl", 0)
        i_pnl = ind.get("total_pnl", 0)
        delta = c_pnl - i_pnl

        c_buys = c.get("buys", 0)
        c_sells = c.get("sells", 0)
        i_buys = ind.get("buys", 0)
        i_sells = ind.get("sells", 0)

        c_start = c.get("start_cash", 0)
        c_end = c.get("cash_after_resolve", 0)
        i_end = ind.get("final_cash", 0)

        total_cont += c_pnl
        total_indep += i_pnl

        notes = []
        if c_buys != i_buys or c_sells != i_sells:
            notes.append(f"TRADES({c_buys}/{c_sells} v {i_buys}/{i_sells})")
        if abs(delta) > 5:
            notes.append("BIG")
        elif abs(delta) > 1:
            notes.append("diff")

        c_reasons = c.get("skip_reasons", {})
        for reason in ["no_cash"]:
            cr = c_reasons.get(reason, 0)
            if cr > 0:
                notes.append(f"no_cash:{cr}")

        if c_start < 50:
            notes.append(f"STARVED(${c_start:.0f})")

        notes_str = " ".join(notes) if notes else "OK"
        print(f"  H{h:02d}  | ${c_pnl:>+7.2f} ${i_pnl:>+7.2f} ${delta:>+6.2f} | {c_buys:>2}/{c_sells:>2}  {i_buys:>2}/{i_sells:>2}  | ${c_start:>6.2f} ${c_end:>6.2f} ${i_end:>6.2f} | {notes_str}")

    print("-" * 115)
    print(f"{'TOTAL':>6} | ${total_cont:>+7.2f} ${total_indep:>+7.2f} ${total_cont - total_indep:>+6.2f}")
    print(f"\nContinuous: ${cont['final_cash']:.2f} final (started ${cont['initial_cash']:.0f})")
    print(f"Independent: ${sum(ind.get('total_pnl', 0) for ind in indep_by_utc.values()):+.2f} cumulative")


def main():
    base = Path("data/sessions")

    s1_full = base / "2026-02-03" / "05-56.jsonl"
    s1_hours = {int(f.stem.split("_hour_")[1]): f
                for f in sorted((base / "2026-02-03").glob("05-56_hour_*.jsonl"))}

    s2_full = base / "2026-02-04" / "02-35.jsonl"
    s2_hours = {int(f.stem.split("_hour_")[1]): f
                for f in sorted((base / "2026-02-04").glob("02-35_hour_*.jsonl"))}

    compare_session("S1 (2026-02-03)", s1_full, s1_hours)
    compare_session("S2 (2026-02-04)", s2_full, s2_hours)


if __name__ == "__main__":
    main()
