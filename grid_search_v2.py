"""Test the improved strategy (circuit breaker + late-hour) vs old settings.

Compares:
1. NEW: boost=8, min_pct=1.2, circuit breaker ON, late-hour ON (current code)
2. OLD BEST: boost=8, min_pct=1.2, circuit breaker OFF, late-hour OFF
3. OLD DEFAULT: boost=10, min_pct=1.0, no protections
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


def run_hour(hour_file, overrides: dict) -> dict:
    """Run a single hour with specific parameter overrides."""
    # Apply module-level overrides
    pt_module.MIN_LEADER_TRADE_PCT = overrides.get("min_pct", Decimal("1.2"))
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = overrides.get("dd_reduce", Decimal("12"))
    pt_module.DRAWDOWN_STOP_THRESHOLD = overrides.get("dd_stop", Decimal("20"))
    pt_module.LATE_HOUR_REDUCE_MIN = overrides.get("late_reduce", 40)
    pt_module.LATE_HOUR_STOP_MIN = overrides.get("late_stop", 52)
    pt_module.PER_MARKET_CAP_PCT = overrides.get("mkt_cap", Decimal("50"))
    pt_module.SCALE_BOOST = overrides.get("boost", Decimal("8"))

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return {"events": 0, "pnl": 0}
    if count == 0:
        return {"events": 0, "pnl": 0}

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.scale_boost = overrides.get("boost", Decimal("8"))
    strategy.on_session_start()

    last_all_prices = {}
    for i, event in enumerate(replayer.loader.events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    liquidate_at_resolution(strategy, last_all_prices)
    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial

    # Collect skip reasons for analysis
    return {
        "events": count, "pnl": round(pnl, 2),
        "skips": dict(strategy.skip_reasons),
        "buys": strategy.buys, "sells": strategy.sells,
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
    hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                hours.append((session, utc_h, hf))
    return hours


VARIANTS = {
    "NEW (8x/1.2%/CB/LH)": {
        "boost": Decimal("8"), "min_pct": Decimal("1.2"),
        "dd_reduce": Decimal("12"), "dd_stop": Decimal("20"),
        "late_reduce": 40, "late_stop": 52,
        "mkt_cap": Decimal("50"),
    },
    "OLD BEST (8x/1.2%)": {
        "boost": Decimal("8"), "min_pct": Decimal("1.2"),
        "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),  # Disabled
        "late_reduce": 99, "late_stop": 99,  # Disabled
        "mkt_cap": Decimal("60"),
    },
    "OLD DEFAULT (10x/1.0%)": {
        "boost": Decimal("10"), "min_pct": Decimal("1.0"),
        "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),
        "late_reduce": 99, "late_stop": 99,
        "mkt_cap": Decimal("60"),
    },
}


def main():
    hours = discover_hours()
    print(f"Testing {len(VARIANTS)} variants across {len(hours)} hours\n")

    results = {name: [] for name in VARIANTS}
    session_results = {name: defaultdict(list) for name in VARIANTS}

    for idx, (session, utc_h, hf) in enumerate(hours):
        for name, overrides in VARIANTS.items():
            r = run_hour(hf, overrides)
            results[name].append(r["pnl"])
            session_results[name][session].append(r["pnl"])

        if (idx + 1) % 20 == 0:
            print(f"  {idx+1}/{len(hours)} hours done...")

    # Print comparison
    print(f"\n{'='*110}")
    print(f"  COMPARISON: {len(hours)} hourly trials")
    print(f"{'='*110}")
    print(f"\n{'Variant':<28} | {'Total':>8} {'Avg':>7} {'WinR':>6} {'W':>4} {'L':>4} | {'StdDev':>7} {'MaxW':>7} {'MaxL':>8} {'Sharpe':>7}")
    print("-" * 110)

    for name, pnl_list in results.items():
        total = sum(pnl_list)
        avg = total / len(pnl_list)
        wins = sum(1 for p in pnl_list if p > 0)
        losses = sum(1 for p in pnl_list if p < 0)
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        std = statistics.stdev(pnl_list) if len(pnl_list) > 1 else 0
        sharpe = avg / std if std > 0 else 0
        maxw = max(pnl_list)
        maxl = min(pnl_list)
        print(f"  {name:<26} | ${total:>+6.0f} ${avg:>+5.2f} {wr:>4.1f}% {wins:>4} {losses:>4} | ${std:>5.2f} ${maxw:>+5.0f} ${maxl:>+6.0f} {sharpe:>+6.3f}")

    # Per-session detail
    print(f"\n{'='*110}")
    print(f"  PER-SESSION BREAKDOWN")
    print(f"{'='*110}")
    sessions = sorted(set(s for s, _, _ in hours))

    header = f"  {'Session':<28}"
    for name in VARIANTS:
        short = name.split("(")[0].strip()
        header += f" | {short:>12}"
    print(header)
    print("-" * 110)

    for session in sessions:
        row = f"  {session:<28}"
        for name in VARIANTS:
            pnl_list = session_results[name].get(session, [])
            s_total = sum(pnl_list)
            s_wins = sum(1 for p in pnl_list if p > 0)
            s_losses = sum(1 for p in pnl_list if p < 0)
            row += f" | ${s_total:>+6.0f} {s_wins}W/{s_losses}L"
        print(row)

    # Loss tail comparison (how many hours lost more than $X)
    print(f"\n{'='*110}")
    print(f"  TAIL RISK COMPARISON")
    print(f"{'='*110}")
    thresholds = [10, 15, 20, 25, 30, 35, 40]
    header = f"  {'Loss > $X':<20}"
    for name in VARIANTS:
        short = name.split("(")[0].strip()
        header += f" | {short:>8}"
    print(header)
    print("-" * 80)

    for threshold in thresholds:
        row = f"  Loss > ${threshold:<16}"
        for name in VARIANTS:
            count = sum(1 for p in results[name] if p < -threshold)
            row += f" | {count:>8}"
        print(row)

    # Circuit breaker & late-hour skip analysis for NEW variant
    print(f"\n  Circuit breaker and late-hour skips (NEW variant):")
    new_overrides = VARIANTS["NEW (8x/1.2%/CB/LH)"]
    total_cb = total_lh = 0
    for session, utc_h, hf in hours:
        r = run_hour(hf, new_overrides)
        total_cb += r.get("skips", {}).get("circuit_breaker", 0)
        total_lh += r.get("skips", {}).get("late_hour", 0)
    print(f"    circuit_breaker skips: {total_cb}")
    print(f"    late_hour skips: {total_lh}")


if __name__ == "__main__":
    main()
