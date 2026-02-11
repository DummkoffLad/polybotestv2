"""Fine-tune circuit breaker and late-hour thresholds.

Test variants with different aggressiveness levels.
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


def run_hour(hour_file, overrides):
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
        return {"pnl": 0}
    if count == 0:
        return {"pnl": 0}

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
    return {"pnl": round(pnl, 2), "skips": dict(strategy.skip_reasons)}


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


# All use boost=8, min_pct=1.2 (grid search winner)
BASE = {"boost": Decimal("8"), "min_pct": Decimal("1.2")}

VARIANTS = {
    # Baseline: no protections
    "No protection": {**BASE, "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),
                      "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("60")},

    # Circuit breaker only (no late-hour)
    "CB $12/$20": {**BASE, "dd_reduce": Decimal("12"), "dd_stop": Decimal("20"),
                   "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("50")},
    "CB $15/$25": {**BASE, "dd_reduce": Decimal("15"), "dd_stop": Decimal("25"),
                   "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("50")},
    "CB $18/$28": {**BASE, "dd_reduce": Decimal("18"), "dd_stop": Decimal("28"),
                   "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("50")},
    "CB $20/$35": {**BASE, "dd_reduce": Decimal("20"), "dd_stop": Decimal("35"),
                   "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("50")},

    # Circuit breaker + gentle late-hour
    "CB$15/25+LH48/55": {**BASE, "dd_reduce": Decimal("15"), "dd_stop": Decimal("25"),
                          "late_reduce": 48, "late_stop": 55, "mkt_cap": Decimal("50")},
    "CB$18/28+LH48/55": {**BASE, "dd_reduce": Decimal("18"), "dd_stop": Decimal("28"),
                          "late_reduce": 48, "late_stop": 55, "mkt_cap": Decimal("50")},

    # Just late-hour, no circuit breaker
    "LH 48/55 only": {**BASE, "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),
                       "late_reduce": 48, "late_stop": 55, "mkt_cap": Decimal("50")},

    # Market cap only
    "MktCap 40%": {**BASE, "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),
                    "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("40")},
    "MktCap 50%": {**BASE, "dd_reduce": Decimal("9999"), "dd_stop": Decimal("9999"),
                    "late_reduce": 99, "late_stop": 99, "mkt_cap": Decimal("50")},
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

    print(f"\n{'='*130}")
    print(f"  RESULTS ({len(hours)} hourly trials) — sorted by Sharpe ratio")
    print(f"{'='*130}")
    print(f"\n{'Variant':<22} | {'Total':>7} {'Avg':>6} {'WR':>5} {'W':>3} {'L':>3} | {'Std':>6} {'MaxW':>6} {'MaxL':>7} {'Sharpe':>7} | >$20L >$30L")
    print("-" * 130)

    rows = []
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
        big_loss = sum(1 for p in pnl_list if p < -20)
        huge_loss = sum(1 for p in pnl_list if p < -30)
        rows.append((sharpe, name, total, avg, wr, wins, losses, std, maxw, maxl, big_loss, huge_loss))

    rows.sort(key=lambda x: x[0], reverse=True)
    for sharpe, name, total, avg, wr, wins, losses, std, maxw, maxl, bl, hl in rows:
        marker = " <-- BEST" if rows[0][1] == name else ""
        print(f"  {name:<20} | ${total:>+5.0f} ${avg:>+4.2f} {wr:>4.1f}% {wins:>3} {losses:>3} | ${std:>4.1f} ${maxw:>+4.0f} ${maxl:>+5.0f} {sharpe:>+6.3f} | {bl:>4} {hl:>5}{marker}")

    # Session breakdown for top 3
    top3 = [r[1] for r in rows[:3]]
    print(f"\n  PER-SESSION for top 3 + baseline:")
    show = top3 + ["No protection"]
    sessions = sorted(set(s for s, _, _ in hours))

    header = f"  {'Session':<26}"
    for name in show:
        short = name[:14]
        header += f" | {short:>14}"
    print(header)
    print("-" * 100)

    for session in sessions:
        row = f"  {session:<26}"
        for name in show:
            pnl_list = session_results[name].get(session, [])
            s_total = sum(pnl_list)
            s_wins = sum(1 for p in pnl_list if p > 0)
            s_losses = sum(1 for p in pnl_list if p < 0)
            row += f" | ${s_total:>+5.0f} {s_wins}W/{s_losses}L"
        print(row)


if __name__ == "__main__":
    main()
